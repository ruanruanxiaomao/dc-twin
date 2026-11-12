"""快照原语的单元测试（ADR-GAME-05 / 方案书 §4.1–4.3）。

三个原语是存档的地基，任何一个悄悄不完整，续玩都会在几十个 tick 之后以
"轨迹莫名偏了"的形式暴露，极难定位。所以这里逐个钉死：

  1. Mulberry32：`from_state(state())` 得到**同一条**随机序列，且原对象不受影响；
  2. Robot：`deserialize(serialize())` 后全字段相等，且续跑轨迹一致；
  3. EventLog：restore 以 append 重开、seq 不断号；截断/错位必须报错。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sim_engine as se  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def make_sim(tmp_path, scenario="baseline"):
    return se.Simulation(
        ROOT / "config" / "scenarios" / ("%s.json" % scenario),
        jload(ROOT / "config" / "sim_spec.json"),
        jload(ROOT / "config" / "fleet_spec.json"),
        jload(ROOT / "config" / "demand_spec.json"),
        jload(ROOT / "data" / "layout.json"),
        jload(ROOT / "data" / "nav_grid.json"),
        jload(ROOT / "data" / "config.json"),
        out_dir=tmp_path / "run",
    )


# ---------------------------------------------------------------- Mulberry32
def test_rng_state_roundtrip_gives_same_stream():
    r1 = se.Mulberry32(20261106)
    for _ in range(17):
        r1.next()
    r2 = se.Mulberry32.from_state(r1.state())
    a = [r1.next() for _ in range(32)]
    b = [r2.next() for _ in range(32)]
    assert a == b, "恢复后的随机序列与原流不一致 → 续玩必然漂移"


def test_rng_restore_is_independent():
    """回档点互不干扰：原流继续前进，不会拖动恢复出来的那条流。"""
    r1 = se.Mulberry32(7)
    r2 = se.Mulberry32.from_state(r1.state())
    for _ in range(3):
        r1.next()
    assert r2.next() == se.Mulberry32(7).next(), "恢复出的流被原流带跑了"

    # 反过来：两条流从同一状态各走一步，必须得到同一个数与同一个后继状态
    r3 = se.Mulberry32(99)
    r4 = se.Mulberry32.from_state(r3.state())
    assert r3.next() == r4.next()
    assert r3.state() == r4.state()


def test_rng_state_is_32bit():
    r = se.Mulberry32(0xDEADBEEF)
    for _ in range(5):
        r.next()
    assert 0 <= r.state() <= 0xFFFFFFFF
    assert se.Mulberry32.from_state(r.state()).state() == r.state()


# --------------------------------------------------------------------- Robot
def test_robot_snapshot_covers_every_mutable_field(tmp_path):
    """附录 A.3 的字段清单必须与实现一致：漏一个 = 续玩少一块状态。"""
    expected = {
        "id", "wid", "home", "pos", "soc", "soc_initial", "state", "order", "legs", "leg",
        "route", "arc", "route_len", "dwell_until", "wait_s", "fault_until",
        "charging_since", "station", "next_station_state", "queued_since", "blocked_by",
        "blocked_s", "yields", "detours", "blocked_total", "detour_try_t", "completed",
        "distance_m", "energy_out", "energy_in", "move_events", "leg_m",
        # 附录 A.3 漏了 charger_id（充电去哪台桩由它决定），实现里已补
        "charger_id",
    }
    assert set(se.Robot.SNAPSHOT_FIELDS) == expected
    # 模型常量不该出现在快照里（由 model 重建）
    for k in ("cap", "speed", "cons", "idle_kw", "charge_kw"):
        assert k not in se.Robot.SNAPSHOT_FIELDS


def test_robot_roundtrip_mid_flight(tmp_path):
    """跑到一半（有在途路线、有载货订单）再做 round-trip，比开局空车更有说服力。"""
    sim = make_sim(tmp_path)
    for _ in range(120):
        if sim.step():
            break
    assert sim.tick_i > 0
    moving = [r for r in sim.robots.values() if r.route and r.state != "IDLE"]
    assert moving, "120 tick 后居然没有在途车辆，测试前提不成立"

    for r in sim.robots.values():
        clone = se.Robot.deserialize(json.loads(json.dumps(r.serialize())), sim.model)
        for k in se.Robot.SNAPSHOT_FIELDS:
            got = getattr(clone, k)
            want = getattr(r, k)
            if k in ("pos", "home", "route", "legs", "order"):
                assert json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True), k
            else:
                assert got == want, k
        # 坐标类型必须还原成元组，否则相等判断会失真
        assert isinstance(clone.pos, tuple)
        assert all(isinstance(p, tuple) for p in clone.route)
        assert clone.soc_pct == pytest.approx(r.soc_pct)


def test_robot_deserialize_rejects_missing_field(tmp_path):
    sim = make_sim(tmp_path)
    snap = sim.robots[next(iter(sim.robots))].serialize()
    snap.pop("arc")
    with pytest.raises(ValueError, match="缺字段"):
        se.Robot.deserialize(snap, sim.model)


def test_robot_deserialize_rejects_unknown_state(tmp_path):
    sim = make_sim(tmp_path)
    snap = sim.robots[next(iter(sim.robots))].serialize()
    snap["state"] = "TELEPORTING"
    with pytest.raises(ValueError, match="状态非法"):
        se.Robot.deserialize(snap, sim.model)


# ------------------------------------------------------------------ EventLog
def test_event_log_restore_continues_seq(tmp_path):
    p = tmp_path / "e.jsonl"
    log = se.EventLog(p, ring=10)
    for i in range(5):
        log.emit(1.0 * i, "X", i=i)
    snap = log.snapshot()
    log.close()

    back = se.EventLog.restore(p, 10, snap)
    assert back.seq == 5
    assert back.counts == {"X": 5}
    e = back.emit(9.0, "Y")
    assert e["seq"] == 6, "续写必须接在 5 后面，不能从 1 重号"
    back.close()

    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [x["seq"] for x in lines] == [1, 2, 3, 4, 5, 6], "历史事件不许被清空"


def test_event_log_restore_rejects_truncation(tmp_path):
    """截断重开（`open(...,"w")`）是 R11，必须被拦下而不是静默重号。"""
    p = tmp_path / "e.jsonl"
    log = se.EventLog(p, ring=10)
    for i in range(5):
        log.emit(1.0 * i, "X")
    snap = log.snapshot()
    log.close()

    # 模拟事故：有人用默认模式（"w"）重开，把 5 条历史清空后又写了 2 条
    bad = se.EventLog(p, ring=10)
    bad.emit(100.0, "Z")
    bad.emit(101.0, "Z")
    bad.close()

    with pytest.raises(ValueError, match="不一致"):
        se.EventLog.restore(p, 10, snap)


def test_event_log_restore_rejects_missing_history(tmp_path):
    p = tmp_path / "e.jsonl"
    with pytest.raises(ValueError, match="缺失"):
        se.EventLog.restore(p, 10, {"seq": 42, "counts": {}, "recent": []})
