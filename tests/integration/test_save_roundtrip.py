"""存档 round-trip（ADR-GAME-05 / 方案书 §8.1 SAVE_OK）。

判据只有一条硬的：**续跑的事件流必须与"不中断跑"逐字节一致**。
这条同时证明了快照的完备性——少存一块状态，续跑的轨迹就会分叉，字节必然不等。

另外四条负测试保证"能存档"不是靠"尽力而为"：缺字段、版本不符、日志对不上、
事件流缺失，都必须报错。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import game_economy as ge  # noqa: E402
import game_save as gs  # noqa: E402
import sim_engine as se  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def specs():
    return (jload(ROOT / "config" / "sim_spec.json"),
            jload(ROOT / "config" / "fleet_spec.json"),
            jload(ROOT / "config" / "demand_spec.json"),
            jload(ROOT / "data" / "layout.json"),
            jload(ROOT / "data" / "nav_grid.json"),
            jload(ROOT / "data" / "config.json"))


def make(tmp_path, name="baseline"):
    sim_spec, fleet, demand, layout, grid, cfg = specs()
    return se.Simulation(ROOT / "config" / "scenarios" / ("%s.json" % name),
                         sim_spec, fleet, demand, layout, grid, cfg,
                         out_dir=tmp_path / "run")


def sha(p):
    return hashlib.sha256(p.read_text(encoding="utf-8").replace("\r\n", "\n")
                           .encode("utf-8")).hexdigest()


def step_n(sim, n):
    for _ in range(n):
        if sim.step():
            return True
    return False


# --------------------------------------------------------------- round-trip
def test_restore_continues_byte_identical(tmp_path):
    """核心判据：中途存档 → 读档 → 续跑，事件流与一口气跑完逐字节一致。"""
    # A：一口气跑完（**不要**先 step 再 run——run() 会重新 _init_run，那是另一局）
    a = make(tmp_path / "a")
    a.run()

    # B：跑到同一 tick → 快照 → 恢复 → 续跑
    b = make(tmp_path / "b")
    step_n(b, 1500)
    snap = b.snapshot()
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(json.dumps(snap), encoding="utf-8")
    b.log.close()

    sim_spec, fleet, demand, layout, grid, cfg = specs()
    r = se.Simulation.restore(json.loads(snap_path.read_text(encoding="utf-8")),
                              ROOT / "config" / "scenarios" / "baseline.json",
                              sim_spec, fleet, demand, layout, grid, cfg,
                              out_dir=tmp_path / "b" / "run")
    r.run()

    assert sha(a.out / "sim_events.jsonl") == sha(r.out / "sim_events.jsonl"), \
        "读档续跑的事件流与原跑不一致 → 快照缺状态或恢复了错的东西"


def test_restore_keeps_seq_continuous(tmp_path):
    sim = make(tmp_path)
    step_n(sim, 800)
    snap = sim.snapshot()
    seq_before = snap["log"]["seq"]
    sim.log.close()

    sim_spec, fleet, demand, layout, grid, cfg = specs()
    r = se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                              sim_spec, fleet, demand, layout, grid, cfg,
                              out_dir=tmp_path / "run")
    assert r.log.seq == seq_before
    e = r.log.emit(1e9, "PROBE")
    assert e["seq"] == seq_before + 1, "续写重号了：ledger 幂等键会把新事件当旧事件吞掉"


def test_snapshot_flushes_event_log(tmp_path):
    """存档时事件日志必须在盘上（回归：缓冲未 flush 导致"磁盘 seq=0 但快照 seq=N"）。

    这个 bug 是打包后的 exe 里先暴露的：自由模式开局事件少，缓冲区没满，
    save 之后文件里一条都没有 → load 报"日志与快照不一致"。
    """
    sim = make(tmp_path)
    step_n(sim, 200)
    snap = sim.snapshot()
    # 不关 log，直接读盘：必须已经能看到 snap["log"]["seq"] 条
    lines = [x for x in (sim.out / "sim_events.jsonl")
             .read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == snap["log"]["seq"], (
        "存档时日志还在缓冲区：盘上 %d 条，快照说 %d 条" % (len(lines), snap["log"]["seq"]))
    sim.log.close()


def test_snapshot_covers_policies_and_demand(tmp_path):
    """策略与需求也是状态：续玩后不能变回默认值。"""
    sim = make(tmp_path)
    step_n(sim, 300)
    sim.enqueue_command({"type": "SET_POLICY",
                         "payload": {"dispatch.policy": "farthest_first",
                                     "dispatch.battery_reserve_pct": 33}})
    sim.enqueue_command({"type": "SET_PRICE",
                         "payload": {"order_type": "OUTBOUND", "price": 40.0}})
    sim.step()
    snap = sim.snapshot()
    sim.log.close()

    sim_spec, fleet, demand, layout, grid, cfg = specs()
    r = se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                              sim_spec, fleet, demand, layout, grid, cfg,
                              out_dir=tmp_path / "run")
    assert r.dispatch_rule == "farthest_first"
    assert r.reserve_pct == pytest.approx(33.0)
    assert r.price_overrides["OUTBOUND"] == pytest.approx(40.0)
    assert r.per_hour == pytest.approx(sim.per_hour)


def test_snapshot_restores_economy(tmp_path):
    sim = make(tmp_path)
    econ = ge.attach(sim, None, initial_capital=300000)
    step_n(sim, 400)
    snap = sim.snapshot()
    cash = econ.cash
    sim.log.close()

    sim_spec, fleet, demand, layout, grid, cfg = specs()
    r = se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                              sim_spec, fleet, demand, layout, grid, cfg,
                              out_dir=tmp_path / "run")
    assert r.economy is not None
    assert r.economy.cash == pytest.approx(cash, abs=1e-9)
    assert r.economy.initial_capital == pytest.approx(300000)
    # 幂等键一起恢复：重复事件仍不许重记
    seen = len(r.economy.postings)
    if r.economy.postings:
        seq, account = r.economy.postings[0][0], r.economy.postings[0][1]
        r.economy._post(seq, account, 999.0, "credit")
        assert len(r.economy.postings) == seen


# --------------------------------------------------------------- 负测试
def test_restore_rejects_bad_kind(tmp_path):
    snap = make(tmp_path).snapshot()
    snap["kind"] = "sim_state"
    sim_spec, fleet, demand, layout, grid, cfg = specs()
    with pytest.raises(ValueError, match="不是 game_snapshot"):
        se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                              sim_spec, fleet, demand, layout, grid, cfg,
                              out_dir=tmp_path / "run")


def test_restore_rejects_version_mismatch(tmp_path):
    snap = make(tmp_path).snapshot()
    snap["schema_version"] = "0.9"
    sim_spec, fleet, demand, layout, grid, cfg = specs()
    with pytest.raises(ValueError, match="版本不符"):
        se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                              sim_spec, fleet, demand, layout, grid, cfg,
                              out_dir=tmp_path / "run")


def test_restore_rejects_missing_field(tmp_path):
    snap = make(tmp_path).snapshot()
    snap.pop("counters")
    sim_spec, fleet, demand, layout, grid, cfg = specs()
    with pytest.raises(ValueError, match="缺字段"):
        se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                              sim_spec, fleet, demand, layout, grid, cfg,
                              out_dir=tmp_path / "run")


def test_restore_rejects_log_mismatch(tmp_path):
    """R11：日志被截断（或快照过期）时，restore 必须报错而不是从头写 seq。"""
    sim = make(tmp_path)
    step_n(sim, 400)
    snap = sim.snapshot()
    sim.log.close()
    # 人为破坏：把事件流清空（模拟有人用 open(...,"w") 重开）
    (sim.out / "sim_events.jsonl").write_text("", encoding="utf-8")
    sim_spec, fleet, demand, layout, grid, cfg = specs()
    with pytest.raises(ValueError, match="不一致|缺失"):
        se.Simulation.restore(snap, ROOT / "config" / "scenarios" / "baseline.json",
                              sim_spec, fleet, demand, layout, grid, cfg,
                              out_dir=tmp_path / "run")


def test_save_rejects_bad_slot_name():
    with pytest.raises(gs.SaveError):
        gs.slot_path("../escape")
    with pytest.raises(gs.SaveError):
        gs.slot_path("")
