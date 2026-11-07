"""画面几何：定位浏览器里真正的「游戏画面」并做归一化。

云游戏的画面可能落在 <video>、<canvas> 或铺满视口的 <iframe> 里，
还可能因为保持宽高比而出现黑边。MaaEnd 的全部 Pipeline 以
1280x720 为基准，因此这里负责：

1. 找到实际显示游戏画面的区域（内容矩形，不含黑边）；
2. 把该区域裁剪、等比缩放并居中补边到 1280x720；
3. 反向把 720p 坐标映射回浏览器视口坐标，供点击/滑动使用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

BASE_W, BASE_H = 1280, 720

# 在页面里找出所有"可能是游戏画面"的元素。
# 只看几何与可见性，不做业务判断，避免依赖具体网页实现。
_PROBE_JS = r"""
(() => {
  const out = [];
  const vw = window.innerWidth, vh = window.innerHeight;
  const areaMin = vw * vh * 0.18;

  function styleVisible(el) {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    if (parseFloat(s.opacity || '1') < 0.05) return false;
    if (s.pointerEvents === 'none' && s.opacity === '0') return false;
    return true;
  }

  function push(el, rect) {
    if (!rect) return;
    const w = rect.width, h = rect.height;
    if (w < 120 || h < 120) return;
    if (w * h < areaMin) return;
    if (!styleVisible(el)) return;
    const tag = el.tagName.toLowerCase();
    let iw = 0, ih = 0, fit = '';
    if (tag === 'video') { iw = el.videoWidth || 0; ih = el.videoHeight || 0; fit = getComputedStyle(el).objectFit || 'fill'; }
    else if (tag === 'canvas') { iw = el.width || 0; ih = el.height || 0; fit = 'fill'; }
    out.push({tag, x: rect.left, y: rect.top, w, h, iw, ih, fit,
              z: parseInt(getComputedStyle(el).zIndex || '0', 10) || 0});
  }

  // 1) 主文档里的 video / canvas
  for (const el of document.querySelectorAll('video, canvas')) {
    push(el, el.getBoundingClientRect());
  }

  // 2) 同源 iframe 里的 video / canvas（递归一层，够用且安全）
  for (const f of document.querySelectorAll('iframe')) {
    const r = f.getBoundingClientRect();
    let inner = null;
    try { inner = f.contentDocument; } catch (e) { inner = null; }
    if (inner) {
      for (const el of inner.querySelectorAll('video, canvas')) {
        const rr = el.getBoundingClientRect();
        push(el, {left: r.left + rr.left, top: r.top + rr.top, width: rr.width, height: rr.height});
      }
    }
    // 3) 跨域 iframe 无法读内部，但铺满时它本身就是画面容器
    if (!inner) {
      if (r.width * r.height >= vw * vh * 0.5) {
        out.push({tag: 'iframe', x: r.left, y: r.top, w: r.width, h: r.height, iw: 0, ih: 0, fit: '', z: 0});
      }
    }
  }
  return out;
})()
"""


@dataclass(frozen=True)
class ContentRect:
    """游戏画面内容在视口中的矩形（CSS 像素）。"""

    x: float
    y: float
    w: float
    h: float
    source: str = ""
    # 视频/画布的原始像素尺寸，用于判断画面是否被放大
    src_w: int = 0
    src_h: int = 0

    @property
    def ratio(self) -> float:
        return self.w / self.h if self.h else 0.0

    @property
    def upscaled(self) -> bool:
        """画面被拉伸放大（源分辨率低于显示尺寸）。"""
        return bool(self.src_w and self.src_h and (self.src_w < self.w * 0.98))


@dataclass(frozen=True)
class ViewTransform:
    """720p 基准坐标 <-> 视口 CSS 坐标 的双向映射。"""

    viewport: Tuple[int, int]
    content: ContentRect
    # 内容区 -> 720p 画布的缩放（等比，取小者）
    scale: float
    # 内容区在 720p 画布中的偏移（居中信箱）
    pad_x: float
    pad_y: float
    game_w: int
    game_h: int

    def to_viewport(self, x: float, y: float) -> Tuple[float, float]:
        """720p 坐标 -> 视口坐标。"""
        gx = (x - self.pad_x) / self.scale
        gy = (y - self.pad_y) / self.scale
        return self.content.x + gx, self.content.y + gy

    def to_game(self, vx: float, vy: float) -> Tuple[float, float]:
        """视口坐标 -> 720p 坐标。"""
        return (
            (vx - self.content.x) * self.scale + self.pad_x,
            (vy - self.content.y) * self.scale + self.pad_y,
        )


def _content_of(item: Dict[str, Any]) -> ContentRect:
    """把元素的显示矩形换算成「不含黑边」的实际画面矩形。"""
    x, y, w, h = float(item["x"]), float(item["y"]), float(item["w"]), float(item["h"])
    iw, ih = float(item.get("iw") or 0), float(item.get("ih") or 0)
    fit = (item.get("fit") or "").lower()
    tag = item.get("tag", "")

    if tag == "video" and iw > 0 and ih > 0 and fit in ("contain", "cover", ""):
        box_ratio = w / h
        src_ratio = iw / ih
        meta = f"video {int(iw)}x{int(ih)}"
        if fit in ("contain", ""):
            # contain：内容完整显示，四周留黑边
            if src_ratio > box_ratio:
                ch = w / src_ratio
                return ContentRect(x, y + (h - ch) / 2.0, w, ch, meta + "-contain", int(iw), int(ih))
            ch = h
            cw = h * src_ratio
            return ContentRect(x + (w - cw) / 2.0, y, cw, ch, meta + "-contain", int(iw), int(ih))
        # cover：内容铺满，超出部分被裁掉
        if src_ratio > box_ratio:
            cw = h * src_ratio
            return ContentRect(x - (cw - w) / 2.0, y, cw, h, meta + "-cover", int(iw), int(ih))
        ch = w / src_ratio
        return ContentRect(x, y - (ch - h) / 2.0, w, ch, meta + "-cover", int(iw), int(ih))

    if tag == "canvas" and iw > 0 and ih > 0:
        return ContentRect(x, y, w, h, f"canvas {int(iw)}x{int(ih)}", int(iw), int(ih))
    if tag == "iframe":
        return ContentRect(x, y, w, h, "iframe")

    return ContentRect(x, y, w, h, tag or "element")


def probe_content(conn) -> Optional[ContentRect]:
    """在页面里探测游戏画面矩形；失败返回 None。"""
    try:
        items = conn.evaluate(_PROBE_JS) or []
    except Exception as exc:
        log.debug("探测画面元素失败: %s", exc)
        return None
    if not items:
        return None

    viewport = _viewport_size(conn)
    vw, vh = viewport
    screen_area = float(vw * vh)

    best = None
    best_score = 0.0
    for item in items:
        rect = _content_of(item)
        if rect.w <= 1 or rect.h <= 1:
            continue
        # 面积占比为主，视频元素额外加分（视频几乎一定是游戏画面）
        score = (rect.w * rect.h) / screen_area
        if item.get("tag") == "video":
            score += 0.35
        elif item.get("tag") == "canvas":
            score += 0.1
        # 明显偏离 16:9 且面积不大的元素降权
        ratio = rect.ratio
        if ratio > 0 and (ratio < 0.9 or ratio > 2.6):
            score *= 0.6
        if score > best_score:
            best_score = score
            best = rect
    return best


def _viewport_size(conn) -> Tuple[int, int]:
    try:
        val = conn.evaluate("({w: window.innerWidth, h: window.innerHeight})") or {}
        w, h = int(val.get("w", BASE_W)), int(val.get("h", BASE_H))
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return BASE_W, BASE_H


def build_transform(viewport: Tuple[int, int], content: ContentRect) -> ViewTransform:
    """计算内容区到 1280x720 画布的等比缩放与居中偏移。"""
    vw, vh = viewport
    cw, ch = content.w, content.h
    if cw <= 0 or ch <= 0:
        content = ContentRect(0, 0, vw, vh, "fallback")
        cw, ch = vw, vh
    scale = min(BASE_W / cw, BASE_H / ch)
    game_w = max(1, int(round(cw * scale)))
    game_h = max(1, int(round(ch * scale)))
    pad_x = (BASE_W - game_w) / 2.0
    pad_y = (BASE_H - game_h) / 2.0
    return ViewTransform(
        viewport=(vw, vh),
        content=content,
        scale=scale,
        pad_x=pad_x,
        pad_y=pad_y,
        game_w=game_w,
        game_h=game_h,
    )


def analyze(conn, cached: Optional[ViewTransform] = None, tolerate: float = 2.0) -> ViewTransform:
    """探测并生成变换；若与缓存几乎一致则复用，减少每帧开销。"""
    viewport = _viewport_size(conn)
    content = probe_content(conn)
    if content is None:
        content = ContentRect(0, 0, viewport[0], viewport[1], "full-viewport")
    transform = build_transform(viewport, content)
    if cached is not None:
        same_view = cached.viewport == transform.viewport
        near = (
            abs(cached.content.x - transform.content.x) <= tolerate
            and abs(cached.content.y - transform.content.y) <= tolerate
            and abs(cached.content.w - transform.content.w) <= tolerate
            and abs(cached.content.h - transform.content.h) <= tolerate
        )
        if same_view and near:
            return cached
    return transform


def letterbox_to_base(image, transform: ViewTransform):
    """把裁剪出来的内容图放进 1280x720 画布（居中、补黑边）。

    image 为 BGR ndarray，尺寸应约等于 content.w x content.h。
    """
    import cv2
    import numpy as np

    h, w = image.shape[:2]
    # 用实际像素尺寸重算，避免 devicePixelRatio 与 CSS 尺寸差异带来的 1px 误差
    scale = min(BASE_W / w, BASE_H / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(image, (nw, nh), interpolation=interp)
    if (nw, nh) == (BASE_W, BASE_H):
        return resized
    canvas = np.zeros((BASE_H, BASE_W, 3), dtype=np.uint8)
    ox = (BASE_W - nw) // 2
    oy = (BASE_H - nh) // 2
    canvas[oy : oy + nh, ox : ox + nw] = resized
    return canvas


def sharpen(image, amount: float = 0.0):
    """轻度反锐化掩模，缓解视频流编码造成的边缘模糊。

    只做亮度通道，避免改变色相影响 ColorMatch 节点。
    """
    if amount <= 0:
        return image
    import cv2

    blur = cv2.GaussianBlur(image, (0, 0), 1.0)
    return cv2.addWeighted(image, 1.0 + amount, blur, -amount, 0)
