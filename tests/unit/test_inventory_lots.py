#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P6b：批次 / 效期 / FEFO 的单元测试。

这里测的是**引擎的判定**，不是"能不能跑"：
  - FEFO 真的先出早到期的货（而不是碰巧顺序对）
  - FIFO 与 FEFO 在同一组批次上**必须给出不同结果**，否则两个策略名是同义词
  - 过期货必须先报废再拣选（同 tick 里"刚过期"的货不许被发出去）
  - 内部移库不许把效期重置（仓储里最常见的"越搬越新鲜"假账）
  - 快照 3.0 必须带批次；效期开着却没有 lots 的档要拒绝
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sim_engine as se  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def make_sim(tmp_path, expiry=None, spec=None):
    """造一局。expiry=dict 时临时改写 inventory_spec 的 expiry 块。

    覆写走 `Simulation.inventory_spec_override`（与 station_slots_override 同套路），
    用完立刻清掉——类属性会跨测试泄漏，那会制造"单独跑过、一起跑就红"的假象。
    """
    base = jload(ROOT / "config" / "inventory_spec.json") if spec is None else dict(spec)
    if expiry is not None:
        base["expiry"] = dict(base["expiry"], **expiry)
    se.Simulation.inventory_spec_override = base
    try:
        sim = se.Simulation(
            ROOT / "config" / "scenarios" / "baseline.json",
            jload(ROOT / "config" / "sim_spec.json"),
            jload(ROOT / "config" / "fleet_spec.json"),
            jload(ROOT / "config" / "demand_spec.json"),
            jload(ROOT / "data" / "layout.json"),
            jload(ROOT / "data" / "nav_grid.json"),
            jload(ROOT / "data" / "config.json"),
            out_dir=tmp_path / "run",
        )
    finally:
        se.Simulation.inventory_spec_override = None
    return sim


