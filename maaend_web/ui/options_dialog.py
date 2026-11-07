"""任务选项对话框：把 ProjectInterface 的 option 渲染成可编辑表单。"""

from __future__ import annotations

import copy
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, Optional

from ..project import OptionDef, Project
from . import theme
from .theme import clean_label


class OptionsDialog(tk.Toplevel):
    def __init__(self, master, project: Project, task_name: str, values: Dict[str, Any]):
        super().__init__(master)
        self.project = project
        self.task = project.tasks[task_name]
        self.local: Dict[str, Any] = copy.deepcopy(values)
        self.result: Optional[Dict[str, Any]] = None
        self.f = theme.fonts()
        p = theme.Palette
        self.title(f"任务选项 · {clean_label(self.task.label)}")
        self.configure(bg=p.bg)
        self.transient(master)

        # 小屏（1366×768 之类）上固定 680 高会顶出屏幕，这里按可用高度收缩，
        # 并把左上角夹在屏幕内，避免对话框跑到可视区域外。
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(620, sw - 80)
        height = min(680, sh - 120)
        x = min(master.winfo_rootx() + 120, sw - width - 20)
        y = min(master.winfo_rooty() + 80, sh - height - 40)
        self.geometry(f"{width}x{height}+{max(20, x)}+{max(20, y)}")
        self.minsize(min(520, width), min(420, height))
        self.grab_set()

        head = ttk.Frame(self, padding=(20, 20, 20, 6))
        head.pack(fill="x")
        ttk.Label(head, text=clean_label(self.task.label), style="TLabel", font=self.f["title"]).pack(anchor="w")
        if self.task.description:
            ttk.Label(head, text=self.task.description, style="Dim.TLabel", wraplength=560, justify="left").pack(
                anchor="w", pady=(6, 0)
            )

        body = ttk.Frame(self, padding=(20, 4, 20, 8))
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=p.bg, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        sb.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=sb.set)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        foot = ttk.Frame(self, padding=(20, 6, 20, 16))
        foot.pack(fill="x")
        ttk.Button(foot, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(foot, text="保存", style="Accent.TButton", command=self._save).pack(side="right", padx=(0, 8))
        ttk.Button(foot, text="恢复默认", command=self._reset).pack(side="left")

        self._render()

    def _on_wheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    # ---------------------------------------------------------------- 渲染

    def _render(self) -> None:
        for child in self.inner.winfo_children():
            child.destroy()
        if not self.task.option:
            ttk.Label(self.inner, text="这个任务没有可调选项。", style="Dim.TLabel").pack(anchor="w", pady=20)
            return
        for name in self.task.option:
            self._render_option(name, self.inner, 0)

    def _render_option(self, name: str, parent, depth: int) -> None:
        opt = self.project.options.get(name)
        if not opt:
            return
        pad = 12 * depth
        card = ttk.Frame(parent, style="Panel.TFrame", padding=(14, 12))
        card.pack(fill="x", padx=(pad, 0), pady=(0, 8))

        top = ttk.Frame(card, style="Panel.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text=opt.label, style="Panel.TLabel", font=self.f["h2"]).pack(side="left")
        if opt.description:
            ttk.Label(
                card, text=opt.description, style="PanelDim.TLabel", wraplength=470, justify="left"
            ).pack(anchor="w", pady=(4, 8))

        if opt.type in ("switch", "select"):
            self._render_choice(card, opt)
            case_name = self._current_case(opt)
            case = opt.case(case_name) if case_name else None
            if case:
                for sub in case.option:
                    self._render_option(sub, card, depth + 1)
        elif opt.type == "checkbox":
            self._render_checkboxes(card, opt)
            picked = self.local.get(opt.name) or []
            for case in opt.cases:
                if case.name in picked:
                    for sub in case.option:
                        self._render_option(sub, card, depth + 1)
        elif opt.type == "input":
            self._render_inputs(card, opt)
        elif opt.type == "hotkey":
            self._render_hotkeys(card, opt)

    def _current_case(self, opt: OptionDef) -> str:
        value = self.local.get(opt.name)
        if isinstance(value, str) and opt.case(value):
            return value
        if value is None:
            return opt.default_case or (opt.cases[0].name if opt.cases else "")
        return str(value)

    def _render_choice(self, parent, opt: OptionDef) -> None:
        var = tk.StringVar(value=self._current_case(opt))
        if opt.type == "switch" and len(opt.cases) == 2:
            row = ttk.Frame(parent, style="Panel.TFrame")
            row.pack(fill="x", pady=(2, 0))
            for case in opt.cases:
                ttk.Radiobutton(
                    row,
                    text=case.label,
                    value=case.name,
                    variable=var,
                    command=lambda: self._choose(opt.name, var.get()),
                ).pack(side="left", padx=(0, 18))
            if not opt.default_case:
                var.set(opt.cases[0].name)
        else:
            box = ttk.Combobox(parent, state="readonly", width=28)
            box["values"] = [c.label for c in opt.cases]
            current = self._current_case(opt)
            label = next((c.label for c in opt.cases if c.name == current), "")
            box.set(label)
            box.pack(anchor="w", pady=(2, 0))

            def on_select(_e=None):
                chosen = next((c.name for c in opt.cases if c.label == box.get()), current)
                self._choose(opt.name, chosen)

            box.bind("<<ComboboxSelected>>", on_select)

    def _render_checkboxes(self, parent, opt: OptionDef) -> None:
        """多选框：按 min_count / max_count 约束，选中项写入 list。"""
        value = self.local.get(opt.name)
        if not isinstance(value, list):
            value = opt.checked()
            self.local[opt.name] = list(value)
        picked = set(value)

        bar = ttk.Frame(parent, style="Panel.TFrame")
        bar.pack(fill="x", pady=(2, 0))
        wrap = ttk.Frame(bar, style="Panel.TFrame")
        wrap.pack(side="left", fill="x", expand=True)
        counter = ttk.Label(bar, text="", style="PanelDim.TLabel")
        counter.pack(side="right", anchor="n", padx=(10, 6))

        boxes: Dict[str, tk.BooleanVar] = {}
        per_row = 3 if len(opt.cases) > 6 else 2
        for idx, case in enumerate(opt.cases):
            var = tk.BooleanVar(value=case.name in picked)
            boxes[case.name] = var
            ttk.Checkbutton(
                wrap,
                text=case.label,
                variable=var,
                style="TCheckbutton",
                command=lambda n=case.name: self._toggle_check(opt, boxes, n, counter),
            ).grid(row=idx // per_row, column=idx % per_row, sticky="w", padx=(0, 16), pady=2)

        def refresh(_e=None):
            self._update_counter(opt, boxes, counter)

        refresh()

    def _update_counter(self, opt: OptionDef, boxes, label) -> None:
        n = sum(1 for v in boxes.values() if v.get())
        parts = [f"已选 {n}/{len(opt.cases)}"]
        if opt.min_count or opt.max_count:
            limit = []
            if opt.min_count:
                limit.append(f"至少 {opt.min_count}")
            if opt.max_count:
                limit.append(f"最多 {opt.max_count}")
            parts.append(" · ".join(limit))
        label.configure(
            text="  ".join(parts),
            foreground=theme.Palette.danger if (
                n < opt.min_count or (opt.max_count and n > opt.max_count)
            ) else theme.Palette.text_faint,
        )

    def _toggle_check(self, opt: OptionDef, boxes, name: str, counter) -> None:
        picked = [n for n, v in boxes.items() if v.get()]
        if opt.max_count and len(picked) > opt.max_count:
            boxes[name].set(False)
            self._update_counter(opt, boxes, counter)
            return
        if opt.min_count and len(picked) < opt.min_count:
            boxes[name].set(True)
            self._update_counter(opt, boxes, counter)
            return
        self.local[opt.name] = [c.name for c in opt.cases if c.name in picked]
        self._update_counter(opt, boxes, counter)
        # 勾选状态会改变可见子选项，整体重绘（保留滚动位置）
        top = self.canvas.yview()[0]
        self.after(30, lambda: (self._render(), self.canvas.yview_moveto(top)))

    def _choose(self, name: str, value: str) -> None:
        self.local[name] = value
        # 切换分支会改变可见的子选项，整体重绘最稳妥
        self.after(30, self._render)

    def _render_inputs(self, parent, opt: OptionDef) -> None:
        data = self.local.get(opt.name)
        if not isinstance(data, dict):
            data = {i.name: i.default for i in opt.inputs}
            self.local[opt.name] = data
        for item in opt.inputs:
            row = ttk.Frame(parent, style="Panel.TFrame")
            row.pack(fill="x", pady=(4, 0))
            ttk.Label(row, text=item.label, style="PanelDim.TLabel", width=22).pack(side="left")
            var = tk.StringVar(value=str(data.get(item.name, item.default)))
            show = "•" if item.password else ""
            entry = ttk.Entry(row, textvariable=var, show=show)
            entry.pack(side="left", fill="x", expand=True)
            if item.verify:
                self._attach_verify(entry, var, item.verify, item.pattern_msg)

            def sync(_e=None, n=item.name, v=var):
                self.local[opt.name][n] = v.get()

            var.trace_add("write", sync)

    def _attach_verify(self, entry, var, pattern: str, message: str) -> None:
        import re

        def check(*_):
            try:
                ok = re.fullmatch(pattern, var.get()) is not None
            except re.error:
                ok = True
            entry.configure(foreground=theme.Palette.text if ok else theme.Palette.danger)

        var.trace_add("write", check)

    def _render_hotkeys(self, parent, opt: OptionDef) -> None:
        data = self.local.get(opt.name)
        if not isinstance(data, dict):
            data = {h.name: h.default for h in opt.hotkeys}
            self.local[opt.name] = data
        for item in opt.hotkeys:
            row = ttk.Frame(parent, style="Panel.TFrame")
            row.pack(fill="x", pady=(4, 0))
            ttk.Label(row, text=item.label, style="PanelDim.TLabel", width=22).pack(side="left")
            var = tk.StringVar(value=str(data.get(item.name, item.default)))
            ttk.Entry(row, textvariable=var, width=12).pack(side="left")
            ttk.Label(row, text="例如 R、F、ALT+A", style="PanelDim.TLabel").pack(side="left", padx=(8, 0))

            def sync(_e=None, n=item.name, v=var):
                self.local[opt.name][n] = v.get()

            var.trace_add("write", sync)

    # ---------------------------------------------------------------- 操作

    def _reset(self) -> None:
        self.local = self.project.default_values(self.task.option)
        self._render()

    def _save(self) -> None:
        self.result = self.local
        self.destroy()
