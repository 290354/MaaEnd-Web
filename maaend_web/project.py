"""Project Interface V2 解析：任务、配置项、预设与资源路径。

MaaEnd 的界面定义遵循 MaaFramework 的 ProjectInterface V2 协议，
这里实现一个够用的解析器，让 MaaEnd-Web 不需要依赖 MXU 也能正确
还原「任务 + 配置项 → pipeline_override」的语义。
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- JSONC


def strip_jsonc(text: str) -> str:
    """去掉 // 注释、/* */ 注释与尾逗号，保留字符串内容。"""
    out: List[str] = []
    i, n = 0, len(text)
    in_str = False
    quote = ""
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                in_str = False
            i += 1
            continue
        if ch in "\"'":
            in_str = True
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    stripped = "".join(out)
    # 尾逗号
    stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
    return stripped


def load_jsonc(path: Path) -> Any:
    return json.loads(strip_jsonc(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------- 数据模型


@dataclass
class InputDef:
    name: str
    label: str
    default: str = ""
    pipeline_type: str = "string"
    verify: str = ""
    pattern_msg: str = ""
    password: bool = False


@dataclass
class HotkeyDef:
    name: str
    label: str
    default: str = ""


@dataclass
class CaseDef:
    name: str
    label: str
    pipeline_override: Dict[str, Any] = field(default_factory=dict)
    option: List[str] = field(default_factory=list)


@dataclass
class OptionDef:
    name: str
    type: str
    label: str
    description: str = ""
    cases: List[CaseDef] = field(default_factory=list)
    inputs: List[InputDef] = field(default_factory=list)
    hotkeys: List[HotkeyDef] = field(default_factory=list)
    # switch / select 是字符串；checkbox 是字符串数组（PI v2.3.0）
    default_case: Any = ""
    min_count: int = 0
    max_count: int = 0  # 0 表示不限制
    pipeline_override: Dict[str, Any] = field(default_factory=dict)
    controller: List[str] = field(default_factory=list)

    def case(self, name: str) -> Optional[CaseDef]:
        for c in self.cases:
            if c.name == name:
                return c
        return None

    def checked(self) -> List[str]:
        """checkbox 的默认选中项，按 cases 顺序返回并剔除无效项。"""
        raw = self.default_case
        if isinstance(raw, str):
            wanted = [raw] if raw else []
        elif isinstance(raw, (list, tuple)):
            wanted = [str(v) for v in raw if v]
        else:
            wanted = []
        names = {c.name for c in self.cases}
        picked = [n for n in wanted if n in names]
        if not picked and self.min_count:
            picked = [c.name for c in self.cases[: self.min_count]]
        return picked

    def default_value(self) -> Any:
        if self.type in ("switch", "select"):
            if isinstance(self.default_case, str) and self.default_case and self.case(self.default_case):
                return self.default_case
            return self.cases[0].name if self.cases else ""
        if self.type == "checkbox":
            return self.checked()
        if self.type == "input":
            return {i.name: i.default for i in self.inputs}
        if self.type == "hotkey":
            return {h.name: h.default for h in self.hotkeys}
        return ""


@dataclass
class TaskDef:
    name: str
    label: str
    entry: str
    description: str = ""
    group: List[str] = field(default_factory=list)
    controller: List[str] = field(default_factory=list)
    option: List[str] = field(default_factory=list)
    pipeline_override: Dict[str, Any] = field(default_factory=dict)
    default_check: bool = False


@dataclass
class PresetDef:
    name: str
    label: str
    description: str = ""
    tasks: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------- 键名 → VK

_VK_NAMES = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "SHIFT": 0x10, "CTRL": 0x11,
    "CONTROL": 0x11, "ALT": 0x12, "PAUSE": 0x13, "CAPSLOCK": 0x14, "ESC": 0x1B,
    "ESCAPE": 0x1B, "SPACE": 0x20, "PAGEUP": 0x21, "PAGEDOWN": 0x22, "END": 0x23,
    "HOME": 0x24, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "INSERT": 0x2D, "DELETE": 0x2E, "NUMLOCK": 0x90, "SCROLLLOCK": 0x91,
    "LSHIFT": 0xA0, "RSHIFT": 0xA1, "LCTRL": 0xA2, "RCTRL": 0xA3,
    "LALT": 0xA4, "RALT": 0xA5, "WIN": 0x5B, "META": 0x5B,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
for _i in range(26):
    _VK_NAMES[chr(ord("A") + _i)] = 0x41 + _i
for _i in range(10):
    _VK_NAMES[str(_i)] = 0x30 + _i
for _i in range(1, 25):
    _VK_NAMES[f"F{_i}"] = 0x70 + _i - 1

_MOD_ALIASES = {
    "CTRL": 0x11, "CONTROL": 0x11, "ALT": 0x12, "SHIFT": 0x10, "WIN": 0x5B, "META": 0x5B,
}


def key_name_to_vk(name: str) -> Optional[int]:
    if not name:
        return None
    return _VK_NAMES.get(name.strip().upper())


def parse_hotkey(text: str) -> Tuple[Optional[int], List[int]]:
    """``"ALT+A"`` -> (65, [18])"""
    if not text:
        return None, []
    parts = [p.strip() for p in re.split(r"[+\-]", text) if p.strip()]
    if not parts:
        return None, []
    primary = key_name_to_vk(parts[-1])
    mods = []
    for p in parts[:-1]:
        vk = _MOD_ALIASES.get(p.upper())
        if vk is not None:
            mods.append(vk)
    return primary, mods


# ---------------------------------------------------------------- 项目


class Project:
    def __init__(self, assets_dir: str | Path, lang: str = "zh_cn"):
        self.assets = Path(assets_dir)
        self.lang = lang
        self.interface: Dict[str, Any] = {}
        self.locales: Dict[str, Any] = {}
        self.tasks: Dict[str, TaskDef] = {}
        self.options: Dict[str, OptionDef] = {}
        self.presets: Dict[str, PresetDef] = {}
        self.groups: List[Dict[str, str]] = []
        self.controllers: List[Dict[str, Any]] = []
        self.resources: List[Dict[str, Any]] = []
        self.agents: List[Dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------ 加载

    def _load(self) -> None:
        iface_path = self.assets / "interface.json"
        if not iface_path.exists():
            raise FileNotFoundError(f"未找到 interface.json: {iface_path}")
        self.interface = load_jsonc(iface_path)
        self._load_locales()

        task_raw: List[Dict[str, Any]] = []
        option_raw: Dict[str, Any] = {}
        preset_raw: List[Dict[str, Any]] = []
        group_raw: List[Dict[str, Any]] = list(self.interface.get("group", []))

        for item in self.interface.get("import", []):
            path = self.assets / item
            if path.is_dir():
                files = sorted(path.rglob("*.json"))
            elif path.exists():
                files = [path]
            else:
                log.debug("import 项不存在，跳过: %s", item)
                continue
            for f in files:
                try:
                    data = load_jsonc(f)
                except Exception as exc:
                    log.warning("解析 %s 失败: %s", f, exc)
                    continue
                if not isinstance(data, dict):
                    continue
                task_raw.extend(data.get("task", []) or [])
                for k, v in (data.get("option", {}) or {}).items():
                    option_raw.setdefault(k, {}).update(v if isinstance(v, dict) else {})
                preset_raw.extend(data.get("preset", []) or [])
                if data.get("group"):
                    group_raw.extend(data["group"])

        task_raw.extend(self.interface.get("task", []) or [])
        for k, v in (self.interface.get("option", {}) or {}).items():
            option_raw.setdefault(k, {}).update(v if isinstance(v, dict) else {})
        preset_raw.extend(self.interface.get("preset", []) or [])
        self.groups = group_raw
        self.controllers = self.interface.get("controller", []) or []
        self.resources = self.interface.get("resource", []) or []
        self.agents = self.interface.get("agent", []) or []
        if isinstance(self.agents, dict):
            self.agents = [self.agents]

        for raw in task_raw:
            task = self._parse_task(raw)
            if task:
                self.tasks[task.name] = task
        for name, raw in option_raw.items():
            opt = self._parse_option(name, raw)
            if opt:
                self.options[name] = opt
        for raw in preset_raw:
            preset = self._parse_preset(raw)
            if preset:
                self.presets[preset.name] = preset

    def _load_locales(self) -> None:
        rel = (self.interface.get("languages") or {}).get(self.lang)
        if not rel:
            rel = (self.interface.get("languages") or {}).get("zh_cn")
        if not rel:
            return
        path = self.assets / rel
        if path.exists():
            try:
                self.locales = load_jsonc(path)
            except Exception as exc:
                log.warning("加载语言文件失败 %s: %s", path, exc)

    def text(self, value: Any, default: str = "") -> str:
        """解析 i18n 字符串：``$key`` / 文件路径 / 直文本。"""
        if value is None:
            return default
        if isinstance(value, list):
            return "\n".join(self.text(v) for v in value)
        if not isinstance(value, str):
            return str(value)
        raw = value.strip()
        if raw.startswith("$"):
            key = raw[1:]
            got = self.locales.get(key)
            if isinstance(got, str):
                return self._resolve_text_file(got)
            return key
        return self._resolve_text_file(raw)

    def _resolve_text_file(self, text: str) -> str:
        candidate = self.assets / text
        try:
            if len(text) < 260 and not text.startswith("http") and candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except OSError:
            pass
        return text

    # ------------------------------------------------------------ 解析

    def _parse_task(self, raw: Dict[str, Any]) -> Optional[TaskDef]:
        name = raw.get("name")
        entry = raw.get("entry") or name
        if not name or not entry:
            return None
        return TaskDef(
            name=name,
            label=self.text(raw.get("label"), name),
            entry=entry,
            description=self.text(raw.get("description")),
            group=list(raw.get("group", []) or []),
            controller=list(raw.get("controller", []) or []),
            option=list(raw.get("option", []) or []),
            pipeline_override=raw.get("pipeline_override", {}) or {},
            default_check=bool(raw.get("default_check", False)),
        )

    def _parse_option(self, name: str, raw: Dict[str, Any]) -> Optional[OptionDef]:
        otype = raw.get("type")
        if not otype:
            return None
        cases = []
        for c in raw.get("cases", []) or []:
            cases.append(
                CaseDef(
                    name=c.get("name", ""),
                    label=self.text(c.get("label"), c.get("name", "")),
                    pipeline_override=c.get("pipeline_override", {}) or {},
                    option=list(c.get("option", []) or []),
                )
            )
        inputs = []
        for i in raw.get("inputs", []) or []:
            inputs.append(
                InputDef(
                    name=i.get("name", ""),
                    label=self.text(i.get("label"), i.get("name", "")),
                    default=str(i.get("default", "")),
                    pipeline_type=i.get("pipeline_type", "string"),
                    verify=i.get("verify", ""),
                    pattern_msg=self.text(i.get("pattern_msg")),
                    password=bool(i.get("password", False)),
                )
            )
        hotkeys = []
        for h in raw.get("hotkeys", []) or []:
            hotkeys.append(
                HotkeyDef(
                    name=h.get("name", ""),
                    label=self.text(h.get("label"), h.get("name", "")),
                    default=str(h.get("default", "")),
                )
            )
        return OptionDef(
            name=name,
            type=otype,
            label=self.text(raw.get("label"), name),
            description=self.text(raw.get("description")),
            cases=cases,
            inputs=inputs,
            hotkeys=hotkeys,
            default_case=raw.get("default_case", "") or ([] if otype == "checkbox" else ""),
            min_count=int(raw.get("min_count", 0) or 0),
            max_count=int(raw.get("max_count", 0) or 0),
            pipeline_override=raw.get("pipeline_override", {}) or {},
            controller=list(raw.get("controller", []) or []),
        )

    def _parse_preset(self, raw: Dict[str, Any]) -> Optional[PresetDef]:
        name = raw.get("name")
        if not name:
            return None
        return PresetDef(
            name=name,
            label=self.text(raw.get("label"), name),
            description=self.text(raw.get("description")),
            tasks=list(raw.get("task", []) or []),
        )

    # ------------------------------------------------------------ 选项

    def default_values(self, option_names: Iterable[str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name in option_names:
            opt = self.options.get(name)
            if not opt:
                continue
            value = opt.default_value()
            out[name] = value
            if opt.type in ("switch", "select"):
                case = opt.case(str(value))
                if case and case.option:
                    out.update(self.default_values(case.option))
            elif opt.type == "checkbox":
                chosen = set(value) if isinstance(value, (list, tuple, set)) else set()
                for case in opt.cases:
                    if case.name in chosen and case.option:
                        out.update(self.default_values(case.option))
        return out

    def resolve_task(self, task_name: str, values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """把任务 + 当前选项取值合并成一份 pipeline_override。"""
        task = self.tasks.get(task_name)
        if not task:
            raise KeyError(f"未知任务: {task_name}")
        values = values or {}
        override: Dict[str, Any] = {}
        _deep_merge(override, task.pipeline_override)
        for opt_name in task.option:
            self._apply_option(opt_name, values, override)
        return override

    def _apply_option(self, name: str, values: Dict[str, Any], out: Dict[str, Any]) -> None:
        opt = self.options.get(name)
        if not opt:
            return
        value = values.get(name, opt.default_value())

        if opt.type in ("switch", "select"):
            case = opt.case(str(value)) if value else None
            if case is None:
                case = opt.case(opt.default_case) or (opt.cases[0] if opt.cases else None)
            if case is None:
                return
            _deep_merge(out, case.pipeline_override)
            for sub in case.option:
                self._apply_option(sub, values, out)
            return

        if opt.type == "checkbox":
            picked = set(value) if isinstance(value, (list, tuple, set)) else set(opt.checked())
            for case in opt.cases:  # 规范：按 cases 数组顺序依次合并
                if case.name not in picked:
                    continue
                _deep_merge(out, case.pipeline_override)
                for sub in case.option:
                    self._apply_option(sub, values, out)
            return

        if opt.type == "input":
            payload = value if isinstance(value, dict) else {}
            data = {i.name: payload.get(i.name, i.default) for i in opt.inputs}
            converted = {
                i.name: _convert(data[i.name], i.pipeline_type) for i in opt.inputs
            }
            _deep_merge(out, _substitute(opt.pipeline_override, converted))
            return

        if opt.type == "hotkey":
            payload = value if isinstance(value, dict) else {}
            table: Dict[str, Any] = {}
            for h in opt.hotkeys:
                raw = str(payload.get(h.name, h.default) or "")
                primary, mods = parse_hotkey(raw)
                table[h.name] = primary if primary is not None else 0
                table[f"{h.name}.primary"] = primary if primary is not None else 0
                for idx, mod in enumerate(mods, start=1):
                    table[f"{h.name}.modifier{idx}"] = mod
            _deep_merge(out, _substitute(opt.pipeline_override, table))
            return


def _convert(value: Any, kind: str) -> Any:
    if kind == "int":
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return 0
    if kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    return str(value)


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    """深合并。

    必须逐层拷贝：界面里每个 case 的 pipeline_override 是共享的源数据，
    如果直接把嵌套 dict 的引用挂到结果里，后续合并会写穿到源对象，
    导致「跑过一次的任务」把选项状态永久污染给之后的所有任务。
    """
    for key, value in (src or {}).items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        elif isinstance(value, (dict, list)):
            dst[key] = copy.deepcopy(value)
        else:
            dst[key] = value


_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?)\}")


def _substitute(obj: Any, table: Dict[str, Any]) -> Any:
    if isinstance(obj, dict):
        return {k: _substitute(v, table) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, table) for v in obj]
    if isinstance(obj, str):
        whole = _PLACEHOLDER.fullmatch(obj.strip())
        if whole and whole.group(1) in table:
            return table[whole.group(1)]
        return _PLACEHOLDER.sub(
            lambda m: str(table[m.group(1)]) if m.group(1) in table else m.group(0), obj
        )
    return obj
