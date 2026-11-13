#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R0 · 解析库容层（G4）与四份新 spec 的口径测试。

分三块，缺一块都不算过关：

1. **公式正对照**（附录 C.1–C.9）：每个数都手算得出来，不用"跑通就行"糊过去。
2. **五条负对照**（资料自带 bug）：修正版与资料原式**必须给出不同结果**。
   只要两版一致，说明修正没生效、或者负对照写反了——这道门就变成装饰。
3. **配置语义**：rack/equipment 的取值不能自相矛盾（驶入式必须 LIFO、设备必须
   在类型库里、`level` 必须标）。这些是 R2/R3 接引擎时的前提。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import capacity_model as cm  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


G = {"clearW": 1.1, "clearD": 1.1, "clearH": 1.3, "structW": 0.07,
     "structD": 0.05, "structH": 0.1, "gap": 0.1, "nParallel": 1, "nDepth": 1}


# ================================================================ 1) 正对照

def test_cell_geometry_matches_appendix_c1():
    geo = cm.cell_geometry(G)
    assert geo["pitch"] == pytest.approx(1.37)          # 1.1+0.07+2*0.1
    assert geo["depth"] == pytest.approx(1.35)          # 1.1+0.05+2*0.1
    assert geo["level_height"] == pytest.approx(1.4)    # 1.3+0.1


def test_unit_capacity_is_levels_times_per_level():
    n = cm.unit_capacity(G, levels_left=4, levels_right=4)
    assert n["per_level_slots"] == 1
    assert n["slots"] == 8                              # 4*1 + 4*1


def test_aisle_aggregate_sums_pitch_and_slots():
    units = [{"pitch": 1.37, "slots": 8}, {"pitch": 1.37, "slots": 8}]
    agg = cm.aisle_aggregate(units)
    assert agg["length"] == pytest.approx(2.74)
    assert agg["slots"] == 16


def test_target_slots_ceil_and_explicit_override():
    assert cm.target_slots(600, 0.85) == 706            # ceil(600/0.85)
    assert cm.target_slots(600, 0.85, target=500) == 500
    with pytest.raises(ValueError, match="occupancy"):
        cm.target_slots(600, 0)


def test_effective_slots_order_and_floor():
    # 名义 100，尾量 5%，混放系数 0.9，再扣冻结 2 / 维修 1 / 禁用 3
    v = cm.effective_slots(100, 0.05, 0.9, frozen=2, maintenance=1, disabled=3)
    assert v == pytest.approx(100 * 0.95 * 0.9 - 6)
    # 扣到负数要夹到 0，不许出现"负货位"
    assert cm.effective_slots(1, 0.5, 1.0, frozen=10) == 0.0


def test_avg_travel_distance_is_half_length_times_factor():
    assert cm.avg_travel_distance(100.0, 1.0) == pytest.approx(50.0)
    assert cm.avg_travel_distance(100.0, 2.0) == pytest.approx(100.0)


def test_avg_height_weights_by_slots_and_uses_mid_level():
    # 4 层各 2 个货位：中位 0.5/1.5/2.5/3.5 层 × 1.4m
    h = cm.avg_height([(1, 2), (2, 2), (3, 2), (4, 2)], 1.5)
    assert h == pytest.approx((0.5 + 1.5 + 2.5 + 3.5) / 4 * 1.5)


def test_stacker_cycles_follow_appendix_c6():
    c = cm.stacker_cycles(D=100, H=10, v_h=2.0, v_v=0.5, t_p=10, t_f=1.0)
    t_h, t_v = 50.0, 20.0
    assert c["single_s"] == pytest.approx(t_h + t_v + 10)
    assert c["composite_s"] == pytest.approx(0.6 * (t_h + t_v) + 2 * 10)


def test_forklift_cycles_follow_appendix_c6():
    c = cm.forklift_cycles(D=100, v=5.0, t_p=12, t_f=1.0)
    assert c["single_s"] == pytest.approx(2 * 20 + 12)
    assert c["composite_s"] == pytest.approx(1.5 * 20 + 2 * 12)


def test_capacity_per_hour_and_aisle_capacity():
    assert cm.capacity_per_hour(120) == pytest.approx(30.0)
    assert cm.aisle_capacity([{"q_per_h": 30, "count": 2},
                              {"q_per_h": 20, "count": 1}]) == pytest.approx(80.0)
    with pytest.raises(ValueError, match="复合周期"):
        cm.capacity_per_hour(0)


