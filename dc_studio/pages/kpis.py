# -*- coding: utf-8 -*-
"""Measured KPI catalogue with definitions, formulas and cross-scenario view."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import theme as T
from ..charts import bar_chart, short_name
from ..widgets import Card, Chip, PillButton, make_tree

LABELS = {
    "orders_generated": "生成订单",
    "orders_completed": "完成订单",
    "orders_lost": "丢失订单",
    "throughput_orders_per_h": "吞吐量",
    "on_time_rate": "准时率",
    "avg_tardiness_s": "平均延误",
    "max_tardiness_s": "最大延误",
    "distance_total_m": "总里程",
    "energy_kwh_total": "总能耗",
    "energy_per_order_kwh": "单均能耗",
    "charge_events": "充电次数",
    "fault_downtime_s": "故障停机",
    "recovery_s": "恢复窗口",
    "replans": "重规划次数",
    "wait_conflicts": "路权等待",
    "task_reassignments": "任务改派",
    "multi_order_robots": "多单机器人",
    "max_orders_per_robot": "单车最多单量",
    "availability": "可用率",
    "idle_rate": "空闲率",
    "max_queue_depth": "最大排队",
    "agents": "车队规模",
    "reservation_double_occupancy": "预约双占",
    "strict_collisions": "严格碰撞",
    "min_pair_distance_m": "最小车距",
    "sim_duration_s": "仿真时长",
    "wall_clock_s": "墙钟耗时",
}

GROUPS = {
    "效率": ["orders_generated", "orders_completed", "orders_lost", "throughput_orders_per_h",
             "on_time_rate", "avg_tardiness_s", "max_tardiness_s", "idle_rate", "max_queue_depth",
             "multi_order_robots", "max_orders_per_robot"],
    "能耗": ["distance_total_m", "energy_kwh_total", "energy_per_order_kwh", "charge_events"],
    "可靠": ["availability", "fault_downtime_s", "recovery_s", "replans", "task_reassignments",
             "wait_conflicts"],
    "安全": ["reservation_double_occupancy", "strict_collisions", "min_pair_distance_m"],
    "运行": ["agents", "sim_duration_s", "wall_clock_s"],
}


class KpiPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.store = app.store
        self.rows = self.store.kpi_rows()
        self.group = "全部"
        self.query = ""
        self.selected = None

        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", padx=18, pady=(12, 8))
        tk.Label(head, text=f"{len(self.rows)} 项实测指标 · 每项都带定义、公式与来源", bg=T.BG,
                 fg=T.MUTED, font=T.font(9)).pack(side="left")
        Chip(head, "measured", fg=T.GREEN).pack(side="left", padx=10)
        Chip(head, "baseline 运行", fg=T.ACCENT).pack(side="left")

        filters = tk.Frame(self, bg=T.BG)
        filters.pack(fill="x", padx=18, pady=(0, 8))
        self._group_btns = {}
        for name in ["全部"] + list(GROUPS):
            btn = PillButton(filters, name, lambda n=name: self.set_group(n), pad=(10, 4))
            btn.pack(side="left", padx=2)
            self._group_btns[name] = btn
        self._group_btns["全部"].set_active(True)

        self.search_var = tk.StringVar()
        entry = tk.Entry(filters, textvariable=self.search_var, bg=T.PANEL_ALT, fg=T.TEXT,
                         insertbackground=T.TEXT, relief="flat", font=T.font(9), width=26)
        entry.pack(side="right", ipady=5, padx=(8, 0))
        entry.insert(0, "")
        entry.bind("<KeyRelease>", self._on_search)
        tk.Label(filters, text="搜索", bg=T.BG, fg=T.DIM, font=T.font(8)).pack(side="right", padx=(10, 4))

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        body.grid_columnconfigure(0, weight=4)
        body.grid_columnconfigure(1, weight=6)
        body.grid_rowconfigure(0, weight=1)

        table_card = Card(body, "指标清单", accent=T.ACCENT)
        table_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.tree = make_tree(table_card.body, [("name", "指标", 140), ("value", "数值", 84),
                                                ("unit", "单位", 98)], height=16)
        vsb = ttk.Scrollbar(table_card.body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        detail = Card(body, "定义与跨场景对比", accent=T.GREEN)
        detail.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.d_title = tk.Label(detail.body, text="选择一个指标", bg=T.PANEL, fg=T.TEXT,
                                font=(T.FONT_FAMILY, 14, "bold"), anchor="w")
        self.d_title.pack(fill="x")
        self.d_meta = tk.Label(detail.body, text="", bg=T.PANEL, fg=T.DIM, font=T.font(8), anchor="w")
        self.d_meta.pack(fill="x", pady=(2, 8))

        self.chart = tk.Canvas(detail.body, bg=T.PANEL, highlightthickness=0, height=220)
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda _e: self._draw_chart())

        self.d_def = tk.Label(detail.body, text="", bg=T.PANEL, fg=T.MUTED, font=T.font(9),
                              anchor="w", justify="left", wraplength=520)
        self.d_def.pack(fill="x", pady=(8, 2))
        self.d_formula = tk.Label(detail.body, text="", bg=T.PANEL, fg=T.ACCENT, font=T.mono(8),
                                  anchor="w", justify="left", wraplength=520)
        self.d_formula.pack(fill="x", pady=2)
        self.d_source = tk.Label(detail.body, text="", bg=T.PANEL, fg=T.DIM, font=T.font(8), anchor="w")
        self.d_source.pack(fill="x", pady=(2, 0))

        self._fill()

    # ------------------------------------------------------------ filters
    def set_group(self, name: str):
        self.group = name
        for key, btn in self._group_btns.items():
            btn.set_active(key == name)
        self._fill()

    def _on_search(self, _event):
        self.query = self.search_var.get().strip().lower()
        self._fill()

    def _visible(self) -> list[dict]:
        rows = self.rows
        if self.group != "全部":
            allowed = set(GROUPS.get(self.group, []))
            rows = [r for r in rows if r["name"] in allowed]
        if self.query:
            rows = [r for r in rows if self.query in r["name"].lower()
                    or self.query in LABELS.get(r["name"], "").lower()
                    or self.query in str(r.get("definition", "")).lower()]
        return rows

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(self._visible()):
            value = row["value"]
            if isinstance(value, float):
                text = f"{value:,.4f}".rstrip("0").rstrip(".")
            else:
                text = str(value)
            self.tree.insert("", "end", iid=row["name"], values=(
                LABELS.get(row["name"], row["name"]), text, row["unit"]),
                tags=("even" if i % 2 else "odd",))
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])

    def _on_select(self, _event):
        sel = self.tree.selection()
        if sel:
            self.selected = sel[0]
            self._show(self.selected)

    def _show(self, name: str):
        row = next((r for r in self.rows if r["name"] == name), None)
        if not row:
            return
        value = row["value"]
        if isinstance(value, float):
            value = f"{value:,.4f}".rstrip("0").rstrip(".")
        self.d_title.configure(text=f"{LABELS.get(name, name)}  ·  {value} {row['unit']}")
        self.d_meta.configure(text=name)
        self.d_def.configure(text="定义：" + (row["definition"] or "—"))
        self.d_formula.configure(text="公式：" + (row["formula"] or "—"))
        self.d_source.configure(text="来源：" + (row["source"] or "—"))
        self._draw_chart()

    def _draw_chart(self):
        name = self.selected
        if not name:
            return
        rows = []
        for sc in self.store.scenarios:
            v = sc["metrics"].get(name)
            if isinstance(v, dict):
                v = v.get("value")
            rows.append({"scenario": sc["scenario"], "value": v or 0,
                         "label": short_name(sc["scenario"])})
        bar_chart(self.chart, rows, "value", "label", highlight="基线", color=T.GREEN,
                  unit=name)
