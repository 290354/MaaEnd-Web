"""界面配色与字体：深色、低饱和，长时间挂机不刺眼。"""

from __future__ import annotations

import re
import sys
import tkinter.font as tkfont

# MaaEnd 的任务/选项名前面带 emoji（🌐🚚🛒…），Tk 默认字体渲染成方框，
# 统一剥掉只保留文字。
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\u2600-\u27BF"
    "\u2B00-\u2BFF"
    "\u2190-\u21FF"
    "\u2300-\u23FF"
    "\u25A0-\u25FF"
    "\uFE0F\u200D\u20E3"
    "]"
)


def clean_label(text: str) -> str:
    return _EMOJI.sub("", text or "").strip()


def pick_font(candidates, size: int, weight: str = "normal") -> tuple:
    try:
        available = set(tkfont.families())
    except Exception:
        available = set()
    for name in candidates:
        if name in available:
            return (name, size, weight)
    return ("TkDefaultFont", size, weight)


class Palette:
    bg = "#17181c"
    bg_alt = "#1e1f25"
    panel = "#24262d"
    panel_alt = "#2b2e37"
    border = "#343843"
    text = "#e8eaf0"
    text_dim = "#9aa0ae"
    text_faint = "#6b7280"
    accent = "#5ad1a5"
    accent_dim = "#3f9c7c"
    accent_text = "#0f1a16"
    warn = "#e5b567"
    danger = "#e06c75"
    ok = "#7ec699"
    selection = "#333a48"
    log_bg = "#131418"


def fonts() -> dict:
    if sys.platform == "darwin":
        base = ["PingFang SC", "Helvetica Neue"]
    elif sys.platform.startswith("win"):
        base = ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"]
    else:
        base = ["Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "DejaVu Sans"]
    mono = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"]
    return {
        "title": pick_font(base, 16, "bold"),
        "h2": pick_font(base, 12, "bold"),
        "body": pick_font(base, 10),
        "small": pick_font(base, 9),
        "mono": pick_font(mono, 9),
        "mono_small": pick_font(mono, 8),
    }


def enable_dpi_awareness() -> None:
    """Windows 高分屏下避免界面发糊。"""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
