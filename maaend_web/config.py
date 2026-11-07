"""配置持久化：记住上次选择的目录、浏览器与画面参数。"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict

from .controller import WebConfig

log = logging.getLogger(__name__)


def default_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "MaaEnd-Web"
    if os.sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MaaEnd-Web"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "maaend-web"


@dataclass
class AppConfig:
    maaend_dir: str = ""
    game_url: str = "https://endfield.hypergryph.com/cloud"
    browser_path: str = ""
    browser_port: int = 0
    headless: bool = False
    agent_enabled: bool = True
    last_tasks: list = field(default_factory=list)
    last_values: Dict[str, Any] = field(default_factory=dict)
    web: WebConfig = field(default_factory=WebConfig)
    window: str = "1280x820"

    @property
    def path(self) -> Path:
        return default_data_dir() / "config.json"

    @classmethod
    def load(cls) -> "AppConfig":
        cfg = cls()
        path = cfg.path
        if not path.exists():
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("读取配置失败: %s", exc)
            return cfg
        web_data = data.pop("web", {}) or {}
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        for key, value in web_data.items():
            if hasattr(cfg.web, key):
                setattr(cfg.web, key, value)
        return cfg

    def save(self) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("保存配置失败: %s", exc)
