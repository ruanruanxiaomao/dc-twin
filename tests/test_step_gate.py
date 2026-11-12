"""STEP_OK 门必须能失败（方案书 §4.4 / §9.1 的配套负测试）。

要防的错误很具体：把 `run()` 拆成 `step()` 时，**漏掉"空闲事件驱动跳 tick"这一段**。
漏了之后百花齐放的"指标大致没变"会让人以为重构成功，但其实：
  - 空闲期每个 tick 都会走到 `tick % kpi_every_ticks == 0`，凭空多出大量 ENERGY_TICK；
  - 事件流的 seq 从此错位，ledger 的事后结算跟着变。
这些在 32 项聚合 KPI 上可能被平均掉（能耗总量几乎不变），所以只有逐字节门抓得住。

本测试做两件事：
  1. 正控：开跳 tick 跑 baseline，与 `tests/golden/step_baseline.json` 逐字节一致；
  2. 负控：关跳 tick 重跑同一场景，**必须**与基准不一致，并明确指出哪份产物被抓到。
第 2 步若也"一致"，说明 STEP_OK 门是假的（永远通过），本测试直接失败。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sim_engine as se  # noqa: E402
import step_baseline as sb  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def baseline_sim(tmp_path):
    return se.Simulation(
        ROOT / "config" / "scenarios" / "baseline.json",
        jload(ROOT / "config" / "sim_spec.json"),
        jload(ROOT / "config" / "fleet_spec.json"),
        jload(ROOT / "config" / "demand_spec.json"),
        jload(ROOT / "data" / "layout.json"),
        jload(ROOT / "data" / "nav_grid.json"),
        jload(ROOT / "data" / "config.json"),
        out_dir=tmp_path,
    )


def run_baseline(tmp_path, jump):
    old = se.Simulation.jump_ticks_override
    se.Simulation.jump_ticks_override = jump
    try:
        sim = baseline_sim(tmp_path)
        sim.run()
    finally:
        se.Simulation.jump_ticks_override = old
    return {name: sb.digest(tmp_path / name) for name in sb.ARTIFACTS}


def golden_baseline():
    g = jload(ROOT / "tests" / "golden" / "step_baseline.json")
    return g["scenarios"]["baseline"]


def test_jump_on_matches_golden(tmp_path):
    """正控：跳 tick 开着 → 与冻结基准逐字节一致。"""
    actual = run_baseline(tmp_path, jump=None)
    problems = sb.compare({"baseline": golden_baseline()}, {"baseline": actual})
    assert not problems, "开着跳 tick 却与基准不一致：%s" % problems


def test_gate_detects_missing_jump(tmp_path):
    """负控：关掉跳 tick → 逐字节门必须报红。"""
    actual = run_baseline(tmp_path, jump=False)
    problems = sb.compare({"baseline": golden_baseline()}, {"baseline": actual})
    assert problems, ("关掉空闲跳 tick 后产物居然与基准一致——说明 STEP_OK 是假门，"
                      "或者跳 tick 分支已经不产生事件差异")
    # 必须是被事件流抓到的，而不是只有耗时字段
    assert any("sim_events.jsonl" in p for p in problems), \
        "抓到差异但不是事件流，门不够硬：%s" % problems


def test_more_energy_windows_without_jump(tmp_path):
    """把"为什么字节会变"落到可解释的机制上：多出来的 ENERGY_TICK 事件。"""
    on = run_baseline(tmp_path / "on", jump=None)
    off = run_baseline(tmp_path / "off", jump=False)
    assert off["sim_events.jsonl"]["lines"] > on["sim_events.jsonl"]["lines"], \
        ("关掉跳 tick 后事件数没变多，说明空闲期根本没被逐 tick 走过，"
         "这个负测试的前提不成立")
