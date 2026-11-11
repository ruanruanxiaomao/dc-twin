#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实数据接入试点：企业 CSV → tick 仿真 → 账本/损益 → 与合成需求对比。

这一步的意义：适配器、外部订单入口、账本以前都"存在"，但从没在**同一条命令**里
串起来跑通过。评审里说的"真实数据接入仍是框架"，指的就是这个。

流程（全部走真实脚本，不模拟）：

    1. scripts/adapters/csv_orders.py     CSV → out/csv_events.jsonl（含引用完整性校验）
    2. out/pilot/scenario.json            复制 baseline，只把 id 改成 pilot_csv
    3. scripts/sim_engine.py --events     **用 CSV 到达时间驱动**，不再合成订单
    4. scripts/ledger.py / pnl_report.py  该次运行单独记账（--no-index，不污染主账）
    5. out/pilot/report.{json,md}         与最近一次合成 baseline 逐项对比

确定性：外部订单到达时间来自数据，服务时长用独立随机流（seed 固定），所以同一份
CSV 跑两遍结果一致。`--check` 就是拿这个性质做回归。

用法：
    python scripts/real_data_pilot.py            # 跑一遍并写报告
    python scripts/real_data_pilot.py --check     # 与冻结值比对（CI 用）
    python scripts/real_data_pilot.py --freeze    # 有意改模型后重冻
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
PILOT = OUT / "pilot"
RUNS = ROOT / "runs" / "pilot_csv"
GOLDEN = ROOT / "tests" / "golden" / "pilot_baseline.json"

#: 对比/冻结哪些指标（都要能从 sim_kpi.json 直接读到）
KEYS = [
    ("orders_generated", "订单到达"),
    ("orders_completed", "完成"),
    ("orders_lost", "丢失"),
    ("throughput_orders_per_h", "吞吐 (单/h)"),
    ("on_time_rate", "准时率"),
    ("avg_tardiness_s", "平均延误 (s)"),
    ("distance_total_m", "总里程 (m)"),
    ("energy_kwh_total", "总耗电 (kWh)"),
    ("energy_per_order_kwh", "单均耗电 (kWh)"),
    ("charge_events", "充电次数"),
    ("max_queue_depth", "峰值排队"),
    ("station_utilization", "工位利用率"),
    ("strict_collisions", "碰撞"),
    ("min_pair_distance_m", "最小车距 (m)"),
]

INT_KEYS = {"orders_generated", "orders_completed", "orders_lost",
            "charge_events", "max_queue_depth", "strict_collisions"}
TOL = 1e-6


def run(cmd) -> int:
    cmd = [str(c) for c in cmd]
    print("$ " + " ".join(cmd[1:]) if len(cmd) > 1 else "$ " + cmd[0])
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def step_csv() -> int:
    return run([sys.executable, "scripts/adapters/csv_orders.py",
                "--csv", "config/orders_sample.csv", "--out", "out/csv_events.jsonl"])


