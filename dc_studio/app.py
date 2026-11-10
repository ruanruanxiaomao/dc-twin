# -*- coding: utf-8 -*-
"""Application shell: sidebar, header, page stack."""

from __future__ import annotations

import ctypes
import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from . import APP_TITLE, APP_VERSION, theme as T
from .data import Store
from .pages import (AreasPage, DocsPage, KpiPage, LayoutPage, OverviewPage,
                    ScenariosPage, ToolsPage)
from .widgets import PillButton, apply_theme

PAGES = [
    ("overview", "◎", "总览", "项目规模 · 能力 · 验收健康度", OverviewPage),
    ("layout", "▦", "布局沙盘", "2D 俯视 · 图层 · AGV 回放", LayoutPage),
    ("areas", "☑", "功能区", "勾选你的功能区 · 实时算占地与深度", AreasPage),
    ("scenarios", "⟳", "场景实验室", "8 个场景 · 实测 KPI · 损益", ScenariosPage),
    ("kpis", "∑", "KPI 指标", "定义 · 公式 · 跨场景对比", KpiPage),
    ("docs", "≡", "计划书", "内置 Markdown 阅读器", DocsPage),
    ("tools", "⚙", "工具箱", "流水线 · 界面入口 · 实时日志", ToolsPage),
]


