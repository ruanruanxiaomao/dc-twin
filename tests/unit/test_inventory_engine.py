"""P6a-1：库存层接进引擎（扣减 / 缺货 / 补货 / 快照 / 确定性与可证伪）。

与 `test_inventory.py`（P6a-0 的数据与配置）分开，理由：
  - P6a-0 只保证"数据铺好且确定"，不碰引擎；
  - P6a-1 一变，这里就该有红——把两类放在同一份文件里，失败原因会被混淆。

覆盖的方案书门：3（STOCKOUT_NEG）、7（SAVE_OK+）、8（DETERMINISM+）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import game_economy as ge  # noqa: E402
import sim_engine as se  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def specs():
    return (jload(ROOT / "config" / "sim_spec.json"), jload(ROOT / "config" / "fleet_spec.json"),
            jload(ROOT / "config" / "demand_spec.json"), jload(ROOT / "data" / "layout.json"),
            jload(ROOT / "data" / "nav_grid.json"), jload(ROOT / "data" / "config.json"))


def make_sim(tmp_path, scenario="baseline", scale=1.0, demand_mult=1.0):
    """构造一局；scale 缩放期初库存、demand_mult 加压需求（缺货实验用）。"""
    sim = se.Simulation(ROOT / "config" / "scenarios" / ("%s.json" % scenario),
                        *specs(), out_dir=tmp_path / "run")
    if scale != 1.0:
        for sku, rows in sim.inv.items():
            for loc in rows:
                sim.inv[sku][loc] = float(rows[loc]) * scale
    if demand_mult != 1.0:
        sim.per_hour = sim.per_hour * demand_mult
        sim.lam = sim.per_hour / 3600.0
    return sim


def events_of(sim):
    """读事件流前必须 flush：文件对象有缓冲，跑到一半直接读会读到空/半截。"""
    try:
        sim.log.f.flush()
    except Exception:          # noqa: BLE001
        pass
    return [json.loads(x) for x in (sim.out / "sim_events.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip()]


# ------------------------------------------------------------------ 基本接线
def test_inventory_enabled_and_events_emitted(tmp_path):
    sim = make_sim(tmp_path)
    sim.run()
    types = {e["type"] for e in events_of(sim)}
    assert sim.inv_enabled
    assert "STOCK_DECREMENT" in types, "完成出库单却没有扣减事件"
    assert "HOLDING_ACCRUED" in types, "持有成本没有计提事件"
    assert sim.inv_holding > 0


def test_baseline_outcomes_unchanged_by_inventory(tmp_path):
    """库存充足时不该改变调度结果：baseline 仍是 38 单 / 0 丢单。

    这条是"最小侵入"的证据：事件流变了、但业务结果没变。
    """
    sim = make_sim(tmp_path)
    sim.run()
    m = sim.metrics["metrics"]
    assert m["orders_completed"]["value"] == 38
    assert m["orders_lost"]["value"] == 0
    assert m["stockout_count"]["value"] == 0
    assert m["fill_rate"]["value"] == pytest.approx(1.0)


# ------------------------------------------------------------------ 门 3 缺货
def test_stockout_blocks_completion(tmp_path):
    sim = make_sim(tmp_path)
    sku = sorted(sim.inv)[0]
    loc = sorted(sim.inv[sku])[0]
    sim.inv[sku][loc] = 0.0
    order = {"order_id": "T-STOCKOUT", "type": "OUTBOUND",
             "warehouse": sim.inv_loc[loc]["warehouse"], "pick_loc": loc,
             "drop_loc": "W-OU-01", "qty": 1.0}
    delivered, ok = sim._inv_settle(order)
    assert (delivered, ok) == (0.0, False)
    evs = events_of(sim)
    assert any(e["type"] == "STOCKOUT" and e.get("sku") == sku for e in evs)
    assert not any(e["type"] == "TASK_COMPLETED" for e in evs), \
        "缺货却仍然发了 TASK_COMPLETED —— 这就是被静默吞掉的缺货"


def test_partial_fill_degrades_delivery(tmp_path):
    sim = make_sim(tmp_path)
    sku = sorted(sim.inv)[0]
    loc = sorted(sim.inv[sku])[0]
    sim.inv[sku][loc] = 0.4
    order = {"order_id": "T-PART", "type": "OUTBOUND",
             "warehouse": sim.inv_loc[loc]["warehouse"], "pick_loc": loc,
             "drop_loc": "W-OU-01", "qty": 1.0}
    delivered, ok = sim._inv_settle(order)
    assert ok and delivered == pytest.approx(0.4)
    assert sim.inv[sku][loc] == pytest.approx(0.0)
    st = [e for e in events_of(sim) if e["type"] == "STOCKOUT"][-1]
    assert st["shortfall"] == pytest.approx(0.6) and st["policy"] == "partial_fill"


def test_shortage_run_loses_orders(tmp_path):
    """整局验证：库存压到极低 + 需求加压 → 出现缺货、完成数下降、丢单上升。"""
    sim = make_sim(tmp_path, scale=0.02, demand_mult=4.0)
    sim.run()
    m = sim.metrics["metrics"]
    assert m["stockout_count"]["value"] > 0, "压力测试下仍是 0 缺货，说明判定是死代码"
    assert m["fill_rate"]["value"] < 1.0
    assert m["orders_lost"]["value"] > 0


# ------------------------------------------------------------------ 门 8 确定性
def test_stock_events_are_deterministic(tmp_path):
    a = make_sim(tmp_path / "a")
    b = make_sim(tmp_path / "b")
    a.run()
    b.run()

    def digest(sim):
        evs = [e for e in events_of(sim)
               if e["type"].startswith(("STOCK", "HOLDING", "REORDER", "PICKFACE"))]
        return json.dumps(evs, sort_keys=True), json.dumps(sim.inv, sort_keys=True)

    ea, sa = digest(a)
    eb, sb = digest(b)
    assert ea == eb, "同种子跑出两份不同的库存事件流"
    assert sa == sb, "期末库存不一致"


def test_unknown_pick_location_raises(tmp_path):
    sim = make_sim(tmp_path)
    order = {"order_id": "T-BAD", "type": "OUTBOUND", "warehouse": "W",
             "pick_loc": "SHELF-NOT-EXIST", "drop_loc": "W-OU-01", "qty": 1.0}
    with pytest.raises(ValueError, match="没有库存映射"):
        sim._inv_settle(order)


# ------------------------------------------------------------------ 门 7 快照
def test_inventory_in_snapshot_and_required(tmp_path):
    sim = make_sim(tmp_path)
    for _ in range(400):
        if sim.step():
            break
    snap = sim.snapshot()
    assert "inventory" in snap and snap["inventory"]["stock"], "快照里没有库存账"
    sim.log.close()

    bad = json.loads(json.dumps(snap))
    bad.pop("inventory")
    with pytest.raises(ValueError, match="缺字段"):
        se.Simulation.restore(bad, ROOT / "config" / "scenarios" / "baseline.json",
                              *specs(), out_dir=tmp_path / "run")


def test_old_snapshot_schema_rejected(tmp_path):
    sim = make_sim(tmp_path)
    sim.step()
    snap = sim.snapshot()
    snap["schema_version"] = "1.0"
    sim.log.close()
    with pytest.raises(ValueError, match="版本不符"):
        se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                              *specs(), out_dir=tmp_path / "run")


def test_snapshot_stock_must_match_event_replay(tmp_path):
    """快照库存与事件流重放不一致 → restore 必须 raise。"""
    sim = make_sim(tmp_path)
    for _ in range(600):
        if sim.step():
            break
    snap = sim.snapshot()
    sku = sorted(snap["inventory"]["stock"])[0]
    loc = sorted(snap["inventory"]["stock"][sku])[0]
    snap["inventory"]["stock"][sku][loc] += 7.0
    sim.log.close()
    with pytest.raises(ValueError, match="不一致"):
        se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                              *specs(), out_dir=tmp_path / "run")


# ------------------------------------------------------------------ 命令
def test_set_reorder_point_command(tmp_path):
    sim = make_sim(tmp_path)
    sim.enqueue_command({"type": "SET_REORDER_POINT",
                         "payload": {"sku": "ALL", "factor": 3.0}})
    sim.step()
    assert sim.reorder_factor == pytest.approx(3.0)
    assert "CMD_SET_REORDER_POINT" in sim.log.counts


def test_set_reorder_point_rejects_unknown_sku(tmp_path):
    sim = make_sim(tmp_path)
    with pytest.raises(ValueError, match="没有这个 SKU"):
        sim.enqueue_command({"type": "SET_REORDER_POINT",
                             "payload": {"sku": "SKU-NOPE", "factor": 2.0}})
    with pytest.raises(ValueError, match="factor"):
        sim.enqueue_command({"type": "SET_REORDER_POINT",
                             "payload": {"sku": "ALL", "factor": 99}})


# ------------------------------------------------------------------ 经济联动
def test_internal_refill_earns_no_revenue(tmp_path):
    """内部移库不是销售：不许凭空产生营收（否则利润被自己的补货灌水）。"""
    sim = make_sim(tmp_path)
    econ = ge.attach(sim, None, initial_capital=500000)
    pair = [(s, v["reserve"][0], v["pick_face"][0])
            for s, v in sorted(sim.inv_sku.items()) if v["reserve"] and v["pick_face"]]
    assert pair, "没有任何 SKU 同时有储备位与拣选面，补货路径无法测试"
    sku, src, dst = pair[0]
    sim.inv[sku][src] = 10.0
    sim.inv[sku][dst] = 1.0
    order = sim._inv_make_order("RPL-TEST", src, dst, 5.0)
    rev0 = econ.revenue
    delivered, ok = sim._inv_settle(order)
    assert ok and delivered == pytest.approx(5.0)
    assert sim.inv[sku][src] == pytest.approx(5.0)
    assert sim.inv[sku][dst] == pytest.approx(6.0)
    assert econ.revenue == pytest.approx(rev0), "内部补货记了收入"
    evs = [e for e in events_of(sim) if e["type"] == "PICKFACE_REFILL"]
    assert evs and evs[-1]["qty"] == pytest.approx(5.0)
