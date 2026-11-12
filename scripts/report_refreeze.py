#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/report_refreeze.py —— 门 6 · REFREEZE_OK（v1 → v2 的归因报告）。

这一轮的招牌不是"没变"，而是**变了要能说清为什么**。所以重冻不是一条命令了事：

  1. `step_baseline_v2.json` / `kpi_baseline_v2.json` 与 v1 并存，v1 永不覆盖；
  2. 本脚本逐场景列出"哪份产物变了、KPI 哪几项变了多少"；
  3. 统计当前运行的事件类型计数，把新事件（STOCK_*/HOLDING_*/REORDER_*/PICKFACE_*）
     的实际次数摆出来——数字必须能与机制对上；
  4. 读 `out/validate_report.json`，确认 A1–A40 里**没有一项因为重冻被放过**。

判定（方案书 §8.3）：
  REFREEZE_OK := step --check(v2) == 0
                 AND step --check=v1 != 0（v1 必须"预期不同"）
                 AND kpi --check(v2) == 0
                 AND 本报告每场景有条目
                 AND A1–A40 全 PASS
                 AND step --self-test == 0（门仍能失败）

用法：
    python scripts/report_refreeze.py            # 生成报告并判定
    python scripts/report_refreeze.py --quiet
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"
RUNS = ROOT / "runs"
OUT = ROOT / "out"

NEW_TYPES = ("STOCK_DECREMENT", "STOCK_RECEIPT", "STOCK_REPLENISH_ARRIVED", "STOCKOUT",
             "REORDER_TRIGGERED", "PICKFACE_REFILL", "HOLDING_ACCRUED",
             # P6b
             "STOCK_EXPIRED")

#: 基准代次 → 文件名模板。加一代只加一行，不改比较逻辑。
STEP_FILES = {"v1": "step_baseline.json", "v2": "step_baseline_v2.json",
              "v3": "step_baseline_v3.json"}
KPI_FILES = {"v1": "kpi_baseline.json", "v2": "kpi_baseline_v2.json",
             "v3": "kpi_baseline_v3.json"}


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def latest_runs():
    best = {}
    for f in RUNS.glob("*/*/sim_kpi.json"):
        scen = f.parts[-3]
        if scen in ("custom", "logs", "pilot_csv", "game"):
            continue
        m = f.stat().st_mtime
        if scen not in best or m > best[scen][0]:
            best[scen] = (m, f.parent)
    return {k: v[1] for k, v in best.items()}


