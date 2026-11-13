#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R2 · 货架存取机理的测试（引擎接线 + 布局展开 + 纯规则）。

三块：

1. **规则**：双深阻挡、驶入/压入的投放差异、重力式 FIFO、LIFO×FEFO 冲突。
2. **布局展开**：`rack_type` 声明了才拆深位；默认布局**一个 cell 都不许有**
   （否则 v3 金标准立刻破，这是 D4 的全部意义）。
3. **引擎**：多深位货道上取后位 → `SLOT_BLOCKED` + `RESHUFFLE` 且真的改到前位；
   LIFO 货道 + FEFO → 开局一条 `FIFO_LIFO_CONFLICT`。
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rack_access as ra     # noqa: E402
import sim_engine as se      # noqa: E402
import spec_layout as sl     # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 1) 纯规则

def test_module_self_test_passes():
    assert ra.self_test() == 0


def test_deep_cell_blocks_rear_slot():
    r = ra.resolve("deep_front_back", [True, True], "pick")
    assert r["slot"] == 0 and r["blocked"] == [1] and r["reshuffle"] is True
    r2 = ra.resolve("deep_front_back", [False, True], "pick")
    assert r2["slot"] == 1 and r2["blocked"] == []


def test_drive_in_and_push_back_put_differently():
    """两者都是 LIFO，但"怎么进去"不同——这正是命名边界要分开的原因。"""
    a = ra.resolve("forklift_enters_aisle", [True, False, False], "put")
    b = ra.resolve("same_side_push_nest", [True, False, False], "put")
    assert a["slot"] == 2, "驶入式应投到最深空位"
    assert b["slot"] == 1, "压入式应投到最浅空位"


def test_gravity_lane_is_fifo():
    r = ra.resolve("gravity_load_to_unload", [True, True, False], "pick")
    assert r["order"] == "FIFO" and r["slot"] == 0
    assert ra.lane_order("gravity_load_to_unload", ["a", "b", "c"]) == ["a", "b", "c"]
    assert ra.lane_order("forklift_enters_aisle", ["a", "b", "c"]) == ["c", "b", "a"]


def test_lifo_rack_conflicts_with_fefo_but_fifo_rack_does_not():
    assert ra.conflict("forklift_enters_aisle", "FEFO")["conflict"] is True
    assert ra.conflict("gravity_load_to_unload", "FEFO")["conflict"] is False
    assert ra.conflict("forklift_enters_aisle", "FIFO")["conflict"] is False


def test_mobile_aisle_closed_is_not_operable():
    r = ra.resolve("aisle_compression", [True], "pick", aisle_open=False)
    assert r["slot"] is None and r["reason"] == "aisle_closed"


def test_effective_slots_never_exceeds_nominal():
    assert ra.effective_slots(100, 0.05, 0.9, frozen=2) == pytest.approx(83.5)
    assert ra.effective_slots(10, 0.5, 1.0, frozen=99) == 0.0


# ---------------------------------------------------------------- 2) 布局展开

def _spec_with(rack_type, zone_index=0):
    spec = jload(ROOT / "config" / "layout_spec.json")
    spec = copy.deepcopy(spec)
    spec["warehouses"][0]["racks"][zone_index]["rack"]["rack_type"] = rack_type
    return spec


def _locs(layout):
    return [loc_ for w in layout["warehouses"] for loc_ in w["locations"]]


def test_default_layout_has_no_cells():
    """默认必须一个 cell 都没有：这是"默认字节不变"的物理体现。"""
    layout = sl.expand(jload(ROOT / "config" / "layout_spec.json"))
    assert sum(1 for loc_ in _locs(layout) if "cell_id" in loc_) == 0


def test_declaring_rack_type_expands_depth_slots():
    base = len(_locs(sl.expand(jload(ROOT / "config" / "layout_spec.json"))))
    layout = sl.expand(_spec_with("double_deep"))
    locs = _locs(layout)
    cells = {loc_["cell_id"] for loc_ in locs if "cell_id" in loc_}
    assert cells, "声明了 rack_type 却没有任何货道"
    assert len(locs) > base, "双深应当比单深多出库位"
    depths = {loc_["depth_index"] for loc_ in locs if "cell_id" in loc_}
    assert depths == {0, 1}, "双深的 depth_index 应为 {0,1}"
    for loc_ in locs:
        if "cell_id" in loc_:
            assert loc_["rack_type"] == "double_deep"
            assert loc_["access_mechanism"] == "deep_front_back"
            assert loc_["n_depth"] == 2


def test_aisle_depth_uses_explicit_lane_depth():
    spec = _spec_with("drive_in")
    spec["warehouses"][0]["racks"][0]["lane_depth"] = 5
    layout = sl.expand(spec)
    locs = [loc_ for loc_ in _locs(layout) if loc_.get("cell_id")]
    assert max(loc_["depth_index"] for loc_ in locs) == 4   # 5 个深位 → 0..4


# ---------------------------------------------------------------- 3) 引擎接线

def _deep_layout(mechanism="deep_front_back", depth=2):
    layout = copy.deepcopy(jload(ROOT / "data" / "layout.json"))
    n = 0
    for w in layout["warehouses"]:
        shelves = [loc_ for loc_ in w["locations"] if loc_["kind"] == "SHELF"]
        for i in range(0, len(shelves) - depth + 1, depth):
            cell = "C-%s-%04d" % (w["id"], n)
            n += 1
            for d, loc in enumerate(shelves[i:i + depth]):
                loc.update({"cell_id": cell, "depth_index": d, "n_depth": depth,
                            "access_mechanism": mechanism, "rack_type": "deep_front_back"})
    return layout


