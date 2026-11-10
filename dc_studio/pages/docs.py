# -*- coding: utf-8 -*-
"""In-app Markdown reader with document list, TOC and search."""

from __future__ import annotations

import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from .. import theme as T
from ..data import open_path
from ..markdown import MarkdownView
from ..widgets import Card, PillButton

DESKTOP_PLAN = Path.home() / "Desktop" / "配送中心数字孪生_完整计划书.md"


class DocsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.store = app.store
        self.docs = self.store.doc_list()
        self.current: Path | None = None

        outer = tk.Frame(self, bg=T.BG)
        outer.pack(fill="both", expand=True, padx=14, pady=12)
        outer.grid_columnconfigure(0, weight=0, minsize=276)
        outer.grid_columnconfigure(1, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        left = tk.Frame(outer, bg=T.BG, width=276)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_propagate(False)

        doc_card = Card(left, "文档", f"{len(self.docs)} 个 Markdown", accent=T.PURPLE)
        doc_card.pack(fill="both", expand=True)
        self.doc_list = self._listbox(doc_card.body, height=10)
        for p in self.docs:
            self.doc_list.insert("end", p.stem)
        self.doc_list.bind("<<ListboxSelect>>", self._on_doc)

        toc_card = Card(left, "目录", "点击跳转", accent=T.ACCENT)
        toc_card.pack(fill="both", expand=True, pady=(10, 0))
        self.toc = self._listbox(toc_card.body, height=12)
        self.toc.bind("<<ListboxSelect>>", self._on_toc)

        right = tk.Frame(outer, bg=T.BG)
        right.grid(row=0, column=1, sticky="nsew")

        bar = tk.Frame(right, bg=T.BG)
        bar.pack(fill="x", pady=(0, 8))
        self.title = tk.Label(bar, text="", bg=T.BG, fg=T.TEXT, font=(T.FONT_FAMILY, 12, "bold"))
        self.title.pack(side="left")
        self.meta = tk.Label(bar, text="", bg=T.BG, fg=T.DIM, font=T.font(8))
        self.meta.pack(side="left", padx=10)

        PillButton(bar, "A−", lambda: self._font(-0.1), pad=(8, 4)).pack(side="right", padx=2)
        PillButton(bar, "A+", lambda: self._font(0.1), pad=(8, 4)).pack(side="right", padx=2)
        PillButton(bar, "浏览器打开", self._open_browser, fg=T.MUTED, pad=(8, 4)).pack(side="right", padx=6)
        PillButton(bar, "定位文件", self._reveal, fg=T.MUTED, pad=(8, 4)).pack(side="right")

        search_bar = tk.Frame(right, bg=T.BG)
        search_bar.pack(fill="x", pady=(0, 6))
        self.search_var = tk.StringVar()
        entry = tk.Entry(search_bar, textvariable=self.search_var, bg=T.PANEL_ALT, fg=T.TEXT,
                         insertbackground=T.TEXT, relief="flat", font=T.font(9))
        entry.pack(side="left", fill="x", expand=True, ipady=6)
        entry.bind("<Return>", lambda _e: self._search())
        tk.Label(search_bar, text="搜索", bg=T.BG, fg=T.DIM, font=T.font(8)).pack(side="left", padx=(8, 4))
        PillButton(search_bar, "下一个", self._search_next, fg=T.ACCENT, pad=(8, 5)).pack(side="left")

        wrap = tk.Frame(right, bg=T.PANEL, highlightbackground=T.BORDER, highlightthickness=1)
        wrap.pack(fill="both", expand=True)
        self.view = MarkdownView(wrap, bg=T.PANEL)
        self.view.pack(fill="both", expand=True)

        if DESKTOP_PLAN.exists():
            note = tk.Label(right, text=f"桌面完整版：{DESKTOP_PLAN.name}", bg=T.BG, fg=T.DIM,
                            font=T.font(8), anchor="w")
            note.pack(fill="x", pady=(6, 0))
            note.bind("<Button-1>", lambda _e: open_path(DESKTOP_PLAN))

        self.after(60, self._load_first)

    @staticmethod
    def _listbox(parent, height=10):
        box = tk.Listbox(parent, bg=T.PANEL, fg=T.MUTED, selectbackground=T.PANEL_HI,
                         selectforeground=T.ACCENT, relief="flat", bd=0, highlightthickness=0,
                         font=T.font(9), height=height, activestyle="none", cursor="hand2")
        box.pack(fill="both", expand=True)
        return box

    # ------------------------------------------------------------ loading
    def _load_first(self):
        if not self.docs:
            self.title.configure(text="未找到 docs/*.md")
            return
        self.doc_list.selection_set(0)
        self._load(self.docs[0])

    def _on_doc(self, _event):
        sel = self.doc_list.curselection()
        if sel:
            self._load(self.docs[sel[0]])

    def _load(self, path: Path):
        self.current = path
        text = self.store.read_text(path)
        self.title.configure(text=path.stem)
        self.meta.configure(text=f"{len(text):,} 字符 · {path.name}")
        self.view.render(text)
        self._fill_toc()
        self.view.text.yview_moveto(0)

    def _fill_toc(self):
        self.toc.delete(0, "end")
        self._toc_lines = []
        for line, level, title in self.view.headings:
            if level > 3:
                continue
            prefix = "  " * (level - 1)
            label = prefix + ("▸ " if level == 1 else "· ") + title
            if len(label) > 22:
                label = label[:21] + "…"
            self.toc.insert("end", label)
            self._toc_lines.append(line)
        if self.toc.size():
            self.toc.selection_set(0)

    def _on_toc(self, _event):
        sel = self.toc.curselection()
        if sel and sel[0] < len(self._toc_lines):
            self.view.scroll_to_line(self._toc_lines[sel[0]])

    # ------------------------------------------------------------ actions
    def _font(self, delta: float):
        self.view.set_scale(self.view.scale + delta)
        self.view._configure_tags()
        if self.current:
            text = self.store.read_text(self.current)
            self.view.render(text)
            self._fill_toc()

    def _search(self):
        ok = self.view.search(self.search_var.get())
        if not ok:
            self.meta.configure(text="未找到匹配")

    def _search_next(self):
        self.view.search_next()

    def _open_browser(self):
        if self.current:
            webbrowser.open(self.current.as_uri())

    def _reveal(self):
        if self.current:
            open_path(self.current.parent)
