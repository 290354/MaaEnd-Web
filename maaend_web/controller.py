"""MaaFramework 自定义控制器：把浏览器里的云游戏变成一台「设备」。

MaaFramework 只关心三件事：截一张图、点一个坐标、按一个键。
这里把这三件事翻译成 Chrome DevTools Protocol 调用，于是
``CloudADB`` / ``Win32`` 能跑的全部 Pipeline 都能原样跑在网页版上。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from maa.controller import CustomController

from . import geometry
from .cdp import CDPConnection
from .browser import BrowserSession
from .input_map import lookup, name_of

log = logging.getLogger(__name__)


@dataclass
class WebConfig:
    """控制器的可调参数，暴露到设置界面。"""

    # 强制把页面视口设为 1280x720：云游戏服务端会按此协商视频流，
    # 画面与 Pipeline 基准 1:1，识别精度最高。
    force_viewport: bool = True
    viewport_w: int = 1280
    viewport_h: int = 720
    # 截图格式：png 无损（默认），jpeg 更快但会损失细节
    screenshot_format: str = "png"
    screenshot_quality: int = 95
    # 反锐化强度，补偿视频编码模糊；0 表示关闭
    sharpen: float = 0.55
    # 相对移动模式：auto / lock / drag
    look_mode: str = "auto"
    # 点击后额外等待（毫秒），补偿云端的输入上行延迟
    click_settle_ms: int = 12
    # 单次截图超时
    screencap_timeout: float = 15.0
    # 是否把最后一次截图保存下来供界面预览
    keep_preview: bool = True


class WebController(CustomController):
    """基于 CDP 的云游戏控制器。"""

    def __init__(self, session: BrowserSession, config: Optional[WebConfig] = None):
        self.session = session
        self.config = config or WebConfig()
        self._conn: Optional[CDPConnection] = None
        self._transform: Optional[geometry.ViewTransform] = None
        self._lock = threading.RLock()
        self._connected = False
        self._uuid = ""
        # pointer lock 下的虚拟光标
        self._cursor = [0.0, 0.0]
        self._down_at: Optional[Tuple[float, float]] = None
        self._lock_mode = False
        self._frame = 0
        self._last_preview: Optional[np.ndarray] = None
        self._last_shot_ts = 0.0
        self._fps_window: list[float] = []
        super().__init__()

    # ---------------------------------------------------------------- 生命周期

    def connect(self) -> bool:
        try:
            conn = self.session.ensure_page(self._url_hint())
            self._conn = conn
            if self.config.force_viewport:
                self._apply_viewport(conn)
            self._transform = geometry.analyze(conn)
            self._uuid = self._detect_uuid()
            self._connected = True
            log.info(
                "已连接网页：视口 %s，画面区 %s，缩放 %.3f",
                self._transform.viewport,
                (
                    round(self._transform.content.x),
                    round(self._transform.content.y),
                    round(self._transform.content.w),
                    round(self._transform.content.h),
                ),
                self._transform.scale,
            )
            return True
        except Exception as exc:
            log.exception("连接网页失败: %s", exc)
            self._connected = False
            return False

    def connected(self) -> bool:
        return self._connected and self._conn is not None and not self._conn.closed

    def request_uuid(self) -> str:
        return self._uuid or "maaend-web"

    def start_app(self, intent: str) -> bool:
        """打开云游戏页面（intent 传 URL 时生效）。"""
        try:
            url = intent.strip() or self._url_hint()
            if not url.startswith("http"):
                url = self._url_hint()
            self.session.ensure_page(url)
            return True
        except Exception as exc:
            log.warning("start_app 失败: %s", exc)
            return False

    def stop_app(self, intent: str) -> bool:
        """关闭当前标签页。"""
        try:
            if self._conn and not self._conn.closed:
                self._conn.try_call("Page.close")
            return True
        except Exception:
            return False

    def inactive(self) -> bool:
        # 浏览器窗口不需要被"失活"，保持画面持续渲染
        return True

    # ---------------------------------------------------------------- 截图

    def screencap(self) -> np.ndarray:
        conn = self._require_conn()
        started = time.perf_counter()
        with self._lock:
            self._frame += 1
            # 每 30 帧校正一次视口，防止用户误触全屏/缩放导致坐标漂移
            if self.config.force_viewport and self._frame % 30 == 1:
                self._apply_viewport(conn)
                self._transform = geometry.analyze(conn, self._transform)
            transform = self._transform or geometry.analyze(conn)

            fmt = self.config.screenshot_format.lower()
            quality = self.config.screenshot_quality if fmt == "jpeg" else None
            data = conn.screenshot(fmt=fmt, quality=quality, timeout=self.config.screencap_timeout)

            image = self._decode(data)
            if image is None:
                raise RuntimeError("无法解码浏览器截图")

            vh, vw = image.shape[:2]
            # 截图是视口原始像素：按 content 区域裁出来
            x0 = max(0, int(round(transform.content.x)))
            y0 = max(0, int(round(transform.content.y)))
            x1 = min(vw, int(round(transform.content.x + transform.content.w)))
            y1 = min(vh, int(round(transform.content.y + transform.content.h)))
            if x1 - x0 < 16 or y1 - y0 < 16:
                x0, y0, x1, y1 = 0, 0, vw, vh

            cropped = image[y0:y1, x0:x1]
            out = geometry.letterbox_to_base(cropped, transform)
            # 只有画面被放大时才锐化：1:1 时锐化没有收益，反而干扰 ColorMatch
            strength = self.config.sharpen if self._needs_sharpen(cropped, transform) else 0.0
            if strength:
                out = geometry.sharpen(out, strength)

            if self.config.keep_preview:
                self._last_preview = out
            self._last_shot_ts = time.time()
            self._fps_window.append(started)
            if len(self._fps_window) > 20:
                self._fps_window.pop(0)
        return np.ascontiguousarray(out)

    def _needs_sharpen(self, cropped: np.ndarray, transform: "geometry.ViewTransform") -> bool:
        if self.config.sharpen <= 0:
            return False
        h, w = cropped.shape[:2]
        if w < self.config.viewport_w - 2 or h < self.config.viewport_h - 2:
            return True  # 内容区小于 720p，必然经过放大
        return transform.content.upscaled

    @staticmethod
    def _decode(data: bytes) -> Optional[np.ndarray]:
        import cv2

        buf = np.frombuffer(data, dtype=np.uint8)
        image = cv2.imdecode(buf, cv2.IMREAD_COLOR)  # BGR
        return image

    # ---------------------------------------------------------------- 指针

    def click(self, x: int, y: int) -> bool:
        try:
            vx, vy = self._map(x, y)
            conn = self._require_conn()
            with self._lock:
                conn.call("Input.dispatchMouseEvent", type="mouseMoved", x=vx, y=vy, button="none")
                conn.call(
                    "Input.dispatchMouseEvent",
                    type="mousePressed",
                    x=vx,
                    y=vy,
                    button="left",
                    buttons=1,
                    clickCount=1,
                )
                conn.call(
                    "Input.dispatchMouseEvent",
                    type="mouseReleased",
                    x=vx,
                    y=vy,
                    button="left",
                    buttons=0,
                    clickCount=1,
                )
            self._sleep(self.config.click_settle_ms)
            return True
        except Exception as exc:
            log.warning("click(%s,%s) 失败: %s", x, y, exc)
            return False

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
        try:
            ax, ay = self._map(x1, y1)
            bx, by = self._map(x2, y2)
            conn = self._require_conn()
            steps = max(2, min(60, int(duration / 16) if duration else 12))
            with self._lock:
                conn.call("Input.dispatchMouseEvent", type="mouseMoved", x=ax, y=ay, button="none")
                conn.call(
                    "Input.dispatchMouseEvent",
                    type="mousePressed",
                    x=ax,
                    y=ay,
                    button="left",
                    buttons=1,
                    clickCount=1,
                )
                for i in range(1, steps + 1):
                    t = i / steps
                    cx = ax + (bx - ax) * t
                    cy = ay + (by - ay) * t
                    conn.call(
                        "Input.dispatchMouseEvent",
                        type="mouseMoved",
                        x=cx,
                        y=cy,
                        button="left",
                        buttons=1,
                    )
                    if duration:
                        time.sleep(max(0.0, duration / 1000.0 / steps))
                conn.call(
                    "Input.dispatchMouseEvent",
                    type="mouseReleased",
                    x=bx,
                    y=by,
                    button="left",
                    buttons=0,
                    clickCount=1,
                )
            return True
        except Exception as exc:
            log.warning("swipe 失败: %s", exc)
            return False

    def touch_down(self, contact: int, x: int, y: int, pressure: int) -> bool:
        # 单指时用鼠标事件，游戏对鼠标的兼容性最好；多指时才启用触摸模拟
        if contact == 0:
            return self._mouse_button("mousePressed", x, y, pressure)
        return self._dispatch_touch("touchStart", [(contact, x, y)])

    def touch_move(self, contact: int, x: int, y: int, pressure: int) -> bool:
        if contact == 0:
            return self._mouse_move_down(x, y)
        return self._dispatch_touch("touchMove", [(contact, x, y)])

    def touch_up(self, contact: int) -> bool:
        if contact == 0:
            return self._mouse_button("mouseReleased", 0, 0, 0, release_only=True)
        return self._dispatch_touch("touchEnd", [])

    def _mouse_button(self, kind: str, x: int, y: int, pressure: int, release_only: bool = False) -> bool:
        try:
            conn = self._require_conn()
            if release_only:
                # 抬起必须落在按下的位置，否则长按/拖拽会在 MaaFramework
                # 「用 down/up 代替 click」的模式下点偏
                lx, ly = self._down_at or self._cursor_viewport()
            else:
                lx, ly = self._map(x, y)
                self._down_at = (lx, ly)
            with self._lock:
                conn.call(
                    "Input.dispatchMouseEvent",
                    type=kind,
                    x=lx,
                    y=ly,
                    button="left",
                    buttons=1 if kind == "mousePressed" else 0,
                    clickCount=1,
                    force=pressure / 1024.0 if pressure else 1.0,
                )
            return True
        except Exception as exc:
            log.warning("mouse %s 失败: %s", kind, exc)
            return False

    def _mouse_move_down(self, x: int, y: int) -> bool:
        try:
            conn = self._require_conn()
            vx, vy = self._map(x, y)
            with self._lock:
                conn.call(
                    "Input.dispatchMouseEvent",
                    type="mouseMoved",
                    x=vx,
                    y=vy,
                    button="left",
                    buttons=1,
                )
            return True
        except Exception as exc:
            log.warning("mouse move 失败: %s", exc)
            return False

    def _dispatch_touch(self, kind: str, points) -> bool:
        try:
            conn = self._require_conn()
            conn.try_call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=10)
            touch_points = []
            for contact, x, y in points:
                vx, vy = self._map(x, y)
                touch_points.append({"x": vx, "y": vy, "id": int(contact), "radiusX": 1, "radiusY": 1, "force": 1})
            with self._lock:
                conn.call("Input.dispatchTouchEvent", type=kind, touchPoints=touch_points)
            return True
        except Exception as exc:
            log.warning("touch %s 失败: %s", kind, exc)
            return False

    def relative_move(self, dx: int, dy: int) -> bool:
        """相对移动：用于 3D 视角转向。

        Pointer Lock 下浏览器用「位置差」计算 movementX/Y，
        而 CDP 允许我们把坐标设到视口外很远处，因此可以维护一个
        远超屏幕尺寸的虚拟光标，长时间转向也不会撞到边界。
        """
        try:
            conn = self._require_conn()
            mode = self._resolve_look_mode(conn)
            if mode == "drag":
                return self._relative_by_drag(dx, dy)

            with self._lock:
                if self._lock_mode:
                    self._cursor[0] += dx
                    self._cursor[1] += dy
                    # 极端情况下回到中心，避免数值无限增长
                    if abs(self._cursor[0]) > 90000 or abs(self._cursor[1]) > 90000:
                        self._cursor[0] = dx
                        self._cursor[1] = dy
                    vx, vy = self._cursor
                else:
                    # 未锁定：在视口内维持虚拟光标
                    vw, vh = self._transform.viewport if self._transform else (1280, 720)
                    self._cursor[0] = min(max(self._cursor[0] + dx, 2), vw - 2)
                    self._cursor[1] = min(max(self._cursor[1] + dy, 2), vh - 2)
                    vx, vy = self._cursor
                conn.call("Input.dispatchMouseEvent", type="mouseMoved", x=vx, y=vy, button="none")
            return True
        except Exception as exc:
            log.warning("relative_move 失败: %s", exc)
            return False

    def _relative_by_drag(self, dx: int, dy: int) -> bool:
        """按住右键拖拽转视角（部分云游戏用这种交互）。"""
        conn = self._require_conn()
        vw, vh = self._transform.viewport if self._transform else (1280, 720)
        cx, cy = vw / 2.0, vh / 2.0
        steps = max(1, min(12, int(max(abs(dx), abs(dy)) / 12) + 1))
        with self._lock:
            conn.call("Input.dispatchMouseEvent", type="mousePressed", x=cx, y=cy, button="right", buttons=2, clickCount=1)
            for i in range(1, steps + 1):
                conn.call(
                    "Input.dispatchMouseEvent",
                    type="mouseMoved",
                    x=cx + dx * i / steps,
                    y=cy + dy * i / steps,
                    button="right",
                    buttons=2,
                )
            conn.call("Input.dispatchMouseEvent", type="mouseReleased", x=cx + dx, y=cy + dy, button="right", buttons=0, clickCount=1)
        return True

    def scroll(self, dx: int, dy: int) -> bool:
        try:
            conn = self._require_conn()
            vx, vy = self._cursor_viewport()
            with self._lock:
                conn.call(
                    "Input.dispatchMouseEvent",
                    type="mouseWheel",
                    x=vx,
                    y=vy,
                    deltaX=int(dx),
                    deltaY=int(dy),
                )
            return True
        except Exception as exc:
            log.warning("scroll 失败: %s", exc)
            return False

    # ---------------------------------------------------------------- 键盘

    def click_key(self, keycode: int) -> bool:
        return self._key_event(keycode, down=True) and self._key_event(keycode, down=False)

    def key_down(self, keycode: int) -> bool:
        return self._key_event(keycode, down=True)

    def key_up(self, keycode: int) -> bool:
        return self._key_event(keycode, down=False)

    def _key_event(self, keycode: int, down: bool) -> bool:
        info = lookup(keycode)
        if info is None:
            log.debug("未映射的按键 VK=%s，尝试原始发送", keycode)
            info = ("", "", "")
        key, code, text = info
        try:
            conn = self._require_conn()
            params = {
                "type": "keyDown" if down else "keyUp",
                "key": key or "",
                "code": code or "",
                "windowsVirtualKeyCode": int(keycode),
                "nativeVirtualKeyCode": int(keycode),
                "autoRepeat": False,
                "isKeypad": False,
                "isSystemKey": False,
                "location": 0,
            }
            if down and text:
                params["text"] = text
                params["unmodifiedText"] = text
            with self._lock:
                conn.call("Input.dispatchKeyEvent", **params)
            return True
        except Exception as exc:
            log.warning("按键 %s(%s) 失败: %s", name_of(keycode), keycode, exc)
            return False

    def input_text(self, text: str) -> bool:
        try:
            conn = self._require_conn()
            with self._lock:
                conn.call("Input.insertText", text=text)
            return True
        except Exception as exc:
            log.warning("输入文本失败: %s", exc)
            return False

    # ---------------------------------------------------------------- 辅助

    def _url_hint(self) -> str:
        return getattr(self.session, "game_url", "") or "about:blank"

    def _require_conn(self) -> CDPConnection:
        if self._conn is None:
            conn = self.session.ensure_page(self._url_hint())
            self._conn = conn
        if self._conn.closed:
            raise RuntimeError("与浏览器的连接已断开")
        return self._conn

    def _apply_viewport(self, conn: CDPConnection) -> None:
        w, h = self.config.viewport_w, self.config.viewport_h
        conn.try_call(
            "Emulation.setDeviceMetricsOverride",
            width=w,
            height=h,
            deviceScaleFactor=1,
            mobile=False,
            screenWidth=w,
            screenHeight=h,
            dontSetVisibleSize=False,
        )
        conn.try_call("Emulation.setScrollbarsHidden", hidden=True)

    def _map(self, x: float, y: float) -> Tuple[float, float]:
        """720p 坐标 -> 视口坐标。"""
        transform = self._transform
        if transform is None:
            return float(x), float(y)
        vx, vy = transform.to_viewport(float(x), float(y))
        vw, vh = transform.viewport
        return min(max(vx, 0.0), vw - 1.0), min(max(vy, 0.0), vh - 1.0)

    def _cursor_viewport(self) -> Tuple[float, float]:
        return self._cursor[0] or 640.0, self._cursor[1] or 360.0

    def _resolve_look_mode(self, conn: CDPConnection) -> str:
        mode = (self.config.look_mode or "auto").lower()
        if mode in ("lock", "drag"):
            return mode
        try:
            locked = bool(conn.evaluate("!!document.pointerLockElement"))
        except Exception:
            locked = False
        self._lock_mode = locked
        if locked:
            # 刚进入锁定状态时把虚拟光标放到中心
            if self._cursor == [0.0, 0.0]:
                vw, vh = self._transform.viewport if self._transform else (1280, 720)
                self._cursor = [vw / 2.0, vh / 2.0]
            return "lock"
        return "absolute"

    def _detect_uuid(self) -> str:
        """尽量拿到一个稳定的设备标识，用于日志与任务缓存。"""
        conn = self._conn
        if conn is None:
            return "maaend-web"
        try:
            info = conn.evaluate(
                "({ua: navigator.userAgent, w: screen.width, h: screen.height})"
            ) or {}
            return f"web-{abs(hash(info.get('ua', ''))) % 10**8:08d}"
        except Exception:
            return "maaend-web"

    @staticmethod
    def _sleep(ms: int) -> None:
        if ms > 0:
            time.sleep(ms / 1000.0)

    # ---------------------------------------------------------------- 对外

    def refresh_transform(self) -> "geometry.ViewTransform":
        """重新探测画面区域（页面布局变化后调用）。"""
        conn = self._require_conn()
        if self.config.force_viewport:
            self._apply_viewport(conn)
        self._transform = geometry.analyze(conn, self._transform)
        return self._transform

    def has_content(self) -> bool:
        t = self._transform
        return bool(t and t.content.w > 200 and t.content.h > 120)

    @property
    def last_preview(self) -> Optional[np.ndarray]:
        return self._last_preview

    def preview_jpeg(self, quality: int = 80) -> Optional[bytes]:
        frame = self._last_preview
        if frame is None:
            return None
        import cv2

        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buf.tobytes() if ok else None

    def stats(self) -> dict:
        if len(self._fps_window) >= 2:
            span = self._fps_window[-1] - self._fps_window[0]
            fps = (len(self._fps_window) - 1) / span if span > 0 else 0.0
        else:
            fps = 0.0
        t = self._transform
        return {
            "fps": round(fps, 1),
            "viewport": list(t.viewport) if t else None,
            "content": [round(t.content.x), round(t.content.y), round(t.content.w), round(t.content.h)] if t else None,
            "scale": round(t.scale, 4) if t else None,
            "pointer_locked": self._lock_mode,
        }

    # ---------------------------------------------------------------- 网页交互

    def page_state(self) -> dict:
        """读取页面状态：登录框、开始游戏按钮、画面元素。"""
        conn = self._require_conn()
        try:
            return conn.evaluate(_PAGE_STATE_JS) or {}
        except Exception as exc:
            log.debug("读取页面状态失败: %s", exc)
            return {}

    def enter_game(self, timeout: float = 240.0, poll: float = 1.5) -> Tuple[bool, str]:
        """点击网页上的「开始游戏」，一路等到进入游戏。

        返回 (是否成功, 说明)。登录需要用户手动完成，这里只负责点按钮与等待。
        """
        conn = self._require_conn()
        deadline = time.time() + timeout
        last_text = ""
        while time.time() < deadline:
            state = self.page_state()
            if state.get("need_login"):
                return False, "需要登录：请在浏览器中完成鹰角通行证登录后重试"
            hit = state.get("enter_button")
            if hit:
                last_text = hit.get("text", "")
                log.info("点击「%s」(%s, %s)", last_text, round(hit["x"]), round(hit["y"]))
                with self._lock:
                    conn.call("Input.dispatchMouseEvent", type="mouseMoved", x=hit["x"], y=hit["y"], button="none")
                    conn.call(
                        "Input.dispatchMouseEvent",
                        type="mousePressed",
                        x=hit["x"],
                        y=hit["y"],
                        button="left",
                        buttons=1,
                        clickCount=1,
                    )
                    conn.call(
                        "Input.dispatchMouseEvent",
                        type="mouseReleased",
                        x=hit["x"],
                        y=hit["y"],
                        button="left",
                        buttons=0,
                        clickCount=1,
                    )
                time.sleep(4.0)
                continue
            if state.get("in_game"):
                return True, "已进入游戏"
            time.sleep(poll)
        return False, f"等待进入游戏超时（最后一次点击：{last_text or '无'}）"


_PAGE_STATE_JS = r"""
(() => {
  const out = {need_login: false, in_game: false, enter_button: null, url: location.href, title: document.title};
  const bodyText = (document.body && document.body.innerText) || '';
  if (/请输入手机号|请输入验证码|扫码登录|密码登录/.test(bodyText)) out.need_login = true;

  const hasStream = [...document.querySelectorAll('video,canvas')].some(el => {
    const r = el.getBoundingClientRect();
    return r.width > innerWidth * 0.6 && r.height > innerHeight * 0.6;
  });
  out.in_game = hasStream && !out.need_login;

  const wanted = ['开始游戏', '进入游戏', '继续游戏', '启动游戏', '进入终末地'];
  const cands = [...document.querySelectorAll('button,a,div,span')];
  let best = null;
  for (const el of cands) {
    const txt = (el.textContent || '').trim();
    if (!txt || txt.length > 12) continue;
    if (!wanted.some(w => txt === w || txt.includes(w))) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 20) continue;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') < 0.3) continue;
    // 取最内层：面积最小的匹配元素，避免点到整块容器
    if (!best || r.width * r.height < best.area) {
      best = {text: txt, x: r.left + r.width / 2, y: r.top + r.height / 2, area: r.width * r.height};
    }
  }
  if (best) out.enter_button = {text: best.text, x: best.x, y: best.y};
  return out;
})()
"""