def test_cross_validate_flags_beyond_tolerance():
    ok = cm.cross_validate(throughput_sim=108, q_stable=100, tolerance_pct=15)
    bad = cm.cross_validate(throughput_sim=120, q_stable=100, tolerance_pct=15)
    assert ok["ok"] is True and ok["deviation_pct"] == pytest.approx(8.0)
    assert bad["ok"] is False and bad["deviation_pct"] == pytest.approx(20.0)


# ================================================================ 2) 负对照（B1–B5）

def test_b1_load_divisor_doubles_the_estimate_when_reverted():
    """B1：资料原式把负荷高估约一倍（分母漏 ×2）。"""
    fixed = cm.load_rate(50, 50, 100)
    buggy = cm.load_rate(50, 50, 100, fix=cm.LEGACY)
    assert fixed == pytest.approx(0.5)
    assert buggy == pytest.approx(1.0)
    assert buggy / fixed == pytest.approx(2.0), "原式必须恰好高一倍，否则不是这个 bug"


def test_b1_capacity_semantics_are_pinned():
    """口径钉住：C_a 是**复合次/h**，所以 in=out=C_a 时恰好饱和。

    方案书 B1 的例子写「in=out=C_a ⇒ ρ=0.5」，那要求 C_a 是单次指令能力。
    两种写法只差一个系数，但混用会让结论差一倍，所以这里显式断言本项目的口径。
    """
    assert cm.load_rate(100, 100, 100) == pytest.approx(1.0)
    assert cm.load_rate(50, 50, 100) == pytest.approx(0.5)


def test_b2_distance_power_makes_forklift_quadratic():
    """B2：D̄ 已含 d_f，原式再乘一次 → d_f²。d_f=2 时原式恰好是 2 倍。"""
    fixed = cm.avg_travel_distance(100, 2.0)
    buggy = cm.avg_travel_distance(100, 2.0, fix=cm.LEGACY)
    assert buggy / fixed == pytest.approx(2.0)
    # 换算到叉车行走时间：原式单指令的行走部分是修正版的两倍
    t_fix = cm.forklift_cycles(fixed, 5.0, 0)["single_s"]
    t_bug = cm.forklift_cycles(buggy, 5.0, 0)["single_s"]
    assert t_bug / t_fix == pytest.approx(2.0)


def test_b3_height_check_must_cover_stacker_too():
    """B3：资料只在叉车分支查高度，堆垛机漏检。"""
    warn = cm.height_warn(1.5, 6, max_height_m=6.0, kind="stacker")
    missed = cm.height_warn(1.5, 6, max_height_m=6.0, kind="stacker", fix=cm.LEGACY)
    assert warn and warn["kind"] == "height_unreachable"
    assert warn["top_m"] == pytest.approx(9.0)
    assert missed is None, "原式对堆垛机不告警，正是要修的 bug"
    # 能力高度足够时不告警（避免"永远告警"的假阳性）
    assert cm.height_warn(1.5, 4, max_height_m=6.0, kind="stacker") is None


def test_b4_overload_is_actually_checked():
    """B4：承载字段存而不用 → 现在按最严的那一项拒绝。"""
    r = cm.overload_check(1500, rack_rating_kg=1000, payload_kg=2000)
    assert r["ok"] is False and r["reason"] == "overload"
    assert r["limit_by"] == "rack_rating" and r["limit_kg"] == 1000
    # 原式不校验 → 同样的输入直接放行
    assert cm.overload_check(1500, rack_rating_kg=1000, fix=cm.LEGACY)["ok"] is True
    # 不超载时不许误报
    assert cm.overload_check(900, rack_rating_kg=1000)["ok"] is True


def test_b5_depth_is_not_hardcoded_to_one():
    """B5：nDepth=2 双深容量必须翻倍；写死 1 时表达不出来。"""
    g = dict(G, nDepth=2)
    assert cm.unit_capacity(g)["slots"] == 16
    assert cm.unit_capacity(g)["n_depth"] == 2
    legacy = cm.unit_capacity(g, fix=cm.LEGACY)
    assert legacy["slots"] == 8 and legacy["n_depth"] == 1


# ================================================================ 3) 告警与汇总

def _aisles():
    return [
        {"id": "a1", "load": 1.4, "capacity": 100, "devices": [{"q": 1}],
         "height_warn": None, "overload_warn": None},
        {"id": "a2", "load": 0.2, "capacity": 0, "devices": [],
         "height_warn": {"kind": "height_unreachable", "top_m": 9.0, "max_height_m": 6.0},
         "overload_warn": {"kind": "overload", "gross_kg": 1500}},
    ]


