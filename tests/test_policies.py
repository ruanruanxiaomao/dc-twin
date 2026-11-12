"""策略层：派单规则 / 充电策略要真的可换、可保存，而不是写死在引擎里。

为什么单独测：这两项以前硬编码在 `_assign` 和 `_run_robot` 里，
界面上改不了、场景里也存不下。抽成策略表以后，必须证明
  1. 未知策略名会立刻报错，而不是悄悄回落到默认（静默回落 = 实验结论作废）；
  2. 换规则真的换车——同一时刻同一订单，nearest 与 farthest 选中不同的车；
  3. 策略随场景 JSON 走：只改 overrides 就能改行为。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sim_engine as se  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def make_sim(tmp_path, name="baseline", overrides=None):
    """复制一个场景到临时目录，按需改 overrides，再构造（不运行）。"""
    sc = jload(ROOT / "config" / "scenarios" / ("%s.json" % name))
    sc["overrides"] = dict(sc.get("overrides") or {})
    sc["overrides"].update(overrides or {})
    p = tmp_path / ("sc-%08d.json" % (abs(hash(json.dumps(sc, sort_keys=True))) % 10 ** 8))
    p.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    return se.Simulation(
        p,
        jload(ROOT / "config" / "sim_spec.json"),
        jload(ROOT / "config" / "fleet_spec.json"),
        jload(ROOT / "config" / "demand_spec.json"),
        jload(ROOT / "data" / "layout.json"),
        jload(ROOT / "data" / "nav_grid.json"),
        jload(ROOT / "data" / "config.json"),
        out_dir=tmp_path / "run",
    )


def pick_order(sim, n=1):
    """一张仓内拣选单（货架→货架），绕开月台门，只测派单本身。"""
    shelves = sim.world.by_kind["W"]["SHELF"]
    src, dst = shelves[0], shelves[1]
    return {"order_id": "P-%02d" % n, "type": "PICK", "warehouse": "W",
            "pick_loc": src["id"], "drop_loc": dst["id"],
            "pick_xy": tuple(src["access_point"]), "drop_xy": tuple(dst["access_point"]),
            "t_arrive": 0.0, "priority": "P2", "deadline": 1e9, "reassignments": 0}


def assigned_agent(sim):
    for r in sim.robots.values():
        if r.order is not None:
            return r.id
    return None


# ------------------------------------------------------------------ 规则表
def test_unknown_policy_fails_loudly(tmp_path):
    with pytest.raises(ValueError):
        make_sim(tmp_path, overrides={"dispatch.policy": "nearest_frist"})  # 拼错
    with pytest.raises(ValueError):
        make_sim(tmp_path, overrides={"battery.charge_policy": "always"})


def test_policy_comes_from_scenario(tmp_path):
    sim = make_sim(tmp_path, overrides={"dispatch.policy": "highest_soc",
                                        "battery.charge_policy": "threshold",
                                        "dispatch.battery_reserve_pct": 33})
    assert sim.dispatch_rule == "highest_soc"
    assert sim.charge_policy == "threshold"
    assert sim.reserve_pct == 33.0


def test_dispatch_rule_picks_a_different_robot(tmp_path):
    near = make_sim(tmp_path, overrides={"dispatch.policy": "nearest_first"})
    far = make_sim(tmp_path, overrides={"dispatch.policy": "farthest_first"})
    for sim in (near, far):
        sim.queue = [pick_order(sim)]
        sim._assign(0.0)
    a, b = assigned_agent(near), assigned_agent(far)
    assert a and b, "两个策略都应派出车"
    assert a != b, "最近优先和最远优先选到了同一台车，说明规则没生效"
