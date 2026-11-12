"""场景 overrides 白名单必须与引擎取值同源（ADR-GAME-08，风险 R10 的回归钉）。

背景事实：引擎 `__init__` 读 `dispatch.policy` / `battery.charge_policy` /
`dispatch.battery_reserve_pct`，而 `validate_scenario` 的白名单原本只有 5 个键，
三个策略键全被判非法。结果是"引擎认识、玩家存不进"——功能静默缺失，不是报错。

这里正负两向都钉：合法值必须放行（否则改回去就红），非法值必须报 issue
（否则白名单变成"随便写什么都行"，校验等于没有）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scenario_tools as st  # noqa: E402
import sim_engine as se  # noqa: E402


def base_scenario():
    return json.loads((ROOT / "config" / "scenarios" / "baseline.json")
                      .read_text(encoding="utf-8"))


def probe(i, key, value):
    d = copy.deepcopy(base_scenario())
    d["id"] = "probe-%02d" % i
    d["overrides"] = {key: value}
    return st.validate_scenario(d)


@pytest.mark.parametrize("i,key,value", [
    (0, "dispatch.policy", "highest_soc"),
    (1, "battery.charge_policy", "threshold"),
    (2, "dispatch.battery_reserve_pct", 30),
    (3, "dispatch.battery_reserve_pct", 0),
    (4, "dispatch.battery_reserve_pct", 95),
])
def test_engine_consumed_overrides_are_accepted(i, key, value):
    issues, _ = probe(i, key, value)
    assert not issues, "引擎会读 %s，校验却拒绝：%s" % (key, issues)


@pytest.mark.parametrize("i,key,value", [
    (10, "dispatch.policy", "random_walk"),
    (11, "dispatch.policy", None),
    (12, "battery.charge_policy", "always"),
    (13, "dispatch.battery_reserve_pct", 150),
    (14, "dispatch.battery_reserve_pct", -1),
    (15, "dispatch.reserve_pct", 20),          # 拼错键名必须被拦
])
def test_invalid_override_values_are_rejected(i, key, value):
    issues, _ = probe(i, key, value)
    assert issues, "非法取值 %s=%r 居然通过了校验" % (key, value)


def test_whitelist_is_derived_from_engine_not_copied():
    """取值来源必须同源：引擎加一种策略，校验自动跟上，不需要改第二处。"""
    assert st.DISPATCH_POLICIES == tuple(sorted(se.DISPATCH_RULES))
    assert st.CHARGE_POLICY_NAMES == tuple(se.CHARGE_POLICIES)


def test_accepted_policy_actually_changes_behaviour(tmp_path):
    """放行只是第一步：写进场景后，引擎必须真的按它跑。

    只测"校验通过"是不够的——校验通过而引擎忽略该键，等于白名单假开。
    """
    d = base_scenario()
    d["id"] = "probe-policy"
    d["overrides"] = {"dispatch.policy": "farthest_first"}
    p = tmp_path / "sc.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def jload(x):
        return json.loads(Path(x).read_text(encoding="utf-8"))

    sim = se.Simulation(p, jload(ROOT / "config/sim_spec.json"),
                        jload(ROOT / "config/fleet_spec.json"),
                        jload(ROOT / "config/demand_spec.json"),
                        jload(ROOT / "data/layout.json"),
                        jload(ROOT / "data/nav_grid.json"),
                        jload(ROOT / "config/../data/config.json"),
                        out_dir=tmp_path / "run")
    assert sim.dispatch_rule == "farthest_first"
