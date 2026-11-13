# -*- coding: utf-8 -*-
"""R5：库容密度重排（进深预算 → 行数）。

守三件事：

1. **默认不动**：不开选项时布局与出厂逐项一致（金标准不能被一个"优化"碰掉）。
2. **密度是真的**：同一进深预算下，巷道窄的设备排得更多，且两边都零违规
   ——密度是合规拿到的，不是靠越界挤出来的。
3. **越界会报**：预算过大、或巷道窄到 AGV 站不进去，L2/L3/L6/L8 必须给 issue。
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import spec_layout as sl            # noqa: E402

CONFIG = ROOT / "config"
DATA = ROOT / "data"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def spec(equipment_type=None, budget=20.0, reflow=True, ast=True):
    s = copy.deepcopy(jload(CONFIG / "layout_spec.json"))
    if ast or reflow:
        s["options"] = {}
        if ast:
            s["options"]["ast_drives_aisle_width"] = True
        if reflow:
            s["options"]["reflow_rows"] = True
    g = s["warehouses"][0]["racks"][0]
    if budget is not None:
        g["depth_budget"] = budget
    if equipment_type:
        g.setdefault("rack", {})["equipment_type"] = equipment_type
    return s


def shelf_count(lay, wid="W"):
    w = next(x for x in lay["warehouses"] if x["id"] == wid)
    return len([loc_ for loc_ in w["locations"] if loc_.get("kind") == "SHELF"])


def rows_of(lay, wid="W"):
    w = next(x for x in lay["warehouses"] if x["id"] == wid)
    return (w.get("reflow") or [{}])[0].get("rows_derived")


# ---------------------------------------------------------------- 纯算术
def test_reflow_rows_arithmetic():
    assert sl.reflow_rows(20.0, 1.2, 3.6) == (4, 15.6)
    assert sl.reflow_rows(20.0, 1.2, 3.1) == (5, 18.4)
    assert sl.reflow_rows(20.0, 1.2, 2.0) == (6, 17.2)
    # 只有一行时不需要尾部巷道
    assert sl.reflow_rows(1.2, 1.2, 4.0) == (1, 1.2)


def test_reflow_rows_rejects_impossible_budget():
    with pytest.raises(ValueError, match="放不下"):
        sl.reflow_rows(1.0, 1.2, 3.0)
    with pytest.raises(ValueError, match="进深"):
        sl.reflow_rows(10.0, 0, 3.0)
    with pytest.raises(ValueError, match="巷道宽"):
        sl.reflow_rows(10.0, 1.2, -1.0)


# ---------------------------------------------------------------- 默认关闭
def test_default_layout_is_untouched():
    lay = sl.expand(jload(CONFIG / "layout_spec.json"))
    w = lay["warehouses"][0]
    shipped = jload(DATA / "layout.json")["warehouses"][0]
    assert "reflow" not in w, "默认态不该出现重排记录"
    assert len(w["racks"]) == len(shipped["racks"])
    assert len([loc_ for loc_ in w["locations"] if loc_.get("kind") == "SHELF"]) == \
        len([loc_ for loc_ in shipped["locations"] if loc_.get("kind") == "SHELF"])


# ---------------------------------------------------------------- 密度
def test_narrower_aisle_yields_more_rows_same_budget():
    cb = sl.expand(spec("forklift_counterbalance"))
    reach = sl.expand(spec("forklift_reach"))
    assert rows_of(cb) == 4 and rows_of(reach) == 5
    assert shelf_count(reach) > shelf_count(cb)
    assert shelf_count(cb) == 320 and shelf_count(reach) == 340


def test_density_is_legal_not_squeezed():
    """密度必须是合规拿到的：两边 0 违规。"""
    for eq in ("forklift_counterbalance", "forklift_reach"):
        s = spec(eq)
        lay = sl.expand(s)
        issues = sl.validate(lay, s)[0]
        assert not issues, (eq, issues[:3])


def test_reflow_records_stay_within_budget():
    lay = sl.expand(spec("forklift_reach"))
    for rec in lay["warehouses"][0]["reflow"]:
        assert rec["depth_used"] <= rec["budget"] + 1e-9
        assert rec["rows_derived"] >= 1


# ---------------------------------------------------------------- 越界
def test_over_budget_hits_boundary():
    """预算一路排到输送线上，必须报出来。"""
    s = spec("forklift_counterbalance", budget=26.0)
    lay = sl.expand(s)
    issues = sl.validate(lay, s)[0]
    assert issues, "越界却没有报任何 issue"
    assert any(code in ("L2", "L3", "L6") for code, _ in issues)


def test_vna_aisle_is_infeasible_for_this_agv():
    """1.8 m 的 AST → 2.0 m 巷道；本机 AGV（R≈0.73 m）站不进去，必须报 L6。

    这不是"实现失败"，是真实的工程结论：窄巷道设备要配窄巷道车，
    用顶升式 AMR 跑 2.0 m 巷道会让访问点落在货架膨胀圈里。
    """
    s = spec("forklift_vna")
    lay = sl.expand(s)
    w = lay["warehouses"][0]
    issues = sl.validate(lay, s)[0]
    assert any(code == "L6" for code, _ in issues)
    # 访问点被夹到巷道中线这件事必须留痕，不能静默改几何
    assert w.get("ap_clamped"), "夹紧了 AP 却没记录"


def test_ap_offset_only_clamped_when_aisle_is_narrow():
    """默认 4.0 m 巷道不该触发夹紧（否则会改掉出厂几何）。"""
    lay = sl.expand(spec("forklift_counterbalance", budget=20.0))
    assert not lay["warehouses"][0].get("ap_clamped")