def test_alerts_covers_all_five_kinds():
    spec = {"alerts": {"capacity_insufficient": True, "aisle_overload": True,
                       "no_equipment": True, "height_unreachable": True, "overload": True}}
    got = cm.alerts(_aisles(), total_slots=500, needed_slots=706, spec=spec)
    kinds = {a["kind"] for a in got}
    assert kinds == {"capacity_insufficient", "aisle_overload", "no_equipment",
                     "height_unreachable", "overload"}, "五类告警必须都能触发"


def test_alerts_respect_switches():
    spec = {"alerts": {"capacity_insufficient": False, "aisle_overload": False,
                       "no_equipment": False, "height_unreachable": False,
                       "overload": False}}
    assert cm.alerts(_aisles(), 500, 706, spec) == []


def test_build_report_is_pure_and_reports_zero_capacity_defensively():
    layout = {"warehouses": [{"id": "W", "zones": [{"id": "Z", "racks": [
        {"id": "R1", "bays": 20}]}]}]}
    cap = jload(ROOT / "config" / "capacity_spec.json")
    rack = jload(ROOT / "config" / "rack_spec.json")
    equip = jload(ROOT / "config" / "equipment_spec.json")
    before = json.dumps([layout, cap], sort_keys=True)

    rep = cm.build_report(layout, rack, equip, cap)
    assert json.dumps([layout, cap], sort_keys=True) == before, "build_report 不许改输入"
    assert rep["totalSlots"] == 10 * 8           # bays//2 个单元 × 8 货位
    assert rep["needed"] == 706
    # 没有设备 → 不算负荷（除零要能挡住），并且必须报 no_equipment
    assert rep["totalLoad"] == 0.0
    assert any(w["kind"] == "no_equipment" for w in rep["warnings"])
    assert any(w["kind"] == "capacity_insufficient" for w in rep["warnings"])


# ================================================================ 4) 配置语义

def test_rack_spec_physical_order_is_consistent():
    spec = jload(ROOT / "config" / "rack_spec.json")
    t = spec["types"]
    # 单端驶入 LIFO、双端贯通 FIFO：这不是配置口味，是结构决定
    assert t["drive_in"]["inventory_order"] == "LIFO" and t["drive_in"]["single_end"]
    assert t["drive_through"]["inventory_order"] == "FIFO" and t["drive_through"]["double_end"]
    # 压入式 LIFO ≠ 托盘重力式 FIFO（命名边界）
    assert t["push_back"]["inventory_order"] == "LIFO"
    assert t["pallet_gravity"]["inventory_order"] == "FIFO"
    # 双深：后位受前位阻挡，且深度必须是 2
    assert t["double_deep"]["n_depth"] == 2 and t["double_deep"]["rear_blocked_by_front"]
    # 横梁式是基线：单深、可达性高
    assert t["selective_pallet"]["n_depth"] == 1 and t["selective_pallet"]["selectivity"] == "high"


def test_every_rack_type_has_level_and_source():
    spec = jload(ROOT / "config" / "rack_spec.json")
    allowed = {"standard", "typical", "assumption", "placeholder", "gap"}
    for name, t in spec["types"].items():
        assert t.get("level") in allowed, "%s 缺 level" % name
        assert t.get("source"), "%s 缺 source" % name


def test_equipment_time_model_keeps_the_fixes():
    """口径守卫：配置里如果把 B1/B2 改回资料原式，必须被这条抓住。"""
    spec = jload(ROOT / "config" / "equipment_spec.json")
    assert spec["time_model"]["load_rate_divisor"] == 2, "B1 被改回原式（分母 ×1）"
    assert spec["time_model"]["distance_factor_power"] == 1, "B2 被改回原式（d_f²）"
    assert spec["time_model"]["height_check"] == "all_types", "B3 被改回只查叉车"


def test_rack_equipment_pairs_exist_in_type_library():
    rack = jload(ROOT / "config" / "rack_spec.json")
    equip = jload(ROOT / "config" / "equipment_spec.json")["types"]
    known = set(equip) | {"manual_pick", "forklift_reach_doubledeep", "telescopic_fork"}
    for name, t in rack["types"].items():
        for e in t["equipment_allowed"]:
            assert e in known, "%s 允许的设备 %s 不在类型库里" % (name, e)


def test_capacity_spec_keeps_fix_semantics():
    cap = jload(ROOT / "config" / "capacity_spec.json")
    assert cap["load_rate_divisor"] == 2
    assert cap["distance_factor_power"] == 1
    assert 0 < cap["availability_factor"] <= 1
    assert cap["cross_validation"]["sim_vs_analytical_tolerance_pct"] == 15
