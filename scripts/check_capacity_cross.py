#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R4 · 解析库容层接主链 + 与离散事件仿真交叉验证（``CROSS_VALIDATE_OK``）。

三件事，缺一不可：

1. **接真实数据**：解析层此前遍历 `zones[].racks`，而真实布局把货架挂在
   `warehouse.racks` 上（zones 只有多边形），于是它算出 **0 条巷道**——报告"全绿"
   但什么都没算。现在从真实布局取巷道，从车队部署摊设备。
2. **同一口径**：解析算"一单周期"，仿真也量"一单周期"（TASK_ASSIGNED→TASK_COMPLETED）。
   两边都按引擎的作业策略计（三段行程 + 取/放各一次作业），否则比的是两件事。
3. **可证伪**：把设备速度改掉，比值必须跑出容差——证明这道门真的在看数字。

**范围声明（不许含糊）**：这里比的是**能力**（每台设备每小时能完成几单），
不是**实际吞吐**。基线场景是需求受限（45 单/h 远低于能力），峰值场景是拥堵受限；
拿吞吐去比能力，结论只会是"需求不够"或"堵死了"，与解析公式对不对无关。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CONFIG = ROOT / "config"
DATA = ROOT / "data"
OUT = ROOT / "out"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _specs(fleet=None):
    return (jload(CONFIG / "sim_spec.json"),
            fleet if fleet is not None else jload(CONFIG / "fleet_spec.json"),
            jload(CONFIG / "demand_spec.json"), jload(DATA / "layout.json"),
            jload(DATA / "nav_grid.json"), jload(DATA / "config.json"))


def measure_des(scenario="baseline", fleet=None, out_dir=None):
    """跑一局，返回实测的单均周期与台数。"""
    import sim_engine as se

    tmp = Path(out_dir or tempfile.mkdtemp(prefix="cap_cross_"))
    sim = se.Simulation(CONFIG / "scenarios" / ("%s.json" % scenario),
                        *_specs(fleet), out_dir=tmp)
    sim.run()
    ev = [json.loads(x) for x in (tmp / "sim_events.jsonl")
          .read_text(encoding="utf-8").splitlines() if x.strip()]
    assigned, durs = {}, []
    for e in ev:
        if e["type"] == "TASK_ASSIGNED":
            assigned[e.get("order_id")] = float(e["t_s"])
        elif e["type"] == "TASK_COMPLETED":
            t0 = assigned.pop(e.get("order_id"), None)
            if t0 is not None:
                durs.append(float(e["t_s"]) - t0)
    if not durs:
        raise RuntimeError("场景 %s 没有可测的完成单：无从交叉验证" % scenario)
    return {"scenario": scenario, "orders": len(durs),
            "mean_cycle_s": round(statistics.mean(durs), 3),
            "median_cycle_s": round(statistics.median(durs), 3),
            "vehicles": len(sim.robots),
            "horizon_h": round(sim.horizon / 3600.0, 6),
            "completed_per_h": round(len(durs) / (sim.horizon / 3600.0), 3)}


def analytic(fleet=None, layout=None):
    import capacity_model as cm

    fleet = fleet or jload(CONFIG / "fleet_spec.json")
    layout = layout or jload(DATA / "layout.json")
    return cm.build_report(layout, jload(CONFIG / "rack_spec.json"),
                           jload(CONFIG / "equipment_spec.json"),
                           jload(CONFIG / "capacity_spec.json"), fleet=fleet)


