"""关卡判定（P4 LEVEL_OK / ADR-GAME-07）。

要钉四件事：
  1. 达标 → 胜；不达标 → 负（性质完全不同：一个是"玩得好不好"，一个是"引擎坏没坏"）
  2. 破产 → 必负，且不会被"利润刚好为正"掩盖
  3. 星级按规则算，不是按"打赢了就给满星"
  4. 未解锁关卡不可进（元进度真的起作用）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import game_economy as ge  # noqa: E402
import game_levels as gl  # noqa: E402
import sim_engine as se  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def make_sim(tmp_path, scenario="baseline", capital=500000, level=None):
    sim = se.Simulation(
        ROOT / "config" / "scenarios" / ("%s.json" % scenario),
        jload(ROOT / "config" / "sim_spec.json"),
        jload(ROOT / "config" / "fleet_spec.json"),
        jload(ROOT / "config" / "demand_spec.json"),
        jload(ROOT / "data" / "layout.json"),
        jload(ROOT / "data" / "nav_grid.json"),
        jload(ROOT / "data" / "config.json"),
        out_dir=tmp_path / "run",
    )
    ge.attach(sim, level, initial_capital=capital)
    return sim


@pytest.fixture(autouse=True)
def isolated_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(gl, "SAVES", tmp_path / "saves")
    monkeypatch.setattr(gl, "META", tmp_path / "saves" / "meta.json")


# ------------------------------------------------------------------ 配置
def test_all_levels_are_valid():
    lvs = gl.list_levels()
    assert len(lvs) >= 4, "至少要有 4 关才能验证难度曲线"
    for lv in lvs:
        assert lv.get("ok", True) is not False, lv


def test_first_level_is_always_unlocked():
    assert gl.is_unlocked("level_01") is True


def test_later_levels_are_locked_initially():
    assert gl.is_unlocked("level_02") is False


def test_winning_level_one_unlocks_level_two(tmp_path):
    lv = gl.load_level("level_01")
    sim = make_sim(tmp_path, "baseline", lv["initial_capital"], lv)
    sim.run()
    res = gl.judge(sim, lv)
    assert res["won"], res
    assert "level_02" in res["unlocks"]
    assert gl.is_unlocked("level_02") is True


# ------------------------------------------------------------------ 判定
def test_objective_failure_is_a_loss(tmp_path):
    """把门槛抬到离谱的高度 → 必须判负，而不是"跑完了就算赢"。"""
    lv = gl.load_level("level_01")
    lv = json.loads(json.dumps(lv))
    lv["objectives"][0]["value"] = 999999      # 利润门槛不可能达到
    sim = make_sim(tmp_path, "baseline", lv["initial_capital"], lv)
    sim.run()
    res = gl.judge(sim, lv)
    assert res["won"] is False
    assert res["stars"] == 0
    assert res["unlocks"] == []


def test_bankruptcy_is_a_loss_even_with_good_metrics(tmp_path):
    """利润达标但现金被打穿 → 必须判负（现金流才是生死线）。"""
    lv = gl.load_level("level_01")
    lv = json.loads(json.dumps(lv))
    for o in lv["objectives"]:
        if o["primary"]:
            o["value"] = -10 ** 9                # 门槛放到最低，保证"指标达标"
    sim = make_sim(tmp_path, "baseline", capital=100000, level=lv)
    sim.economy.grace_s = 5.0
    sim.enqueue_command({"type": "BUY_AGV", "payload": {"wid": "W"}})
    sim.run()
    assert sim.economy.game_over, "测试前提不成立：没有破产"
    res = gl.judge(sim, lv)
    assert res["won"] is False
    assert res["bankrupt"] is True


def test_stars_are_rule_based(tmp_path):
    """一星与三星必须能区分开：星级是规则算出来的，不是"赢了就给三颗"。"""
    lv = gl.load_level("level_01")
    sim = make_sim(tmp_path, "baseline", lv["initial_capital"], lv)
    sim.run()
    res = gl.judge(sim, lv)
    assert res["won"]
    assert res["stars"] >= 1
    assert 1 <= res["stars"] <= 3

    # 把一星门槛设到当前水平以下、三星门槛设到天上 → 只能拿 1 星
    hard = json.loads(json.dumps(lv))
    hard["stars"] = [
        {"stars": 1, "require": [{"metric": "operating_profit", "op": ">=", "value": -1e9}]},
        {"stars": 3, "require": [{"metric": "operating_profit", "op": ">=", "value": 1e9}]},
    ]
    sim2 = make_sim(tmp_path / "b", "baseline", lv["initial_capital"], hard)
    sim2.run()
    res2 = gl.judge(sim2, hard)
    assert res2["stars"] == 1, "三星门槛高到不可能，却给了 %d 星" % res2["stars"]


def test_meta_progress_keeps_best_result(tmp_path):
    lv = gl.load_level("level_01")
    sim = make_sim(tmp_path, "baseline", lv["initial_capital"], lv)
    sim.run()
    first = gl.judge(sim, lv)
    meta = gl.load_meta()
    assert meta["levels"]["level_01"]["wins"] == first["wins"] if "wins" in first else True
    best = meta["levels"]["level_01"]["stars"]

    # 再打一次更差的成绩，星级不许被拉低
    worse = json.loads(json.dumps(lv))
    worse["stars"] = [{"stars": 1, "require": [{"metric": "operating_profit", "op": ">=", "value": -1e9}]}]
    sim2 = make_sim(tmp_path / "c", "baseline", lv["initial_capital"], worse)
    sim2.run()
    gl.judge(sim2, worse)
    assert gl.load_meta()["levels"]["level_01"]["stars"] == max(best, 1)


# ------------------------------------------------------------------ 指标解析
def test_unknown_metric_is_rejected_not_ignored():
    sim = None
    with pytest.raises(gl.LevelError, match="未知指标"):
        gl.metric_value(type("S", (), {"metrics": {"metrics": {}}, "economy": None})(),
                        "no_such_metric")


def test_level_schema_exists_and_is_valid_json():
    p = ROOT / "config" / "schemas" / "level.schema.json"
    assert p.exists()
    d = jload(p)
    assert d["type"] == "object"
    assert "objectives" in d["required"]