def events_of(sim):
    try:
        sim.log.f.flush()
    except Exception:                       # noqa: BLE001
        pass
    return [json.loads(x) for x in (sim.out / "sim_events.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip()]


def plant(sim, sku, loc, lots):
    """把某个库位的批次换成给定的一组（测判定时不能靠随机数据碰运气）。"""
    sim.lots[(sku, loc)] = []
    sim.lot_heap = [h for h in sim.lot_heap if (h[2], h[3]) != (sku, loc)]
    sim.inv.setdefault(sku, {})[loc] = 0.0
    for lot in lots:
        sim._lot_push(sku, loc, dict(lot))
        sim.inv[sku][loc] += float(lot["qty"])


def first_two_lot_location(sim):
    for (sku, loc), arr in sorted(sim.lots.items()):
        if len(arr) >= 2:
            return sku, loc
    pytest.skip("期初数据里没有含两个批次的库位")


# ---------------------------------------------------------------- 装载不变量

def test_every_location_lot_sum_matches_stock(tmp_path):
    """期初批次之和必须等于期初结存，否则一开始两套账就是分叉的。"""
    sim = make_sim(tmp_path)
    assert sim.inv_enabled and sim.lot_expiry_enabled
    assert sim._inv_lot_check() == []
    assert sim.lots, "效期已启用却没有装入任何批次"
    assert sim.lot_policy == "FEFO"


def test_total_lot_units_equals_total_stock(tmp_path):
    sim = make_sim(tmp_path)
    a = sum(q for rows in sim.inv.values() for q in rows.values())
    b = sum(loc_["qty"] for arr in sim.lots.values() for loc_ in arr)
    assert a == pytest.approx(b, abs=1e-9)


# ---------------------------------------------------------------- FEFO 判定

def test_fefo_consumes_earliest_expiry_first(tmp_path):
    sim = make_sim(tmp_path)
    sku, loc = first_two_lot_location(sim)
    plant(sim, sku, loc, [
        {"lot_id": "LOT-LATE", "qty": 10.0, "t_in": 0.0, "expiry_t": 9000.0},
        {"lot_id": "LOT-EARLY", "qty": 10.0, "t_in": 100.0, "expiry_t": 4000.0},
    ])
    sim.lot_policy = "FEFO"
    sim._inv_dec(sku, loc, 5.0, {"order_id": "T"}, "pick")

    early = next(loc_ for loc_ in sim.lots[(sku, loc)] if loc_["lot_id"] == "LOT-EARLY")
    late = next(loc_ for loc_ in sim.lots[(sku, loc)] if loc_["lot_id"] == "LOT-LATE")
    assert early["qty"] == pytest.approx(5.0), "FEFO 没有先扣早到期的批次"
    assert late["qty"] == pytest.approx(10.0), "晚到期的批次被提前扣了"
    assert sim.fefo_violations == 0


def test_fifo_and_fefo_are_actually_different(tmp_path):
    """门：同一组批次，两个策略必须给出不同结果。

    构造一个真实场景——后到货的那批效期更短（供应商给了更短的日期码）：
      批次 A：t_in=0，效期 9000（早入库、晚到期）
      批次 B：t_in=50，效期 3000（晚入库、早到期）
    FIFO 先出 A，FEFO 先出 B。若两者一致，说明策略切换是装饰。
    """
    sim = make_sim(tmp_path)
    sku, loc = first_two_lot_location(sim)
    lots = [{"lot_id": "LOT-A", "qty": 10.0, "t_in": 0.0, "expiry_t": 9000.0},
            {"lot_id": "LOT-B", "qty": 10.0, "t_in": 50.0, "expiry_t": 3000.0}]

    plant(sim, sku, loc, lots)
    sim.lot_policy = "FIFO"
    sim._inv_dec(sku, loc, 4.0, {"order_id": "T-FIFO"}, "pick")
    fifo_left = {loc_["lot_id"]: loc_["qty"] for loc_ in sim.lots[(sku, loc)]}

    plant(sim, sku, loc, lots)
    sim.lot_policy = "FEFO"
    sim._inv_dec(sku, loc, 4.0, {"order_id": "T-FEFO"}, "pick")
    fefo_left = {loc_["lot_id"]: loc_["qty"] for loc_ in sim.lots[(sku, loc)]}

    assert fifo_left["LOT-A"] == pytest.approx(6.0), "FIFO 应扣最早入库的 LOT-A"
    assert fefo_left["LOT-B"] == pytest.approx(6.0), "FEFO 应扣最早到期的 LOT-B"
    assert fifo_left != fefo_left, "FIFO 与 FEFO 结果相同 → 策略切换无效"


def test_fefo_violation_counter_catches_bad_order(tmp_path):
    """把排序写反，违规计数必须 > 0。

    这条守的是"FEFO 的实现"而不是"FEFO 的名字"：如果哪天有人把排序改成倒序，
    功能测试可能因为数据凑巧而对不上、也可能照样通过，但计数器一定会非零。
    """
    sim = make_sim(tmp_path)
    sku, loc = first_two_lot_location(sim)
    plant(sim, sku, loc, [
        {"lot_id": "LOT-EARLY", "qty": 10.0, "t_in": 0.0, "expiry_t": 1000.0},
        {"lot_id": "LOT-LATE", "qty": 10.0, "t_in": 0.0, "expiry_t": 9000.0},
    ])
    sim.lot_policy = "FEFO"
    # 故意反转顺序（实例属性覆盖方法，不改源码）
    sim._lot_order = lambda arr: sorted(arr, key=lambda loc_: (-loc_["expiry_t"], loc_["lot_id"]))
    sim._inv_dec(sku, loc, 1.0, {"order_id": "T-BAD"}, "pick")
    assert sim.fefo_violations == 1, "排序反了却没记违规，等于这个 KPI 是死的"


def test_decrement_carries_lot_detail_in_event(tmp_path):
    """事件里必须能查到"这一单从哪批出的"，否则 FEFO 只是口头承诺。"""
    sim = make_sim(tmp_path)
    sku, loc = first_two_lot_location(sim)
    plant(sim, sku, loc, [{"lot_id": "LOT-X", "qty": 8.0, "t_in": 0.0,
                           "expiry_t": 5000.0}])
    sim._inv_dec(sku, loc, 2.0, {"order_id": "T-DETAIL"}, "pick")
    ev = [e for e in events_of(sim) if e["type"] == "STOCK_DECREMENT"][-1]
    assert ev["lots"] == [{"lot_id": "LOT-X", "qty": 2.0, "expiry_t": 5000.0}]


# ---------------------------------------------------------------- 到期报废

def test_expired_lot_is_written_off_and_removed(tmp_path):
    sim = make_sim(tmp_path)
    sku, loc = first_two_lot_location(sim)
    plant(sim, sku, loc, [
        {"lot_id": "LOT-STALE", "qty": 6.0, "t_in": 0.0, "expiry_t": 10.0},
        {"lot_id": "LOT-FRESH", "qty": 6.0, "t_in": 0.0, "expiry_t": 99_999.0},
    ])
    sim._inv_expire_sweep(50.0)

    assert [loc_["lot_id"] for loc_ in sim.lots[(sku, loc)]] == ["LOT-FRESH"]
    assert sim.inv[sku][loc] == pytest.approx(6.0)
    assert sim.lot_expired_units == pytest.approx(6.0)
    ev = [e for e in events_of(sim) if e["type"] == "STOCK_EXPIRED"][-1]
    assert ev["lot_id"] == "LOT-STALE" and ev["qty"] == pytest.approx(6.0)
    assert ev["waste_posting"] == "none", "报废口径必须显式写进事件，不能靠默认"


def test_expired_stock_cannot_be_picked(tmp_path):
    """过期货在拣选前就必须报废：同一 tick 里"刚过期"的货不许发出去。"""
    sim = make_sim(tmp_path)
    sku, loc = first_two_lot_location(sim)
    plant(sim, sku, loc, [{"lot_id": "LOT-DEAD", "qty": 5.0, "t_in": 0.0,
                           "expiry_t": 100.0}])
    sim.t = 200.0
    sim._inv_expire_sweep(200.0)
    assert sim.inv[sku][loc] == pytest.approx(0.0)
    with pytest.raises(ValueError, match="库存不足"):
        sim._inv_dec(sku, loc, 1.0, {"order_id": "T"}, "pick")


def test_expiry_disabled_reproduces_v2_event_stream(tmp_path):
    """效期关掉时事件流必须与 v2 基准逐字节一致。

    这条比"能跑"强得多：它证明 P6b 的所有改动都挂在 `expiry.enabled` 开关后面，
    没有偷偷改动旧路径。v2 基准文件在 `tests/golden/step_baseline_v2.json`。
    """
    golden_p = ROOT / "tests" / "golden" / "step_baseline_v2.json"
    if not golden_p.exists():
        pytest.skip("没有 v2 基准可比")
    sim = make_sim(tmp_path, expiry={"enabled": False})
    assert sim.lot_expiry_enabled is False
    assert not sim.lots, "效期关掉却还装入了批次"
    sim.run()
    import hashlib
    import step_baseline as sb           # 复用基准自己的归一化，别在本文件抄一遍
    want = jload(golden_p)["scenarios"]["baseline"]["sim_events.jsonl"]["sha256"]
    # 用 read_text（而不是 read_bytes().decode）：基准自己就是按文本读的，
    # Windows 上字节层的 \r\n 会让哈希对不上，而那不是"内容不一致"。
    raw = (sim.out / "sim_events.jsonl").read_text(encoding="utf-8")
    got = hashlib.sha256(sb.normalise(raw).encode("utf-8")).hexdigest()
    assert got == want, "效期关闭时事件流与 v2 基准不一致：P6b 漏了开关保护"


# ---------------------------------------------------------------- 移库不改效期

def test_internal_move_keeps_expiry(tmp_path):
    """内部移库不许重新给保质期：那是"越搬越新鲜"的假账。"""
    sim = make_sim(tmp_path)
    sku, loc = first_two_lot_location(sim)
    dst = next((loc_ for loc_ in sim.inv_sku[sku]["pick_face"] if loc_ != loc), None)
    if dst is None:
        pytest.skip("该 SKU 没有另一个拣选面")
    plant(sim, sku, loc, [{"lot_id": "LOT-MOVE", "qty": 9.0, "t_in": 0.0,
                           "expiry_t": 4321.0}])
    sim.inv.setdefault(sku, {})[dst] = 0.0
    sim.lots.pop((sku, dst), None)
    sim._inv_move_lots(sku, loc, sku, dst, 4.0)

    moved = sim.lots[(sku, dst)][0]
    assert moved["lot_id"] == "LOT-MOVE"
    assert moved["expiry_t"] == pytest.approx(4321.0), "移库把效期重算了"
    assert moved["qty"] == pytest.approx(4.0)


# ---------------------------------------------------------------- 快照

def test_snapshot_roundtrip_keeps_lots(tmp_path):
    sim = make_sim(tmp_path)
    for _ in range(400):
        if sim.step():
            break
    snap = sim.snapshot()
    assert snap["schema_version"] == "4.0"
    assert snap["inventory"]["lots"], "快照里没有批次明细"
    before = {k: [dict(loc_) for loc_ in v] for k, v in sim.lots.items()}
    sim.log.close()

    spec = (jload(ROOT / "config" / "sim_spec.json"), jload(ROOT / "config" / "fleet_spec.json"),
            jload(ROOT / "config" / "demand_spec.json"), jload(ROOT / "data" / "layout.json"),
            jload(ROOT / "data" / "nav_grid.json"), jload(ROOT / "data" / "config.json"))
    restored = se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                                     *spec, out_dir=tmp_path / "run")
    after = {k: [dict(loc_) for loc_ in v] for k, v in restored.lots.items()}
    assert after == before, "读档后批次明细变了"
    assert restored._inv_lot_check() == []
    restored.log.close()


def test_snapshot_without_lots_is_rejected_when_expiry_enabled(tmp_path):
    """效期开着却没有批次：那种档能跑，但 FEFO 会静默退化，必须拒绝。"""
    sim = make_sim(tmp_path)
    sim.step()
    snap = sim.snapshot()
    snap["inventory"].pop("lots")
    sim.log.close()
    spec = (jload(ROOT / "config" / "sim_spec.json"), jload(ROOT / "config" / "fleet_spec.json"),
            jload(ROOT / "config" / "demand_spec.json"), jload(ROOT / "data" / "layout.json"),
            jload(ROOT / "data" / "nav_grid.json"), jload(ROOT / "data" / "config.json"))
    with pytest.raises(ValueError, match="缺少 inventory.lots"):
        se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                             *spec, out_dir=tmp_path / "run")


