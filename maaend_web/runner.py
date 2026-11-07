"""MaaFramework 运行时封装：资源、控制器、Agent 子进程与任务队列。

一次「运行」的完整链路：

    assets/resource  ┐
    resource_web     ├─> Resource ──┐
                                   ├─> Tasker ──> Pipeline
    Chrome/Edge <──CDP── WebController ┘
                                   └─> AgentClient ──> go-service / cpp-algo
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from maa.agent_client import AgentClient
from maa.event_sink import NotificationType
from maa.resource import Resource
from maa.tasker import Tasker, TaskerEventSink
from maa.toolkit import Toolkit

from .browser import BrowserSession
from .controller import WebConfig, WebController
from .project import Project

log = logging.getLogger(__name__)

IS_WIN = sys.platform.startswith("win")


@dataclass
class EngineConfig:
    assets_dir: Path
    # resource 是 MaaEnd 的主体资源；resource_cloud 是 MaaEnd 官方给「云·终末地」
    # 准备的差异覆盖包（登录界面改用 News 识别、部分颜色阈值下调）。
    # 两者都由用户本地安装包提供，项目本身不分发任何游戏资源文件。
    resource_packs: List[str] = field(default_factory=lambda: ["resource", "resource_cloud"])
    user_data_dir: Path = field(default_factory=lambda: Path.home() / ".maaend-web")
    log_dir: Optional[Path] = None
    game_url: str = "https://endfield.hypergryph.com/cloud"
    browser_path: str = ""
    browser_port: int = 0  # >0 表示接管已运行的浏览器
    headless: bool = False
    user_dir: str = ""
    agent_enabled: bool = True
    web: WebConfig = field(default_factory=WebConfig)


class TaskState:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class _Sink(TaskerEventSink):
    def __init__(self, engine: "Engine"):
        self.engine = engine

    def on_tasker_task(self, tasker, noti_type, detail) -> None:
        self.engine._emit_task_event(noti_type, detail)

    def on_raw_notification(self, tasker, msg: str, details: Dict[str, Any]) -> None:
        if msg.startswith("Tasker.Task") or msg.startswith("Tasker.TaskerTask"):
            return
        handler = self.engine.on_raw
        if handler:
            try:
                handler(msg, details)
            except Exception:
                pass


class Engine:
    """把资源、控制器、Agent 组装成可运行的整体。"""

    def __init__(self, config: EngineConfig):
        self.config = config
        self.project: Optional[Project] = None
        self.resource: Optional[Resource] = None
        self.tasker: Optional[Tasker] = None
        self.controller: Optional[WebController] = None
        self.session: Optional[BrowserSession] = None
        self.agents: List[subprocess.Popen] = []
        self.agent_client: Optional[AgentClient] = None
        self._sink_id: Optional[int] = None
        self._running = threading.Event()
        self._stop_requested = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self.on_log: Optional[Callable[[str], None]] = None
        self.on_task_state: Optional[Callable[[str, str, dict], None]] = None
        self.on_raw: Optional[Callable[[str, dict], None]] = None
        self.current_task: str = ""

    # ---------------------------------------------------------------- 初始化

    def prepare(self) -> None:
        cfg = self.config
        assets = resolve_assets_dir(cfg.assets_dir)
        self.config.assets_dir = assets
        if not (assets / "interface.json").exists():
            raise FileNotFoundError(f"{assets} 下没有 interface.json，请选择 MaaEnd 安装目录")

        cfg.user_data_dir.mkdir(parents=True, exist_ok=True)
        log_dir = cfg.log_dir or (cfg.user_data_dir / "debug")
        log_dir.mkdir(parents=True, exist_ok=True)
        Toolkit.init_option(str(log_dir))

        self.project = Project(assets, lang="zh_cn")

        # MaaEnd 的 pipeline 引用了大量 Custom 节点，没有 Agent 会大面积失败
        if cfg.agent_enabled:
            self._start_agents()
        else:
            log.warning("未启用 Agent：包含自研识别/动作的任务将无法运行")

        self.resource = Resource()
        loaded = []
        for pack in cfg.resource_packs:
            path = Path(pack)
            if not path.is_absolute():
                path = assets / pack
            if not path.exists():
                log.debug("资源包不存在，跳过: %s", path)
                continue
            job = self.resource.post_bundle(str(path))
            job.wait()
            if job.succeeded:
                loaded.append(path.name)
            else:
                log.warning("资源包加载失败: %s", path)
        if not loaded:
            raise RuntimeError("没有任何资源包加载成功")
        if "resource_cloud" in loaded:
            log.info("已加载云端差异资源包 resource_cloud")
        elif (assets / "resource").is_dir():
            log.warning("未找到 resource_cloud：云版登录界面与部分取色可能识别失败")

        if self.agent_client and cfg.agent_enabled:
            try:
                ok = self.agent_client.bind(self.resource)
                log.info("Agent 绑定资源: %s", ok)
            except Exception as exc:
                log.warning("Agent 绑定资源失败: %s", exc)

        self.session = BrowserSession(
            executable=cfg.browser_path or None,
            user_data_dir=str(cfg.user_data_dir / "browser"),
            port=cfg.browser_port,
            headless=cfg.headless,
        )
        self.session.game_url = cfg.game_url
        if cfg.browser_port:
            self.session.attach(cfg.browser_port)
        else:
            self.session.start(cfg.game_url)

        self.controller = WebController(self.session, cfg.web)
        self.tasker = Tasker()
        if not self.tasker.bind(self.resource, self.controller):
            raise RuntimeError("Tasker 绑定失败")
        if not self.controller.connect():
            raise RuntimeError("无法连接浏览器页面，请确认云·终末地已打开")

        # 某些页面在加载完成后才创建视频元素，给它一点时间
        for _ in range(24):
            if self.controller.has_content():
                break
            time.sleep(0.25)
            self.controller.refresh_transform()

        sink = _Sink(self)
        self._sink_id = self.tasker.add_sink(sink)

        if self.agent_client:
            try:
                self.agent_client.connect()
                recos = self.agent_client.custom_recognition_list
                actions = self.agent_client.custom_action_list
                log.info("Agent 已连接：%d 个自定义识别 / %d 个自定义动作", len(recos), len(actions))
            except Exception as exc:
                log.warning("Agent 连接失败: %s", exc)

        if not self.tasker.inited:
            raise RuntimeError("Tasker 初始化失败（资源或控制器不可用）")

    def _start_agents(self) -> None:
        assets = Path(self.config.assets_dir)
        specs = self.project.agents if self.project else []
        # 安装目录可能是 <root>/assets 或直接 <root>（资源就在根下），
        # 按 child_exec 能解析成可执行文件的位置反推根目录
        root = assets.parent
        for cand in (assets.parent, assets, assets.parent.parent):
            if any(_find_agent_exec(cand, spec.get("child_exec", "")) for spec in specs):
                root = cand
                break
        if not specs:
            return

        identifier = f"maaend-web-{os.getpid()}"
        try:
            self.agent_client = AgentClient(identifier)
        except Exception as exc:
            log.warning("创建 AgentClient 失败: %s", exc)
            self.agent_client = None
            return

        env = os.environ.copy()
        # MaaEnd 官方安装包里有 <root>/maafw 目录；从源码或 pip 版运行时，
        # 让 Agent 直接用 maafw Python 包自带的动态库，避免"缺库"启动失败。
        lib_dir = root / "maafw"
        if not any(lib_dir.glob("libMaaFramework.*")) and not (lib_dir / "MaaFramework.dll").exists():
            bundled = _bundled_lib_dir()
            if bundled:
                env["MAAFW_BINARY_PATH"] = str(bundled)
                log.info("Agent 使用内置 MaaFramework 库：%s", bundled)
        env.update(
            {
                "PI_INTERFACE_VERSION": str(self.project.interface.get("interface_version", "2.11.0")),
                "PI_CLIENT_NAME": "MaaEnd-Web",
                "PI_CLIENT_VERSION": "1.0.0",
                "PI_CLIENT_LANGUAGE": "zh_cn",
                "PI_CLIENT_MAAFW_VERSION": _maafw_version(),
                "PI_VERSION": str(self.project.interface.get("version", "v0.1.0")),
                "PI_USER_DATA_DIR": str(self.config.user_data_dir),
                "PI_CONTROLLER": json.dumps(
                    {
                        "name": "CloudWeb",
                        "type": "Custom",
                        "display_short_side": 720,
                    },
                    ensure_ascii=False,
                ),
                "PI_RESOURCE": json.dumps({"name": "官服", "path": [str(assets / "resource")]}, ensure_ascii=False),
            }
        )

        for spec in specs:
            exec_rel = spec.get("child_exec", "")
            path = _find_agent_exec(root, exec_rel)
            if path is None:
                log.warning("Agent 可执行文件缺失: %s（相关功能将不可用）", root / exec_rel)
                continue
            args = [str(path), identifier] + [str(a) for a in spec.get("child_args", [])]
            try:
                proc = subprocess.Popen(
                    args,
                    cwd=str(root),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.agents.append(proc)
                log.info("已启动 Agent: %s (pid=%s)", path.name, proc.pid)
            except Exception as exc:
                log.warning("启动 Agent 失败 %s: %s", path, exc)

    # ---------------------------------------------------------------- 运行

    def run_tasks(self, jobs: List[Dict[str, Any]], on_done: Optional[Callable[[], None]] = None) -> None:
        """后台线程顺序执行任务。jobs: [{"task": name, "values": {...}}]"""
        if self._running.is_set():
            raise RuntimeError("已有任务在运行")
        self._running.set()
        self._stop_requested.clear()

        def worker() -> None:
            try:
                for job in jobs:
                    if self._stop_requested.is_set():
                        self._emit(job["task"], TaskState.SKIPPED, {})
                        continue
                    self._run_one(job)
            finally:
                self._running.clear()
                if on_done:
                    try:
                        on_done()
                    except Exception:
                        pass

        self._worker = threading.Thread(target=worker, name="maa-runner", daemon=True)
        self._worker.start()

    def _run_one(self, job: Dict[str, Any]) -> None:
        name = job["task"]
        values = job.get("values") or {}
        assert self.tasker and self.project
        self.current_task = name
        try:
            override = self.project.resolve_task(name, values)
        except Exception as exc:
            log.exception("解析任务失败: %s", exc)
            self._emit(name, TaskState.FAILED, {"error": str(exc)})
            return

        task = self.project.tasks[name]
        self._emit(name, TaskState.RUNNING, {"entry": task.entry})
        log.info("开始任务：%s", task.label)
        started = time.time()
        try:
            detail = self.tasker.post_task(task.entry, override).wait().get()
            elapsed = time.time() - started
            if detail is None:
                self._emit(name, TaskState.FAILED, {"error": "任务没有返回详情", "elapsed": elapsed})
                return
            ok = bool(detail.status.succeeded)
            nodes = len(getattr(detail, "node_id_list", []) or [])
            self._emit(
                name,
                TaskState.SUCCESS if ok else TaskState.FAILED,
                {"elapsed": round(elapsed, 1), "nodes": nodes},
            )
            log.info("任务结束：%s，耗时 %.1fs，%s", task.label, elapsed, "成功" if ok else "失败")
        except Exception as exc:
            log.exception("任务异常: %s", exc)
            self._emit(name, TaskState.FAILED, {"error": str(exc)})

    def stop(self) -> None:
        self._stop_requested.set()
        if self.tasker:
            try:
                self.tasker.post_stop()
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def _emit(self, task: str, state: str, detail: Dict[str, Any]) -> None:
        if self.on_task_state:
            try:
                self.on_task_state(task, state, detail)
            except Exception:
                pass

    def _emit_task_event(self, noti_type, detail) -> None:
        try:
            entry = getattr(detail, "entry", "") or self.current_task
            state = {
                NotificationType.Starting: TaskState.RUNNING,
                NotificationType.Succeeded: TaskState.SUCCESS,
                NotificationType.Failed: TaskState.FAILED,
            }.get(noti_type)
            if state and self.on_task_state:
                self.on_task_state(entry, state, {"notify": True})
        except Exception:
            pass

    # ---------------------------------------------------------------- 清理

    def shutdown(self) -> None:
        self.stop()
        for proc in self.agents:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:
                pass
        self.agents.clear()
        if self.agent_client:
            try:
                self.agent_client.disconnect()
            except Exception:
                pass
        if self.session:
            self.session.close(kill=True)


def _find_agent_exec(root: Path, rel: str) -> Optional[Path]:
    """定位 Agent 可执行文件；源码仓库里同名路径是目录，必须排除。"""
    if not rel:
        return None
    base = Path(rel)
    if not base.is_absolute():
        base = root / rel
    candidates = [base]
    if IS_WIN:
        candidates.append(base.with_suffix(".exe"))
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _bundled_lib_dir() -> Optional[Path]:
    try:
        import maa

        bin_dir = Path(maa.__file__).resolve().parent / "bin"
        if bin_dir.is_dir():
            return bin_dir
    except Exception:
        pass
    return None


def _maafw_version() -> str:
    try:
        from importlib.metadata import version

        return version("maafw")
    except Exception:
        return "5.15.0"


def resolve_assets_dir(path: Path | str) -> Path:
    """把「安装目录」或「assets 目录」统一成含 interface.json 的目录。"""
    p = Path(path)
    if (p / "interface.json").exists():
        return p
    if (p / "assets" / "interface.json").exists():
        return p / "assets"
    return p


def find_maaend_dir(start: Optional[Path] = None) -> Optional[Path]:
    """在常见位置找 MaaEnd 安装目录（含 interface.json 与 resource 目录）。"""
    candidates: List[Path] = []
    if start:
        candidates.append(Path(start))
    env = os.environ.get("MAAEND_HOME")
    if env:
        candidates.append(Path(env))
    here = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else Path.cwd()
    candidates.extend(
        [
            here,
            here / "MaaEnd",
            here.parent / "MaaEnd",
            here.parent,
            Path.cwd(),
            Path.cwd() / "MaaEnd",
            Path.home() / "MaaEnd",
            Path.home() / "Desktop" / "MaaEnd",
            Path("/opt/MaaEnd"),
        ]
    )
    if IS_WIN:
        for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")):
            if base:
                candidates.append(Path(base) / "MaaEnd")
    for cand in candidates:
        try:
            resolved = resolve_assets_dir(cand)
            if (resolved / "interface.json").exists() and (resolved / "resource").is_dir():
                return cand.resolve()
        except OSError:
            continue
    return None
