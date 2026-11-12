"""实时经济层（ADR-GAME-04 / 方案书 §6.1）。

ECON_OK 的判据是"独立复算"：不看经济层自己报的利润，而是拿事件流重算一遍现金变化。
这里就是那个复算器。四件事逐个钉：

  1. 现金守恒：Δcash == Σ贷 − Σ借（1e-6）；
  2. 金额与事后账本同源：同一事件流的收入总额与 `ledger.build_entries` 一致；
  3. 破产真能触发（而不是永远不触发）；
  4. 双重计账被幂等键拦下。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
for p in (ROOT / "scripts",):
    sys.path.insert(0, str(p))

import game_economy as ge  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import sim_engine as se  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def build(tmp_path, initial_capital=500000, scenario="baseline"):
    sim = se.Simulation(
        ROOT / "config" / "scenarios" / ("%s.json" % scenario),
        jload(ROOT / "config" / "sim_spec.json"),
        jload(ROOT / "config" / "fleet_spec.json"),
        jload(ROOT / "config" / "demand_spec.json"),
        jload(ROOT / "data" / "layout.json"),
        jload(ROOT / "data" / "nav_grid.json"),
        jload(ROOT / "data" / "config.json"),
        out_dir=tmp_path / "run",
    )
    econ = ge.attach(sim, None, initial_capital=initial_capital)
    return sim, econ


def events(sim):
    return [json.loads(x) for x in (sim.out / "sim_events.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip()]


# ------------------------------------------------------------------ 守恒
def test_cash_conservation(tmp_path):
    sim, econ = build(tmp_path)
    sim.run()
    ok, residual, detail = econ.conservation()
    assert ok, "现金不守恒，残差 %r（%r）" % (residual, detail)


def test_independent_recompute_from_event_stream(tmp_path):
    """不看 cash，只看事件流重算：这就是"独立复算"，不能用账层自报的数字。"""
    sim, econ = build(tmp_path)
    sim.run()
    cost = jload(ROOT / "config" / "cost_spec.json")
    m = float(getattr(sim, "tariff_multiplier", 1.0))
    evs = events(sim)
    revenue = 0.0
    consumables = sla = lost = energy = 0.0
    for e in evs:
        if e["type"] == "TASK_COMPLETED":
            revenue += ((cost["revenue"]["per_order"][e["order_type"]]
                         + cost["revenue"]["per_line_extra"]) * m)
            consumables += cost["variable_costs"]["consumables_per_order"]
            if e.get("tardiness_s", 0) > 0:
                sla += e["tardiness_s"] * cost["revenue"]["sla_penalty_per_tardy_s"]
        elif e["type"] == "ORDER_LOST":
            lost += cost["revenue"]["lost_order_penalty"]
        elif e["type"] == "ENERGY_TICK":
            energy += ((e["motion_kwh"] + e["idle_kwh"])
                       * cost["variable_costs"]["energy_tariff_per_kwh"])

    assert econ.revenue == pytest.approx(revenue, abs=1e-6)
    assert econ.variable == pytest.approx(consumables + sla + lost + energy, abs=1e-6)
    expected_cash = econ.initial_capital + revenue - (consumables + sla + lost + energy) - econ.fixed
    assert econ.cash == pytest.approx(expected_cash, abs=1e-6)


def test_revenue_matches_post_hoc_ledger(tmp_path):
    """游戏营收必须等于"事后台账营收 × 市场价倍率"——这是可断言的关系，
    不是两套账。若有人悄悄在游戏侧加了一套价格表，这条会立刻红。"""
    sim, econ = build(tmp_path)
    sim.run()
    cost = jload(ROOT / "config" / "cost_spec.json")
    entries = ledger_mod.build_entries(events(sim), cost, "x", sim.horizon / 3600.0,
                                      ge.site_area(jload(ROOT / "data" / "layout.json")),
                                      len(sim.robots))
    post_rev = sum(e["amount"] for e in entries if e["account"].startswith("Revenue:"))
    m = float(getattr(sim, "tariff_multiplier", 1.0))
    assert econ.revenue == pytest.approx(post_rev * m, rel=1e-9)


def test_fixed_cost_formula_matches_ledger():
    """固定成本两份实现不许分叉（一份在 ledger，一份在 game_economy）。"""
    cost = jload(ROOT / "config" / "cost_spec.json")
    a = ge.fixed_costs(cost, 3.5, 4000.0, 21)
    b = ledger_mod.fixed_costs(cost, 3.5, 4000.0, 21)
    # ledger 每科目 round(…, 6)，这里不 round（逐小时计提时 round 会累积漂移）
    assert a == pytest.approx(b, rel=1e-6)


# ------------------------------------------------------------------ 破产
def test_bankruptcy_really_triggers(tmp_path):
    """现金耗尽必须在宽限期后判负，而不是"永远差一点"。

    故意用"过度投资"这条路：10 万现金买一台 12 万的车 → 立刻资不抵债。
    这也正是玩法张力的来源（买车扩产 vs 现金流断裂）。
    """
    sim, econ = build(tmp_path, initial_capital=100000)
    econ.grace_s = 5.0
    sim.enqueue_command({"type": "BUY_AGV", "payload": {"wid": "W"}})
    sim.run()
    assert econ.game_over, "买车把钱花成负数，超过宽限期却没有判负"
    assert econ.cash < 0
    assert econ.bankrupt_grace_hit


def test_no_false_bankruptcy_with_enough_capital(tmp_path):
    sim, econ = build(tmp_path, initial_capital=5_000_000)
    sim.run()
    assert not econ.game_over
    assert econ.cash > 0


# ------------------------------------------------------- 价格弹性与幂等
def test_price_increase_reduces_orders(tmp_path):
    """改价必须真的改单量，否则"定价"这个动作是装饰。

    价格是**游戏口径**：OUTBOUND 基准 = 6.0 × 倍率 5 = 30。这里翻到 60。
    """
    m = float(ge._jload(ROOT / "config" / "economy_spec.json").get("tariff_multiplier", 1.0))
    base_price = 6.0 * m

    plain, _ = build(tmp_path / "plain")
    plain.run()
    base_orders = plain.metrics["metrics"]["orders_generated"]["value"]

    dear, _ = build(tmp_path / "dear")
    dear.enqueue_command({"type": "SET_PRICE",
                          "payload": {"order_type": "OUTBOUND", "price": base_price * 2}})
    dear.run()
    dear_orders = dear.metrics["metrics"]["orders_generated"]["value"]

    assert dear_orders < base_orders, (
        "OUTBOUND 单价从 %.1f 涨到 %.1f，单量却没降（%d → %d）"
        % (base_price, base_price * 2, base_orders, dear_orders))


def test_price_cut_increases_orders(tmp_path):
    m = float(ge._jload(ROOT / "config" / "economy_spec.json").get("tariff_multiplier", 1.0))
    plain, _ = build(tmp_path / "plain")
    plain.run()
    base = plain.metrics["metrics"]["orders_generated"]["value"]

    cheap, _ = build(tmp_path / "cheap")
    cheap.enqueue_command({"type": "SET_PRICE",
                           "payload": {"order_type": "OUTBOUND", "price": 6.0 * m * 0.5}})
    cheap.run()
    assert cheap.metrics["metrics"]["orders_generated"]["value"] > base


def test_double_posting_is_blocked(tmp_path):
    """同一 (seq, account) 记两次必须被拦下（R3 的探针）。"""
    sim, econ = build(tmp_path)
    # 直接构造一条合法事件：幂等键是 (seq, account)，不需要真跑出一单才能测
    e = {"seq": 90001, "t_s": 100.0, "type": "TASK_COMPLETED", "order_type": "OUTBOUND",
         "order_id": "ORD-TEST", "warehouse": "W", "tardiness_s": 0.0}
    before = len(econ.postings)
    rev_before, var_before = econ.revenue, econ.variable
    econ.on_event(e)
    after_first = len(econ.postings)
    econ.on_event(e)
    econ.on_event(e)
    assert after_first > before, "首次记账没写任何分录"
    assert len(econ.postings) == after_first, "重复记账没有被幂等键拦下"
    consumables = jload(ROOT / "config" / "cost_spec.json")["variable_costs"]["consumables_per_order"]
    assert econ.revenue == pytest.approx(rev_before + econ.revenue_of(e), abs=1e-9)
    assert econ.variable == pytest.approx(var_before + consumables, abs=1e-9)
    assert econ.revenue_of(e) == pytest.approx(
        (jload(ROOT / "config" / "cost_spec.json")["revenue"]["per_order"]["OUTBOUND"]
         + jload(ROOT / "config" / "cost_spec.json")["revenue"]["per_line_extra"])
        * float(getattr(sim, "tariff_multiplier", 1.0)))


def test_capex_is_charged_on_buy(tmp_path):
    sim, econ = build(tmp_path)
    sim.enqueue_command({"type": "BUY_AGV", "payload": {"wid": "W"}})
    sim.step()
    unit = jload(ROOT / "config" / "cost_spec.json")["fixed_costs_per_day"]["robot_depreciation"]["capex_per_unit"]
    assert econ.capex == pytest.approx(unit)
    assert econ.snapshot()["cash"] < econ.initial_capital


def test_sell_agv_requires_idle_robot(tmp_path):
    sim, _ = build(tmp_path)
    rid = next(iter(sim.robots))
    for _ in range(5):
        if sim.robots[rid].order is not None:
            break
        sim.step()
    sim.enqueue_command({"type": "SELL_AGV", "payload": {"agent": rid}})
    if sim.robots[rid].order is None:
        sim.step()
        assert rid not in sim.robots
    else:
        with pytest.raises(ValueError, match="正在执行任务"):
            sim.step()
