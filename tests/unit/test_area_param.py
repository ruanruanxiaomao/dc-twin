# -*- coding: utf-8 -*-
"""R8.6：厂房面积参数化。

三条都要，缺一不可：

1. 面积一变，**导航格必须跟着变**（壳体变了网格不变，等于只换了张皮）；
2. 面积一变，货架/货位**不许丢**（放大反而更少，说明坐标是绝对写死的）；
3. 缩到装不下，**必须拒绝**（退出码非 0 + L2/L3/L6 报 issue），不许当告警放过。
"""
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import check_area_param as cap   # noqa: E402
import _subproc                  # noqa: E402


def test_area_gate_passes():
    r = _subproc.run(["scripts/check_area_param.py", "--quiet"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "AREA_PARAM_OK" in (r.stdout or "")


def test_area_gate_self_test():
    r = _subproc.run(["scripts/check_area_param.py", "--self-test"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "AREA_SELF_TEST PASS" in (r.stdout or "")


def test_no_change_is_caught():
    """同一份布局自比必须判红：否则"放大后网格变了"只是恒等式。"""
    import json
    lay = json.loads((ROOT / "out" / "exp" / "area" / "base_layout.json")
                     .read_text(encoding="utf-8"))
    ok, _detail, problems = cap.check_param(lay, lay)
    assert not ok and problems


def test_silent_shrink_is_caught():
    ok, _detail, _problems = cap.check_shrink(0, {"issues": []})
    assert not ok


def _bays_file(tag):
    import json
    return json.loads((ROOT / "out" / "exp" / "area" / ("%s_layout.json" % tag))
                      .read_text(encoding="utf-8"))


def test_width_axis_gain_lands_on_east_column_only():
    """R8.10：宽度 +20 的跨数增量必须全落在东列，左列一格不动。"""
    narrow, wide = _bays_file("bays_narrow"), _bays_file("bays_wide")
    ok, detail, problems = cap.check_bays(0, wide, {"issues": []}, narrow, None)
    assert ok, problems
    assert detail["east_gain"] and all(v > 0 for v in detail["east_gain"].values())
    assert all(v == 0 for v in detail["west_delta"].values())


def test_width_axis_no_gain_is_caught():
    """同宽对照：开关打开但宽度没变，门必须判红（否则门只认开关状态）。"""
    narrow = _bays_file("bays_narrow")
    ok, _detail, problems = cap.check_bays(0, narrow, {"issues": []}, narrow, None)
    assert not ok and problems
