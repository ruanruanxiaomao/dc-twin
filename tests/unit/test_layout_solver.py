# -*- coding: utf-8 -*-
"""R8.7 / D10b：功能带重排求解器（纯函数层）。

这一层没有 Blender、没有 rng、没有时钟，所以断言可以写得死一点：
给定输入必须给同一输出，且每种"想当然"的错误都要有对应用例钉住。

四条最容易做错的地方，逐条有测试：

1. **没有基线不许推断**——猜一个常量会把四个仓一起改布局（真踩过）；
2. **幂等**——求解器被调用两次不能把功能带推两次；
3. **取三个上限的最小值**——只看到功能带、看不到同列下一组，A 区会吃掉 C 区的地；
4. **放不下必须抛错**——静默返回一行是"算不出来就凑一个"。
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import layout_solver as ls   # noqa: E402


def _wh():
    spec = json.loads((ROOT / "config" / "layout_spec.json").read_text(encoding="utf-8"))
    return copy.deepcopy(spec["warehouses"][0])


def test_band_is_areas_stations_and_chargers():
    """功能带必须含充电位：漏了它，充电位访问点会被货架压住（实测那 1 条残留）。"""
    wh = _wh()
    band = ls.functional_band(wh)
    # stations/areas 在 62..67，充电位在 68.5（ap_dz=-2 → 66.5）
    assert band["z0"] == pytest.approx(62.0)
    assert band["z1"] >= 68.5


def test_no_baseline_means_no_shift():
    """没有 area_baseline 就不许推断原始进深。"""
    wh = _wh()
    out, rec = ls.shift_band(wh, 84)
    assert rec["shift"] == 0
    assert "area_baseline" in rec["note"]
    assert out["areas"][0]["rect"] == _wh()["areas"][0]["rect"]


def test_shift_moves_band_and_keeps_docks():
    """后墙后移 14 m，功能带跟着走 14 m；月台（锚南墙）一个数不动。"""
    wh = _wh()
    wh["area_baseline"] = {"depth": 70}
    docks_before = copy.deepcopy(wh["docks"])
    out, rec = ls.shift_band(wh, 84)
    assert rec["shift"] == pytest.approx(14.0)
    assert out["stations"][0]["rect"][1] == pytest.approx(76.0)
    assert out["docks"] == docks_before


def test_shift_is_idempotent():
    """跑两次不能推两次——门会重跑，翻倍会让所有断言失去意义。"""
    wh = _wh()
    wh["area_baseline"] = {"depth": 70}
    ls.shift_band(wh, 84)
    z1 = wh["stations"][0]["rect"][3]
    _out, rec2 = ls.shift_band(wh, 84)
    assert rec2["shift"] == pytest.approx(0.0)
    assert wh["stations"][0]["rect"][3] == pytest.approx(z1)


def test_shift_is_zero_at_baseline():
    """进深没变就不该有任何位移——默认布局必须逐字节不变。"""
    wh = _wh()
    wh["area_baseline"] = {"depth": 70}
    _out, rec = ls.shift_band(wh, 70)
    assert rec["shift"] == pytest.approx(0.0)


def test_limit_takes_sibling_into_account():
    """A 区的上限是 C 区起点，不是功能带——否则 A 会一路排到后墙吃掉 C 的地。"""
    wh = _wh()
    band = ls.functional_band(wh)
    groups = {g["zone"]: g for g in wh["racks"]}
    a = ls.free_depth_for_group(wh, groups["A"], 1.35, 4.0, band,
                                siblings=wh["racks"])
    c = ls.free_depth_for_group(wh, groups["C"], 1.35, 4.0, band,
                                siblings=wh["racks"])
    assert a["blocked_by"].startswith("sibling_zone_")
    assert c["blocked_by"].startswith("functional_band")
    assert a["limit_z"] < c["limit_z"]


def test_more_depth_more_budget():
    """面积变大 → 后一区的预算必须变大（这就是"面积变成库容"的那一跳）。"""
    wh = _wh()
    wh["area_baseline"] = {"depth": 70}
    band70 = ls.functional_band(wh)
    c = [g for g in wh["racks"] if g["zone"] == "C"][0]
    before = ls.free_depth_for_group(wh, c, 1.35, 4.0, band70, siblings=wh["racks"])
    ls.shift_band(wh, 84)
    band84 = ls.functional_band(wh)
    after = ls.free_depth_for_group(wh, c, 1.35, 4.0, band84, siblings=wh["racks"])
    assert after["budget"] > before["budget"] + 10


def test_cramped_group_raises():
    """起点已经越过上限时必须抛错，不许静默返回"一行"。"""
    wh = _wh()
    band = ls.functional_band(wh)
    g = copy.deepcopy([x for x in wh["racks"] if x["zone"] == "C"][0])
    g["origin"] = [4, 66]      # 起点进深 66+3=69，已经在功能带里
    with pytest.raises(ls.SolveError):
        ls.free_depth_for_group(wh, g, 1.35, 4.0, band, siblings=[g])
