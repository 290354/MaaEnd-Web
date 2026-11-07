"""环境自检：把「跑不起来」的原因一次性说清楚。"""

from __future__ import annotations

import importlib.util
import logging
import platform
import sys
from pathlib import Path

from .config import AppConfig
from .runner import _find_agent_exec, find_maaend_dir, resolve_assets_dir

log = logging.getLogger(__name__)

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"


def _line(mark: str, title: str, detail: str = "") -> None:
    print(f"{mark} {title}" + (f"  —  {detail}" if detail else ""))


def run_checks(cfg: AppConfig) -> bool:
    print("MaaEnd-Web 环境自检")
    print("=" * 60)
    ok = True

    _line(OK, "Python", f"{platform.python_version()} / {platform.system()} {platform.machine()}")
    if sys.version_info < (3, 10):
        _line(FAIL, "Python 版本过低", "需要 3.10 及以上")
        ok = False

    for mod, hint in [
        ("maa", "pip install maafw"),
        ("cv2", "pip install opencv-python"),
        ("numpy", "pip install numpy"),
        ("PIL", "pip install pillow"),
        ("websocket", "pip install websocket-client"),
    ]:
        if importlib.util.find_spec(mod):
            _line(OK, f"依赖 {mod}")
        else:
            _line(FAIL, f"缺少依赖 {mod}", hint)
            ok = False

    if importlib.util.find_spec("maa"):
        try:
            from importlib.metadata import version

            _line(OK, "MaaFramework", version("maafw"))
        except Exception as exc:
            _line(WARN, "MaaFramework 版本未知", str(exc))

    maaend = Path(cfg.maaend_dir) if cfg.maaend_dir else find_maaend_dir()
    if maaend:
        maaend = resolve_assets_dir(maaend)
    if not maaend or not (maaend / "interface.json").exists():
        _line(FAIL, "MaaEnd 目录", "未找到 interface.json，请在设置中指定")
        ok = False
    else:
        _line(OK, "MaaEnd 目录", str(maaend))
        for sub in ("resource", "resource/image", "resource/pipeline"):
            p = maaend / sub
            if p.exists():
                _line(OK, f"  资源 {sub}")
            else:
                _line(FAIL, f"  资源 {sub} 缺失")
                ok = False
        model = maaend / "resource" / "model"
        if model.is_dir() and any(model.iterdir()):
            _line(OK, "  OCR / 模型资源")
        else:
            _line(WARN, "  OCR / 模型资源为空", "OCR 类节点会失败，建议使用官方完整安装包")

        for agent in ("agent/go-service", "agent/cpp-algo"):
            exe = _find_agent_exec(maaend.parent, agent) or _find_agent_exec(maaend, agent)
            if exe:
                _line(OK, f"  Agent {agent}")
            else:
                _line(WARN, f"  Agent {agent} 缺失", "依赖 Agent 的任务将无法运行")

    from .browser import find_browser

    browser = find_browser(cfg.browser_path or None)
    if browser:
        _line(OK, "浏览器", str(browser))
    else:
        _line(FAIL, "浏览器", "未找到 Chrome / Edge，请在设置中指定")
        ok = False

    cloud_res = maaend / "resource_cloud"
    if cloud_res.is_dir():
        _line(OK, "云端差异资源包", str(cloud_res))
    else:
        _line(WARN, "未找到 resource_cloud", "云版登录界面与部分取色可能识别失败，请更新 MaaEnd")

    _line(OK, "游戏地址", cfg.game_url)
    print("=" * 60)
    print("结论：" + ("环境就绪" if ok else "存在必须修复的问题"))
    return ok
