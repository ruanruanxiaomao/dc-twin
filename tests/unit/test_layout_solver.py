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


# ---------------- R8.10 宽度轴 ----------------

def test_bays_blocked_by_east_wall_or_spawn():
    """跨数的上限必须能说出"被谁挡住"：右列被东墙/待命位挡，左列被兄弟组挡。"""
    wh = _wh()
    groups = {g["zone"]: g for g in wh["racks"]}
    spawns = [{"x": float(p[0]), "id": "spawn_%02d" % (i + 1)}
              for i, p in enumerate(wh["spawns"]["points"])]
    a = ls.free_bays_for_group(wh, groups["A"], 3.0, 0.6, siblings=wh["racks"],
                               rows=4, rack_depth=1.2, aisle=4.0, blockers=spawns)
    b = ls.free_bays_for_group(wh, groups["B"], 3.0, 0.6, siblings=wh["racks"],
                               rows=4, rack_depth=1.2, aisle=4.0, blockers=spawns)
    assert a["blocked_by"].startswith("sibling_zone_B")
    assert b["blocked_by"].startswith("spawn_")
    assert a["limit_x"] < b["limit_x"]


def test_more_width_more_bays_only_on_east_column():
    """宽度 +20：东列跨数涨，左列一格不动（红利不该落到被兄弟组挡住的列）。"""
    wh = _wh()
    groups = {g["zone"]: g for g in wh["racks"]}
    spawns = [{"x": float(p[0]), "id": "spawn_%02d" % (i + 1)}
              for i, p in enumerate(wh["spawns"]["points"])]
    kw = dict(siblings=wh["racks"], rows=4, rack_depth=1.2, aisle=4.0, blockers=spawns)
    before = {z: ls.free_bays_for_group(wh, g, 3.0, 0.6, **kw)["bays"]
              for z, g in groups.items()}
    wh["size"]["width"] = 120.0
    w100 = {p[0] for p in wh["spawns"]["points"]}          # 待命位还没移：会被挡住
    assert max(w100) == pytest.approx(98.25)
    after = {z: ls.free_bays_for_group(wh, g, 3.0, 0.6, **kw)["bays"]
             for z, g in groups.items()}
    # 厂房变宽了，但待命位还钉在 98.25 —— 右列**一跨都不该涨**（挡得住才是对的）
    assert after["B"] == before["B"]
    assert after["A"] == before["A"]
    # 待命位跟着东墙走了，才拿得到完整宽度红利
    wh["area_baseline"] = {"width": 100}
    ls.shift_spawns_x(wh, 120)
    spawns2 = [{"x": float(p[0]), "id": "spawn_%02d" % (i + 1)}
               for i, p in enumerate(wh["spawns"]["points"])]
    kw2 = dict(kw, blockers=spawns2)
    freed = {z: ls.free_bays_for_group(wh, g, 3.0, 0.6, **kw2)["bays"]
             for z, g in groups.items()}
    assert freed["B"] > after["B"] + 3
    assert freed["A"] == before["A"]


def test_spawn_x_shift_is_idempotent_and_reversible():
    """待命位锚东墙：推两次不叠加，改回原宽能回到原坐标。"""
    wh = _wh()
    east0 = max(p[0] for p in wh["spawns"]["points"])
    west0 = min(p[0] for p in wh["spawns"]["points"])
    wh["area_baseline"] = {"width": 100}
    ls.shift_spawns_x(wh, 120)
    east1 = sorted({p[0] for p in wh["spawns"]["points"]})
    ls.shift_spawns_x(wh, 120)
    east2 = sorted({p[0] for p in wh["spawns"]["points"]})
    assert east1 == east2, "幂等：第二次不能把点再推 20 m"
    assert max(east2) == pytest.approx(east0 + 20)
    assert min(p[0] for p in wh["spawns"]["points"]) == pytest.approx(west0)
    ls.shift_spawns_x(wh, 100)
    assert max(p[0] for p in wh["spawns"]["points"]) == pytest.approx(east0)


def test_no_width_baseline_means_no_shift():
    """没有 area_baseline.width 就不推断（与 shift_band 同一条纪律）。"""
    wh = _wh()
    before = [list(p) for p in wh["spawns"]["points"]]
    _wh2, rec = ls.shift_spawns_x(wh, 140)
    assert rec["moved"] == 0 and rec["dx"] == 0.0
    assert wh["spawns"]["points"] == before


def test_orientation_z_bays_raises():
    """宽度轴只实现 orientation='x'：别的排布必须报错，不许猜方向。"""
    wh = _wh()
    g = copy.deepcopy(wh["racks"][0])
    g["orientation"] = "z"
    with pytest.raises(ls.SolveError):
        ls.free_bays_for_group(wh, g, 3.0, 0.6, siblings=[g], rows=4)


def test_cramped_bays_raises():
    """起点已经贴到东墙、连一跨都放不下时必须抛错。"""
    wh = _wh()
    g = copy.deepcopy(wh["racks"][0])
    g["origin"] = [96, 10]     # start_x=98，limit=99，剩 1 m < 3.6 m 节距
    with pytest.raises(ls.SolveError):
        ls.free_bays_for_group(wh, g, 3.0, 0.6, siblings=[g], rows=4)
