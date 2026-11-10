# -*- coding: utf-8 -*-
"""Colour palette, fonts and small drawing helpers."""

from __future__ import annotations

import tkinter as tk

# ---------------------------------------------------------------- palette
# 2026-11 视觉重做：底色略提亮（纯黑在大屏上刺眼），层级靠"亮度阶梯"而不是粗边框。
BG = "#0a0e16"           # 页面底
SIDEBAR = "#0d1219"      # 侧栏（比页面更沉，形成左侧暗区）
SIDEBAR_ACTIVE = "#182131"
SIDEBAR_HOVER = "#121a26"
PANEL = "#111823"        # 卡片
PANEL_ALT = "#161f2d"    # 卡片内的子块（行、指标格）
PANEL_HI = "#1e2a3c"     # hover / 选中
BORDER = "#202b3b"       # 常态边框（很淡，只做分隔不做重音）
BORDER_HI = "#31415a"    # 强调边框 / 焦点
TEXT = "#eef3fb"
MUTED = "#9babc4"
DIM = "#697a93"
ACCENT = "#4dc9f6"
ACCENT_DARK = "#1f7f9e"
MAGENTA = "#f72585"
GREEN = "#2ee6a6"
AMBER = "#f5c451"
RED = "#ff5c7a"
PURPLE = "#b388ff"
BLUE = "#4d8dff"

CANVAS_BG = "#080c13"
GRID = "#121a26"

# 圆角半径（tk 没有原生圆角，靠 Canvas 画）
RADIUS = 12
RADIUS_SM = 8
# 卡片顶部 1px 内高光，制造"玻璃"错觉
HIGHLIGHT = "#1b2536"

ZONE_COLORS = ["#3b82f6", "#a855f7", "#22d3ee", "#f59e0b", "#34d399", "#f472b6"]

PHASE_COLORS = {
    "MOVE": ACCENT,
    "ROTATE": PURPLE,
    "FACE": PURPLE,
    "PICK": AMBER,
    "DROP": MAGENTA,
    "WAIT": MUTED,
    "PARK": DIM,
    "RETURN": GREEN,
}

FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"


def font(size: int = 10, weight: str = "normal") -> tuple:
    return (FONT_FAMILY, size, weight)


def label(size: int = 9) -> tuple:
    """小标题：全大写感靠字号+灰色实现，中文不加字距。"""
    return (FONT_FAMILY, size, "normal")


def mono(size: int = 10, weight: str = "normal") -> tuple:
    return (MONO_FAMILY, size, weight)


# ---------------------------------------------------------------- colours
def _clamp(v: float) -> int:
    return max(0, min(255, int(round(v))))


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def mix(a: str, b: str, t: float) -> str:
    """Blend two hex colours; t=0 -> a, t=1 -> b."""
    t = max(0.0, min(1.0, t))
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    return rgb_to_hex((_clamp(ra + (rb - ra) * t), _clamp(ga + (gb - ga) * t), _clamp(ba + (bb - ba) * t)))


def shade(color: str, t: float) -> str:
    return mix(color, "#000000", t) if t >= 0 else mix(color, "#ffffff", -t)


# ---------------------------------------------------------------- canvas helpers
def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r=10, **kwargs):
    """Draw a rounded rectangle and return the item id."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kwargs)


def dashed_line(canvas: tk.Canvas, x1, y1, x2, y2, dash=(4, 4), **kwargs):
    return canvas.create_line(x1, y1, x2, y2, dash=dash, **kwargs)
