"""设置对话框：MaaEnd 目录、浏览器与画面参数。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from ..config import AppConfig
from ..runner import find_maaend_dir, resolve_assets_dir
from . import theme


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, cfg: AppConfig, first_run: bool = False):
        super().__init__(master)
        self.cfg = cfg
        self.saved = False
        self.maaend_changed = False
        self.f = theme.fonts()
        p = theme.Palette
        self.title("设置")
        self.configure(bg=p.bg)
        self.transient(master)
        self.resizable(False, False)
        self.grab_set()

        wrap = ttk.Frame(self, padding=22)
        wrap.pack(fill="both", expand=True)

        ttk.Label(wrap, text="MaaEnd 目录", style="TLabel", font=self.f["h2"]).pack(anchor="w")
        ttk.Label(
            wrap,
            text="指向 MaaEnd 安装目录（内含 interface.json 与 resource 文件夹）",
            style="Dim.TLabel",
        ).pack(anchor="w", pady=(2, 6))
        row = ttk.Frame(wrap)
        row.pack(fill="x", pady=(0, 14))
        self.dir_var = tk.StringVar(value=cfg.maaend_dir)
        ttk.Entry(row, textvariable=self.dir_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", command=self._browse).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="自动检测", command=self._detect).pack(side="left", padx=(6, 0))

        ttk.Label(wrap, text="游戏地址", style="TLabel", font=self.f["h2"]).pack(anchor="w")
        ttk.Label(wrap, text="云·终末地网页版地址，一般无需修改", style="Dim.TLabel").pack(anchor="w", pady=(2, 6))
        self.url_var = tk.StringVar(value=cfg.game_url)
        ttk.Entry(wrap, textvariable=self.url_var, width=64).pack(fill="x", pady=(0, 14))

        ttk.Label(wrap, text="浏览器", style="TLabel", font=self.f["h2"]).pack(anchor="w")
        ttk.Label(wrap, text="留空则自动查找 Chrome / Edge", style="Dim.TLabel").pack(anchor="w", pady=(2, 6))
        brow = ttk.Frame(wrap)
        brow.pack(fill="x", pady=(0, 14))
        self.browser_var = tk.StringVar(value=cfg.browser_path)
        ttk.Entry(brow, textvariable=self.browser_var).pack(side="left", fill="x", expand=True)
        ttk.Button(brow, text="浏览…", command=self._browse_browser).pack(side="left", padx=(6, 0))

        opts = ttk.Frame(wrap)
        opts.pack(fill="x", pady=(0, 12))
        self.force_var = tk.BooleanVar(value=cfg.web.force_viewport)
        ttk.Checkbutton(
            opts, text="强制 1280×720 视口（推荐，画面与识别基准 1:1）", variable=self.force_var
        ).pack(anchor="w")
        self.agent_var = tk.BooleanVar(value=cfg.agent_enabled)
        ttk.Checkbutton(opts, text="启用 Agent（战斗、寻路、基质识别等高级功能需要）", variable=self.agent_var).pack(
            anchor="w", pady=(6, 0)
        )
        self.headless_var = tk.BooleanVar(value=cfg.headless)
        ttk.Checkbutton(opts, text="无头模式（不显示浏览器窗口，需要配合有效登录态）", variable=self.headless_var).pack(
            anchor="w", pady=(6, 0)
        )

        ttk.Label(wrap, text="画面锐化", style="TLabel", font=self.f["h2"]).pack(anchor="w", pady=(8, 0))
        ttk.Label(
            wrap,
            text="仅在画面被放大时生效，用于补偿视频流模糊；0 表示关闭",
            style="Dim.TLabel",
        ).pack(anchor="w", pady=(2, 4))
        srow = ttk.Frame(wrap)
        srow.pack(fill="x", pady=(0, 14))
        self.sharp_var = tk.DoubleVar(value=cfg.web.sharpen)
        self.sharp_label = ttk.Label(srow, text=f"{cfg.web.sharpen:.2f}", style="Dim.TLabel", width=6)
        scale = ttk.Scale(
            srow, from_=0.0, to=1.5, variable=self.sharp_var, command=lambda v: self.sharp_label.configure(text=f"{float(v):.2f}")
        )
        scale.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.sharp_label.pack(side="left")

        adv = ttk.Frame(wrap)
        adv.pack(fill="x", pady=(0, 16))
        ttk.Label(adv, text="接管已运行的浏览器端口", style="Dim.TLabel").pack(side="left")
        self.port_var = tk.StringVar(value=str(cfg.browser_port or ""))
        ttk.Entry(adv, textvariable=self.port_var, width=8).pack(side="left", padx=(8, 0))
        ttk.Label(adv, text="（留空则自行启动）", style="Dim.TLabel").pack(side="left", padx=(4, 0))

        btns = ttk.Frame(wrap)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="保存", style="Accent.TButton", command=self._save).pack(side="right", padx=(0, 8))

        self.update_idletasks()
        self._center(master)
        if first_run and not self.dir_var.get():
            self.after(200, self._detect)

    def _center(self, master) -> None:
        self.update_idletasks()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _browse(self) -> None:
        path = filedialog.askdirectory(title="选择 MaaEnd 安装目录", parent=self)
        if path:
            self.dir_var.set(path)

    def _browse_browser(self) -> None:
        path = filedialog.askopenfilename(title="选择浏览器可执行文件", parent=self)
        if path:
            self.browser_var.set(path)

    def _detect(self) -> None:
        found = find_maaend_dir()
        if found:
            self.dir_var.set(str(found))
            messagebox.showinfo("已找到", f"检测到 MaaEnd：\n{found}", parent=self)
        else:
            messagebox.showwarning("未找到", "未能自动定位 MaaEnd，请手动选择安装目录。", parent=self)

    def _save(self) -> None:
        directory = self.dir_var.get().strip()
        if not directory:
            messagebox.showwarning("提示", "请先选择 MaaEnd 目录。", parent=self)
            return
        # 校验用解析后的资源目录，保存用用户选的安装根
        # （Agent 需要相对安装根定位，不能只存 assets）
        resolved = resolve_assets_dir(Path(directory))
        if not (resolved / "interface.json").exists():
            messagebox.showwarning("目录不正确", "该目录下没有 interface.json，请选择 MaaEnd 安装目录。", parent=self)
            return
        directory = str(Path(directory).resolve())
        self.maaend_changed = directory != self.cfg.maaend_dir
        self.cfg.maaend_dir = directory
        self.cfg.game_url = self.url_var.get().strip() or self.cfg.game_url
        self.cfg.browser_path = self.browser_var.get().strip()
        self.cfg.agent_enabled = bool(self.agent_var.get())
        self.cfg.headless = bool(self.headless_var.get())
        self.cfg.web.force_viewport = bool(self.force_var.get())
        self.cfg.web.sharpen = round(float(self.sharp_var.get()), 2)
        port_text = self.port_var.get().strip()
        self.cfg.browser_port = int(port_text) if port_text.isdigit() else 0
        self.cfg.save()
        self.saved = True
        self.destroy()
