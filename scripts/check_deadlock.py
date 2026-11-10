"""check_deadlock.py — 证明"死锁退让"不是装饰。

背景：`sim_engine` 的交通层是**反应式距离门控**——只看"下一步会不会贴到别的车"。
这种实现天然会在窄巷道里互相顶死：A 等 B 让、B 等 A 让，两边都不动。项目原来的
吞吐指标一直在被这个缺陷压低，只是 40 项验收里没有一条检查"车有没有卡住"。

本脚本用**同一个场景、只改退让阈值**做对照（DC_YIELD_AFTER_S）：

  · 关闭退让（阈值调成 999999）：必须出现长时间顶死、必须更多丢单
  · 默认退让（8 s）：            顶死必须被压到清障阈值以内、吞吐必须更高

只要关闭退让时吞吐没有明显变差，就说明退让这段代码其实没有起作用。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = ROOT / "config" / "scenarios" / "peak.json"      # 车多、需求密，最容易顶死
JAM_CLEAR_AFTER_S = 300.0                                   # 与 sim_engine 保持一致


def run(yield_after: float, out_dir: Path):
    env = dict(os.environ)
    env["DC_YIELD_AFTER_S"] = str(yield_after)
    cmd = [sys.executable, "scripts/sim_engine.py",
           "--scenario", str(SCENARIO.relative_to(ROOT)),
           "--out-dir", str(out_dir)]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if p.returncode != 0:
        tail = (p.stdout or "")[-400:] + (p.stderr or "")[-400:]
        raise SystemExit("FAIL 仿真退出码 %d\n%s" % (p.returncode, tail))
    kpi = next(Path(out_dir).rglob("sim_kpi.json"))
    return json.loads(kpi.read_text(encoding="utf-8"))["metrics"]


def val(m, k):
    return float(m[k]["value"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="死锁退让可证伪检查")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="dc_deadlock_"))
    off = run(999999, tmp / "yield_off")
    on = run(8, tmp / "yield_on")

    thr_off, thr_on = val(off, "throughput_orders_per_h"), val(on, "throughput_orders_per_h")
    lost_off, lost_on = val(off, "orders_lost"), val(on, "orders_lost")
    blk_off, blk_on = val(off, "max_blocked_s"), val(on, "max_blocked_s")
    tot_off = val(off, "blocked_time_total_s")
    tot_on = val(on, "blocked_time_total_s")
    jam_off, jam_on = val(off, "jam_clears"), val(on, "jam_clears")
    y_on = val(on, "deadlock_yields")

    fails = []
    if blk_off < JAM_CLEAR_AFTER_S:
        fails.append("关闭退让后最长受阻只有 %.0fs（< %.0fs）——说明这个场景本来就不会顶死，"
                     "对照实验无效" % (blk_off, JAM_CLEAR_AFTER_S))
    if thr_on <= thr_off + 1e-9:
        fails.append("开启退让后吞吐没有变高（%.1f vs %.1f）——退让没起作用"
                     % (thr_on, thr_off))
    if tot_off < 2.0 * tot_on:
        fails.append("关闭退让后全员累计受阻只有 %.0fs，不到开启时（%.0fs）的两倍——"
                     "对照不成立" % (tot_off, tot_on))
    if y_on <= 0:
        fails.append("退让计数为 0，代码路径没被走到")
    if blk_on > JAM_CLEAR_AFTER_S + 1e-6:
        fails.append("开启退让后仍有车被挡 %.0fs 超过清障阈值 %.0fs"
                     % (blk_on, JAM_CLEAR_AFTER_S))
    if lost_on > lost_off:
        fails.append("开启退让后丢单反而更多（%.0f vs %.0f）" % (lost_on, lost_off))

    if not a.quiet:
        print("  退让关闭：吞吐 %5.1f/h  丢单 %3.0f  累计受阻 %7.0fs  清障 %2.0f 次"
              % (thr_off, lost_off, tot_off, jam_off))
        print("  退让开启：吞吐 %5.1f/h  丢单 %3.0f  累计受阻 %7.0fs  清障 %2.0f 次  退让 %4.0f 次"
              % (thr_on, lost_on, tot_on, jam_on, y_on))

    if fails:
        print("DEADLOCK_FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print("DEADLOCK_OK throughput %.0f -> %.0f /h, blocked time %.0f -> %.0f s"
          % (thr_off, thr_on, tot_off, tot_on))
    return 0


if __name__ == "__main__":
    sys.exit(main())
