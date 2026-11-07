"""命令行模式：适合放进计划任务或远程挂机。"""

from __future__ import annotations

import logging
import signal
import time
from pathlib import Path
from typing import Optional

from .config import AppConfig, default_data_dir
from .project import Project
from .runner import Engine, EngineConfig, TaskState, find_maaend_dir

log = logging.getLogger(__name__)


def run_cli(cfg: AppConfig, tasks: Optional[str] = None) -> int:
    maaend = Path(cfg.maaend_dir) if cfg.maaend_dir else find_maaend_dir()
    if not maaend or not (maaend / "interface.json").exists():
        print("未找到 MaaEnd 目录，请用 --maaend 指定，或在图形界面里设置一次。")
        return 2

    project = Project(maaend)
    names = [t.strip() for t in (tasks or "").split(",") if t.strip()]
    if not names:
        print("可用任务：")
        for t in project.tasks.values():
            print(f"  {t.name:28s} {t.label}")
        print("\n用法：--tasks DailyRewards,DijiangRewards")
        return 0
    unknown = [n for n in names if n not in project.tasks]
    if unknown:
        print(f"未知任务：{', '.join(unknown)}")
        return 2

    engine_cfg = EngineConfig(
        assets_dir=maaend,
        user_data_dir=default_data_dir() / "runtime",
        game_url=cfg.game_url,
        browser_path=cfg.browser_path,
        browser_port=cfg.browser_port,
        headless=cfg.headless,
        agent_enabled=cfg.agent_enabled,
        web=cfg.web,
    )
    engine = Engine(engine_cfg)
    engine.on_task_state = lambda t, s, d: print(f"[{s}] {t} {d if s == TaskState.FAILED else ''}".rstrip())
    engine.prepare()
    if not engine.controller:
        print("控制器未就绪")
        return 3

    ok, msg = engine.controller.enter_game(timeout=300)
    print("进入游戏：", msg)
    if not ok:
        print("请先在浏览器中完成登录，然后重新运行。")
        engine.shutdown()
        return 4

    values = project.default_values(project.options.keys())
    engine.run_tasks([{"task": n, "values": values} for n in names])

    stopped = False

    def on_sigint(_sig, _frm):
        nonlocal stopped
        stopped = True
        print("\n收到中断，正在停止…")
        engine.stop()

    signal.signal(signal.SIGINT, on_sigint)
    while engine.running:
        time.sleep(0.4)
    engine.shutdown()
    return 0 if not stopped else 130
