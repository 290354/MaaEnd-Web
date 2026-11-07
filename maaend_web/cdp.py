"""极简 Chrome DevTools Protocol 客户端。

只依赖 ``websocket-client``，不引入 Playwright/Selenium，
方便随 MaaEnd-Web 一起打包，也避免与用户本机浏览器驱动版本冲突。

设计要点：
* 单条 WebSocket 连接承载全部命令与事件，读线程统一分发；
* ``call()`` 同步等待应答并支持超时，避免界面卡死；
* 事件回调在独立线程执行，回调里可以安全地再次 ``call()``。
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
from typing import Any, Callable, Dict, Optional

import websocket

log = logging.getLogger(__name__)


class CDPError(RuntimeError):
    """CDP 命令返回 error 字段时抛出。"""

    def __init__(self, method: str, payload: Dict[str, Any]):
        self.method = method
        self.payload = payload
        message = payload.get("message", "unknown error")
        data = payload.get("data")
        super().__init__(f"{method} 失败: {message}" + (f" ({data})" if data else ""))


class CDPConnection:
    """一条到某个 target（page / iframe / browser）的 CDP 连接。"""

    # Chrome 的截图 base64 可能有好几 MB，给足缓冲
    _MAX_FRAME = 128 * 1024 * 1024

    def __init__(self, ws_url: str, timeout: float = 20.0):
        self.ws_url = ws_url
        self.timeout = timeout
        self._ws = websocket.create_connection(
            ws_url,
            timeout=timeout,
            max_size=self._MAX_FRAME,
            # Chrome M111+ 会校验 Origin，省掉它就不需要给浏览器加 --remote-allow-origins
            suppress_origin=True,
        )
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._pending_cv = threading.Condition()
        self._handlers: Dict[str, list[Callable[[Dict[str, Any]], None]]] = {}
        self._closed = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, name="cdp-reader", daemon=True)
        self._reader.start()

    # ---------------------------------------------------------------- 基础

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            try:
                raw = self._ws.recv()
            except Exception as exc:  # 连接关闭 / 超时
                if not self._closed.is_set():
                    log.debug("CDP 读线程退出: %s", exc)
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue

            if "id" in msg:
                with self._pending_cv:
                    slot = self._pending.pop(msg["id"], None)
                    if slot is not None:
                        slot["response"] = msg
                    self._pending_cv.notify_all()
                continue

            method = msg.get("method", "")
            for handler in list(self._handlers.get(method, ())):
                try:
                    handler(msg.get("params", {}))
                except Exception:  # 事件回调不应影响读线程
                    log.exception("CDP 事件回调异常: %s", method)

        # 唤醒所有等待者，避免调用方永久阻塞
        self._closed.set()
        with self._pending_cv:
            for slot in self._pending.values():
                slot["response"] = {"error": {"message": "连接已关闭"}}
            self._pending.clear()
            self._pending_cv.notify_all()

    def call(self, method: str, timeout: Optional[float] = None, **params: Any) -> Dict[str, Any]:
        """同步发送命令并返回 result。"""
        if self._closed.is_set():
            raise CDPError(method, {"message": "连接已关闭"})

        with self._lock:
            msg_id = next(self._ids)
        slot: Dict[str, Any] = {"response": None}
        with self._pending_cv:
            self._pending[msg_id] = slot

        payload = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        try:
            self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            with self._pending_cv:
                self._pending.pop(msg_id, None)
            raise CDPError(method, {"message": f"发送失败: {exc}"}) from exc

        wait = self.timeout if timeout is None else timeout
        deadline_ok = False
        with self._pending_cv:
            deadline_ok = self._pending_cv.wait_for(
                lambda: slot["response"] is not None, timeout=wait
            )
            if not deadline_ok:
                self._pending.pop(msg_id, None)
        if not deadline_ok:
            raise CDPError(method, {"message": f"超时（{wait:.1f}s）"})

        response = slot["response"]
        if "error" in response:
            raise CDPError(method, response["error"])
        return response.get("result", {})

    def try_call(self, method: str, **params: Any) -> Optional[Dict[str, Any]]:
        """调用命令，失败只记日志并返回 None。"""
        try:
            return self.call(method, **params)
        except CDPError as exc:
            log.debug("%s", exc)
            return None

    def on(self, method: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        self._handlers.setdefault(method, []).append(handler)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._ws.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- 便捷封装

    def evaluate(self, expression: str, await_promise: bool = False, timeout: Optional[float] = None) -> Any:
        """执行 JS 并返回 JSON 化的结果。"""
        result = self.call(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=await_promise,
            userGesture=True,
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            raise CDPError("Runtime.evaluate", {"message": detail.get("text", "JS 异常"),
                                                "data": str(detail.get("exception", {}).get("description", ""))[:400]})
        return result.get("result", {}).get("value")

    def screenshot(self, fmt: str = "png", quality: Optional[int] = None,
                   clip: Optional[Dict[str, float]] = None,
                   optimize_for_speed: bool = True,
                   timeout: Optional[float] = 20.0) -> bytes:
        """截取页面，返回图片二进制。"""
        params: Dict[str, Any] = {
            "format": fmt,
            "fromSurface": True,
            "captureBeyondViewport": False,
            "optimizeForSpeed": optimize_for_speed,
        }
        if quality is not None and fmt == "jpeg":
            params["quality"] = int(quality)
        if clip:
            params["clip"] = clip
        result = self.call("Page.captureScreenshot", timeout=timeout, **params)
        import base64

        return base64.b64decode(result["data"])