def step_scenario() -> int:
    base = json.loads((ROOT / "config" / "scenarios" / "baseline.json").read_text(encoding="utf-8"))
    base["id"] = "pilot_csv"
    base["name"] = "真实 CSV 订单试点"
    PILOT.mkdir(parents=True, exist_ok=True)
    (PILOT / "scenario.json").write_text(
        json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def step_sim() -> Path | None:
    rc = run([sys.executable, "scripts/sim_engine.py",
              "--scenario", "out/pilot/scenario.json",
              "--events", "out/csv_events.jsonl"])
    if rc:
        return None
    snaps = sorted(RUNS.glob("*/sim_kpi.json"), key=lambda p: p.stat().st_mtime)
    return snaps[-1].parent if snaps else None


def step_ledger(run_dir: Path) -> int:
    rel = run_dir.relative_to(ROOT).as_posix()
    rc = run([sys.executable, "scripts/ledger.py", "--run", rel, "--no-index"])
    if rc:
        return rc
    return run([sys.executable, "scripts/pnl_report.py", "--run", rel, "--no-index"])


def read_kpi(run_dir: Path) -> dict:
    d = json.loads((run_dir / "sim_kpi.json").read_text(encoding="utf-8"))
    return {k: (d.get("metrics") or {}).get(k, {}).get("value") for k, _ in KEYS}


def latest_synthetic() -> dict | None:
    """最近一次合成 baseline 的 tick 指标，用来做对照。"""
    snaps = sorted((ROOT / "runs" / "baseline").glob("*/sim_kpi.json"),
                   key=lambda p: p.stat().st_mtime)
    if not snaps:
        return None
    d = json.loads(snaps[-1].read_text(encoding="utf-8"))
    return {k: (d.get("metrics") or {}).get(k, {}).get("value") for k, _ in KEYS}


def build_report(run_dir: Path) -> dict:
    pilot = read_kpi(run_dir)
    synth = latest_synthetic() or {}
    csv_rows = sum(1 for _ in (OUT / "csv_events.jsonl").open(encoding="utf-8"))
    adv = json.loads((OUT / "csv_adapter_report.json").read_text(encoding="utf-8"))
    pnl = run_dir / "pnl.json"
    rep = {
        "schema_version": "1.0",
        "kind": "real_data_pilot",
        "source": {"csv": adv.get("csv"), "rows_in_csv": adv.get("rows_out"),
                   "events": csv_rows, "schema_valid": adv.get("schema_valid")},
        "run": {"dir": run_dir.relative_to(ROOT).as_posix(),
                "scenario": "pilot_csv", "engine": "sim"},
        "pilot": pilot,
        "synthetic_baseline": synth,
        "delta": {k: (None if (pilot.get(k) is None or synth.get(k) is None)
                      else round(pilot[k] - synth[k], 6)) for k, _ in KEYS},
        "pnl": (json.loads(pnl.read_text(encoding="utf-8")) if pnl.exists() else None),
    }
    return rep


def render_md(rep: dict) -> str:
    p, s = rep["pilot"], rep["synthetic_baseline"]
    lines = [
        "# 真实数据接入试点：企业 CSV 订单 vs 合成需求",
        "",
        "> 由 `python scripts/real_data_pilot.py` 生成。两种运行**共用**同一套布局、"
        "车队、服务时长与调度策略，唯一差别是订单从哪来。",
        "",
        "## 数据来源",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| CSV | `%s` |" % rep["source"]["csv"],
        "| 有效订单 | %s 行 |" % rep["source"]["rows_in_csv"],
        "| 引用完整性 | %s |" % ("PASS" if rep["source"]["schema_valid"] else "FAIL"),
        "| 运行目录 | `%s` |" % rep["run"]["dir"],
        "",
        "## 指标对比",
        "",
        "| 指标 | 合成 baseline | CSV 试点 | 差值 |",
        "|---|---|---|---|",
    ]
    for k, label in KEYS:
        pv, sv = p.get(k), s.get(k)
        def fmt(v, _float=isinstance(pv, float) or isinstance(sv, float)):
            if v is None:
                return "—"
            if _float:
                return "%.4f" % v if abs(v) < 10 else "%.1f" % v
            return str(v)
        lines.append("| %s | %s | %s | %s |"
                     % (label, fmt(sv), fmt(pv),
                        "—" if rep["delta"].get(k) is None else
                        ("%+g" % rep["delta"][k])))
    if rep.get("pnl"):
        lines += ["", "## 该次运行的损益（演示价格）", "",
                  "| 项 | 值 |", "|---|---|",
                  "| 完成订单 | %s |" % rep["pnl"].get("orders_completed"),
                  "| 收入 | %.2f |" % rep["pnl"].get("revenue_total", 0),
                  "| 运营利润 | %.2f |" % rep["pnl"].get("operating_profit", 0),
                  "| 单均利润 | %.2f |" % (rep["pnl"].get("per_order") or {}).get("profit", 0)]
    lines += ["", "## 说明了什么", "",
              "- 这条链路走的是**真实脚本**：CSV 适配器 → 外部订单流 → tick 仿真 → 账本 → 损益，"
              "不是把 CSV 读进来再合成一遍。",
              "- 订单到达时间由数据决定，服务时长用独立随机流，所以同一份 CSV 跑两遍结果一致。",
              "- 结论**只对这份样例 CSV 成立**：24 单、覆盖 W/F1/F2/F3，不能外推到真实业务量。",
              ""]
    return "\n".join(lines)


def do_freeze(rep: dict) -> int:
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps({
        "schema_version": "1.0", "kind": "pilot_baseline",
        "note": "真实 CSV 订单试点的确定性指标；改模型后需要显式 --freeze",
        "tolerance_rel": TOL,
        "metrics": rep["pilot"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PILOT_FROZEN %d 项 -> %s" % (len(rep["pilot"]),
                                        GOLDEN.relative_to(ROOT).as_posix()))
    return 0


def compare(expected: dict, actual: dict) -> list[str]:
    bad = []
    for k, ev in expected.items():
        av = actual.get(k)
        if av is None:
            bad.append("%s：实测缺失（期望 %r）" % (k, ev))
            continue
        if k in INT_KEYS:
            if int(ev) != int(av):
                bad.append("%s：%s → %s" % (k, ev, av))
            continue
        scale = max(abs(ev), abs(av), 1e-12)
        if abs(ev - av) / scale > TOL:
            bad.append("%s：%s → %s（相对 %.2e）" % (k, ev, av, abs(ev - av) / scale))
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="真实 CSV 订单试点")
    ap.add_argument("--check", action="store_true", help="与冻结值比对，不重冻")
    ap.add_argument("--freeze", action="store_true", help="重冻基准值")
    ap.add_argument("--no-report", action="store_true", help="只跑不写报告")
    a = ap.parse_args(argv)

    for fn in (step_csv, step_scenario):
        if fn():
            return 2
    run_dir = step_sim()
    if run_dir is None:
        return 2
    if step_ledger(run_dir):
        print("记账失败（不影响仿真结论，但损益列为空）", file=sys.stderr)

    rep = build_report(run_dir)
    if not a.no_report:
        (PILOT / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (PILOT / "report.md").write_text(render_md(rep), encoding="utf-8")
        print("PILOT_REPORT %s" % (PILOT / "report.md").relative_to(ROOT).as_posix())

    if a.freeze:
        return do_freeze(rep)
    if a.check:
        if not GOLDEN.exists():
            print("缺少 %s；先跑一次 --freeze" % GOLDEN, file=sys.stderr)
            return 2
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))["metrics"]
        bad = compare(expected, rep["pilot"])
        if bad:
            print("PILOT_DRIFT %d 处：" % len(bad))
            for b in bad:
                print("  - " + b)
            return 1
        print("PILOT_OK  %d 项指标与基准一致" % len(expected))
        return 0

    p = rep["pilot"]
    print("PILOT_OK orders=%s completed=%s on_time=%s min_sep=%s"
          % (p.get("orders_generated"), p.get("orders_completed"),
             p.get("on_time_rate"), p.get("min_pair_distance_m")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