class NavItem(tk.Canvas):
    """自绘导航行：圆角高亮块 + 左侧 3px 强调条，比 Frame+Label 干净得多。"""

    H = 40

    def __init__(self, parent, glyph: str, label: str, command):
        super().__init__(parent, height=self.H, bg=T.SIDEBAR,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.glyph = glyph
        self.text = label
        self.active = False
        self._hover = False
        self.bind("<Button-1>", lambda _e: self.command())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Configure>", lambda _e: self._draw())

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        if w < 20:
            w = 196
        h = self.H
        if self.active:
            T.round_rect(self, 2, 3, w - 2, h - 3, 9, fill=T.SIDEBAR_ACTIVE, outline="")
            T.round_rect(self, 3.5, 13, 6, h - 13, 1.2, fill=T.ACCENT, outline="")
        elif self._hover:
            T.round_rect(self, 2, 3, w - 2, h - 3, 9, fill=T.SIDEBAR_HOVER, outline="")
        self.create_text(26, h / 2, text=self.glyph,
                         fill=T.ACCENT if self.active else T.DIM,
                         font=("Segoe UI Symbol", 13))
        self.create_text(48, h / 2, text=self.text, anchor="w",
                         fill=T.TEXT if self.active else T.MUTED,
                         font=T.font(10, "bold" if self.active else "normal"))

    def _enter(self, _e=None):
        self._hover = True
        if not self.active:
            self._draw()

    def _leave(self, _e=None):
        self._hover = False
        if not self.active:
            self._draw()

    def set_active(self, active: bool):
        self.active = active
        self._draw()


class StudioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.store = Store()
        self.pages: dict[str, tk.Frame] = {}
        self.nav: dict[str, NavItem] = {}
        self.page_meta = {key: (label, sub) for key, _g, label, sub, _cls in PAGES}
        self.current = "overview"

        self.title(APP_TITLE)
        self.configure(bg=T.BG)
        self.geometry(self._centered(1420, 900))
        self.minsize(1160, 740)
        apply_theme(self)
        self._set_icon()

        self._build()
        self.show("overview")
        # 快捷键按 PAGES 顺序自动绑定，加页面不用再改这段
        for i, (key, _g, _l, _s, _cls) in enumerate(PAGES, start=1):
            self.bind("<Control-Key-%d>" % i, lambda _e, k=key: self.show(k))
        self.bind("<F5>", lambda _e: self.reload())
        self._tick_clock()

    # ------------------------------------------------------------ geometry
    def _centered(self, w: int, h: int) -> str:
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 20)
        return f"{w}x{h}+{x}+{y}"

    def _set_icon(self):
        ico = self.store.root / "assets" / "dc_studio.ico"
        if ico.exists():
            try:
                self.iconbitmap(default=str(ico))
                return
            except Exception:
                pass
        try:
            img = tk.PhotoImage(width=32, height=32)
            img.put(T.ACCENT, to=(0, 0, 32, 32))
            img.put("#0a1120", to=(4, 4, 28, 28))
            img.put(T.ACCENT, to=(8, 8, 24, 24))
            self.iconphoto(True, img)
            self._icon_ref = img
        except Exception:
            pass

    # ------------------------------------------------------------ layout
    def _build(self):
        self.sidebar = tk.Frame(self, bg=T.SIDEBAR, width=228)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._sidebar()

        main = tk.Frame(self, bg=T.BG)
        main.pack(side="left", fill="both", expand=True)
        self._header(main)

        self.stack = tk.Frame(main, bg=T.BG)
        self.stack.pack(fill="both", expand=True)
        self.stack.grid_rowconfigure(0, weight=1)
        self.stack.grid_columnconfigure(0, weight=1)

    def _sidebar(self):
        logo = tk.Frame(self.sidebar, bg=T.SIDEBAR)
        logo.pack(fill="x", padx=18, pady=(24, 20))
        badge = tk.Canvas(logo, width=40, height=40, bg=T.SIDEBAR, highlightthickness=0)
        badge.pack(side="left")
        T.round_rect(badge, 1, 1, 39, 39, 11, fill=T.mix(T.SIDEBAR, T.ACCENT, 0.16),
                     outline=T.mix(T.SIDEBAR, T.ACCENT, 0.38))
        badge.create_text(20, 20, text="DC", fill=T.ACCENT,
                          font=(T.MONO_FAMILY, 12, "bold"))
        text = tk.Frame(logo, bg=T.SIDEBAR)
        text.pack(side="left", padx=11)
        tk.Label(text, text="DC TWIN", bg=T.SIDEBAR, fg=T.TEXT,
                 font=(T.FONT_FAMILY, 12, "bold")).pack(anchor="w")
        tk.Label(text, text="STUDIO  ·  本地驾驶舱", bg=T.SIDEBAR, fg=T.DIM,
                 font=T.font(8)).pack(anchor="w", pady=(1, 0))

        tk.Label(self.sidebar, text="工 作 台", bg=T.SIDEBAR, fg=T.DIM,
                 font=T.font(8, "bold")).pack(anchor="w", padx=24, pady=(2, 8))
        for key, glyph, label, _sub, _cls in PAGES:
            item = NavItem(self.sidebar, glyph, label, lambda k=key: self.show(k))
            item.pack(fill="x", padx=10, pady=1)
            self.nav[key] = item

        bottom = tk.Frame(self.sidebar, bg=T.SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=18, pady=16)
        tk.Frame(bottom, bg=T.mix(T.SIDEBAR, T.BORDER, 0.7), height=1).pack(fill="x", pady=(0, 12))
        state = tk.Frame(bottom, bg=T.SIDEBAR)
        state.pack(anchor="w")
        dot = tk.Canvas(state, width=8, height=8, bg=T.SIDEBAR, highlightthickness=0)
        dot.pack(side="left", pady=(1, 0))
        dot.create_oval(1, 1, 7, 7, fill=T.GREEN, outline="")
        tk.Label(state, text="只读 · 纯本地", bg=T.SIDEBAR, fg=T.MUTED,
                 font=T.font(8, "bold")).pack(side="left", padx=(6, 0))
        tk.Label(bottom, text=f"v{APP_VERSION}  ·  Ctrl+1…{len(PAGES)} 切换",
                 bg=T.SIDEBAR, fg=T.DIM, font=T.font(8)).pack(anchor="w", pady=(4, 8))
        path = str(self.store.root)
        if len(path) > 26:
            path = "…" + path[-25:]
        tk.Label(bottom, text=path, bg=T.SIDEBAR, fg=T.DIM, font=T.mono(7)).pack(anchor="w")

    def _header(self, parent):
        head = tk.Frame(parent, bg=T.BG)
        head.pack(fill="x", padx=22, pady=(16, 4))
        left = tk.Frame(head, bg=T.BG)
        left.pack(side="left")
        self.page_title = tk.Label(left, text="", bg=T.BG, fg=T.TEXT,
                                   font=(T.FONT_FAMILY, 17, "bold"), anchor="w")
        self.page_title.pack(anchor="w")
        self.page_sub = tk.Label(left, text="", bg=T.BG, fg=T.DIM, font=T.font(9), anchor="w")
        self.page_sub.pack(anchor="w", pady=(2, 0))

        right = tk.Frame(head, bg=T.BG)
        right.pack(side="right")
        self.clock = tk.Label(right, text="", bg=T.BG, fg=T.MUTED, font=(T.MONO_FAMILY, 9))
        self.clock.pack(side="right", padx=(10, 0))
        PillButton(right, "刷新数据", self.reload, fg=T.ACCENT, pad=(10, 5)).pack(side="right")

    # ------------------------------------------------------------ pages
    def show(self, key: str):
        if key not in self.pages:
            for pkey, _g, _l, _s, cls in PAGES:
                if pkey == key:
                    page = cls(self.stack, self)
                    page.grid(row=0, column=0, sticky="nsew")
                    self.pages[key] = page
                    break
        for pkey, page in self.pages.items():
            if pkey == key:
                page.tkraise()
        for pkey, item in self.nav.items():
            item.set_active(pkey == key)
        label, sub = self.page_meta[key]
        self.page_title.configure(text=label)
        self.page_sub.configure(text=sub)
        self.current = key

    def reload(self):
        try:
            self.store = Store()
            for page in self.pages.values():
                page.destroy()
            self.pages.clear()
            self.show(self.current)
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("刷新失败", str(exc))

    def _tick_clock(self):
        self.clock.configure(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self.after(1000, self._tick_clock)


def main() -> int:
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = StudioApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
