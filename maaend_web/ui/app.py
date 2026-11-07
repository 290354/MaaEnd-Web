"""MaaEnd-Web 主窗口。"""

from __future__ import annotations

import io
import logging
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

from PIL import Image, ImageTk

from .. import __version__
from ..config import AppConfig, default_data_dir
from ..project import Project
from ..runner import Engine, EngineConfig, TaskState, find_maaend_dir, resolve_assets_dir
from . import theme
from .theme import clean_label
from .options_dialog import OptionsDialog
from .settings_dialog import SettingsDialog

log = logging.getLogger(__name__)


class _QueueHandler(logging.Handler):
    def __init__(self, q: "queue.Queue[tuple]"):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put((record.levelno, self.format(record)))
        except Exception:
            pass


class App(tk.Tk):
    def __init__(self, config: Optional[AppConfig] = None):
        theme.enable_dpi_awareness()
        super().__init__()
        self.config_data = config or AppConfig.load()
        self.project: Optional[Project] = None
        self.engine: Optional[Engine] = None
        self.task_checks: Dict[str, bool] = {}
        self.option_values: Dict[str, Any] = {}
        self._logs: "queue.Queue[tuple]" = queue.Queue()
        self._states: "queue.Queue[tuple]" = queue.Queue()
        self._preview_img: Optional[ImageTk.PhotoImage] = None
        self._busy = False
        self._photo_cache = None

        self._setup_window()
        self._setup_style()
        self._build_ui()
        self._install_logging()
        self.after(120, self._drain_queues)
        self.after(400, self._tick_preview)
        self.after(200, self._boot)

    # ---------------------------------------------------------------- 外观

    def _setup_window(self) -> None:
        self.title(f"MaaEnd-Web · 云·终末地网页版自动化  v{__version__}")
        self.configure(bg=theme.Palette.bg)
        try:
            w, h = self.config_data.window.split("x")
            self.geometry(f"{w}x{h}")
        except Exception:
            self.geometry("1280x820")
        self.minsize(1040, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_style(self) -> None:
        p = theme.Palette
        self.f = theme.fonts()
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=p.bg, foreground=p.text, font=self.f["body"], borderwidth=0)
        style.configure("TFrame", background=p.bg)
        style.configure("Panel.TFrame", background=p.panel)
        style.configure("TLabel", background=p.bg, foreground=p.text)
        style.configure("Dim.TLabel", background=p.bg, foreground=p.text_dim, font=self.f["small"])
        style.configure("Panel.TLabel", background=p.panel, foreground=p.text)
        style.configure("PanelDim.TLabel", background=p.panel, foreground=p.text_dim, font=self.f["small"])
        style.configure("Title.TLabel", background=p.bg, foreground=p.text, font=self.f["title"])
        style.configure("TButton", background=p.panel_alt, foreground=p.text, padding=(14, 7), font=self.f["body"])
        style.map("TButton", background=[("active", p.selection), ("disabled", p.panel)])
        style.configure(
            "Accent.TButton",
            background=p.accent,
            foreground=p.accent_text,
            padding=(20, 9),
            font=theme.pick_font(["Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC"], 11, "bold"),
        )
        style.map("Accent.TButton", background=[("active", "#6fe3b6"), ("disabled", p.panel_alt)],
                  foreground=[("disabled", p.text_faint)])
        style.configure("Danger.TButton", background="#3a2a2e", foreground=p.danger, padding=(16, 9))
        style.map("Danger.TButton", background=[("active", "#4a3238")])
        style.configure(
            "Treeview",
            background=p.panel,
            fieldbackground=p.panel,
            foreground=p.text,
            rowheight=30,
            borderwidth=0,
            font=self.f["body"],
        )
        style.map("Treeview", background=[("selected", p.selection)], foreground=[("selected", p.text)])
        style.configure(
            "Treeview.Heading", background=p.bg_alt, foreground=p.text_dim, relief="flat",
            font=self.f["small"], padding=(8, 6),
        )
        style.map("Treeview.Heading", background=[("active", p.panel_alt)])
        style.configure("TNotebook", background=p.bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=p.bg_alt, foreground=p.text_dim, padding=(18, 8), font=self.f["body"])
        style.map("TNotebook.Tab", background=[("selected", p.panel)], foreground=[("selected", p.text)])
        style.configure(
            "TProgressbar", background=p.accent, troughcolor=p.panel_alt,
            borderwidth=0, thickness=3, bordercolor=p.panel_alt, lightcolor=p.accent, darkcolor=p.accent,
        )
        style.configure("TCombobox", fieldbackground=p.panel_alt, background=p.panel_alt,
                        foreground=p.text, arrowcolor=p.text_dim, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", p.panel_alt)])
        style.configure("TEntry", fieldbackground=p.panel_alt, foreground=p.text, insertcolor=p.text, padding=6)
        style.configure("TCheckbutton", background=p.panel, foreground=p.text, font=self.f["body"])
        style.map("TCheckbutton", background=[("active", p.panel)])
        style.configure("TSpinbox", fieldbackground=p.panel_alt, foreground=p.text, arrowcolor=p.text_dim)

    # ---------------------------------------------------------------- 布局

    def _build_ui(self) -> None:
        p = theme.Palette
        header = ttk.Frame(self, padding=(20, 14, 20, 10))
        header.pack(fill="x")
        left = ttk.Frame(header)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="MaaEnd · Web", style="Title.TLabel").pack(side="left")
        ttk.Label(
            left,
            text="  云·终末地网页版自动化",
            style="Dim.TLabel",
        ).pack(side="left", padx=(6, 0), pady=(4, 0))
        self.env_label = ttk.Label(header, text="未连接", style="Dim.TLabel")
        self.env_label.pack(side="right", padx=(12, 0))
        ttk.Button(header, text="设置", command=self._open_settings).pack(side="right")
        self.btn_enter = ttk.Button(header, text="连接并进入游戏", command=self._connect)
        self.btn_enter.pack(side="right", padx=(0, 8))

        body = ttk.Frame(self, padding=(20, 0, 20, 12))
        body.pack(fill="both", expand=True)

        # 左侧任务面板
        left_panel = ttk.Frame(body, style="Panel.TFrame", width=380)
        left_panel.pack(side="left", fill="y", padx=(0, 12))
        left_panel.pack_propagate(False)

        head = ttk.Frame(left_panel, style="Panel.TFrame", padding=(14, 12, 14, 6))
        head.pack(fill="x")
        ttk.Label(head, text="任务", style="Panel.TLabel", font=self.f["h2"]).pack(side="left")
        self.count_label = ttk.Label(head, text="0 项已选", style="PanelDim.TLabel")
        self.count_label.pack(side="right")

        tools = ttk.Frame(left_panel, style="Panel.TFrame", padding=(14, 0, 14, 8))
        tools.pack(fill="x")
        ttk.Button(tools, text="全选", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(tools, text="清空", command=lambda: self._set_all(False)).pack(side="left", padx=6)
        self.preset_var = tk.StringVar(value="预设")
        self.preset_box = ttk.Combobox(tools, textvariable=self.preset_var, state="readonly", width=14)
        self.preset_box.pack(side="right")
        self.preset_box.bind("<<ComboboxSelected>>", self._apply_preset)

        tree_wrap = ttk.Frame(left_panel, style="Panel.TFrame")
        tree_wrap.pack(fill="both", expand=True, padx=(8, 4), pady=(0, 6))
        self.tree = ttk.Treeview(tree_wrap, show="tree", selectmode="none")
        self.tree.column("#0", width=286, stretch=True)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("group", foreground=p.text_dim, font=self.f["h2"])
        self.tree.tag_configure("task", foreground=p.text)
        self.tree.tag_configure("checked", foreground=p.accent)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double)

        hint = ttk.Frame(left_panel, style="Panel.TFrame", padding=(14, 0, 14, 10))
        hint.pack(fill="x")
        ttk.Label(hint, text="双击任务可调整选项", style="PanelDim.TLabel").pack(side="left")

        # 右侧主区域
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook

        preview_tab = ttk.Frame(notebook, style="Panel.TFrame")
        notebook.add(preview_tab, text="实时画面")
        self.preview_label = tk.Label(
            preview_tab,
            bg=theme.Palette.log_bg,
            fg=p.text_faint,
            text="连接后这里显示浏览器里的游戏画面",
            font=self.f["body"],
            bd=0,
            highlightthickness=0,
        )
        self.preview_label.pack(fill="both", expand=True, padx=10, pady=10)

        log_tab = ttk.Frame(notebook, style="Panel.TFrame")
        notebook.add(log_tab, text="运行日志")
        self.log_text = tk.Text(
            log_tab,
            bg=theme.Palette.log_bg,
            fg=p.text_dim,
            insertbackground=p.text,
            relief="flat",
            wrap="word",
            font=self.f["mono"],
            padx=12,
            pady=10,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        log_sb = ttk.Scrollbar(log_tab, orient="vertical", command=self.log_text.yview)
        log_sb.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_sb.set, state="disabled")
        self.log_text.tag_configure("info", foreground=p.text_dim)
        self.log_text.tag_configure("warn", foreground=p.warn)
        self.log_text.tag_configure("error", foreground=p.danger)
        self.log_text.tag_configure("ok", foreground=p.ok)
        self.log_text.tag_configure("time", foreground=p.text_faint)

        # 底部操作条
        footer = ttk.Frame(self, padding=(20, 4, 20, 16))
        footer.pack(fill="x")
        self.btn_start = ttk.Button(footer, text="开始运行", style="Accent.TButton", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(footer, text="停止", style="Danger.TButton", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=10)
        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=(16, 16))
        self.status_label = ttk.Label(footer, text="就绪", style="Dim.TLabel")
        self.status_label.pack(side="right")

    # ---------------------------------------------------------------- 日志

    def _install_logging(self) -> None:
        handler = _QueueHandler(self._logs)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.setLevel(logging.INFO)
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)

    def _drain_queues(self) -> None:
        try:
            for _ in range(80):
                level, msg = self._logs.get_nowait()
                self._append_log(msg, level)
        except queue.Empty:
            pass
        try:
            while True:
                kind, payload = self._states.get_nowait()
                self._handle_state(kind, payload)
        except queue.Empty:
            pass
        self.after(120, self._drain_queues)

    def _append_log(self, msg: str, level: int) -> None:
        tag = "info"
        if level >= logging.ERROR:
            tag = "error"
        elif level >= logging.WARNING:
            tag = "warn"
        elif "成功" in msg or "完成" in msg:
            tag = "ok"
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp} ", "time")
        self.log_text.insert("end", msg + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _handle_state(self, kind: str, payload: Any) -> None:
        if kind == "status":
            self.status_label.configure(text=str(payload))
        elif kind == "env":
            self.env_label.configure(text=str(payload))
        elif kind == "progress":
            self.progress["value"] = float(payload)
        elif kind == "task":
            task, state, detail = payload
            self._on_task_state(task, state, detail)
        elif kind == "connected":
            self._on_connected(payload)
        elif kind == "connect_failed":
            self._on_connect_failed(payload)
        elif kind == "notify":
            title, body = payload
            messagebox.showinfo(title, body, parent=self)

    # ---------------------------------------------------------------- 启动

    def _boot(self) -> None:
        maaend = self.config_data.maaend_dir or find_maaend_dir()
        if not maaend:
            self._append_log("未找到 MaaEnd 安装目录，请先在设置里指定。", logging.WARNING)
            self._open_settings(first_run=True)
            return
        self.config_data.maaend_dir = str(maaend)
        self._load_project(Path(maaend))

    def _load_project(self, path: Path) -> None:
        try:
            self.project = Project(resolve_assets_dir(path), lang="zh_cn")
        except Exception as exc:
            self._append_log(f"加载项目失败：{exc}", logging.ERROR)
            return
        self._fill_tree()
        self.option_values = self.project.default_values(self.project.options.keys())
        self.option_values.update(self.config_data.last_values or {})
        self._fill_presets()
        self._append_log(
            f"已加载 MaaEnd：{len(self.project.tasks)} 个任务 / {len(self.project.options)} 个配置项",
            logging.INFO,
        )
        version = self.project.interface.get("version", "")
        self.env_label.configure(text=f"MaaEnd 资源 {version}".strip())

    # ---------------------------------------------------------------- 任务树

    def _fill_tree(self) -> None:
        assert self.project
        self.tree.delete(*self.tree.get_children())
        self.task_checks.clear()
        groups: List[tuple[str, str]] = []
        for g in self.project.groups:
            name = g.get("name", "")
            label = self.project.text(g.get("label"), name)
            groups.append((name, label))
        known = [g[0] for g in groups]
        buckets: Dict[str, List[str]] = {g: [] for g in known}
        buckets["__other__"] = []
        for task in self.project.tasks.values():
            gid = task.group[0] if task.group else "__other__"
            buckets.setdefault(gid, []).append(task.name)
        for gid, label in groups + [("__other__", "其他")]:
            names = buckets.get(gid) or []
            if not names:
                continue
            parent = self.tree.insert("", "end", text=clean_label(label), open=True, tags=("group",))
            for name in names:
                task = self.project.tasks[name]
                self.task_checks[name] = False
                self.tree.insert(
                    parent, "end", iid=f"task:{name}", text=f"○  {clean_label(task.label)}",
                    tags=("task",),
                )
        self._refresh_count()

    def _fill_presets(self) -> None:
        if not self.project:
            return
        items = ["预设"] + [p.label for p in self.project.presets.values()]
        self.preset_box.configure(values=items)

    def _on_tree_click(self, event) -> None:
        if not self.project:
            return
        iid = self.tree.identify_row(event.y)
        if not iid or not iid.startswith("task:"):
            return
        name = iid.split(":", 1)[1]
        checked = not self.task_checks.get(name, False)
        self.task_checks[name] = checked
        label = clean_label(self.project.tasks[name].label)
        self.tree.item(
            iid,
            text=("●  " if checked else "○  ") + label,
            tags=("checked",) if checked else ("task",),
        )
        self._refresh_count()

    def _on_tree_double(self, event) -> None:
        if not self.project:
            return
        iid = self.tree.identify_row(event.y)
        if not iid or not iid.startswith("task:"):
            return
        name = iid.split(":", 1)[1]
        dlg = OptionsDialog(self, self.project, name, self.option_values)
        self.wait_window(dlg)
        if dlg.result is not None:
            self.option_values = dlg.result
            self.config_data.last_values = dict(self.option_values)

    def _set_all(self, value: bool) -> None:
        for name in self.task_checks:
            self.task_checks[name] = value
            iid = f"task:{name}"
            if self.tree.exists(iid):
                label = clean_label(self.project.tasks[name].label) if self.project else name
                self.tree.item(
                    iid,
                    text=("●  " if value else "○  ") + label,
                    tags=("checked",) if value else ("task",),
                )
        self._refresh_count()

    def _refresh_count(self) -> None:
        n = sum(1 for v in self.task_checks.values() if v)
        self.count_label.configure(text=f"{n} 项已选")

    def _apply_preset(self, _event=None) -> None:
        if not self.project:
            return
        label = self.preset_var.get()
        preset = next((p for p in self.project.presets.values() if p.label == label), None)
        if not preset:
            return
        self._set_all(False)
        for item in preset.tasks:
            name = item.get("name")
            if name in self.task_checks and item.get("enabled", True):
                self.task_checks[name] = True
                iid = f"task:{name}"
                if self.tree.exists(iid):
                    label = clean_label(self.project.tasks[name].label)
                    self.tree.item(iid, text="●  " + label, tags=("checked",))
            opts = item.get("option")
            if isinstance(opts, dict):
                self.option_values.update(opts)
        self._refresh_count()
        self._append_log(f"已套用预设：{label}", logging.INFO)

    # ---------------------------------------------------------------- 连接

    def _open_settings(self, first_run: bool = False) -> None:
        dlg = SettingsDialog(self, self.config_data, first_run=first_run)
        self.wait_window(dlg)
        if dlg.saved:
            if dlg.maaend_changed and self.config_data.maaend_dir:
                self._load_project(Path(self.config_data.maaend_dir))

    def _connect(self) -> None:
        if self._busy:
            return
        if not self.project:
            self._open_settings(first_run=True)
            return
        self._busy = True
        self.btn_enter.configure(state="disabled")
        self._states.put(("status", "正在启动浏览器并加载资源…"))
        self._append_log("开始连接…", logging.INFO)
        # 重新连接时先收掉上一套资源，避免浏览器进程越开越多
        if self.engine:
            try:
                self.engine.shutdown()
            except Exception:
                pass
            self.engine = None

        cfg = self.config_data
        engine_cfg = EngineConfig(
            assets_dir=Path(cfg.maaend_dir),
            user_data_dir=default_data_dir() / "runtime",
            game_url=cfg.game_url,
            browser_path=cfg.browser_path,
            browser_port=cfg.browser_port,
            headless=cfg.headless,
            agent_enabled=cfg.agent_enabled,
            web=cfg.web,
        )
        engine = Engine(engine_cfg)
        engine.on_task_state = lambda t, s, d: self._states.put(("task", (t, s, d)))
        self.engine = engine

        def work() -> None:
            try:
                engine.prepare()
                self._states.put(("connected", engine))
            except Exception as exc:
                log.exception("连接失败")
                self._states.put(("connect_failed", str(exc)))

        threading.Thread(target=work, name="connect", daemon=True).start()

    def _on_connected(self, engine: Engine) -> None:
        self._busy = False
        self.btn_enter.configure(state="normal", text="重新连接")
        self._states.put(("status", "已连接，可以开始运行"))
        self._states.put(("env", "已连接"))
        self._append_log("连接成功，画面已就绪。", logging.INFO)
        threading.Thread(target=self._enter_game_async, daemon=True).start()

    def _on_connect_failed(self, message: str) -> None:
        self._busy = False
        self.btn_enter.configure(state="normal")
        self._states.put(("status", "连接失败"))
        self._append_log(f"连接失败：{message}", logging.ERROR)
        messagebox.showerror("连接失败", message, parent=self)

    def _enter_game_async(self) -> None:
        if not self.engine or not self.engine.controller:
            return
        ok, msg = self.engine.controller.enter_game(timeout=300)
        self._append_log(("进入游戏：" if ok else "进入游戏未完成：") + msg, logging.INFO if ok else logging.WARNING)
        self._states.put(("status", msg if ok else "等待登录 / 进入游戏"))
        if not ok and "登录" in msg:
            self._states.put((
                "notify",
                ("需要登录", "请在打开的浏览器窗口中完成鹰角通行证登录。\n登录完成后点击右上角「重新连接」，程序会自动进入游戏。"),
            ))

    # ---------------------------------------------------------------- 运行

    def _start(self) -> None:
        if not self.engine:
            messagebox.showinfo("提示", "请先点击「连接并进入游戏」。", parent=self)
            return
        if self.engine.running:
            return
        jobs = [
            {"task": name, "values": self.option_values}
            for name, checked in self.task_checks.items()
            if checked
        ]
        if not jobs:
            messagebox.showinfo("提示", "还没有勾选任何任务。", parent=self)
            return
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress["value"] = 0
        self._append_log(f"开始执行 {len(jobs)} 个任务", logging.INFO)
        self._job_total = len(jobs)
        self._job_done = 0
        self.config_data.last_tasks = [j["task"] for j in jobs]
        self.config_data.save()
        self.engine.run_tasks(jobs, on_done=self._on_all_done)

    def _stop(self) -> None:
        if self.engine:
            self._append_log("正在停止…", logging.WARNING)
            self.engine.stop()
        self.btn_stop.configure(state="disabled")

    def _on_task_state(self, task: str, state: str, detail: dict) -> None:
        # sink 的通知只用来刷新状态文字，进度以 post_task 的最终结果为准，避免重复计数
        if detail.get("notify"):
            if state == TaskState.RUNNING and self.project and task in self.project.tasks:
                self._states.put(("status", f"正在运行：{clean_label(self.project.tasks[task].label)}"))
            return
        label = task
        if self.project and task in self.project.tasks:
            label = clean_label(self.project.tasks[task].label)
        if state == TaskState.RUNNING:
            self._states.put(("status", f"正在运行：{label}"))
        elif state == TaskState.SUCCESS:
            self._job_done = getattr(self, "_job_done", 0) + 1
            self._states.put(("progress", 100 * self._job_done / max(1, getattr(self, "_job_total", 1))))
            self._append_log(f"✔ {label} 完成（{detail.get('elapsed', 0)}s）", logging.INFO)
        elif state == TaskState.FAILED:
            self._job_done = getattr(self, "_job_done", 0) + 1
            self._states.put(("progress", 100 * self._job_done / max(1, getattr(self, "_job_total", 1))))
            self._append_log(f"✘ {label} 失败：{detail.get('error', '识别或动作未命中')}", logging.ERROR)
        elif state == TaskState.SKIPPED:
            self._append_log(f"· {label} 已跳过", logging.WARNING)

    def _on_all_done(self) -> None:
        self._states.put(("status", "全部任务结束"))
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.progress["value"] = 100
        self._append_log("全部任务结束。", logging.INFO)

    # ---------------------------------------------------------------- 预览

    def _tick_preview(self) -> None:
        try:
            self._update_preview()
        except Exception:
            pass
        self.after(500, self._tick_preview)

    def _update_preview(self) -> None:
        ctrl = getattr(self.engine, "controller", None) if self.engine else None
        if not ctrl:
            return
        stats = ctrl.stats()
        if stats.get("viewport"):
            self.env_label.configure(
                text=f"已连接 · {stats['viewport'][0]}×{stats['viewport'][1]} · {stats.get('fps', 0)} fps"
            )
        # 空闲时主动抓一帧，任务运行时由 MaaFramework 的截图驱动，不重复截
        if self.engine and not self.engine.running:
            try:
                ctrl.screencap()
            except Exception:
                return
        data = ctrl.preview_jpeg(quality=82)
        if not data:
            return
        img = Image.open(io.BytesIO(data))
        box = self.preview_label.winfo_width(), self.preview_label.winfo_height()
        if box[0] < 40 or box[1] < 40:
            return
        scale = min(box[0] / img.width, box[1] / img.height)
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        img = img.resize(size, Image.BILINEAR)
        self._preview_img = ImageTk.PhotoImage(img)
        self.preview_label.configure(image=self._preview_img, text="")

    # ---------------------------------------------------------------- 关闭

    def _on_close(self) -> None:
        try:
            self.config_data.window = f"{self.winfo_width()}x{self.winfo_height()}"
            self.config_data.web = self.engine.config.web if self.engine else self.config_data.web
            self.config_data.save()
        except Exception:
            pass
        if self.engine:
            try:
                self.engine.shutdown()
            except Exception:
                pass
        self.destroy()
