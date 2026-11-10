# -*- coding: utf-8 -*-
"""Hand-drawn canvas charts (no matplotlib dependency)."""

from __future__ import annotations

import math
import tkinter as tk

from . import theme as T


def _fmt(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def bar_chart(canvas: tk.Canvas, rows: list[dict], value_key: str = "throughput",
              label_key: str = "scenario", highlight: str | None = None,
              color: str = T.ACCENT, unit: str = "") -> None:
    canvas.delete("all")
    w = max(canvas.winfo_width(), 200)
    h = max(canvas.winfo_height(), 120)
    if not rows:
        canvas.create_text(w / 2, h / 2, text="暂无数据", fill=T.DIM, font=T.font(10))
        return
    left, right, top, bottom = 46, 14, 18, 34
    plot_w, plot_h = w - left - right, h - top - bottom
    values = [float(r.get(value_key) or 0) for r in rows]
    vmax = max(values) if values else 1
    vmax = vmax * 1.18 if vmax > 0 else 1
    # grid
    for i in range(5):
        y = top + plot_h * i / 4
        val = vmax * (1 - i / 4)
        canvas.create_line(left, y, w - right, y, fill=T.GRID, width=1)
        canvas.create_text(left - 8, y, text=_fmt(val), fill=T.DIM, font=T.font(8), anchor="e")
    n = len(rows)
    slot = plot_w / max(n, 1)
    bw = min(slot * 0.52, 46)
    for i, row in enumerate(rows):
        v = float(row.get(value_key) or 0)
        cx = left + slot * (i + 0.5)
        bh = 0 if vmax <= 0 else plot_h * v / vmax
        y1 = top + plot_h - bh
        name = str(row.get(label_key, ""))
        active = highlight is None or name == highlight
        fill = color if active else T.mix(color, T.PANEL, 0.72)
        canvas.create_rectangle(cx - bw / 2, y1, cx + bw / 2, top + plot_h,
                                fill=fill, outline="")
        if active:
            canvas.create_text(cx, y1 - 9, text=_fmt(v), fill=T.TEXT, font=T.font(8, "bold"))
        label_color = T.TEXT if active else T.DIM
        canvas.create_text(cx, top + plot_h + 14, text=short_name(name),
                           fill=label_color, font=T.font(8), anchor="n")
    if unit:
        canvas.create_text(left, 8, text=unit, fill=T.DIM, font=T.font(8), anchor="w")


SHORT = {
    "baseline": "基线",
    "peak": "峰值",
    "holiday": "大促",
    "night": "夜班",
    "robot_failure": "故障",
    "dock_blocked": "月台堵",
    "layout_variant_f4": "布局F4",
    "scale_50": "50车",
}


def short_name(name: str) -> str:
    return SHORT.get(name, name)


def radar(canvas: tk.Canvas, axes: list[str], values: list[float],
          color: str = T.ACCENT, max_value: float = 100.0, labels: bool = True) -> None:
    canvas.delete("all")
    w = max(canvas.winfo_width(), 160)
    h = max(canvas.winfo_height(), 160)
    cx, cy = w / 2, h / 2 + 4
    radius = min(w, h) / 2 - (34 if labels else 10)
    n = len(axes)
    if n < 3:
        return
    for ring in (0.25, 0.5, 0.75, 1.0):
        pts = []
        for i in range(n):
            ang = -math.pi / 2 + 2 * math.pi * i / n
            pts += [cx + radius * ring * math.cos(ang), cy + radius * ring * math.sin(ang)]
        canvas.create_polygon(pts, outline=T.BORDER, fill="", width=1)
    for i in range(n):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        x, y = cx + radius * math.cos(ang), cy + radius * math.sin(ang)
        canvas.create_line(cx, cy, x, y, fill=T.BORDER)
        if labels:
            lx, ly = cx + (radius + 16) * math.cos(ang), cy + (radius + 16) * math.sin(ang)
            anchor = "center"
            if math.cos(ang) > 0.3:
                anchor = "w"
            elif math.cos(ang) < -0.3:
                anchor = "e"
            canvas.create_text(lx, ly, text=axes[i], fill=T.MUTED, font=T.font(8), anchor=anchor)
    pts = []
    for i in range(n):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        r = radius * max(0.0, min(1.0, values[i] / max_value))
        pts += [cx + r * math.cos(ang), cy + r * math.sin(ang)]
    canvas.create_polygon(pts, outline=color, fill=T.mix(color, T.PANEL, 0.55),
                          width=2, stipple="gray50")
    for i in range(0, len(pts), 2):
        canvas.create_oval(pts[i] - 2.5, pts[i + 1] - 2.5, pts[i] + 2.5, pts[i + 1] + 2.5,
                           fill=color, outline="")


def donut(canvas: tk.Canvas, pct: float, center: str, sub: str = "",
          color: str = T.GREEN, size: int | None = None) -> None:
    canvas.delete("all")
    w = max(canvas.winfo_width(), 120)
    h = max(canvas.winfo_height(), 120)
    side = size or min(w, h) - 10
    x1, y1 = (w - side) / 2, (h - side) / 2
    x2, y2 = x1 + side, y1 + side
    canvas.create_arc(x1, y1, x2, y2, start=0, extent=359.99, style="arc",
                      outline=T.PANEL_HI, width=10)
    extent = -359.99 * max(0.0, min(1.0, pct))
    if abs(extent) > 0.1:
        canvas.create_arc(x1, y1, x2, y2, start=90, extent=extent, style="arc",
                          outline=color, width=10)
    canvas.create_text(w / 2, h / 2 - 6, text=center, fill=T.TEXT,
                       font=(T.FONT_FAMILY, int(side * 0.19), "bold"))
    if sub:
        canvas.create_text(w / 2, h / 2 + side * 0.17, text=sub, fill=T.DIM, font=T.font(8))


def sparkline(canvas: tk.Canvas, values: list[float], color: str = T.ACCENT) -> None:
    canvas.delete("all")
    w, h = max(canvas.winfo_width(), 40), max(canvas.winfo_height(), 20)
    if len(values) < 2:
        return
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pts = []
    for i, v in enumerate(values):
        pts += [4 + (w - 8) * i / (len(values) - 1), h - 4 - (h - 8) * (v - lo) / span]
    canvas.create_line(pts, fill=color, width=2, smooth=True)
