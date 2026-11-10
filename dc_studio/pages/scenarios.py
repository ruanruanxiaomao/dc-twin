# -*- coding: utf-8 -*-
"""Scenario lab: 8 frozen scenarios, measured KPIs and P&L."""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk

from .. import theme as T
from ..charts import bar_chart, short_name
from ..data import open_path
from ..widgets import Card, Chip, PillButton, make_tree


class ScenariosPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.store = app.store
        self.rows = self.store.scenarios
        self.selected = self.rows[0]["scenario"] if self.rows else None
        self.compare = "baseline" if self.rows else None
        self._syncing = False

        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", padx=18, pady=(12, 8))
        tk.Label(head, text="8 个冻结场景，全部由 tick 级 DES 实际执行（非估算）", bg=T.BG,
                 fg=T.MUTED, font=T.font(9)).pack(side="left")
        Chip(head, "同种子 20261106", fg=T.ACCENT).pack(side="left", padx=10)
        Chip(head, "实测 KPI", fg=T.GREEN).pack(side="left")

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        body.grid_columnconfigure(0, weight=5)
        body.grid_columnconfigure(1, weight=6)
        body.grid_rowconfigure(0, weight=1)

        self._table(body)
        self._detail(body)
        if self.rows:
            self._select(self.rows[0]["scenario"])

    # ------------------------------------------------------------ table
    def _table(self, parent):
        card = Card(parent, "场景总表", "点击一行查看详情", accent=T.MAGENTA)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        tree_wrap = tk.Frame(card.body, bg=T.PANEL)
        tree_wrap.pack(fill="x")
        cols = [("scenario", "场景", 110), ("orders", "完成单", 62), ("throughput", "吞吐 单/h", 94),
                ("on_time", "准时率", 64), ("idle", "空闲率", 64), ("profit", "营业利润", 96)]
        self.tree = make_tree(tree_wrap, cols, height=10)
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for i, row in enumerate(self.rows):
            self.tree.insert("", "end", iid=row["scenario"], values=(
                short_name(row["scenario"]),
                row["orders"],
                f"{row['throughput']:.0f}",
                f"{row['on_time'] * 100:.0f}%",
                f"{row['idle'] * 100:.0f}%",
                f"{row['profit']:,.1f}",
            ), tags=("even" if i % 2 else "odd",))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        insight = tk.Frame(card.body, bg=T.PANEL)
        insight.pack(fill="both", expand=True, pady=(12, 0))
        tk.Label(insight, text="读表提示", bg=T.PANEL, fg=T.DIM,
                 font=T.font(8, "bold")).pack(anchor="w", pady=(0, 6))
        for color, text in (
            (T.ACCENT, "峰值 58 单/h，是基线的 2.3 倍；\n但营业利润仍为负，“忙”不等于“赚钱”。"),
            (T.PURPLE, "夜班只有 1 单/h、空闲率 83%，\n用来验证低负荷能耗与待机成本。"),
            (T.AMBER, "50 车吞吐 54，低于峰值 58：\n继续加车会被工位/月台卡住，瓶颈不在车。"),
        ):
            line = tk.Frame(insight, bg=T.PANEL)
            line.pack(fill="x", pady=2)
            dot = tk.Canvas(line, bg=T.PANEL, width=8, height=8, highlightthickness=0)
            dot.pack(side="left", pady=4)
            dot.create_oval(1, 1, 7, 7, fill=color, outline="")
            tk.Label(line, text=text, bg=T.PANEL, fg=T.MUTED, font=T.font(8),
                     justify="left", anchor="w").pack(side="left", padx=(6, 0), fill="x", expand=True)

        foot = tk.Frame(card.body, bg=T.PANEL)
        foot.pack(fill="x", pady=(10, 0))
        tk.Label(foot, text="对比基线", bg=T.PANEL, fg=T.DIM, font=T.font(8)).pack(side="left")
        self.compare_var = tk.StringVar(value=short_name(self.compare) if self.compare else "")
        combo = ttk.Combobox(foot, textvariable=self.compare_var, state="readonly", width=14,
                             values=[short_name(r["scenario"]) for r in self.rows])
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", self._on_compare)
        PillButton(foot, "打开 runs 目录", lambda: open_path(self.store.runs_dir),
                   fg=T.MUTED, pad=(8, 4)).pack(side="right")

    def _on_select(self, _event):
        sel = self.tree.selection()
        if sel and sel[0] != self.selected:
            self._select(sel[0])

    def _on_compare(self, _event):
        label = self.compare_var.get()
        for row in self.rows:
            if short_name(row["scenario"]) == label:
                self.compare = row["scenario"]
                break
        self._select(self.selected)

    # ------------------------------------------------------------ detail
    def _detail(self, parent):
        self.card = Card(parent, "场景详情", accent=T.ACCENT)
        self.card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.card.body.grid_columnconfigure(0, weight=1)
        self.card.body.grid_rowconfigure(2, weight=1)

        self.title = tk.Label(self.card.body, text="", bg=T.PANEL, fg=T.TEXT,
                              font=(T.FONT_FAMILY, 15, "bold"), anchor="w")
        self.title.grid(row=0, column=0, sticky="ew")
        self.subtitle = tk.Label(self.card.body, text="", bg=T.PANEL, fg=T.DIM,
                                 font=T.font(8), anchor="w")
        self.subtitle.grid(row=1, column=0, sticky="ew", pady=(2, 10))

        chart_holder = tk.Frame(self.card.body, bg=T.PANEL)
        chart_holder.grid(row=2, column=0, sticky="nsew")
        self.chart = tk.Canvas(chart_holder, bg=T.PANEL, highlightthickness=0, height=190)
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda _e: self._draw_chart())

        self.delta = tk.Label(self.card.body, text="", bg=T.PANEL, fg=T.MUTED,
                              font=T.font(8), anchor="w")
        self.delta.grid(row=3, column=0, sticky="ew", pady=(4, 8))

        stats = tk.Frame(self.card.body, bg=T.PANEL)
        stats.grid(row=4, column=0, sticky="ew")
        for c in range(4):
            stats.grid_columnconfigure(c, weight=1, uniform="s")
        self._stat_cells = []
        labels = ["完成单量", "平均延误", "总里程", "总能耗"]
        for i, name in enumerate(labels):
            cell = tk.Frame(stats, bg=T.PANEL_ALT, highlightbackground=T.BORDER, highlightthickness=1)
            cell.grid(row=0, column=i, sticky="nsew", padx=3)
            tk.Label(cell, text=name, bg=T.PANEL_ALT, fg=T.DIM, font=T.font(8)).pack(anchor="w", padx=8, pady=(6, 0))
            val = tk.Label(cell, text="—", bg=T.PANEL_ALT, fg=T.TEXT, font=T.font(11, "bold"))
            val.pack(anchor="w", padx=8, pady=(0, 6))
            self._stat_cells.append(val)

        econ = tk.Frame(self.card.body, bg=T.PANEL)
        econ.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self._econ_labels = {}
        for key, label in (("revenue", "收入"), ("profit", "营业利润"),
                           ("breakeven", "盈亏平衡单量"), ("per_order", "单均收入")):
            line = tk.Frame(econ, bg=T.PANEL)
            line.pack(fill="x", pady=2)
            tk.Label(line, text=label, bg=T.PANEL, fg=T.DIM, font=T.font(8), width=12,
                     anchor="w").pack(side="left")
            value = tk.Label(line, text="—", bg=T.PANEL, fg=T.TEXT, font=T.font(9, "bold"), anchor="w")
            value.pack(side="left")
            self._econ_labels[key] = value

        bar = tk.Frame(self.card.body, bg=T.PANEL)
        bar.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        PillButton(bar, "打开运行目录", self._open_run, fg=T.ACCENT, pad=(10, 5)).pack(side="left")
        PillButton(bar, "打开 sim_kpi.json", self._open_kpi, fg=T.MUTED, pad=(10, 5)).pack(side="left", padx=6)
        PillButton(bar, "打开损益表", self._open_pnl, fg=T.MUTED, pad=(10, 5)).pack(side="left")

    def _select(self, name: str):
        row = self.store.scenario(name)
        if not row:
            return
        self.selected = name
        if self.tree.exists(name) and self.tree.selection() != (name,):
            self.tree.selection_set(name)
        self.title.configure(text=short_name(name))
        self.subtitle.configure(text=f"run_id {row['run_id']} · seed {row['seed']}")
        m = row["metrics"]
        values = [f"{row['orders']}", f"{row['tardiness']:.1f} s",
                  f"{row['distance']:.0f} m", f"{row['energy']:.2f} kWh"]
        for lbl, val in zip(self._stat_cells, values):
            lbl.configure(text=val)
        total = row["revenue"] or 0
        per_order = total / row["orders"] if row["orders"] else 0
        self._econ_labels["revenue"].configure(text=f"¥ {row['revenue']:,.1f}")
        profit = self._econ_labels["profit"]
        profit.configure(text=f"¥ {row['profit']:,.1f}",
                         fg=T.RED if row["profit"] < 0 else T.GREEN)
        self._econ_labels["breakeven"].configure(text=f"{row['breakeven']} 单")
        self._econ_labels["per_order"].configure(text=f"¥ {per_order:.2f}")

        base = self.store.scenario(self.compare) if self.compare else None
        if base and base["scenario"] != name:
            dt = row["throughput"] - base["throughput"]
            dp = row["profit"] - base["profit"]
            do = row["orders"] - base["orders"]
            self.delta.configure(
                text=(f"对比 {short_name(base['scenario'])}：吞吐 {dt:+.0f} 单/h · "
                      f"完成 {do:+d} 单 · 营业利润 {dp:+,.1f} 元"))
        else:
            self.delta.configure(text="（选择上方“对比基线”查看差值）")
        self._draw_chart()

    def _draw_chart(self):
        rows = self.rows
        if not rows:
            return
        bar_chart(self.chart, rows, "throughput", "scenario", highlight=self.selected,
                  color=T.ACCENT, unit="实测吞吐 单/h")

    # ------------------------------------------------------------ actions
    def _open_run(self):
        row = self.store.scenario(self.selected)
        if row and row.get("run_dir"):
            open_path(row["run_dir"])

    def _open_kpi(self):
        row = self.store.scenario(self.selected)
        if row and row.get("run_dir"):
            open_path(str(row["run_dir"]) + r"\sim_kpi.json")

    def _open_pnl(self):
        row = self.store.scenario(self.selected)
        if row and row.get("run_dir"):
            open_path(str(row["run_dir"]) + r"\pnl.json")
