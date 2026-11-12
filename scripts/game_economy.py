#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/game_economy.py —— 实时经济层（ADR-GAME-04）。

**只做钱，不做仿真**：现金、科目、破产判定。需求的量在引擎里（`SET_PRICE` 改的是
`sim.per_hour`），这里的 `price_overrides` 只负责"按什么价收钱"。两边共用
`config/cost_spec.json`，金额公式与 `ledger.py` 逐条对齐，避免两套口径。

挂载方式：包住 `sim.log.emit`，每发一条事件就同步记一笔账。这样
  - 实时账本与事后 `ledger.py` 消费的是**同一份事件流**（单一事实来源）；
  - 引擎完全不知道经济层的存在（分层边界干净，批跑产物逐字节不变）。

固定成本按**仿真小时**计提（ADR-GAME-04 后果一）：`RUN_SETTLED` 那一笔**不重复记**，
否则同一段固定成本会被记两次——这正是 R3「双重计账」的典型形态。
"""
from __future__ import annotations

import json
from pathlib import Path

from dc_root import project_root  # noqa: E402
ROOT = project_root(__file__)
CONFIG = ROOT / "config"
DATA = ROOT / "data"


def _jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def fixed_costs(cost, hours, area, agents):
    """与 `ledger.fixed_costs` 同公式（这里复制是为了不把 scripts/ 变成包依赖，
    并有 `test_economy.py::test_fixed_cost_matches_ledger` 守着两边不许分叉）。"""
    f = cost["fixed_costs_per_day"]
    lab = f["labor"]
    dep = f["robot_depreciation"]
    capex_total = dep["capex_per_unit"] * agents
    return {
        "Labor": lab["operators"] * lab["hourly_rate"] * (1 + lab["supervision_ratio"]) * hours,
        "Depreciation": dep["capex_per_unit"] / dep["life_h"] * agents * hours,
        "Maintenance": capex_total * f["maintenance_pct_of_capex_per_year"] / (365 * 24) * hours,
        "Rent": f["space_rent_per_m2_month"] * area / (30 * 24) * hours,
        "IT": f["it_systems_per_day"] / 24 * hours,
    }


def site_area(layout):
    """与 `ledger.area_m2` 同口径：warehouses 是**列表**，面积按 width×depth 求和。"""
    ws = layout.get("warehouses", [])
    rows = ws.values() if isinstance(ws, dict) else ws
    return sum(float(w["size"]["width"]) * float(w["size"]["depth"]) for w in rows)


class Economy:
    """一局的现金账。所有方法都在会话锁内被调用（GameSession 保证）。"""

    def __init__(self, sim, cost, area, initial_capital, grace_s=300.0):
        self.sim = sim
        self.cost = cost
        self.area = area
        self.initial_capital = float(initial_capital)
        self.cash = float(initial_capital)
        self.grace_s = float(grace_s)
        # 分类累计（给 HUD 与守恒复算用；不是另一套账，而是同一批分录的汇总）
        self.revenue = 0.0
        self.variable = 0.0
        self.fixed = 0.0
        self.capex = 0.0
        self.salvage = 0.0
        self.postings = []                  # [(seq, account, amount, direction)]
        self._seen = set()                  # 幂等键 (seq, account)
        self._fixed_accrued_h = 0.0
        self.bankrupt_since = None
        self.bankrupt_grace_hit = False
        self.game_over = False
        self.over = {"fixed": {}, "counts": {}}
        self._in_hook = False

    # ---- 事件 → 分录（与 ledger.build_entries 一一对应）
    def on_event(self, e):
        if self._in_hook:                    # 账层自己发事件时不递归
            return
        self._in_hook = True
        try:
            self._accrue_fixed(float(e.get("t_s") or 0.0))
            typ = e.get("type")
            seq = e.get("seq")
            if typ == "TASK_COMPLETED":
                self._on_task_completed(e, seq)
            elif typ == "ORDER_LOST":
                self._post(seq, "Cost:LostOrder",
                           self.cost["revenue"]["lost_order_penalty"], "debit")
            elif typ == "ENERGY_TICK":
                kwh = float(e.get("motion_kwh") or 0.0) + float(e.get("idle_kwh") or 0.0)
                self._post(seq, "Cost:Variable:Energy",
                           kwh * self.cost["variable_costs"]["energy_tariff_per_kwh"], "debit")
            elif typ == "CMD_BUY_AGV":
                unit = self.cost["fixed_costs_per_day"]["robot_depreciation"]["capex_per_unit"]
                self._post(seq, "Capex:Robot", float(unit), "debit")
            elif typ == "CMD_SELL_AGV":
                unit = self.cost["fixed_costs_per_day"]["robot_depreciation"]["capex_per_unit"]
                refund = float(unit) * float(e.get("refund_pct") or 0.0)
                self._post(seq, "Capex:Robot:Salvage", refund, "credit")
            elif typ == "RUN_SETTLED":
                # 固定成本已按小时计提，这里刻意不记（否则双记，见模块 docstring）
                pass
            self._check_bankrupt(float(e.get("t_s") or 0.0))
        finally:
            self._in_hook = False

    def _on_task_completed(self, e, seq):
        rev = self.revenue_of(e)
        self._post(seq, "Revenue:" + str(e.get("order_type")), rev, "credit")
        self._post(seq, "Cost:Variable:Consumables",
                   self.cost["variable_costs"]["consumables_per_order"], "debit")
        tard = float(e.get("tardiness_s") or 0.0)
        if tard > 0:
            self._post(seq, "Cost:SLA_Penalty",
                       tard * self.cost["revenue"]["sla_penalty_per_tardy_s"], "debit")

    def revenue_of(self, e):
        """单笔收入 = 市场价（可被 SET_PRICE 覆盖）+ 行数附加费，均乘市场倍率。

        倍率只有一个来源（`economy_spec.tariff_multiplier`，引擎与经济层共读同一份），
        所以"游戏营收 == 演示台账营收 × 倍率"是可断言的关系，不是两套账。
        """
        typ = str(e.get("order_type"))
        m = float(getattr(self.sim, "tariff_multiplier", 1.0) or 1.0)
        base = self.cost["revenue"]["per_order"].get(typ, 0.0) * m
        price = float(getattr(self.sim, "price_overrides", {}).get(typ, base))
        return price + self.cost["revenue"]["per_line_extra"] * m

    def _accrue_fixed(self, t_s):
        hours_now = t_s / 3600.0
        delta = hours_now - self._fixed_accrued_h
        if delta <= 1e-12:
            return
        fc = fixed_costs(self.cost, delta, self.area, len(self.sim.robots))
        for name, amount in fc.items():
            if amount:
                self.over["fixed"][name] = self.over["fixed"].get(name, 0.0) + amount
                self.fixed += amount
                self.cash -= amount
        self._fixed_accrued_h = hours_now

    def _post(self, seq, account, amount, direction):
        key = (seq, account)
        if key in self._seen:                # 幂等：同一条源事件不许记两次
            return
        self._seen.add(key)
        amount = float(amount)
        if direction == "credit":
            self.cash += amount
            if account.startswith("Revenue:"):
                self.revenue += amount
            elif account.endswith(":Salvage"):
                self.salvage += amount
        else:
            self.cash -= amount
            if account == "Capex:Robot":
                self.capex += amount
            elif account.startswith("Cost:Variable:"):
                self.variable += amount
        self.postings.append((seq, account, amount, direction))

    def _check_bankrupt(self, t_s):
        if self.game_over:
            return
        if self.cash < 0:
            if self.bankrupt_since is None:
                self.bankrupt_since = t_s
            elif t_s - self.bankrupt_since > self.grace_s:
                self.game_over = True
                self.bankrupt_grace_hit = True
        else:
            self.bankrupt_since = None

    # ---- 查询
    def profit(self):
        return self.revenue + self.salvage - self.variable - self.fixed - self.capex

    def operating_profit(self):
        """经营利润：不含 capex（资本支出不进利润表，这是会计口径不是选择）。"""
        return self.revenue + self.salvage - self.variable - self.fixed

    def snapshot(self):
        return {
            "cash": round(self.cash, 2),
            "initial_capital": round(self.initial_capital, 2),
            "revenue": round(self.revenue, 2),
            "variable_cost": round(self.variable, 2),
            "fixed_cost": round(self.fixed, 2),
            "capex": round(self.capex, 2),
            "salvage": round(self.salvage, 2),
            "operating_profit": round(self.operating_profit(), 2),
            "profit": round(self.profit(), 2),
            "fixed_breakdown": {k: round(v, 2) for k, v in self.over["fixed"].items()},
            "bankrupt_warning": self.bankrupt_since is not None,
            "game_over": self.game_over,
            "postings": len(self.postings),
            "prices_source": ("game_balance" if float(getattr(self.sim, "tariff_multiplier", 1.0)) != 1.0
                              else self.cost.get("prices_source", "demo")),
            "tariff_multiplier": float(getattr(self.sim, "tariff_multiplier", 1.0)),
            "price_overrides": dict(getattr(self.sim, "price_overrides", {})),
        }

    # ---- 快照（存档要连钱一起存，否则续玩等于换了个人管账）----
    def snapshot_full(self):
        return {
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "grace_s": self.grace_s,
            "revenue": self.revenue,
            "variable": self.variable,
            "fixed": self.fixed,
            "capex": self.capex,
            "salvage": self.salvage,
            "fixed_accrued_h": self._fixed_accrued_h,
            "bankrupt_since": self.bankrupt_since,
            "game_over": self.game_over,
            "bankrupt_grace_hit": self.bankrupt_grace_hit,
            "postings": [list(x) for x in self.postings],
            "seen": [list(x) for x in sorted(self._seen)],
            "cost": self.cost,
            "area": self.area,
        }

    # ---- 守恒自检（ECON_OK 的"独立复算"由测试脚本做，这里给一份内建对账）
    def conservation(self):
        """Δcash 必须等于 Σ贷 − Σ借 − 固定成本计提。返回 (ok, 残差, 明细)。

        固定成本刻意不在 `postings` 里：它是按时间计提的，没有源事件 seq，
        无法套用 (seq, account) 幂等键。所以守恒式里单列一项，而不是硬塞一个假 seq。
        """
        credits = sum(a for _, acc, a, d in self.postings if d == "credit")
        debits = sum(a for _, acc, a, d in self.postings if d == "debit")
        delta = self.cash - self.initial_capital
        residual = delta - (credits - debits - self.fixed)
        return abs(residual) < 1e-6, residual, {"credits": credits, "debits": debits,
                                                "fixed_accrued": self.fixed, "delta": delta}


def attach(sim, level=None, initial_capital=None):
    """把经济层挂到一局上。level 可为 None（自由模式）。"""
    cost = sim.cost_spec or _jload(CONFIG / "cost_spec.json")
    es = getattr(sim, "econ_spec", None) or (_jload(CONFIG / "economy_spec.json")
                                             if (CONFIG / "economy_spec.json").exists() else {})
    cap = initial_capital
    if cap is None:
        cap = (level or {}).get("initial_capital")
    if cap is None:
        cap = es.get("initial_capital", 500000)
    grace = (level or {}).get("bankrupt_grace_s", es.get("bankrupt_grace_s", 300))
    import sim_engine as se
    area = site_area(se.jload(DATA / "layout.json"))
    econ = Economy(sim, cost, area, cap, grace)
    sim.economy = econ

    original = sim.log.emit

    def emit(t_s, typ, **kw):
        e = original(t_s, typ, **kw)
        econ.on_event(e)
        return e

    sim.log.emit = emit
    return econ


def restore_onto(sim, snap):
    """把快照里的账恢复到一个刚建好的 sim 上（与 attach 一样要重挂 emit 钩子）。"""
    econ = Economy(sim, snap["cost"], float(snap["area"]),
                   float(snap["initial_capital"]), float(snap["grace_s"]))
    econ.cash = float(snap["cash"])
    econ.revenue = float(snap["revenue"])
    econ.variable = float(snap["variable"])
    econ.fixed = float(snap["fixed"])
    econ.capex = float(snap["capex"])
    econ.salvage = float(snap["salvage"])
    econ._fixed_accrued_h = float(snap["fixed_accrued_h"])
    econ.bankrupt_since = snap.get("bankrupt_since")
    econ.game_over = bool(snap.get("game_over", False))
    econ.bankrupt_grace_hit = bool(snap.get("bankrupt_grace_hit", False))
    econ.postings = [tuple(x) for x in snap.get("postings") or []]
    econ._seen = {tuple(x) for x in snap.get("seen") or []}
    sim.economy = econ

    original = sim.log.emit

    def emit(t_s, typ, **kw):
        e = original(t_s, typ, **kw)
        econ.on_event(e)
        return e

    sim.log.emit = emit
    return econ
