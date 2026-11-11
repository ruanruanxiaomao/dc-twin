#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KPI 基准：把 8 个场景的实测指标冻成快照，跑完链路后逐项比对。

为什么需要它：这个项目的可信度全在"数字是跑出来的"，但**没有任何东西保证下次跑
出来的还是同一批数字**。改一行服务时长、动一下派单策略，KPI 会悄悄变，文档却还
写着旧值——审查的人无法分辨"改进了"还是"跑坏了"。

做法：只冻结**确定性指标**（种子固定 → 结果可复现），显式排除 `wall_clock_s`
这类跟机器快慢有关的量。比对用相对容差，计数类要求精确相等。

用法：
    python scripts/kpi_baseline.py --freeze   # 重新冻结（改模型后要有意识地做）
    python scripts/kpi_baseline.py --check    # CI 用：跑完 scenarios 链后比对
    python scripts/kpi_baseline.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
GOLDEN = ROOT / "tests" / "golden" / "kpi_baseline.json"

#: 冻结哪些指标。刻意不含 wall_clock_s（跟机器有关）、不含诊断计数之外的随机量。
METRICS = [
    "orders_generated", "orders_completed", "orders_lost",
    "throughput_orders_per_h", "on_time_rate", "avg_tardiness_s", "max_tardiness_s",
    "distance_total_m", "energy_kwh_total", "charge_events",
    "wait_conflicts", "task_reassignments", "max_queue_depth",
    "strict_collisions", "reservation_double_occupancy", "min_pair_distance_m",
    "station_served", "station_utilization", "deadlock_yields", "deadlock_detours",
    "jam_clears", "availability", "idle_rate", "sim_duration_s",
]

#: 计数类：必须精确相等，容差为 0
INT_METRICS = {"orders_generated", "orders_completed", "orders_lost", "charge_events",
               "wait_conflicts", "task_reassignments", "max_queue_depth",
               "strict_collisions", "reservation_double_occupancy", "station_served",
               "deadlock_yields", "deadlock_detours", "jam_clears"}

TOL = 1e-6          # 浮点的相对容差


def latest_runs() -> dict[str, Path]:
    """每个场景取最新一次 tick 仿真快照（按 mtime）。"""
    best: dict[str, tuple[float, Path]] = {}
    for f in RUNS.glob("*/*/sim_kpi.json"):
        scen = f.parts[-3]
        if scen in ("custom", "logs"):
            continue
        m = f.stat().st_mtime
        if scen not in best or m > best[scen][0]:
            best[scen] = (m, f.parent)
    return {k: v[1] for k, v in best.items()}


def read_metrics(run_dir: Path) -> dict:
    d = json.loads((run_dir / "sim_kpi.json").read_text(encoding="utf-8"))
    out = {}
    for k in METRICS:
        v = (d.get("metrics") or {}).get(k)
        if isinstance(v, dict):
            out[k] = v.get("value")
    return out


def freeze(path: Path = GOLDEN) -> int:
    runs = latest_runs()
    if not runs:
        print("没有找到 runs/*/*/sim_kpi.json，先跑 `dc run --scope scenarios`", file=sys.stderr)
        return 2
    data = {
        "schema_version": "1.0",
        "kind": "kpi_baseline",
        "note": "确定性指标快照；改模型后必须显式 --freeze，并在提交信息里说明为什么变了",
        "tolerance_rel": TOL,
        "metrics": METRICS,
        "scenarios": {},
    }
    for scen in sorted(runs):
        data["scenarios"][scen] = {
            "run": runs[scen].name,
            "values": read_metrics(runs[scen]),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("KPI_BASELINE_FROZEN %d 场景 -> %s" % (len(data["scenarios"]),
                                                path.relative_to(ROOT).as_posix()))
    return 0


def compare(expected: dict, actual: dict) -> list[str]:
    """纯函数：expected/actual 都是 {scenario: {metric: value}}。"""
    problems = []
    for scen, exp in expected.items():
        if scen not in actual:
            problems.append("%s：场景缺失（这次没跑？）" % scen)
            continue
        for k, ev in exp.items():
            av = actual[scen].get(k)
            if ev is None or av is None:
                problems.append("%s/%s：基准 %r，实测 %r" % (scen, k, ev, av))
                continue
            if k in INT_METRICS:
                if int(ev) != int(av):
                    problems.append("%s/%s：%s → %s" % (scen, k, ev, av))
                continue
            # 相对容差；两个都接近 0 时用绝对容差兜底
            scale = max(abs(ev), abs(av), 1e-12)
            if abs(ev - av) / scale > TOL:
                problems.append("%s/%s：%s → %s（相对偏差 %.2e）"
                                % (scen, k, ev, av, abs(ev - av) / scale))
    return problems


def check(path: Path = GOLDEN) -> int:
    if not path.exists():
        print("缺少基准文件 %s；先用 --freeze 生成" % path, file=sys.stderr)
        return 2
    golden = json.loads(path.read_text(encoding="utf-8"))
    expected = {k: v["values"] for k, v in golden["scenarios"].items()}
    runs = latest_runs()
    actual = {k: read_metrics(v) for k, v in runs.items()}
    missing = sorted(set(expected) - set(actual))
    if missing:
        print("缺少场景快照：%s\n先跑 `python -m dc_twin run --scope scenarios`"
              % ", ".join(missing), file=sys.stderr)
        return 2
    problems = compare(expected, actual)
    if problems:
        print("KPI_DRIFT %d 处：" % len(problems))
        for p in problems[:40]:
            print("  - " + p)
        if len(problems) > 40:
            print("  … 其余 %d 处略" % (len(problems) - 40))
        print("\n如果是**有意**改动：确认后跑 `python scripts/kpi_baseline.py --freeze`，"
              "并在提交信息里说明哪几项为什么变。")
        return 1
    n = sum(len(v) for v in expected.values())
    print("KPI_BASELINE_OK  %d 场景 / %d 项指标与基准一致" % (len(expected), n))
    return 0


def self_test() -> int:
    """证明比对真的会失败：改一个计数、改一个小数，都必须被抓。"""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    expected = {k: v["values"] for k, v in golden["scenarios"].items()}
    base = {k: dict(v) for k, v in expected.items()}
    if compare(expected, base):
        print("KPI_BASELINE_SELFTEST FAIL 原样比对居然不通过")
        return 1

    scen = sorted(base)[0]
    k_int = next(k for k in base[scen] if k in INT_METRICS)
    k_flt = next(k for k in base[scen] if k not in INT_METRICS and base[scen][k])
    bad = {k: dict(v) for k, v in base.items()}
    bad[scen][k_int] = bad[scen][k_int] + 1
    bad[scen][k_flt] = bad[scen][k_flt] * 1.01
    hit = compare(expected, bad)
    if len(hit) >= 2:
        print("KPI_BASELINE_SELFTEST OK  计数漂移与浮点漂移都能抓到（%d 处）" % len(hit))
        return 0
    print("KPI_BASELINE_SELFTEST FAIL 只抓到 %d 处" % len(hit))
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="KPI 基准快照")
    ap.add_argument("--freeze", action="store_true", help="重新冻结快照")
    ap.add_argument("--check", action="store_true", help="与快照比对")
    ap.add_argument("--self-test", action="store_true", help="证明比对会失败")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.freeze:
        return freeze()
    return check()


if __name__ == "__main__":
    sys.exit(main())