def _sim(tmp_path, layout):
    return se.Simulation(
        ROOT / "config" / "scenarios" / "baseline.json",
        jload(ROOT / "config" / "sim_spec.json"),
        jload(ROOT / "config" / "fleet_spec.json"),
        jload(ROOT / "config" / "demand_spec.json"),
        layout, jload(ROOT / "data" / "nav_grid.json"),
        jload(ROOT / "data" / "config.json"),
        out_dir=tmp_path / "run")


def _events(sim):
    try:
        sim.log.f.flush()
    except Exception:                     # noqa: BLE001
        pass
    return [json.loads(x) for x in (sim.out / "sim_events.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip()]


def test_default_layout_builds_no_cells(tmp_path):
    sim = _sim(tmp_path, jload(ROOT / "data" / "layout.json"))
    assert sim.rack_cells == {} and sim.loc_cell == {}
    sim.run()
    assert not [e for e in _events(sim) if e["type"] in ("SLOT_BLOCKED", "RESHUFFLE",
                                                          "FIFO_LIFO_CONFLICT")]


def test_rear_slot_pick_is_blocked_and_redirected(tmp_path):
    sim = _sim(tmp_path, _deep_layout())
    assert sim.rack_cells, "带货道元数据的布局没建出索引"
    # 找一个两深位都有货的货道
    cell = None
    for cid, c in sim.rack_cells.items():
        if len(c["locs"]) == 2 and all(sim._loc_qty(loc_) > 0 for loc_ in c["locs"]):
            cell = c
            break
    if cell is None:
        pytest.skip("没有两深位都有货的货道")
    front, back = cell["locs"]

    sim.t = 100.0
    target, moved = sim._resolve_slot(back, "pick")
    # `_resolve_slot` 的返回类型跟随入参：传 id 得 id，传 dict 得 dict（引擎里传 dict）
    tid = target["id"] if isinstance(target, dict) else target
    assert tid == front, "后位被挡时应改到前位作业"
    assert moved is True
    assert sim.rack_blocked_n == 1 and sim.rack_reshuffle_n == 1
    kinds = [e["type"] for e in _events(sim)]
    assert "SLOT_BLOCKED" in kinds and "RESHUFFLE" in kinds


def test_front_slot_pick_is_not_flagged(tmp_path):
    sim = _sim(tmp_path, _deep_layout())
    cell = next(c for c in sim.rack_cells.values() if len(c["locs"]) == 2)
    front = cell["locs"][0]
    target, moved = sim._resolve_slot(front, "pick")
    tid = target["id"] if isinstance(target, dict) else target
    assert tid == front and moved is False
    assert sim.rack_blocked_n == 0


def test_lifo_rack_reports_fefo_conflict(tmp_path):
    sim = _sim(tmp_path, _deep_layout(mechanism="forklift_enters_aisle"))
    assert sim.lot_policy == "FEFO"
    sim.run()
    ev = [e for e in _events(sim) if e["type"] == "FIFO_LIFO_CONFLICT"]
    assert ev, "物理 LIFO 货道 + FEFO 却没有冲突事件"
    assert ev[0]["cells"] >= 1 and ev[0]["lot_policy"] == "FEFO"
    assert sim.rack_conflict_n >= 1


def test_fifo_rack_has_no_conflict(tmp_path):
    sim = _sim(tmp_path, _deep_layout(mechanism="gravity_load_to_unload"))
    sim.run()
    assert sim.rack_conflict_n == 0
    assert not [e for e in _events(sim) if e["type"] == "FIFO_LIFO_CONFLICT"]


def test_snapshot_keeps_rack_counters(tmp_path):
    """R2 计数器必须进快照，否则续玩会从 0 重新计数（收检指出的潜伏 bug）。"""
    layout = _deep_layout()
    sim = _sim(tmp_path, layout)
    sim.step()
    sim.rack_blocked_n = 7
    sim.rack_reshuffle_n = 3
    sim.rack_conflict_n = 2
    snap = sim.snapshot()
    sim.log.close()
    # 3.1（R2 计数器）→ 4.0（R3 逐车 model_id）；旧档一律显式拒绝（D2）
    assert snap["schema_version"] == "4.0", "加了计数器就该升版（D2：旧档显式拒绝）"
    c = snap["counters"]
    assert (c["rack_blocked_n"], c["rack_reshuffle_n"], c["rack_conflict_n"]) == (7, 3, 2)

    restored = se.Simulation.restore(
        snap, ROOT / "config" / "scenarios" / "baseline.json",
        jload(ROOT / "config" / "sim_spec.json"),
        jload(ROOT / "config" / "fleet_spec.json"),
        jload(ROOT / "config" / "demand_spec.json"),
        layout, jload(ROOT / "data" / "nav_grid.json"),
        jload(ROOT / "data" / "config.json"),
        out_dir=tmp_path / "run")
    assert restored.rack_blocked_n == 7
    assert restored.rack_reshuffle_n == 3
    assert restored.rack_conflict_n == 2
    restored.log.close()