def event_census(run_dir):
    from collections import Counter
    c = Counter()
    p = run_dir / "sim_events.jsonl"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                c[json.loads(line)["type"]] += 1
    return dict(sorted(c.items()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="门 6 · 重冻归因报告（base → target）")
    ap.add_argument("--base", default="v2", choices=sorted(STEP_FILES))
    ap.add_argument("--target", default="v3", choices=sorted(STEP_FILES))
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    base_p = GOLDEN / STEP_FILES[a.base]
    tgt_p = GOLDEN / STEP_FILES[a.target]
    if not tgt_p.exists():
        print("还没有 %s 基准：先跑 `python scripts/step_baseline.py --freeze-%s`"
              % (a.target, a.target), file=sys.stderr)
        return 2
    base = jload(base_p)
    tgt = jload(tgt_p)
    kb = jload(GOLDEN / KPI_FILES[a.base])
    kt = jload(GOLDEN / KPI_FILES[a.target])
    runs = latest_runs()

    report = {"schema_version": "1.0", "kind": "refreeze_report",
              "base": a.base, "target": a.target,
              "note": "旧基准永不覆盖；本报告把'变了什么'逐条落到场景/产物/KPI/事件类型上",
              "scenarios": {}}
    for scen in sorted(set(base["scenarios"]) | set(tgt["scenarios"])):
        s1 = (base["scenarios"].get(scen) or {})
        s2 = (tgt["scenarios"].get(scen) or {})
        changed = sorted(k for k in set(s1) | set(s2)
                         if (s1.get(k) or {}).get("sha256") != (s2.get(k) or {}).get("sha256"))
        kd = {}
        a1 = (kb["scenarios"].get(scen) or {}).get("values") or {}
        a2 = (kt["scenarios"].get(scen) or {}).get("values") or {}
        for k in sorted(set(a1) | set(a2)):
            if k not in a1:
                kd[k] = [None, a2[k]]
            elif k not in a2:
                kd[k] = [a1[k], None]
            elif isinstance(a1[k], (int, float)) and isinstance(a2[k], (int, float)):
                if abs(a1[k] - a2[k]) > 1e-9:
                    kd[k] = [a1[k], a2[k]]
        census = event_census(runs[scen]) if scen in runs else {}
        report["scenarios"][scen] = {
            "changed_artifacts": changed,
            "kpi_changes": kd,
            "new_event_counts": {t: census.get(t, 0) for t in NEW_TYPES},
            "event_types_total": len(census),
        }

    # 工程不变量：A1–A40 必须全过（重冻不许放过任何一项）
    vr = jload(OUT / "validate_report.json") if (OUT / "validate_report.json").exists() else {}
    checks_raw = vr if isinstance(vr, list) else (vr.get("checks") or [])
    failed = [c.get("item") or c.get("name") for c in checks_raw
              if str(c.get("status") or "").upper() != "PASS" and c.get("ok") is not True]
    report["assertions_impact"] = {
        "total": len(checks_raw), "failed": failed,
        "verdict": "PASS" if checks_raw and not failed else "FAIL",
        "note": "工程不变量（零碰撞/几何/交通/电量）不得因重冻放宽；失败即引擎坏了",
    }

    def rc(args):
        r = subprocess.run([sys.executable] + args, cwd=str(ROOT),
                           capture_output=True, text=True)
        return r.returncode

    c_tgt = rc(["scripts/step_baseline.py", "--check=" + a.target])
    c_base = rc(["scripts/step_baseline.py", "--check=" + a.base])
    c_ktgt = rc(["scripts/kpi_baseline.py", "--check=" + a.target])
    c_st = rc(["scripts/step_baseline.py", "--self-test"])
    per_scen_ok = all(bool(v["changed_artifacts"]) for v in report["scenarios"].values())

    checks = {
        "target_byte_identical": c_tgt == 0,
        "base_expected_to_differ": c_base != 0,
        "target_kpi_consistent": c_ktgt == 0,
        "self_test_still_fails": c_st == 0,
        "every_scenario_changed": per_scen_ok,
        "engineering_invariants_pass": report["assertions_impact"]["verdict"] == "PASS",
    }
    ok = all(checks.values())
    report["checks"] = checks
    report["verdict"] = "REFREEZE_OK" if ok else "REFREEZE_FAIL"
    if c_base == 0:
        # 旧基准与新基准完全一致 → 新机制根本没进事件流，这本身是 bug
        report["diagnosis"] = ("%s 与 %s 完全一致：新机制没有进入事件流，"
                               "重冻是空操作，必须判红" % (a.base, a.target))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "refreeze_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not a.quiet:
        print("%s  %s→%s · 场景 %d · 验收 %d 项/%s · 旧基准差异=%s"
              % (report["verdict"], a.base, a.target, len(report["scenarios"]),
                 report["assertions_impact"]["total"],
                 report["assertions_impact"]["verdict"],
                 "是" if c_base != 0 else "否（异常）"))
        for scen, v in report["scenarios"].items():
            print("  %-18s 变 %d 份产物 · KPI 变 %d 项 · 新事件 %s"
                  % (scen, len(v["changed_artifacts"]), len(v["kpi_changes"]),
                     {k: n for k, n in v["new_event_counts"].items() if n}))
        for k, v in checks.items():
            if not v:
                print("  - 未通过：" + k)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
