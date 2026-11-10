# -*- coding: utf-8 -*-
"""Overview page: scale, capability, health and a render gallery."""

from __future__ import annotations

import tkinter as tk

from .. import theme as T
from ..charts import bar_chart, donut, radar
from ..data import open_path
from ..widgets import Card, Chip, ScrollFrame, StatCard, hline

try:
    from PIL import Image, ImageOps, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False


class OverviewPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.store = app.store
        self._thumbs: list = []

        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        body.grid_columnconfigure(0, weight=1)

        self._hero(body)
        self._stats(body)
        self._capability_and_health(body)
        self._scenario_and_facts(body)
        self._gallery(body)

        tk.Frame(body, bg=T.BG, height=18).grid(row=9, column=0)

    # ------------------------------------------------------------ hero
    def _hero(self, parent):
        hero = Card(parent, fill=T.SIDEBAR, padding=24)
        hero.grid(row=0, column=0, sticky="ew", padx=28, pady=(20, 12))
        inner = tk.Frame(hero.body, bg=T.SIDEBAR)
        inner.pack(fill="x")

        left = tk.Frame(inner, bg=T.SIDEBAR)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text="配送中心数字孪生 · 项目驾驶舱", bg=T.SIDEBAR, fg=T.TEXT,
                 font=(T.FONT_FAMILY, 18, "bold")).pack(anchor="w")
        tk.Label(left, text="4 仓 + 21 台 AGV · tick 级离散事件仿真 · 事件溯源账本 · 40 项独立验收",
                 bg=T.SIDEBAR, fg=T.MUTED, font=T.font(10)).pack(anchor="w", pady=(4, 10))
        chips = tk.Frame(left, bg=T.SIDEBAR)
        chips.pack(anchor="w")
        for text, fg in (("L3.5 运输层孪生", T.ACCENT), ("MIT License", T.GREEN),
                         ("Blender 5.2", T.PURPLE), ("只读 · 不联网", T.MUTED)):
            Chip(chips, text, fg=fg, bg=T.PANEL_ALT).pack(side="left", padx=(0, 8))

        right = tk.Frame(inner, bg=T.SIDEBAR)
        right.pack(side="right", padx=(18, 0))
        tk.Label(right, text="产物更新时间", bg=T.SIDEBAR, fg=T.DIM, font=T.font(8)).pack(anchor="e")
        tk.Label(right, text=self.store.freshness(), bg=T.SIDEBAR, fg=T.TEXT,
                 font=(T.MONO_FAMILY, 11, "bold")).pack(anchor="e", pady=(2, 8))
        tk.Label(right, text="项目目录", bg=T.SIDEBAR, fg=T.DIM, font=T.font(8)).pack(anchor="e")
        tk.Label(right, text=str(self.store.root), bg=T.SIDEBAR, fg=T.MUTED,
                 font=T.font(8)).pack(anchor="e")

    # ------------------------------------------------------------ stats
    def _stats(self, parent):
        wrap = tk.Frame(parent, bg=T.BG)
        wrap.grid(row=1, column=0, sticky="ew", padx=28)
        for c in range(4):
            wrap.grid_columnconfigure(c, weight=1, uniform="stat")
        counts = self.store.counts
        health = self.store.health()
        tasks = self.store.task_summary()
        cards = [
            ("仓库", counts.get("warehouses", 4), "W + F1/F2/F3", T.BLUE, "座"),
            ("货架", counts.get("racks", 0), "双面 / 4 层", T.PURPLE, "组"),
            ("库位", counts.get("locations", 0), "含 392 个货架位", T.ACCENT, "个"),
            ("AGV", counts.get("robots", 0), "3 台/前置仓 + 12 台主仓", T.GREEN, "台"),
            ("月台门", 38, "10 个月台区", T.AMBER, "扇"),
            ("场景", len(self.store.scenarios), "8 个冻结场景", T.MAGENTA, "个"),
            ("验收", f"{health['passed']}/{health['total']}", "A1–A40 独立校验", T.GREEN, "项"),
            ("任务闭环", tasks.get("assigned", 0), "0 PENDING", T.ACCENT, "单"),
        ]
        for i, (label, value, note, accent, unit) in enumerate(cards):
            card = StatCard(wrap, label, value, note, accent, unit)
            card.grid(row=i // 4, column=i % 4, sticky="nsew", padx=6, pady=6)

    # ------------------------------------------------------------ capability
    def _capability_and_health(self, parent):
        row = tk.Frame(parent, bg=T.BG)
        row.grid(row=2, column=0, sticky="nsew", padx=28, pady=(8, 0))
        row.grid_columnconfigure(0, weight=5)
        row.grid_columnconfigure(1, weight=4)
        row.grid_rowconfigure(0, weight=1)

        cap = Card(row, "能力成熟度（自评）", "对照 DC 流程级孪生的六维自评", accent=T.ACCENT)
        cap.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        canvas = tk.Canvas(cap.body, bg=T.PANEL, highlightthickness=0, height=240)
        canvas.pack(fill="both", expand=True)
        axes = ["布局/几何", "调度/路径", "过程/产能", "库存/订单", "成本/账本", "可视化"]
        values = [92, 80, 26, 18, 72, 90]
        canvas.bind("<Configure>", lambda _e: radar(canvas, axes, values))
        tk.Label(cap.body, text="过程层与库存层是当前最大短板：区域只有落点，没有服务时间与容量。",
                 bg=T.PANEL, fg=T.DIM, font=T.font(8), wraplength=520, justify="left").pack(anchor="w", pady=(6, 0))

        health = self.store.health()
        total = health["total"] or 1
        ratio = health["passed"] / total
        hc = Card(row, "验收健康度", "out/validate_report.json", accent=T.GREEN)
        hc.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        top = tk.Frame(hc.body, bg=T.PANEL)
        top.pack(fill="x")
        donut_canvas = tk.Canvas(top, bg=T.PANEL, highlightthickness=0, width=150, height=150)
        donut_canvas.pack(side="left")
        donut_canvas.bind("<Configure>", lambda _e: donut(donut_canvas, ratio,
                                                          f"{ratio * 100:.0f}%", "PASS", T.GREEN))
        right = tk.Frame(top, bg=T.PANEL)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        for key, val, color in (("验收项", f"{health['passed']} / {health['total']}", T.TEXT),
                                ("失败项", str(len(health["failed"])), T.RED if health["failed"] else T.MUTED),
                                ("实测 KPI", f"{len(self.store.kpi_metrics)} 项", T.TEXT),
                                ("KPI 定义", "含公式 + 来源", T.MUTED)):
            line = tk.Frame(right, bg=T.PANEL)
            line.pack(fill="x", pady=3)
            tk.Label(line, text=key, bg=T.PANEL, fg=T.DIM, font=T.font(8), width=8,
                     anchor="w").pack(side="left")
            tk.Label(line, text=val, bg=T.PANEL, fg=color, font=T.font(9, "bold"),
                     anchor="w").pack(side="left")
        hline(hc.body, pad=(0, 4))
        tk.Label(hc.body, text="最近通过的检查", bg=T.PANEL, fg=T.DIM, font=T.font(8)).pack(anchor="w")
        for item in health["items"][-4:]:
            line = tk.Frame(hc.body, bg=T.PANEL)
            line.pack(fill="x", pady=1)
            tk.Label(line, text="✓", bg=T.PANEL, fg=T.GREEN, font=T.font(9, "bold")).pack(side="left")
            tk.Label(line, text=str(item.get("item", ""))[:34], bg=T.PANEL, fg=T.MUTED,
                     font=T.font(8), anchor="w").pack(side="left", padx=(6, 0))

    # ------------------------------------------------------------ scenarios
    def _scenario_and_facts(self, parent):
        row = tk.Frame(parent, bg=T.BG)
        row.grid(row=3, column=0, sticky="nsew", padx=28, pady=(12, 0))
        row.grid_columnconfigure(0, weight=5)
        row.grid_columnconfigure(1, weight=4)

        card = Card(row, "场景吞吐对比", "实测 orders/h · 8 个场景同种子", accent=T.MAGENTA)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        canvas = tk.Canvas(card.body, bg=T.PANEL, highlightthickness=0, height=190)
        canvas.pack(fill="both", expand=True)
        rows = self.store.scenarios
        canvas.bind("<Configure>", lambda _e: bar_chart(canvas, rows, "throughput", "scenario",
                                                        highlight=None, color=T.MAGENTA, unit="单/h"))
        tk.Label(card.body, text="峰值场景 58 单/h；夜班仅 1 单/h，用于验证低负荷能耗与待机成本。",
                 bg=T.PANEL, fg=T.DIM, font=T.font(8)).pack(anchor="w", pady=(4, 0))

        facts = Card(row, "关键事实", "全部可由产物文件复核", accent=T.BLUE)
        facts.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        traffic = self.store.traffic
        tasks = self.store.task_summary()
        build = self.store.build
        lines = [
            ("最小车距", f"{traffic.get('min_pair_distance_m', 0):.2f} m", "阈值 1.4592 m"),
            ("回放时长", f"{self.store.path_t_end:.1f} s", "21 台 AGV 闭环"),
            ("订单闭环", f"{tasks.get('closed_loop_orders', 0)} 单", "5 类业务动作"),
            ("库位占用", f"{build.get('slotting', {}).get('assigned', 0)} / 392", "ABC 分层 96/84/60"),
            ("Blender", build.get("blender", "—"), "程序化建模"),
            ("GLB", f"{self.store.artifact.get('glb', {}).get('bytes', 0) / 1024:.0f} KB", "882 节点"),
        ]
        for key, value, note in lines:
            line = tk.Frame(facts.body, bg=T.PANEL)
            line.pack(fill="x", pady=3)
            tk.Label(line, text=key, bg=T.PANEL, fg=T.DIM, font=T.font(9), width=8,
                     anchor="w").pack(side="left")
            tk.Label(line, text=value, bg=T.PANEL, fg=T.TEXT, font=T.font(9, "bold"),
                     anchor="w", width=14).pack(side="left")
            tk.Label(line, text=note, bg=T.PANEL, fg=T.DIM, font=T.font(8), anchor="w").pack(side="left")

    # ------------------------------------------------------------ gallery
    def _gallery(self, parent):
        card = Card(parent, "渲染图廊", "out/renders · 点击用系统看图器打开", accent=T.PURPLE)
        card.grid(row=4, column=0, sticky="ew", padx=28, pady=(12, 0))
        strip = tk.Frame(card.body, bg=T.PANEL)
        strip.pack(fill="x")
        items = self.store.gallery()[:4]
        if not items:
            tk.Label(strip, text="尚未找到渲染图", bg=T.PANEL, fg=T.DIM, font=T.font(9)).pack()
            return
        for i, (path, title) in enumerate(items):
            cell = tk.Frame(strip, bg=T.PANEL_ALT, cursor="hand2")
            cell.grid(row=0, column=i, padx=(0, 10), sticky="nsew")
            strip.grid_columnconfigure(i, weight=1, uniform="thumb")
            if HAS_PIL:
                try:
                    img = ImageOps.fit(Image.open(path).convert("RGB"), (260, 146), method=Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self._thumbs.append(photo)
                    lbl = tk.Label(cell, image=photo, bg=T.PANEL_ALT)
                except Exception:
                    lbl = tk.Label(cell, text="预览失败", bg=T.PANEL_ALT, fg=T.DIM)
            else:
                lbl = tk.Label(cell, text=path.name, bg=T.PANEL_ALT, fg=T.DIM)
            lbl.pack(padx=4, pady=(4, 0))
            cap = tk.Label(cell, text=title, bg=T.PANEL_ALT, fg=T.MUTED, font=T.font(8))
            cap.pack(pady=(2, 5))
            for widget in (cell, lbl, cap):
                widget.bind("<Button-1>", lambda _e, p=path: open_path(p))