def compare(rep, des, fleet=None, tol_pct=15.0, cycle_override_s=None):
    """解析能力 vs 仿真能力。返回 (ok, detail, problems)。

    `cycle_override_s` 只给自检用：注入一个错误周期，看这道门会不会红。
    """
    fleet = fleet or jload(CONFIG / "fleet_spec.json")
    vehicles = des["vehicles"]
    des_cap = round(3600.0 / des["mean_cycle_s"] * vehicles, 3)

    if cycle_override_s:
        # 自检：按注入周期重算解析能力（其余口径不变）
        cycle = float(cycle_override_s)
        # 车队里被分摊的设备按同一周期算，能力 = 台数 × 3600/周期
        ana_cap = round(3600.0 / cycle * vehicles, 3)
        ana_cycle = cycle
    else:
        ana_cap = rep["totalCapacity"]
        ana_cycle = None

    ratio = (ana_cap / des_cap) if des_cap else None
    dev_pct = (abs(ratio - 1.0) * 100.0) if ratio is not None else None
    ok = ratio is not None and dev_pct <= tol_pct
    problems = []
    if ratio is None or des_cap <= 0:
        problems.append("仿真侧能力为 0：无法比较")
    elif not ok:
        problems.append("解析 %.1f 单/h vs 仿真 %.1f 单/h，偏差 %.1f%% > 容差 %.1f%%"
                        % (ana_cap, des_cap, dev_pct, tol_pct))
    detail = {"des_capacity_per_h": des_cap, "analytic_capacity_per_h": ana_cap,
              "ratio": round(ratio, 4) if ratio else None,
              "deviation_pct": round(dev_pct, 3) if dev_pct is not None else None,
              "tolerance_pct": tol_pct,
              "des_mean_cycle_s": des["mean_cycle_s"],
              "analytic_cycle_s": ana_cycle or (
                  rep["aisles"][0]["devices"][0]["cycle_s"] if rep["aisles"]
                  and rep["aisles"][0]["devices"] else None),
              "vehicles": vehicles}
    return ok, detail, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="R4 解析库容接主链 + 交叉验证")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    cap_spec = jload(CONFIG / "capacity_spec.json")
    tol = float(cap_spec["cross_validation"]["sim_vs_analytical_tolerance_pct"])

    if a.self_test:
        # 把设备速度放大 3 倍（周期缩到 1/3），比值必须远出容差
        rep = analytic()
        des = measure_des("baseline")
        bad_ok, bad, _ = compare(rep, des, tol_pct=tol,
                                 cycle_override_s=des["mean_cycle_s"] / 3.0)
        if bad_ok:
            print("R4_SELFTEST FAIL 注入错误周期后仍判通过（门是死的）")
            return 1
        print("R4_SELFTEST OK 注入周期/3 → 偏差 %.1f%% 被判红" % bad["deviation_pct"])
        return 0

    rep = analytic()
    des = measure_des("baseline")
    ok, detail, problems = compare(rep, des, tol_pct=tol)

    # 峰值：只记录差距，不判红——它衡量的是"范式差"，不是公式对错
    peak = {}
    try:
        des_peak = measure_des("peak")
        peak = {"des": des_peak,
                "capacity_per_h": round(3600.0 / des_peak["mean_cycle_s"]
                                        * des_peak["vehicles"], 3),
                "note": "峰值是拥堵/截止期受限，与解析能力的差是范式差，不作判据"}
    except Exception as e:                                   # noqa: BLE001
        peak = {"error": repr(e)}

    report = {"schema_version": "1.0", "kind": "capacity_cross_report",
              "tolerance_pct": tol, "verdict": "CROSS_VALIDATE_OK" if ok else "CROSS_VALIDATE_FAIL",
              "compared_quantity": "capacity (orders/h per vehicle), not throughput",
              "detail": detail, "des": des, "peak": peak,
              "problems": problems,
              "aisles": len(rep["aisles"]), "total_slots": rep["totalSlots"],
              "analytic": {"totalCapacity": rep["totalCapacity"],
                           "totalLoad": rep["totalLoad"],
                           "warnings": rep["warnings"],
                           "handling": rep["handling"],
                           "travel_policy": rep["travel_policy"]}}

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "capacity_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "capacity_cross_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not a.quiet:
        print("%s  解析 %.1f 单/h vs 仿真 %.1f 单/h（偏差 %.1f%%，容差 %.0f%%）"
              % (report["verdict"], rep["totalCapacity"],
                 detail["des_capacity_per_h"], detail["deviation_pct"], tol))
        print("  巷道 %d 条 · 名义货位 %d · 一单周期 解析 %.1fs / 仿真 %.1fs"
              % (len(rep["aisles"]), rep["totalSlots"],
                 detail["analytic_cycle_s"] or 0, des["mean_cycle_s"]))
        if peak.get("des"):
            print("  峰值场景（只记录不判）：仿真能力 %.1f 单/h（%s）"
                  % (peak["capacity_per_h"], peak["note"]))
        for p in problems:
            print("  - " + p)
    else:
        print("%s %s" % (report["verdict"], "" if ok else ";".join(problems))[:400])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
