# -*- coding: utf-8 -*-
"""功能区勾选台：用户勾自己配送中心要哪些功能区，实时看占地/深度/产能。

这一页是 `scripts/build_variant.py` 的可视化前端，但**排布计算不重写**——
直接加载 `build_variant.py` 里的 `pack()`，保证"界面看到的"和"真正生成的"
是同一套算法。若两边各写一份，必然漂移。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, ttk

from .. import theme as T
from ..widgets import Card, Chip, PillButton, ScrollFrame

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 功能区分组 → 颜色，用于清单小圆点和预览矩形
GROUP_COLORS = {
    "入库": "#48c9f0",
    "存储": "#4d8dff",
    "拣选": "#2ee6a6",
    "出库": "#f5c451",
    "逆向": "#ff5c7a",
    "支持": "#b388ff",
}
FALLBACK_COLOR = "#93a7c9"


def _load_engine(root: Path):
    """加载 build_variant.py 作为排布引擎（模块级只有常量，无副作用）。"""
    path = root / "scripts" / "build_variant.py"
    spec = importlib.util.spec_from_file_location("dc_build_variant", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Check(tk.Canvas):
    """自绘勾选框：圆角方块 + 勾号，比 ☑ 字符干净。"""

    def __init__(self, parent, on: bool, command, bg: str):
        super().__init__(parent, width=20, height=20, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._on = on
        self._cmd = command
        self.bind("<Button-1>", lambda _e: self._cmd())
        self._draw()

    def _draw(self):
        self.delete("all")
        if self._on:
            T.round_rect(self, 2, 2, 18, 18, 6, fill=T.ACCENT, outline="")
            self.create_line(6.5, 10, 9.5, 13, 14, 7, fill="#06202b", width=2,
                             capstyle="round", joinstyle="round")
        else:
            T.round_rect(self, 2, 2, 18, 18, 6, fill="", outline=T.BORDER_HI)

    def set_on(self, on: bool):
        self._on = on
        self._draw()

    def set_bg(self, bg: str):
        self.configure(bg=bg)


class AreasPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.store = app.store
        self.root: Path = self.store.root

        self.catalog_file = self.root / "config" / "areas_catalog.json"
        self.selection_file = self.root / "config" / "areas_selection.json"
        self.base_spec = self.root / "config" / "layout_spec.json"
        self.variant_spec = self.root / "config" / "layout_spec_areas.json"

        self.proc: subprocess.Popen | None = None
        self.busy = False
        self.rows: dict[str, dict] = {}
        self.enabled: set[str] = set()

        self._load_data()
        self._build()
        self._refresh()

    # ------------------------------------------------------------ data
    def _load_data(self):
        self.engine = _load_engine(self.root)
        raw = json.loads(self.catalog_file.read_text(encoding="utf-8"))
        self.templates: dict[str, dict] = {t["template"]: t for t in raw["templates"]}
        self.presets: dict[str, list[str]] = dict(self.engine.PRESETS)

        sel = (json.loads(self.selection_file.read_text(encoding="utf-8"))
               if self.selection_file.exists() else {})
        self.sel_raw = sel
        chosen = [k for k in (sel.get("enabled") or []) if k in self.templates]
        self.enabled = set(chosen) if chosen else set(self.presets.get("ecommerce") or [])

        place = dict(sel.get("placement") or {})
        self.x_margin = float(place.get("x_margin_m", 4.0))
        self.gap = float(place.get("gap_m", 2.0))
        self.offset = float(place.get("band_start_offset_m", 2.0))
        self.north_aisle = float((sel.get("expand") or {}).get("north_aisle_m", 2.0))

        base = json.loads(self.base_spec.read_text(encoding="utf-8"))
        wh = next(w for w in base["warehouses"] if w["id"] == "W")
        self.width = float(wh["size"]["width"])
        self.base_depth = float(wh["size"]["depth"])
        others = [float(w["origin"][1]) for w in base["warehouses"] if w["id"] != "W"]
        # 与 build_variant.build 保持同一口径：北侧前置仓南墙 - 2 m 净距
        self.depth_limit = (min(others) - 2.0) if others else self.base_depth + 30

    # ------------------------------------------------------------ layout
    def _build(self):
        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", padx=26, pady=(10, 8))
        tk.Label(head, text="点一行即勾选 · 右侧实时算占地与深度 · 生成只写变体 spec",
                 bg=T.BG, fg=T.MUTED, font=T.font(9)).pack(side="left")
        Chip(head, "主链不受影响", fg=T.ACCENT).pack(side="left", padx=(12, 0))

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=26, pady=(0, 20))
        body.grid_columnconfigure(0, weight=4, uniform="w")
        body.grid_columnconfigure(1, weight=5, uniform="w")
        body.grid_rowconfigure(0, weight=1)

        # ---------------- 左列：清单（上）+ 输出（下）
        left_col = tk.Frame(body, bg=T.BG)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_col.grid_rowconfigure(0, weight=3)
        left_col.grid_rowconfigure(1, weight=2)
        left_col.grid_columnconfigure(0, weight=1)

        left = Card(left_col, "功能区清单", "点一行即勾选；数字是模板建议值", accent=T.ACCENT)
        left.grid(row=0, column=0, sticky="nsew")

        tools = tk.Frame(left.body, bg=T.PANEL)
        tools.pack(fill="x", pady=(0, 8))
        for label, cmd, color in (("全部", self._select_all, T.MUTED),
                                  ("清空", self._select_none, T.MUTED),
                                  ("最小套餐", lambda: self._apply_preset("minimal"), T.GREEN),
                                  ("电商套餐", lambda: self._apply_preset("ecommerce"), T.ACCENT)):
            PillButton(tools, label, cmd, fg=color, pad=(9, 3)).pack(side="left", padx=(0, 5))

        scroll = ScrollFrame(left.body, bg=T.PANEL)
        scroll.pack(fill="both", expand=True)
        self.list_inner = scroll.inner

        groups: dict[str, list[str]] = {}
        for key, tpl in self.templates.items():
            groups.setdefault(tpl.get("group") or "其他", []).append(key)
        order = ["入库", "存储", "拣选", "出库", "逆向", "支持"]

        for gname in sorted(groups, key=lambda g: (order.index(g) if g in order else 99, g)):
            gcolor = GROUP_COLORS.get(gname, FALLBACK_COLOR)
            bar = tk.Frame(self.list_inner, bg=T.PANEL)
            bar.pack(fill="x", pady=(8, 2))
            dot = tk.Canvas(bar, bg=T.PANEL, width=8, height=8, highlightthickness=0)
            dot.pack(side="left", padx=(2, 6))
            dot.create_oval(1, 1, 7, 7, fill=gcolor, outline="")
            tk.Label(bar, text=gname, bg=T.PANEL, fg=T.MUTED,
                     font=T.font(8, "bold")).pack(side="left")
            for key in sorted(groups[gname], key=lambda k: -self.templates[k]["priority"]):
                self._row(self.list_inner, key, gcolor)

        # ---------------- 右：统计 + 预览 + 动作 + 日志
        right = tk.Frame(body, bg=T.BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        stats = Card(right, "当前方案", "", accent=T.PURPLE)
        stats.grid(row=0, column=0, sticky="ew")
        line = tk.Frame(stats.body, bg=T.PANEL)
        line.pack(fill="x")
        for c in range(4):
            line.grid_columnconfigure(c, weight=1, uniform="s")

        def cell(key, note, col):
            box = tk.Frame(line, bg=T.PANEL_ALT)
            box.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
            tk.Label(box, text=key, bg=T.PANEL_ALT, fg=T.DIM,
                     font=T.font(8)).pack(anchor="w", padx=12, pady=(9, 0))
            val = tk.Label(box, text="—", bg=T.PANEL_ALT, fg=T.TEXT,
                           font=(T.FONT_FAMILY, 16, "bold"))
            val.pack(anchor="w", padx=12, pady=(1, 0))
            tk.Label(box, text=note, bg=T.PANEL_ALT, fg=T.DIM,
                     font=T.font(7)).pack(anchor="w", padx=12, pady=(1, 9))
            return val

        self.stat_sel = cell("已选", f"共 {len(self.templates)} 个", 0)
        self.stat_area = cell("占地", "作业带内", 1)
        self.stat_depth = cell("需要深度", f"上限 {self.depth_limit:.0f} m", 2)
        self.stat_thr = cell("理论产能", "合计 单/h", 3)

        self.verdict = tk.Label(stats.body, text="", bg=T.PANEL, fg=T.MUTED,
                                font=T.font(8), anchor="w", justify="left", wraplength=560)
        self.verdict.pack(fill="x", pady=(10, 0))

        preview = Card(right, "占位预览", "W 仓俯视：灰色是既有货架区，彩色是你要加的功能区",
                       accent=T.GREEN)
        preview.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.canvas = tk.Canvas(preview.body, bg=T.CANVAS_BG, highlightthickness=0, height=200)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._draw_preview())

        actions = tk.Frame(right, bg=T.BG)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        PillButton(actions, "生成并校验", self._build_variant, fg=T.ACCENT,
                   pad=(16, 7)).pack(side="left")
        PillButton(actions, "用变体跑一遍", self._run_variant, fg=T.MAGENTA,
                   pad=(16, 7)).pack(side="left", padx=8)
        PillButton(actions, "只保存勾选", self._save_selection, fg=T.MUTED,
                   pad=(14, 7)).pack(side="left")
        self.state = tk.Label(actions, text="空闲", bg=T.BG, fg=T.DIM, font=T.font(8))
        self.state.pack(side="right")

        log_card = Card(left_col, "输出", "生成 / 校验的实时 stdout", accent=T.AMBER)
        log_card.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.log = tk.Text(log_card.body, bg="#05090f", fg="#a9e6cf",
                           insertbackground="#a9e6cf", relief="flat", bd=0,
                           font=T.mono(8), wrap="word", padx=10, pady=8, height=3)
        vsb = ttk.Scrollbar(log_card.body, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=vsb.set, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._append("$ 勾选左侧功能区 → 点“生成并校验”→ 点“用变体跑一遍”。\n"
                     "$ 改动只写 config/layout_spec_areas.json，主链的 layout_spec.json 不动。\n")

    # ------------------------------------------------------------ rows
    def _row(self, parent, key: str, color: str):
        tpl = self.templates[key]
        row = tk.Frame(parent, bg=T.PANEL_ALT, highlightbackground=T.BORDER, highlightthickness=1)
        row.pack(fill="x", pady=2)

        box = _Check(row, key in self.enabled, lambda k=key: self._toggle(k), T.PANEL_ALT)
        box.pack(side="left", padx=(10, 2), pady=8)

        mid = tk.Frame(row, bg=T.PANEL_ALT)
        mid.pack(side="left", fill="x", expand=True, pady=5)
        name = tk.Label(mid, text=tpl["name"], bg=T.PANEL_ALT, fg=T.TEXT,
                        font=T.font(9, "bold"), anchor="w")
        name.pack(anchor="w")
        svc = (tpl.get("process") or {}).get("service_time_s") or {}
        slots = (tpl.get("process") or {}).get("parallel_slots")
        meta = "W-%s · %d×%d m · %s" % (
            key, tpl["size"][0], tpl["size"][1],
            ("%d 位 × %ss" % (slots, int(svc["mean"]))) if slots and svc.get("mean") else "无工时")
        tk.Label(mid, text=meta, bg=T.PANEL_ALT, fg=T.DIM, font=T.font(7)).pack(anchor="w")

        thr = self._throughput(tpl)
        tk.Label(row, text=("%.0f/h" % thr) if thr else "—", bg=T.PANEL_ALT,
                 fg=color if thr else T.DIM, font=T.mono(8)).pack(side="right", padx=(0, 10))

        self.rows[key] = {"frame": row, "box": box, "name": name, "mid": mid, "thr": thr}

        for widget in (row, box, mid, name):
            widget.bind("<Button-1>", lambda _e, k=key: self._toggle(k))
        row.bind("<Enter>", lambda _e, k=key: self._hover(k, True))
        row.bind("<Leave>", lambda _e, k=key: self._hover(k, False))

    def _hover(self, key: str, inside: bool):
        r = self.rows[key]
        if key in self.enabled:
            return
        bg = T.PANEL_HI if inside else T.PANEL_ALT
        for w in (r["frame"], r["mid"], r["name"]):
            w.configure(bg=bg)
        r["box"].set_bg(bg)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _throughput(tpl: dict) -> float:
        proc = tpl.get("process") or {}
        svc = proc.get("service_time_s") or {}
        slots, mean = proc.get("parallel_slots"), svc.get("mean")
        if not slots or not mean:
            return 0.0
        return slots * 3600.0 / float(mean)

    def _plan(self):
        """用引擎的 pack() 现算排布，返回 (placed, need_depth, target_depth)。"""
        items = sorted((self.templates[k] for k in self.enabled),
                       key=lambda t: -t["priority"])
        band_z0 = self.base_depth + self.offset
        placed, depth_used = self.engine.pack(
            items, (self.x_margin, band_z0, self.width - self.x_margin),
            self.x_margin, self.gap)
        need = self.base_depth + self.offset + depth_used + self.north_aisle
        target = max(need, self.base_depth + 18.0)
        return placed, need, target

    def _toggle(self, key: str):
        if key in self.enabled:
            self.enabled.discard(key)
        else:
            self.enabled.add(key)
        self._refresh()

    def _select_all(self):
        self.enabled = set(self.templates)
        self._refresh()

    def _select_none(self):
        self.enabled = set()
        self._refresh()

    def _apply_preset(self, name: str):
        ids = self.presets.get(name)
        self.enabled = set(ids) if ids else set(self.templates)
        self._refresh()

    # ------------------------------------------------------------ refresh
    def _refresh(self):
        for key, r in self.rows.items():
            on = key in self.enabled
            bg = T.PANEL_HI if on else T.PANEL_ALT
            for w in (r["frame"], r["mid"], r["name"]):
                w.configure(bg=bg)
            r["box"].set_bg(bg)
            r["box"].set_on(on)
            r["name"].configure(fg=T.TEXT if on else T.MUTED)

        placed, need, target = self._plan()
        area = sum(p["size"][0] * p["size"][1] for p in placed)
        thr = sum(self._throughput(self.templates[k]) for k in self.enabled)

        self.stat_sel.configure(text=str(len(self.enabled)))
        self.stat_area.configure(text="%.0f m²" % area)
        self.stat_depth.configure(text="%.1f m" % target,
                                  fg=T.RED if need > self.depth_limit + 1e-9 else T.TEXT)
        self.stat_thr.configure(text="%.0f" % thr)

        # 判据用 need（真实占位需求），不用 target（含"默认至少加深 18 m"的下限）。
        # 否则 88 m 恰好等于上限时，余量恒为 0，告警永远亮着，就失去了信号意义。
        if need > self.depth_limit + 1e-9:
            msg = ("✗ 放不下：需要 %.1f m，北侧上限 %.1f m（F1 南墙 − 2 m 净距）。"
                   "去掉一两个区，或先把前置仓整体北移。"
                   % (need, self.depth_limit))
            color = T.RED
        elif not self.enabled:
            msg = "· 一个也没选。至少选一个再生成，否则变体只有空作业带。"
            color = T.AMBER
        else:
            spare = self.depth_limit - need
            head = "%d 个区、%.0f m²、W 深度 %.1f m" % (len(placed), area, target)
            if spare >= 3.0:
                msg = "✓ 可生成：%s；北侧还剩 %.1f m（上限 %.0f m）。" % (head, spare, self.depth_limit)
                color = T.GREEN
            else:
                msg = ("△ 可生成但已用满：%s；北侧只剩 %.1f m，再加区就会超限（上限 %.0f m）。"
                       % (head, spare, self.depth_limit))
                color = T.AMBER
        self.verdict.configure(text=msg, fg=color)

        self._draw_preview()
        return placed

    def _draw_preview(self):
        """左：等比俯视图；右：图例 + 摘要。空白用来放信息，不是浪费。"""
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 90 or h < 90:
            return

        placed, need, target = self._plan()
        legend_w = 154                  # 图例固定占右侧，地图不可能压到它
        map_w = max(160, w - legend_w - 26)
        pad = 12
        ruler_h = 26                     # 给底部比例尺留位，否则会被画布裁掉
        span_z = max(target, self.base_depth + 1)
        s = min((map_w - 2 * pad) / self.width, (h - 2 * pad - ruler_h) / span_z)
        ox = pad + (map_w - 2 * pad - self.width * s) / 2
        oy = pad + (h - 2 * pad - ruler_h - span_z * s) / 2

        def X(x):
            return ox + x * s

        def Y(z):
            return oy + (span_z - z) * s

        # 10 m 网格（先画，后面被填充盖住的部分自然被遮）
        for gx in range(0, int(self.width) + 1, 10):
            c.create_line(X(gx), Y(0), X(gx), Y(span_z), fill=T.GRID)
        for gz in range(0, int(span_z) + 1, 10):
            c.create_line(X(0), Y(gz), X(self.width), Y(gz), fill=T.GRID)

        # 既有仓区：底色 + 货架排示意 + 月台带（不是精确几何，只表示"这里已经满了"）
        c.create_rectangle(X(0), Y(self.base_depth), X(self.width), Y(0),
                           fill=T.mix(T.CANVAS_BG, T.PANEL_ALT, 0.5), outline=T.BORDER_HI)
        rack_fill = T.mix(T.CANVAS_BG, T.PANEL_HI, 0.85)
        gx = 6.0
        while gx < self.width - 4:
            c.create_rectangle(X(gx), Y(58), X(gx + 3.2), Y(12),
                               fill=rack_fill, outline="")
            gx += 7.0
        c.create_rectangle(X(0), Y(9), X(self.width), Y(1),
                           fill=T.mix(T.CANVAS_BG, T.BLUE, 0.16), outline="")
        c.create_text(X(2), Y(5), anchor="w", text="月台", fill=T.MUTED, font=T.font(7))
        c.create_text(X(self.width / 2), Y(10.5), anchor="s",
                      text="既有货架 / 工位（%d × %d m，零改动）"
                           % (self.width, self.base_depth),
                      fill=T.MUTED, font=T.font(8))

        # 北扩作业带
        band_top = max(target, self.base_depth)
        c.create_rectangle(X(0), Y(band_top), X(self.width), Y(self.base_depth),
                           fill="", outline=T.BORDER_HI, dash=(3, 4))

        # 功能区
        lbl_font = tkfont.Font(font=T.mono(7))
        groups: dict[str, int] = {}
        for p in placed:
            x0, z0, x1, z1 = p["rect"]
            grp = p.get("group") or "其他"
            color = GROUP_COLORS.get(grp, FALLBACK_COLOR)
            groups[grp] = groups.get(grp, 0) + 1
            c.create_rectangle(X(x0), Y(z1), X(x1), Y(z0),
                               fill=T.mix(T.CANVAS_BG, color, 0.30),
                               outline=color, width=1)
            # 用实际字宽判断：宁可少写一行标签，也不让文字压出矩形边框
            label = p["template"].replace("-", "")
            if (lbl_font.measure(label) + 12 <= (x1 - x0) * s
                    and (z1 - z0) * s >= 16):
                c.create_text((X(x0) + X(x1)) / 2, (Y(z0) + Y(z1)) / 2,
                              text=label, fill=color, font=lbl_font)

        # 上限线
        if need > self.depth_limit + 1e-9:
            c.create_line(X(0), Y(self.depth_limit), X(self.width), Y(self.depth_limit),
                          fill=T.RED, dash=(5, 4))
            c.create_text(X(self.width) - 2, Y(self.depth_limit) - 8, anchor="se",
                          text="已越界", fill=T.RED, font=T.font(7, "bold"))

        # 右侧：图例 + 摘要（垂直空间有限，压成紧凑两段）
        # 底部比例尺：让"多深"这件事可量
        ruler_y = Y(0) + 14
        c.create_line(X(0), ruler_y, X(self.width), ruler_y, fill=T.BORDER)
        for gx in (0, 25, 50, 75, self.width):
            c.create_line(X(gx), ruler_y - 3, X(gx), ruler_y + 3, fill=T.BORDER_HI)
            c.create_text(X(gx), ruler_y + 11, text="%d" % gx,
                          fill=T.DIM, font=T.mono(7))
        c.create_text(X(0) - 6, ruler_y + 11, anchor="e", text="m",
                      fill=T.DIM, font=T.font(7))

        lx = w - legend_w + 8
        ly = pad
        c.create_text(lx, ly, anchor="nw", text="图例", fill=T.DIM, font=T.font(8, "bold"))
        ly += 17
        order = ["入库", "存储", "拣选", "出库", "逆向", "支持"]
        for grp in sorted(groups, key=lambda g: (order.index(g) if g in order else 99, g)):
            color = GROUP_COLORS.get(grp, FALLBACK_COLOR)
            c.create_oval(lx, ly + 4, lx + 8, ly + 12, fill=color, outline="")
            c.create_text(lx + 14, ly + 8, anchor="w", text="%s %d" % (grp, groups[grp]),
                          fill=T.MUTED, font=T.font(8))
            ly += 18

        area = sum(p["size"][0] * p["size"][1] for p in placed)
        thr = sum(self._throughput(self.templates[k]) for k in self.enabled)
        ly += 4
        c.create_line(lx, ly, lx + max(80, w - lx - 14), ly, fill=T.BORDER)
        ly += 12
        c.create_text(lx, ly, anchor="nw",
                      text="%d 个区 · %.0f m²" % (len(placed), area),
                      fill=T.TEXT, font=T.font(9, "bold"))
        ly += 19
        for text, color in (("深度 %.1f m" % target, T.MUTED),
                            ("%.0f 单/h" % thr, T.MUTED),
                            ("上限 %.0f m" % self.depth_limit,
                             T.RED if need > self.depth_limit + 1e-9 else T.DIM)):
            c.create_text(lx, ly, anchor="nw", text=text, fill=color, font=T.mono(8))
            ly += 16

    # ------------------------------------------------------------ actions
    def _save_selection(self) -> bool:
        items = sorted((self.templates[k] for k in self.enabled),
                       key=lambda t: -t["priority"])
        band_z0 = self.base_depth + self.offset
        placed, depth_used = self.engine.pack(
            items, (self.x_margin, band_z0, self.width - self.x_margin),
            self.x_margin, self.gap)
        target = max(self.base_depth + self.offset + depth_used + self.north_aisle,
                     self.base_depth + 18.0)
        if target > self.depth_limit + 1e-9:
            messagebox.showerror(
                "深度超限",
                "需要 %.1f m，北侧上限 %.1f m。\n请先去掉一两个功能区。"
                % (target, self.depth_limit))
            return False

        out = dict(self.sel_raw)
        out.update({
            "schema_version": "1.0",
            "warehouse": "W",
            "enabled": sorted(self.enabled, key=lambda k: -self.templates[k]["priority"]),
            "expand": {"mode": "north", "target_depth_m": round(target, 2),
                       "north_aisle_m": self.north_aisle},
        })
        self.selection_file.write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.sel_raw = out
        self._append("$ 已保存勾选：%d 个功能区 → config/areas_selection.json\n"
                     % len(self.enabled))
        return True

    def _build_variant(self):
        if not self.enabled:
            messagebox.showinfo("没有选择", "先勾至少一个功能区。")
            return
        if not self._save_selection():
            return
        self._run("生成变体", [sys.executable, "scripts/build_variant.py", "--build"])

    def _run_variant(self):
        if not self.variant_spec.exists():
            messagebox.showinfo("还没有变体", "先点“生成并校验”，再跑。")
            return
        self._run("变体跑一遍", [
            sys.executable, "scripts/run_bundle.py", "--scope", "scenarios",
            "--spec", "config/layout_spec_areas.json", "--label", "variant_from_studio"])

    # ------------------------------------------------------------ runner
    def _append(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _run(self, title: str, cmd: list[str]):
        if self.busy:
            self._append("$ 已有任务在运行，请稍候。\n")
            return
        self.busy = True
        self.state.configure(text="运行中：%s" % title, fg=T.AMBER)
        self._append("\n$ %s\n" % " ".join(cmd))
        threading.Thread(target=self._worker, args=(cmd,), daemon=True).start()

    def _worker(self, cmd: list[str]):
        t0 = time.time()
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(self.root), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=CREATE_NO_WINDOW)
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.after(0, self._append, line)
            code = self.proc.wait()
            self.after(0, self._append, "$ 退出码 %d · %.1fs\n" % (code, time.time() - t0))
            self.after(0, self._done, code == 0)
        except Exception as exc:  # pragma: no cover
            self.after(0, self._append, "$ 启动失败：%s\n" % exc)
            self.after(0, self._done, False)

    def _done(self, ok: bool):
        self.busy = False
        self.proc = None
        self.state.configure(text="空闲" if ok else "上一次任务失败",
                             fg=T.GREEN if ok else T.RED)