def test_tampered_lot_qty_is_rejected(tmp_path):
    """改批次数量让 sum(lots) != stock，restore 必须 raise。"""
    sim = make_sim(tmp_path)
    sim.step()
    snap = sim.snapshot()
    sku = next(s for s, per in snap["inventory"]["lots"].items() if per)
    loc = next(iter(snap["inventory"]["lots"][sku]))
    snap["inventory"]["lots"][sku][loc][0]["qty"] += 3.0
    sim.log.close()
    spec = (jload(ROOT / "config" / "sim_spec.json"), jload(ROOT / "config" / "fleet_spec.json"),
            jload(ROOT / "config" / "demand_spec.json"), jload(ROOT / "data" / "layout.json"),
            jload(ROOT / "data" / "nav_grid.json"), jload(ROOT / "data" / "config.json"))
    with pytest.raises(ValueError, match="批次明细与聚合结存不一致"):
        se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                             *spec, out_dir=tmp_path / "run")


def test_old_snapshot_2_0_is_rejected(tmp_path):
    sim = make_sim(tmp_path)
    sim.step()
    snap = sim.snapshot()
    snap["schema_version"] = "2.0"
    sim.log.close()
    spec = (jload(ROOT / "config" / "sim_spec.json"), jload(ROOT / "config" / "fleet_spec.json"),
            jload(ROOT / "config" / "demand_spec.json"), jload(ROOT / "data" / "layout.json"),
            jload(ROOT / "data" / "nav_grid.json"), jload(ROOT / "data" / "config.json"))
    with pytest.raises(ValueError, match="版本不符"):
        se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                             *spec, out_dir=tmp_path / "run")


# ---------------------------------------------------------------- 命令

def test_set_lot_policy_command(tmp_path):
    sim = make_sim(tmp_path)
    sim.enqueue_command({"type": "SET_LOT_POLICY", "payload": {"policy": "FIFO"}})
    sim.step()
    assert sim.lot_policy == "FIFO"
    assert "CMD_SET_LOT_POLICY" in sim.log.counts
    with pytest.raises(ValueError, match="FEFO / FIFO"):
        sim.enqueue_command({"type": "SET_LOT_POLICY", "payload": {"policy": "LIFO"}})
