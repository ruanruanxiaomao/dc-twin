# -*- coding: utf-8 -*-
"""R3 设备类型层：车型库、多车型车队、AST 耦合、按车型采购。

这些用例守的都是"看起来对、其实错"的地方：

* 混编车队里按**统一**车型参数跑车（电量/速度/间隔都退回小车）；
* 采购把 capex 记进利润表（现金对、利润错）；
* AST 耦合"打开"了但其实没生效（或者关了还生效）；
* 快照丢了 `model_id`，续玩后全车队退回默认车型而台数一台不少。
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import equipment_ref as er          # noqa: E402
import spec_layout as sl            # noqa: E402
import sim_engine as se             # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


CONFIG = ROOT / "config"
DATA = ROOT / "data"


def specs(fleet=None):
    return (jload(CONFIG / "sim_spec.json"),
            fleet if fleet is not None else jload(CONFIG / "fleet_spec.json"),
            jload(CONFIG / "demand_spec.json"),
            jload(DATA / "layout.json"), jload(DATA / "nav_grid.json"),
            jload(DATA / "config.json"))


def make_sim(tmp_path, fleet=None, seed=None):
    sc = jload(CONFIG / "scenarios" / "baseline.json")
    if seed is not None:
        sc = dict(sc, seed=seed)
    p = Path(tmp_path) / "scenario.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sc), encoding="utf-8")
    return se.Simulation(p, *specs(fleet), out_dir=Path(tmp_path) / "run")


def mixed_fleet():
    """W 仓 4 台小车 + 8 台大车，其余仓照旧（总台数不变，便于比对）。"""
    f = jload(CONFIG / "fleet_spec.json")
    f["deployment"] = [
        {"warehouse": "W", "model": "AMR-250", "count": 4, "home_policy": "fixed"},
        {"warehouse": "W", "model": "AMR-500", "count": 8, "home_policy": "fixed"},
        {"warehouse": "F1", "model": "AMR-250", "count": 3, "home_policy": "fixed"},
        {"warehouse": "F2", "model": "AMR-250", "count": 3, "home_policy": "fixed"},
        {"warehouse": "F3", "model": "AMR-250", "count": 3, "home_policy": "fixed"},
    ]
    return f


# ------------------------------------------------------------------ 车型库
def test_model_index_rejects_missing_fields():
    f = jload(CONFIG / "fleet_spec.json")
    bad = json.loads(json.dumps(f))
    del bad["models"][0]["battery_kwh"]
    with pytest.raises(ValueError, match="缺字段"):
        er.model_index(bad)


def test_model_index_rejects_duplicate_id():
    f = jload(CONFIG / "fleet_spec.json")
    bad = json.loads(json.dumps(f))
    bad["models"].append(dict(bad["models"][0]))
    with pytest.raises(ValueError, match="重复"):
        er.model_index(bad)


def test_deployment_expands_in_order():
    dm = er.deployment_models(mixed_fleet())
    assert [m["id"] for m in dm["W"]] == ["AMR-250"] * 4 + ["AMR-500"] * 8
    assert er.deployed_model_ids(mixed_fleet()) == ["AMR-250", "AMR-500"]
    assert er.deployed_model_ids(jload(CONFIG / "fleet_spec.json")) == ["AMR-250"]


def test_deployment_rejects_unknown_model():
    f = jload(CONFIG / "fleet_spec.json")
    f["deployment"][0]["model"] = "NO-SUCH"
    with pytest.raises(ValueError, match="不存在的车型"):
        er.deployment_models(f)


def test_equipment_types_are_consistent():
    assert er.check_against_equipment(jload(CONFIG / "fleet_spec.json")) == []


# ------------------------------------------------------------------ 多车型车队
def test_mixed_fleet_gets_per_model_parameters(tmp_path):
    sim = make_sim(tmp_path, fleet=mixed_fleet())
    small = [r for r in sim.robots.values() if r.model_id == "AMR-250"]
    big = [r for r in sim.robots.values() if r.model_id == "AMR-500"]
    # W 的 4 台 + F1/F2/F3 各 3 台 = 13 台小车；W 的 8 台大车
    assert len(small) == 13 and len(big) == 8, (len(small), len(big))
    # 电池容量、速度、能耗必须按车型不同——统一值就是"多车型"没生效
    assert {r.cap for r in small} == {1.6}
    assert {r.cap for r in big} == {3.2}
    assert big[0].speed < small[0].speed
    # 大车外廓更大 → 最小安全间隔必须更大，否则混编时大车贴着小车走
    assert big[0].min_sep > small[0].min_sep
    sim.log.close()


def test_mixed_fleet_state_labels_every_robot(tmp_path):
    sim = make_sim(tmp_path, fleet=mixed_fleet())
    sim.run()
    st = jload(sim.out / "sim_state.json")
    assert all("model" in v for v in st["robots"].values())
    assert {v["model"] for v in st["robots"].values()} == {"AMR-250", "AMR-500"}


def test_single_model_state_has_no_model_key(tmp_path):
    """单车型不许输出 model 键：那是噪音，还会改掉金标准字节。"""
    sim = make_sim(tmp_path)
    sim.run()
    st = jload(sim.out / "sim_state.json")
    assert not any("model" in v for v in st["robots"].values())


def test_mixed_fleet_is_deterministic(tmp_path):
    a = make_sim(tmp_path / "a", fleet=mixed_fleet())
    b = make_sim(tmp_path / "b", fleet=mixed_fleet())
    a.run()
    b.run()
    ka = [(rid, r.model_id, r.cap, round(r.pos[0], 6), round(r.pos[1], 6))
          for rid, r in sorted(a.robots.items())]
    kb = [(rid, r.model_id, r.cap, round(r.pos[0], 6), round(r.pos[1], 6))
          for rid, r in sorted(b.robots.items())]
    assert ka == kb


def test_mixed_fleet_snapshot_keeps_models(tmp_path):
    sim = make_sim(tmp_path, fleet=mixed_fleet())
    for _ in range(300):
        if sim.step():
            break
    snap = sim.snapshot()
    sim.log.close()
    assert snap["schema_version"] == "4.0"
    assert snap["robots"][sorted(snap["robots"])[0]]["model_id"] in ("AMR-250", "AMR-500")

    # 必须恢复到**同一个** out_dir：快照里的 seq 要与那份事件日志对得上，
    # 换个目录等于"用空日志续一段有历史的存档"，restore 会（正确地）拒绝。
    restored = se.Simulation.restore(
        snap, CONFIG / "scenarios" / "baseline.json", *specs(mixed_fleet()),
        out_dir=Path(tmp_path) / "run")
    assert {r.model_id for r in restored.robots.values()} == {"AMR-250", "AMR-500"}
    for r in restored.robots.values():
        assert r.cap == (1.6 if r.model_id == "AMR-250" else 3.2)
    restored.log.close()


def test_snapshot_with_unknown_model_is_rejected(tmp_path):
    sim = make_sim(tmp_path, fleet=mixed_fleet())
    sim.step()
    snap = sim.snapshot()
    sim.log.close()
    rid = sorted(snap["robots"])[0]
    snap["robots"][rid]["model_id"] = "GONE-MODEL"
    with pytest.raises(ValueError, match="不在当前车队配置"):
        se.Simulation.restore(snap, CONFIG / "scenarios" / "baseline.json",
                              *specs(mixed_fleet()), out_dir=Path(tmp_path) / "run2")


def test_old_snapshot_version_is_rejected(tmp_path):
    sim = make_sim(tmp_path)
    sim.step()
    snap = sim.snapshot()
    sim.log.close()
    snap["schema_version"] = "3.1"
    with pytest.raises(ValueError, match="版本不符"):
        se.Simulation.restore(snap, CONFIG / "scenarios" / "baseline.json",
                              *specs(), out_dir=Path(tmp_path) / "run2")


# ------------------------------------------------------------------ AST 耦合
def test_ast_off_keeps_default_aisle():
    d = jload(CONFIG / "layout_spec.json")["defaults"]
    r = sl.aisle_width_for({"rack": {"equipment_type": "forklift_vna"}}, d, ast_enabled=False)
    assert r["source"] == "default"
    assert r["width"] == pytest.approx(d["aisle"]["cross"])


def test_ast_on_narrows_by_equipment():
    d = jload(CONFIG / "layout_spec.json")["defaults"]
    vna = sl.aisle_width_for({"rack": {"equipment_type": "forklift_vna"}}, d, ast_enabled=True)
    cb = sl.aisle_width_for({"rack": {"equipment_type": "forklift_counterbalance"}},
                            d, ast_enabled=True)
    assert vna["source"] == "ast" and cb["source"] == "ast"
    assert vna["width"] == pytest.approx(1.8 / 1.0 + 0.2)   # 2.0
    assert cb["width"] == pytest.approx(3.4 + 0.2)          # 3.6
    assert vna["width"] < cb["width"]


def test_ast_below_min_aisle_is_clamped_and_reported():
    d = jload(CONFIG / "layout_spec.json")["defaults"]
    r = sl.aisle_width_for({"rack": {"equipment_type": "forklift_vna"}}, d,
                           ast_enabled=True, ast_mm={"forklift_vna": 600})
    assert r["raw"] < r["min_aisle"]
    assert r["clamped"] is True
    assert r["width"] == pytest.approx(r["min_aisle"])


def test_ast_overrides_explicit_but_explicit_wins_without_type():
    """声明了设备类型 = 允许被它驱动；没声明时显式值才是最终值。

    出厂的每个货架组都写着 aisle_width: 4.0，如果显式值永远压过 AST，
    那"开启耦合"就是个空开关——第一版正是这么写的。
    """
    d = jload(CONFIG / "layout_spec.json")["defaults"]
    driven = sl.aisle_width_for({"aisle_width": 5.5, "rack": {"equipment_type": "forklift_vna"}},
                                d, ast_enabled=True)
    assert driven["source"] == "ast" and driven["width"] == pytest.approx(2.0)
    assert driven["explicit_width"] == pytest.approx(5.5)      # 被覆盖的值要留痕
    plain = sl.aisle_width_for({"aisle_width": 5.5, "rack": {}}, d, ast_enabled=True)
    assert plain["source"] == "explicit" and plain["width"] == pytest.approx(5.5)


def test_ast_enabled_without_any_equipment_type_raises():
    """开了耦合却没有任何组声明设备类型 → 必须报错。

    单个组不声明是合法的（该组豁免）；**全局一个都没有**才是配置错误——
    那时开关是开的、行为一点没变，还全绿，是最难发现的一类。
    """
    import copy
    spec = jload(CONFIG / "layout_spec.json")
    bad = copy.deepcopy(spec)
    bad["options"] = {"ast_drives_aisle_width": True}
    with pytest.raises(ValueError, match="equipment_type"):
        sl.expand(bad)


def test_ast_expansion_changes_pitch_not_location_count():
    import copy
    base = jload(CONFIG / "layout_spec.json")
    on = copy.deepcopy(base)
    on["options"] = {"ast_drives_aisle_width": True}
    on["warehouses"][0]["racks"][0].setdefault("rack", {})["equipment_type"] = "forklift_vna"
    off_lay = sl.expand(copy.deepcopy(base))
    on_lay = sl.expand(on)
    n_off = sum(1 for w in off_lay["warehouses"] for loc_ in w["locations"])
    n_on = sum(1 for w in on_lay["warehouses"] for loc_ in w["locations"])
    assert n_off == n_on
    z_off = sorted(round(loc_["access_point"][1], 4)
                   for w in off_lay["warehouses"] for loc_ in w["locations"]
                   if (loc_.get("rack_id") or "").startswith("RACK-W"))
    z_on = sorted(round(loc_["access_point"][1], 4)
                  for w in on_lay["warehouses"] for loc_ in w["locations"]
                  if (loc_.get("rack_id") or "").startswith("RACK-W"))
    assert z_off != z_on, "开启了 AST 耦合，货架行距却没变——耦合没生效"


# ------------------------------------------------------------------ 采购
def test_buy_equipment_charges_its_own_price():
    import game_economy as ge
    tmp = Path(tempfile.mkdtemp(prefix="equiptest_"))
    sim = make_sim(tmp)
    econ = ge.attach(sim, None, initial_capital=500000)
    sim.enqueue_command({"type": "BUY_EQUIPMENT",
                         "payload": {"wid": "W", "model": "AMR-500"}})
    sim.run()
    assert econ.capex == pytest.approx(168000.0)
    assert any(r.model_id == "AMR-500" for r in sim.robots.values())
    ok, resid, _ = econ.conservation()
    assert ok and abs(resid) < 1e-6
    # capex 不进经营利润
    assert econ.operating_profit() == pytest.approx(
        econ.revenue + econ.salvage - econ.variable - econ.fixed - econ.holding)


def test_buy_equipment_rejects_unknown_model():
    tmp = Path(tempfile.mkdtemp(prefix="equiptest2_"))
    sim = make_sim(tmp)
    with pytest.raises(ValueError, match="没有这个车型"):
        sim.enqueue_command({"type": "BUY_EQUIPMENT",
                             "payload": {"wid": "W", "model": "NO-SUCH"}})
    with pytest.raises(ValueError, match="没有这个仓"):
        sim.enqueue_command({"type": "BUY_EQUIPMENT",
                             "payload": {"wid": "NOPE", "model": "AMR-250"}})
    sim.log.close()


def test_buy_agv_alias_still_emits_legacy_event():
    tmp = Path(tempfile.mkdtemp(prefix="equiptest3_"))
    sim = make_sim(tmp)
    sim.enqueue_command({"type": "BUY_AGV", "payload": {"wid": "W"}})
    sim.run()
    assert sim.log.counts.get("CMD_BUY_AGV") == 1
    assert not sim.log.counts.get("CMD_BUY_EQUIPMENT")
