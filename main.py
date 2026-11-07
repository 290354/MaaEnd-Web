#!/usr/bin/env python3
"""MaaEnd-Web 启动入口。

用法：
    python main.py                 # 启动图形界面
    python main.py --cli           # 命令行模式
    python main.py --check         # 自检（环境、资源、浏览器）
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="MaaEnd-Web · 云·终末地网页版自动化")
    parser.add_argument("--cli", action="store_true", help="命令行模式")
    parser.add_argument("--check", action="store_true", help="环境自检")
    parser.add_argument("--maaend", help="MaaEnd 安装目录")
    parser.add_argument("--url", help="云游戏页面地址")
    parser.add_argument("--tasks", help="要执行的任务名，逗号分隔")
    parser.add_argument("--port", type=int, default=0, help="接管已运行的浏览器调试端口")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    args = parser.parse_args()

    setup_logging(args.debug)

    from maaend_web.config import AppConfig

    cfg = AppConfig.load()
    if args.maaend:
        cfg.maaend_dir = args.maaend
    if args.url:
        cfg.game_url = args.url
    if args.port:
        cfg.browser_port = args.port

    if args.check:
        from maaend_web.diagnostics import run_checks

        return 0 if run_checks(cfg) else 1

    if args.cli:
        from maaend_web.cli import run_cli

        return run_cli(cfg, args.tasks)

    from maaend_web.ui.app import App

    app = App(cfg)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
