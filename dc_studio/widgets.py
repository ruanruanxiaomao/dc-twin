# -*- coding: utf-8 -*-
"""Reusable Tk widgets with the cockpit look.

设计约定（2026-11 重做）：
  · 圆角靠 Canvas 画，卡片用「背景画布 + 内容 Frame」两层实现，接口保持不变
    （`Card.body` 仍是普通 Frame），所以各页面无需改动即可享受圆角。
  · 层级靠亮度阶梯（BG < PANEL < PANEL_ALT < PANEL_HI），不靠粗边框。
  · 一个强调色只出现在：卡片顶部 2px 条、按钮文字、选中态、关键数字。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from . import theme as T


def _outer_bg(widget, fallback: str = T.BG) -> str:
    """取父容器底色：圆角外的像素必须与父容器同色，否则会出现方角黑边。"""
    try:
        return widget.cget("bg")
    except Exception:
        return fallback


# ---------------------------------------------------------------- ttk theme
def apply_theme(root: tk.Misc) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=T.BG, foreground=T.TEXT, font=T.font(10))
    style.configure("Treeview",
                    background=T.PANEL, fieldbackground=T.PANEL, foreground=T.TEXT,
                    rowheight=32, borderwidth=0, font=T.font(9))
    style.configure("Treeview.Heading",
                    background=T.PANEL_ALT, foreground=T.MUTED, relief="flat",
                    font=T.font(9, "bold"), padding=(10, 8), borderwidth=0)
    style.map("Treeview",
              background=[("selected", T.PANEL_HI)],
              foreground=[("selected", T.ACCENT)])
    style.map("Treeview.Heading", background=[("active", T.PANEL_HI)])
    style.configure("TCombobox",
                    fieldbackground=T.PANEL_ALT, background=T.PANEL_ALT, foreground=T.TEXT,
                    arrowcolor=T.MUTED, bordercolor=T.BORDER, lightcolor=T.PANEL_ALT,
                    darkcolor=T.PANEL_ALT, padding=7)
    style.map("TCombobox",
              fieldbackground=[("readonly", T.PANEL_ALT)],
              foreground=[("readonly", T.TEXT)],
              selectbackground=[("readonly", T.PANEL_ALT)],
              selectforeground=[("readonly", T.TEXT)])
    style.configure("Vertical.TScrollbar", background=T.PANEL_HI, troughcolor=T.BG,
                    bordercolor=T.BG, arrowcolor=T.BG, width=8, arrowsize=0)
    style.map("Vertical.TScrollbar", background=[("active", T.BORDER_HI)])
    style.configure("Horizontal.TScrollbar", background=T.PANEL_HI, troughcolor=T.BG,
                    bordercolor=T.BG, arrowcolor=T.BG)
    style.configure("TSeparator", background=T.BORDER)
    style.configure("TEntry", fieldbackground=T.PANEL_ALT, foreground=T.TEXT,
                    insertcolor=T.TEXT, bordercolor=T.BORDER, padding=7)


# ---------------------------------------------------------------- cards
class Card(tk.Frame):
    """圆角卡片：Canvas 画底，内容放上层。`.body` 是给页面用的内容容器。"""

    def __init__(self, parent, title: str | None = None, subtitle: str | None = None,
                 accent: str | None = None, padding: int = 16,
                 radius: int = T.RADIUS, fill: str = T.PANEL, **kwargs):
        bg = kwargs.pop("bg", None) or _outer_bg(parent)
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0, **kwargs)
        self._accent = accent
        self._radius = radius
        self._padding = padding
        self._fill = fill
        self._back = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._back.place(x=0, y=0, relwidth=1, relheight=1)

        top = padding + (8 if accent else 0)
        if title:
            head = tk.Frame(self, bg=fill)
            head.pack(fill="x", padx=padding, pady=(top, 0))
            tk.Label(head, text=title, bg=fill, fg=T.TEXT,
                     font=T.font(11, "bold")).pack(side="left")
            if subtitle:
                tk.Label(head, text=subtitle, bg=fill, fg=T.DIM,
                         font=T.font(8)).pack(side="left", padx=(10, 0), pady=(3, 0))
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=padding,
                       pady=(10 if title else top, padding))

        # 子控件按创建顺序在上层，无需 lower（Canvas.lower 是 tag_lower，会报错）
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        c = self._back
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 8:
            return
        c.delete("all")
        T.round_rect(c, 0.5, 0.5, w - 0.5, h - 0.5, self._radius,
                     fill=self._fill, outline=T.BORDER)
        if self._accent:
            # 顶部 2px 强调条：两端留 padding，避免破坏圆角
            T.round_rect(c, self._padding, 1.5, w - self._padding, 3.5, 1,
                         fill=self._accent, outline="")


class StatCard(tk.Frame):
    def __init__(self, parent, label: str, value: str, note: str = "",
                 accent: str = T.ACCENT, unit: str = "", radius: int = T.RADIUS):
        bg = _outer_bg(parent)
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self._accent = accent
        self._radius = radius
        self._back = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._back.place(x=0, y=0, relwidth=1, relheight=1)

        body = tk.Frame(self, bg=T.PANEL_ALT)
        body.pack(side="left", fill="both", expand=True, padx=(20, 14), pady=12)
        tk.Label(body, text=label, bg=T.PANEL_ALT, fg=T.MUTED,
                 font=T.font(8, "bold")).pack(anchor="w")
        row = tk.Frame(body, bg=T.PANEL_ALT)
        row.pack(anchor="w", pady=(3, 0))
        tk.Label(row, text=str(value), bg=T.PANEL_ALT, fg=T.TEXT,
                 font=(T.FONT_FAMILY, 21, "bold")).pack(side="left")
        if unit:
            tk.Label(row, text=unit, bg=T.PANEL_ALT, fg=T.DIM,
                     font=T.font(9)).pack(side="left", padx=(5, 0), pady=(9, 0))
        if note:
            tk.Label(body, text=note, bg=T.PANEL_ALT, fg=T.DIM,
                     font=T.font(8)).pack(anchor="w")

        # 子控件按创建顺序在上层，无需 lower（Canvas.lower 是 tag_lower，会报错）
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        c = self._back
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 8:
            return
        c.delete("all")
        T.round_rect(c, 0.5, 0.5, w - 0.5, h - 0.5, self._radius,
                     fill=T.PANEL_ALT, outline=T.BORDER)
        # 左侧 3px 竖条：圆角矩形，避开上下圆角
        T.round_rect(c, 1.5, 10, 4.5, h - 10, 1.5, fill=self._accent, outline="")


class Chip(tk.Canvas):
    """小胶囊标签（语义色）。"""

    def __init__(self, parent, text: str, fg: str = T.MUTED, bg: str = T.PANEL_ALT,
                 pad=(9, 3), **kwargs):
        fnt = tkfont.Font(font=T.font(8, "bold"))
        w = fnt.measure(text) + pad[0] * 2
        h = fnt.metrics("linespace") + pad[1] * 2
        super().__init__(parent, width=w, height=h, bg=_outer_bg(parent),
                         highlightthickness=0, bd=0, **kwargs)
        T.round_rect(self, 1, 1, w - 1, h - 1, h / 2, fill=bg, outline=T.mix(bg, fg, 0.35))
        self.create_text(w / 2, h / 2, text=text, fill=fg, font=fnt)


class PillButton(tk.Canvas):
    """胶囊按钮。保留 `set_active()`，并支持 `configure(text=...)`。"""

    def __init__(self, parent, text: str, command=None, fg: str = T.MUTED,
                 bg: str = T.PANEL_ALT, hover: str = T.PANEL_HI,
                 active_fg: str = T.ACCENT, pad=(12, 6), radius: int | None = None,
                 font=None):
        self._fnt = tkfont.Font(font=font or T.font(9, "bold"))
        w = self._fnt.measure(text) + pad[0] * 2
        h = self._fnt.metrics("linespace") + pad[1] * 2
        super().__init__(parent, width=w, height=h, bg=_outer_bg(parent),
                         highlightthickness=0, bd=0, cursor="hand2")
        self._text = text
        self._command = command
        self._fg, self._bg, self._hover = fg, bg, hover
        self._active_fg = active_fg
        self._radius = radius if radius is not None else h / 2
        self._pw, self._ph = w, h
        self._active = False
        self._draw()
        self.bind("<Enter>", lambda _e: self._paint(self._hover))
        self.bind("<Leave>", lambda _e: self._paint(self._bg))
        self.bind("<Button-1>", self._on_click)

    def _draw(self, fill: str | None = None, fg: str | None = None):
        self.delete("all")
        fill = fill or self._bg
        fg = fg or self._fg
        T.round_rect(self, 1, 1, self._pw - 1, self._ph - 1, self._radius,
                     fill=fill, outline=T.mix(fill, T.TEXT, 0.06))
        self.create_text(self._pw / 2, self._ph / 2, text=self._text, fill=fg, font=self._fnt)

    def _paint(self, fill: str):
        if self._active:
            return
        self._draw(fill=fill)

    def _on_click(self, _e=None):
        if self._command:
            self._command()

    def set_active(self, active: bool):
        self._active = active
        self._draw(fill=self._hover if active else self._bg,
                   fg=self._active_fg if active else self._fg)

    def set_text(self, text: str):
        self._text = text
        self._draw()

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        text = kwargs.pop("text", None)
        fg = kwargs.pop("fg", None)
        if fg is not None:
            self._fg = fg
        if text is not None:
            self.set_text(text)
        elif fg is not None:
            self._draw()
        if cnf or kwargs:
            super().configure(cnf, **kwargs)

    config = configure


# ---------------------------------------------------------------- scrolling
class ScrollFrame(tk.Frame):
    """Vertically scrollable container; put content into `.inner`."""

    def __init__(self, parent, bg: str = T.BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y", padx=(2, 0))
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda _e: self._unbind_wheel())

    def _on_inner(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event):
        if getattr(event, "num", None) == 4:
            step = -3
        elif getattr(event, "num", None) == 5:
            step = 3
        else:
            step = -3 if event.delta > 0 else 3
        self.canvas.yview_scroll(step, "units")
        return "break"

    def to_top(self):
        self.canvas.yview_moveto(0)


# ---------------------------------------------------------------- misc
def hline(parent, pad=(0, 8)) -> tk.Frame:
    bg = _outer_bg(parent)
    line = tk.Frame(parent, bg=T.mix(bg, T.BORDER, 0.65), height=1)
    line.pack(fill="x", pady=pad)
    return line


def kv(parent, key: str, value: str, key_w: int = 10, color: str = T.TEXT):
    row = tk.Frame(parent, bg=parent.cget("bg"))
    row.pack(fill="x", pady=2)
    tk.Label(row, text=key, bg=row.cget("bg"), fg=T.DIM, font=T.font(9),
             width=key_w, anchor="w").pack(side="left")
    tk.Label(row, text=value, bg=row.cget("bg"), fg=color, font=T.font(9),
             anchor="w", justify="left", wraplength=420).pack(side="left", fill="x", expand=True)
    return row


def zebra(tree: ttk.Treeview) -> None:
    tree.tag_configure("odd", background=T.PANEL)
    tree.tag_configure("even", background=T.PANEL_ALT)
    tree.tag_configure("hot", foreground=T.AMBER)
    tree.tag_configure("bad", foreground=T.RED)


def make_tree(parent, columns: list[tuple[str, str, int]], height: int = 12) -> ttk.Treeview:
    tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings",
                        height=height, selectmode="browse")
    for key, label, width in columns:
        anchor = "w" if key in ("name", "scenario", "definition", "source") else "e"
        tree.heading(key, text=label, anchor=anchor)
        tree.column(key, width=width, anchor=anchor, stretch=(key in ("name", "definition")))
    zebra(tree)
    return tree
