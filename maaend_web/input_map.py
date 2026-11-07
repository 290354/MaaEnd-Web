"""Windows 虚拟键码（MaaFramework 使用）到 CDP 键盘事件的映射。

MaaFramework 的 ``key`` / ``click_key`` 参数沿用 Win32 Virtual-Key，
这里翻译成浏览器能理解的 ``key`` / ``code`` / ``keyCode`` 三元组。
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# modifiers 位掩码
MOD_ALT = 1
MOD_CTRL = 2
MOD_META = 4
MOD_SHIFT = 8

KeyInfo = Tuple[str, str, str]  # (key, code, text)


def _letters() -> Dict[int, KeyInfo]:
    out: Dict[int, KeyInfo] = {}
    for i in range(26):
        ch = chr(ord("a") + i)
        out[0x41 + i] = (ch, f"Key{ch.upper()}", ch)
    return out


def _digits() -> Dict[int, KeyInfo]:
    out: Dict[int, KeyInfo] = {}
    for i in range(10):
        out[0x30 + i] = (str(i), f"Digit{i}", str(i))
    return out


def _function_keys() -> Dict[int, KeyInfo]:
    out: Dict[int, KeyInfo] = {}
    for i in range(1, 25):
        out[0x70 + i - 1] = (f"F{i}", f"F{i}", "")
    return out


def _numpad() -> Dict[int, KeyInfo]:
    out: Dict[int, KeyInfo] = {}
    for i in range(10):
        out[0x60 + i] = (str(i), f"Numpad{i}", str(i))
    out.update(
        {
            0x6A: ("*", "NumpadMultiply", "*"),
            0x6B: ("+", "NumpadAdd", "+"),
            0x6D: ("-", "NumpadSubtract", "-"),
            0x6E: (".", "NumpadDecimal", "."),
            0x6F: ("/", "NumpadDivide", "/"),
        }
    )
    return out


_SPECIAL: Dict[int, KeyInfo] = {
    0x08: ("Backspace", "Backspace", ""),
    0x09: ("Tab", "Tab", ""),
    0x0D: ("Enter", "Enter", "\r"),
    0x13: ("Pause", "Pause", ""),
    0x14: ("CapsLock", "CapsLock", ""),
    0x1B: ("Escape", "Escape", ""),
    0x20: (" ", "Space", " "),
    0x21: ("PageUp", "PageUp", ""),
    0x22: ("PageDown", "PageDown", ""),
    0x23: ("End", "End", ""),
    0x24: ("Home", "Home", ""),
    0x25: ("ArrowLeft", "ArrowLeft", ""),
    0x26: ("ArrowUp", "ArrowUp", ""),
    0x27: ("ArrowRight", "ArrowRight", ""),
    0x28: ("ArrowDown", "ArrowDown", ""),
    0x2C: ("PrintScreen", "PrintScreen", ""),
    0x2D: ("Insert", "Insert", ""),
    0x2E: ("Delete", "Delete", ""),
    0x90: ("NumLock", "NumLock", ""),
    0x91: ("ScrollLock", "ScrollLock", ""),
    0x10: ("Shift", "ShiftLeft", ""),
    0x11: ("Control", "ControlLeft", ""),
    0x12: ("Alt", "AltLeft", ""),
    0xA0: ("Shift", "ShiftLeft", ""),
    0xA1: ("Shift", "ShiftRight", ""),
    0xA2: ("Control", "ControlLeft", ""),
    0xA3: ("Control", "ControlRight", ""),
    0xA4: ("Alt", "AltLeft", ""),
    0xA5: ("Alt", "AltRight", ""),
    0x5B: ("Meta", "MetaLeft", ""),
    0x5C: ("Meta", "MetaRight", ""),
    # OEM 键
    0xBA: (";", "Semicolon", ";"),
    0xBB: ("=", "Equal", "="),
    0xBC: (",", "Comma", ","),
    0xBD: ("-", "Minus", "-"),
    0xBE: (".", "Period", "."),
    0xBF: ("/", "Slash", "/"),
    0xC0: ("`", "Backquote", "`"),
    0xDB: ("[", "BracketLeft", "["),
    0xDC: ("\\", "Backslash", "\\"),
    0xDD: ("]", "BracketRight", "]"),
    0xDE: ("'", "Quote", "'"),
}

KEYMAP: Dict[int, KeyInfo] = {}
KEYMAP.update(_letters())
KEYMAP.update(_digits())
KEYMAP.update(_function_keys())
KEYMAP.update(_numpad())
KEYMAP.update(_SPECIAL)


def lookup(vk: int) -> Optional[KeyInfo]:
    return KEYMAP.get(int(vk))


def name_of(vk: int) -> str:
    info = lookup(vk)
    return info[0] if info else f"VK_{vk}"


def modifier_bit(vk: int) -> int:
    vk = int(vk)
    if vk in (0x10, 0xA0, 0xA1):
        return MOD_SHIFT
    if vk in (0x11, 0xA2, 0xA3):
        return MOD_CTRL
    if vk in (0x12, 0xA4, 0xA5):
        return MOD_ALT
    if vk in (0x5B, 0x5C):
        return MOD_META
    return 0
