# -*- coding: utf-8 -*-
"""R4：解析库容层接真实数据 + 与仿真交叉验证。

守的是三类"看着有数、其实没算"的问题：

1. 解析层遍历 `zones[].racks`，而真实布局把货架挂在 `warehouse.racks` 上
   → 巷道数 0、能力 0，报告却仍然"无告警"。
2. 设备按货位份额摊到巷道是**小数**（W 的 12 台 / 16 条巷道 = 0.75 台/巷），
   用 `int()` 截断会让主仓能力变 0，而总能力还是正的。
3. 解析的"一单"少算两段行程或一次作业 → 比仿真快四成，却被当成"模型乐观"。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import capacity_model as cm            # noqa: E402
import check_capacity_cross as cx      # noqa: E402

CONFIG = ROOT / "config"
DATA = ROOT / "data"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rep():
    return cm.build_report(jload(DATA / "layout.json"), jload(CONFIG / "rack_spec.json"),
                           jload(CONFIG / "equipment_spec.json"),
                           jload(CONFIG / "capacity_spec.json"),
                           fleet=jload(CONFIG / "fleet_spec.json"))


def test_aisles_come_from_real_layout(rep):
    """真实布局必须能算出巷道；0 条就是"没接上"。"""
    assert len(rep["aisles"]) == 22, [a["id"] for a in rep["aisles"]][:5]
    wh = {}
    for a in rep["aisles"]:
        wh[a["warehouse"]] = wh.get(a["warehouse"], 0) + 1
    assert wh == {"W": 16, "F1": 2, "F2": 2, "F3": 2}
    assert rep["totalSlots"] == 1496


def test_every_aisle_has_equipment_and_capacity(rep):
    """每条巷道都要有设备与正能力——主仓全 0 是截断 bug 的症状。"""
    empty = [a["id"] for a in rep["aisles"] if not a["devices"]]
    assert not empty, "这些巷道没有设备：%s" % empty[:3]
    zero = [a["id"] for a in rep["aisles"] if a["capacity"] <= 0]
    assert not zero, "这些巷道能力为 0：%s" % zero[:3]
    assert rep["totalCapacity"] > 0


def test_device_counts_are_fractional(rep):
    """12 台摊 16 条巷道 = 0.75 台/巷：小数必须保留，不能被截成 0。"""
    counts = {d["count"] for a in rep["aisles"] for d in a["devices"]}
    assert any(0 < c < 1 for c in counts), counts
    w_rows = [a for a in rep["aisles"] if a["warehouse"] == "W"]
    assert all(abs(a["devices"][0]["count"] - 0.75) < 1e-9 for a in w_rows[:5])


def test_cycle_time_matches_engine_policy(rep):
    """一单 = 三段行程 + 取/放各一次作业 + 两次停靠。"""
    cap = jload(CONFIG / "capacity_spec.json")
    a = next(x for x in rep["aisles"] if x["warehouse"] == "W")
    v = jload(CONFIG / "equipment_spec.json")["types"]["agv_latent_rmfs"]["speed_mps"]
    d_bar = cm.avg_travel_distance(a["length"], cap["demand"]["distFactor"])
    expect = int(cap["travel_policy"]["legs"]) * d_bar / v \
        + 2 * cap["handling"]["station_s"] + 2 * cap["handling"]["dwell_s"]
    assert a["devices"][0]["cycle_s"] == pytest.approx(expect, abs=1e-6)


def test_cross_validation_within_tolerance(rep):
    """解析能力 vs 仿真能力：容差内。数值必须来自实测，不是写死的。"""
    tol = float(jload(CONFIG / "capacity_spec.json")
                ["cross_validation"]["sim_vs_analytical_tolerance_pct"])
    des = cx.measure_des("baseline")
    ok, detail, problems = cx.compare(rep, des, tol_pct=tol)
    assert ok, problems
    assert 0 < detail["deviation_pct"] <= tol
    assert detail["vehicles"] == 21


def test_cross_validation_can_fail(rep):
    """把周期改成 1/3，门必须红——否则这道门只是打印数字。"""
    des = cx.measure_des("baseline")
    ok, detail, problems = cx.compare(rep, des, tol_pct=15.0,
                                      cycle_override_s=des["mean_cycle_s"] / 3.0)
    assert not ok
    assert detail["deviation_pct"] > 15.0
    assert problems
