#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_dock.py — 月台门占用与排队：独立复算 + 容量对照。

v4.7 之前月台只有"能被封"一个行为：车到了停 2 s、不占门、不计工时。于是 5 扇门
和 1 扇门在仿真里完全等价，"装卸"这件事在模型里不存在。接入之后要回答两个问题：

  1. **账对不对**：从事件独立复算装卸次数、平均时长、占用率，跟引擎自报的 KPI 对账。
     只信自报数字的守卫等于没做守卫。
  2. **容量约束真的生效吗**：同一份压力需求（只有出库单）下把门数从 8 压到 1，
     必须出现等门队列与等待；门放开后队列必须消化掉。
     一个"永远不排队"的队列，和一段没写的代码，观测结果一模一样。

基线（45 单/h、38 扇门）下门远未饱和——占用率只有 2–7%、队列 0。这是结论，不是
失败：它说明这个仓当前的瓶颈不在月台。所以容量证明只能靠压力对照，不能靠基线。

已知边界：月台走廊是一条车道，所以模型里"门"是时间窗资源（一次装卸占 150/180 s），
AGV 只在门口做短交接。若让 AGV 在门口干等整个装卸时长，车会把走廊堵死
（实测 peak 59→35 单），那是栅格布线的产物，不是装卸工时该有的效果。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "scripts" / "sim_engine.py"
BASE = ROOT / "config" / "scenarios" / "baseline.json"


def run(scenario: Path, out_dir: Path, doors=None, horizon=None):
    cmd = [sys.executable, str(ENGINE), "--scenario", str(scenario), "--out-dir", str(out_dir)]
    if doors:
        cmd += ["--dock-slots", str(doors)]
    if horizon:
        cmd += ["--horizon", str(horizon)]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("FAIL 仿真退出码 %d\n%s\n%s"
                         % (p.returncode, (p.stdout or "")[-400:], (p.stderr or "")[-400:]))
    kpi_p = sorted(out_dir.rglob("sim_kpi.json"))[0]
    kpi = json.loads(kpi_p.read_text(encoding="utf-8"))["metrics"]
    ev = [json.loads(line) for line in
          (kpi_p.parent / "sim_events.jsonl").read_text(encoding="utf-8").splitlines()
          if line.strip()]
    return kpi, ev


def val(m, k):
    return float(m[k]["value"])


