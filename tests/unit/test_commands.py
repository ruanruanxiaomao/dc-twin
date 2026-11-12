"""命令队列（ADR-GAME-03 / 方案书 §4.5）。

要证的三件事，缺一件这一层就等于没有：
  1. **确定性**：同种子 + 同命令流跑两次，事件流逐字节一致（CMD_OK）；
  2. **未知即抛错**：未知命令、非法 payload、未知策略名一律 raise，不许静默回落
     ——静默回落会让"改了策略"和"没改"跑出同一份结果，A/B 直接作废；
  3. **登记表没有空档**：8 类命令要么真生效，要么明确 NotImplementedError。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sim_engine as se  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def make_sim(tmp_path, name="baseline"):
    return se.Simulation(
        ROOT / "config" / "scenarios" / ("%s.json" % name),
        jload(ROOT / "config" / "sim_spec.json"),
        jload(ROOT / "config" / "fleet_spec.json"),
        jload(ROOT / "config" / "demand_spec.json"),
        jload(ROOT / "data" / "layout.json"),
        jload(ROOT / "data" / "nav_grid.json"),
        jload(ROOT / "data" / "config.json"),
        out_dir=tmp_path / "run",
    )


#: 固定命令流：策略、需求、故障、买车各来一次。用绝对 tick 而不是"跑到某状态再发"，
#: 否则"两次跑一致"会被状态驱动掩盖。
COMMAND_SCRIPT = [
    (0, {"type": "SET_POLICY", "payload": {"dispatch.policy": "highest_soc"}}),
    (50, {"type": "SET_DEMAND", "payload": {"orders.per_hour": 60}}),
    (80, {"type": "BUY_AGV", "payload": {"wid": "F1"}}),
    (120, {"type": "INJECT_FAULT", "payload": {"type": "robot_down",
                                               "agent": "ANY_BUSY", "duration_s": 120}}),
    (200, {"type": "SET_POLICY", "payload": {"dispatch.policy": "nearest_first",
                                             "dispatch.battery_reserve_pct": 25}}),
]


def run_with_script(tmp_path, script, horizon=None):
    """按 tick 号投放命令。

    注意"投放"必须在 step() 之前：tick 0 的命令若等到第一次 step 之后再入队，
    会因为空闲跳 tick 直接跳到 horizon 而永远不生效（这个坑正是运行命令流时踩到的，
    所以这里用 flush() 把"到点的命令"在每次推进前一次性推入）。
    """
    sim = make_sim(tmp_path)
    if horizon is not None:
        sim.horizon = horizon
        sim.n_ticks = int(sim.horizon / sim.tick)
    pending = sorted(script)
    sim._init_run()

    def flush():
        while pending and pending[0][0] <= sim.tick_i:
            _, cmd = pending.pop(0)
            sim.enqueue_command(cmd)

    flush()
    while not sim.step():
        flush()
    flush()
    if pending:                       # 跳 tick 越过了投放点：补投，保证命令不丢
        for _, cmd in pending:
            sim.enqueue_command(cmd)
        while not sim.step():
            pass
    sim._finalize()
    return sim


def digest(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------- 确定性 CMD_OK
def test_same_seed_same_commands_byte_identical(tmp_path):
    a = run_with_script(tmp_path / "a", COMMAND_SCRIPT)
    b = run_with_script(tmp_path / "b", COMMAND_SCRIPT)
    da, db = digest(a.out / "sim_events.jsonl"), digest(b.out / "sim_events.jsonl")
    assert da == db, "同种子 + 同命令流跑出两份不同的事件流 → 命令破坏了确定性"
    assert a.metrics["metrics"]["orders_completed"]["value"] == \
        b.metrics["metrics"]["orders_completed"]["value"]


def test_command_stream_actually_changes_outcome(tmp_path):
    """命令不能是"发了个寂寞"：比起不干预，结果必须真的不同。"""
    plain = run_with_script(tmp_path / "plain", [])
    acted = run_with_script(tmp_path / "acted", COMMAND_SCRIPT)
    assert (digest(plain.out / "sim_events.jsonl")
            != digest(acted.out / "sim_events.jsonl")), "命令全发了，事件流却一模一样"
    assert "CMD_SET_POLICY" in acted.log.counts
    assert "CMD_BUY_AGV" in acted.log.counts


def test_every_command_is_logged(tmp_path):
    """每条命令都要留事件，否则"命令流 + 种子"无法重放。"""
    sim = run_with_script(tmp_path, COMMAND_SCRIPT[:3])
    for typ in ("CMD_SET_POLICY", "CMD_SET_DEMAND", "CMD_BUY_AGV"):
        assert sim.log.counts.get(typ, 0) >= 1, "%s 没有留下事件，重放会断链" % typ


# ------------------------------------------------------------- 未知即抛错
def test_unknown_command_raises(tmp_path):
    sim = make_sim(tmp_path)
    with pytest.raises(ValueError, match="unknown command"):
        sim.enqueue_command({"type": "TELEPORT_FLEET", "payload": {}})


def test_bad_payloads_raise(tmp_path):
    sim = make_sim(tmp_path)
    cases = [
        ({"type": "SET_POLICY", "payload": {"dispatch.policy": "random_walk"}}, "dispatch.policy"),
        ({"type": "SET_POLICY", "payload": {"battery.charge_policy": "always"}}, "charge_policy"),
        ({"type": "SET_POLICY", "payload": {}}, "策略键"),
        ({"type": "SET_POLICY", "payload": {"dispatch_speed": 3}}, "不认识的键"),
        ({"type": "SET_DEMAND", "payload": {"orders.per_hour": 0}}, "orders.per_hour"),
        ({"type": "SET_DEMAND", "payload": {"orders.per_hour": 10 ** 6}}, "orders.per_hour"),
        ({"type": "INJECT_FAULT", "payload": {"type": "meteor", "duration_s": 10}}, "INJECT_FAULT"),
        ({"type": "INJECT_FAULT", "payload": {"type": "robot_down", "agent": "NOPE-1",
                                              "duration_s": 10}}, "没有这台车"),
        ({"type": "INJECT_FAULT", "payload": {"type": "dock_blocked", "dock": "NO-DOCK",
                                              "duration_s": 10}}, "没有这个月台"),
        ({"type": "BUY_AGV", "payload": {"wid": "F9"}}, "没有这个仓"),
        ({"type": "BUY_AGV", "payload": {"wid": "W", "home": [1]}}, "home"),
    ]
    for cmd, kw in cases:
        with pytest.raises(ValueError, match=kw):
            sim.enqueue_command(cmd)


def test_unimplemented_commands_do_not_silently_pass(tmp_path):
    """没实现的那几个必须明着抛 NotImplementedError；已实现的（P3 的
    SELL_AGV / SET_PRICE）则不许再抛——否则玩家永远用不上。"""
    sim = make_sim(tmp_path)
    for typ, payload in (("BUILD_STATION", {}), ("MANUAL_ASSIGN", {})):
        with pytest.raises(NotImplementedError):
            sim.enqueue_command({"type": typ, "payload": payload})
    assert "BUILD_STATION" in se.NOT_IMPLEMENTED
    assert "SELL_AGV" not in se.NOT_IMPLEMENTED, "SELL_AGV 已经实现，不该还在占位表里"
    assert "SET_PRICE" not in se.NOT_IMPLEMENTED, "SET_PRICE 已经实现，不该还在占位表里"


def test_command_registry_has_handler_for_all(tmp_path):
    sim = make_sim(tmp_path)
    for typ in se.COMMANDS:
        assert callable(sim._handler_for(typ)), "%s 登记了却没有处理器" % typ


# --------------------------------------------------------------- 语义正确性
def test_buy_agv_adds_a_real_robot(tmp_path):
    sim = make_sim(tmp_path)
    before = len(sim.robots)
    sim.enqueue_command({"type": "BUY_AGV", "payload": {"wid": "W", "home": [20.0, 20.0]}})
    sim.step()
    assert len(sim.robots) == before + 1
    new = [r for r in sim.robots.values() if r.id.startswith("W-BUY")][0]
    assert new.wid == "W"
    assert sim.world.walkable(sim.world.to_cell(new.pos)), "买车生成在不可走格子里"
    assert new.soc == pytest.approx(sim.spec["battery"]["initial_soc_pct"]
                                    / 100.0 * sim.model["battery_kwh"], rel=1e-3)


def test_set_policy_takes_effect_on_dispatch(tmp_path):
    sim = make_sim(tmp_path)
    sim.enqueue_command({"type": "SET_POLICY", "payload": {"dispatch.policy": "farthest_first"}})
    sim.step()
    assert sim.dispatch_rule == "farthest_first"
    assert sim.overrides["dispatch.policy"] == "farthest_first", "策略没写回 overrides，存场景就丢了"


def test_set_demand_updates_arrival_rate(tmp_path):
    sim = make_sim(tmp_path)
    sim.enqueue_command({"type": "SET_DEMAND", "payload": {"orders.per_hour": 120}})
    sim.step()
    assert sim.per_hour == 120
    assert sim.lam == pytest.approx(120 / 3600.0)


def test_inject_fault_lands_in_fault_list(tmp_path):
    sim = make_sim(tmp_path)
    n0 = len(sim.faults)
    sim.enqueue_command({"type": "INJECT_FAULT",
                         "payload": {"type": "robot_down", "agent": "ANY_BUSY",
                                     "duration_s": 60}})
    sim.step()
    assert len(sim.faults) == n0 + 1
    f = sim.faults[-1]
    assert f["start"] == pytest.approx(sim.t)
    assert f["end"] == pytest.approx(sim.t + 60)
    assert "CMD_INJECT_FAULT" in sim.log.counts


def test_batch_mode_queue_stays_empty(tmp_path):
    """批跑必须完全不碰命令层：否则 STEP_OK 会被"顺手加的功能"顶翻。"""
    sim = make_sim(tmp_path)
    sim.run()
    assert sim._cmd_queue == []
