"""check_station.py — 证明"工位排队"不是摆设。

M1-02 把 process_spec 的服务时间接进了 tick 循环。但"接了"不等于"真的会排队"：
如果并行位足够，队列永远是 0，那这段代码就等于没写。本脚本用**同一个场景、
只改并行位**做对照，证明产能收缩真的会传导到队列、等待与吞吐：

  · slots=1 → 必须出现 STATION_QUEUED，平均等待 > 0
  · slots=4 → 排队必须消失（或显著变少），利用率必须更低（同样的活摊到更多工位）

注意：这里**不**断言"容量放开吞吐就更高"。实测正好相反（slots=1 62/h、
slots=4 40/h），原因是放开并行位后同时在路上跑的车更多，巷道拥堵反而加重
（累计受阻 26674 s → 36004 s，清障 2 → 3 次）。也就是说瓶颈在通行、不在工位。
把吞吐写成断言会把这个真实结论掩盖成一个"失败"。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = ROOT / "config" / "scenarios" / "holiday.json"   # 需求最密的场景，容量约束才看得出来


def run(slots: int, out_dir: Path):
    cmd = [sys.executable, "scripts/sim_engine.py",
           "--scenario", str(SCENARIO.relative_to(ROOT)),
           "--station-slots", str(slots),
           "--out-dir", str(out_dir)]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        tail = (p.stdout or "")[-400:] + (p.stderr or "")[-400:]
        raise SystemExit("FAIL 仿真退出码 %d\n%s" % (p.returncode, tail))
    kpi = next(out_dir.rglob("sim_kpi.json"))
    return json.loads(kpi.read_text(encoding="utf-8"))["metrics"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="工位排队可证伪检查")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="dc_station_"))
    try:
        tight = run(1, tmp / "slots1")
        loose = run(4, tmp / "slots4")
    finally:
        pass

    def val(m, k):
        v = m[k]["value"]
        return float(v)

    q_tight = val(tight, "station_queue_max")
    w_tight = val(tight, "station_queue_wait_avg_s")
    thr_tight = val(tight, "throughput_orders_per_h")
    q_loose = val(loose, "station_queue_max")
    thr_loose = val(loose, "throughput_orders_per_h")
    served = val(loose, "station_served")
    w_loose = val(loose, "station_queue_wait_avg_s")
    u_tight = val(tight, "station_utilization")
    u_loose = val(loose, "station_utilization")
    blk_tight = val(tight, "blocked_time_total_s")
    blk_loose = val(loose, "blocked_time_total_s")

    fails = []
    if served <= 0:
        fails.append("工位一次都没被用到（station_served=0）")
    if q_tight < 1 or w_tight <= 0:
        fails.append("并行位=1 仍无排队（queue_max=%s wait=%s）——排队逻辑没生效"
                     % (q_tight, w_tight))
    if q_loose >= q_tight:
        fails.append("并行位 4 的队列没有比 1 更短（%s vs %s）" % (q_loose, q_tight))
    if u_loose > u_tight + 1e-9:
        fails.append("并行位放开后利用率没降（%.4f vs %.4f）——容量没有真的摊开"
                     % (u_loose, u_tight))

    if not a.quiet:
        print("  并行位=1：queue_max=%d  wait=%.2fs  util=%.3f  throughput=%.1f/h  blocked=%.0fs"
              % (q_tight, w_tight, u_tight, thr_tight, blk_tight))
        print("  并行位=4：queue_max=%d  wait=%.2fs  util=%.3f  throughput=%.1f/h  blocked=%.0fs"
              % (q_loose, w_loose, u_loose, thr_loose, blk_loose))
        if thr_loose < thr_tight:
            print("  注：并行位放开后吞吐反而降到 %.1f/h——瓶颈在巷道通行，不在工位"
                  % thr_loose)

    shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        for f in fails:
            print("  [FAIL] %s" % f)
        print("STATION_FAIL %d 项" % len(fails))
        return 1
    print("STATION_OK 并行位 4→1：排队 %d→%d、吞吐 %.1f→%.1f/h（容量约束真的生效）"
          % (q_loose, q_tight, thr_loose, thr_tight))
    return 0


if __name__ == "__main__":
    sys.exit(main())