def write_stress(tmp: Path) -> Path:
    """只跑出库单：需求远高于少量门的产能，队列必然出现。"""
    sc = json.loads(BASE.read_text(encoding="utf-8"))
    sc["id"] = "dock_stress"
    sc["name"] = "月台门容量压力（仅出库单）"
    sc["overrides"] = {"demand.orders.per_hour": 300,
                       "demand.orders.mix": {"OUTBOUND": 1.0},
                       "fleet.size": 14}
    sc.pop("disruptions", None)
    sc.pop("assertions", None)
    p = tmp / "dock_stress.json"
    p.write_text(json.dumps(sc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="月台门占用/排队：复算与容量对照")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="dc_dock_"))
    m_base, ev_base = run(BASE, tmp / "base")
    stress = write_stress(tmp)
    m1, _ = run(stress, tmp / "doors1", doors=1, horizon=1200)
    m8, _ = run(stress, tmp / "doors8", doors=8, horizon=1200)

    fails = []

    # ---- 1) 门数与工时来源：直接读 layout / process_spec 对账 ----
    layout = json.loads((ROOT / "data" / "layout.json").read_text(encoding="utf-8"))
    doors = sum(len(d.get("doors") or [])
                for w in layout["warehouses"] for d in w.get("docks") or [])
    zones = sum(len(w.get("docks") or []) for w in layout["warehouses"])
    if int(val(m_base, "dock_doors")) != doors:
        fails.append("门数应为 %d，引擎报 %d" % (doors, int(val(m_base, "dock_doors"))))
    if int(val(m_base, "docks_defined")) != zones:
        fails.append("月台区数应为 %d，引擎报 %d" % (zones, int(val(m_base, "docks_defined"))))

    proc = json.loads((ROOT / "config" / "process_spec.json").read_text(encoding="utf-8"))
    t_in = next((s["time_s"] for s in proc["process_steps"] if s["from"] == "DOCK_IN"), None)
    t_out = next((s["time_s"] for s in proc["process_steps"] if s["to"] == "DOCK_OUT"), None)
    if not (t_in and t_out):
        fails.append("process_spec 缺 DOCK_IN/DOCK_OUT 工时，装卸时长没有来源")
    else:
        mean = val(m_base, "dock_service_mean_s")
        if not min(t_in, t_out) * 0.5 <= mean <= max(t_in, t_out) * 1.5:
            fails.append("实测装卸 %.1fs 与过程规格 %s/%s s 不在同一量级" % (mean, t_in, t_out))

    # ---- 2) 从事件独立复算：次数 / 时长 / 占用率 / 交接 ----
    reserved = [e for e in ev_base if e["type"] == "DOCK_RESERVED"]
    handoff = [e for e in ev_base if e["type"] == "DOCK_HANDOFF"]
    if len(reserved) != int(val(m_base, "dock_served")):
        fails.append("dock_served 复算不一致：事件 %d，引擎 %d"
                     % (len(reserved), int(val(m_base, "dock_served"))))
    if len(handoff) != int(val(m_base, "dock_handoffs")):
        fails.append("dock_handoffs 复算不一致：事件 %d，引擎 %d"
                     % (len(handoff), int(val(m_base, "dock_handoffs"))))

    horizon = val(m_base, "sim_duration_s")
    busy = sum(float(e.get("service_s") or 0.0) for e in reserved)
    if reserved:
        mean_ev = busy / len(reserved)
        if abs(mean_ev - val(m_base, "dock_service_mean_s")) > 1e-3:
            fails.append("平均装卸时长复算不一致：%.3f vs %.3f"
                         % (mean_ev, val(m_base, "dock_service_mean_s")))
    util_ev = busy / (doors * horizon) if doors and horizon else 0.0
    if abs(util_ev - val(m_base, "dock_utilization")) > 2e-3:
        fails.append("占用率复算不一致：%.4f vs %.4f" % (util_ev, val(m_base, "dock_utilization")))

    # ---- 3) 容量对照：门少必须排队、等待；门多必须消化掉 ----
    q1, w1, d1 = (val(m1, "dock_queue_max"), val(m1, "dock_queue_wait_avg_s"),
                  val(m1, "orders_completed"))
    q8, w8, d8 = (val(m8, "dock_queue_max"), val(m8, "dock_queue_wait_avg_s"),
                  val(m8, "orders_completed"))
    if int(val(m1, "dock_doors")) != zones:
        fails.append("--dock-slots 1 后门数应等于区数 %d，实际 %d"
                     % (zones, int(val(m1, "dock_doors"))))
    if q1 < 1 or w1 <= 0:
        fails.append("1 扇门/区时没有出现等门（qmax=%d, wait=%.1fs）——容量约束没生效"
                     % (q1, w1))
    if w8 >= w1:
        fails.append("门数 1→8 等待没有下降（%.1f → %.1f s）" % (w1, w8))

    if not a.quiet or fails:
        print("  基线 %d 门 / %d 区：装卸 %d 次（AGV 交接 %d 次），平均 %.1fs，占用率 %.3f"
              % (doors, zones, int(val(m_base, "dock_served")),
                 int(val(m_base, "dock_handoffs")),
                 val(m_base, "dock_service_mean_s"), val(m_base, "dock_utilization")))
        capacity = doors * horizon / max(val(m_base, "dock_service_mean_s"), 1e-9)
        print("  门产能上限约 %.0f 次/班，实际 %d 次 → 余量 %.0f 倍（月台不是瓶颈）"
              % (capacity, int(val(m_base, "dock_served")),
                 capacity / max(val(m_base, "dock_served"), 1)))
        print("  压力对照（300 单/h 仅出库，20 min）：")
        print("    1 门/区：qmax=%2d  wait=%6.1fs  完成 %d 单" % (q1, w1, d1))
        print("    8 门/区：qmax=%2d  wait=%6.1fs  完成 %d 单" % (q8, w8, d8))

    if fails:
        for f in fails:
            print("  [FAIL] %s" % f)
        print("DOCK_FAIL %d 项" % len(fails))
        return 1
    print("DOCK_OK %d 门 / %d 区 · 装卸 %d 次 %.1fs/次 · 事件复算一致 · "
          "门 1→8：等待 %.0f→%.0f s、完成 %d→%d 单"
          % (doors, zones, int(val(m_base, "dock_served")),
             val(m_base, "dock_service_mean_s"), w1, w8, d1, d8))
    return 0


if __name__ == "__main__":
    sys.exit(main())
