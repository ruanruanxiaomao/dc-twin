#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_reorder.py —— 门 2 · REORDER_OK（补货逻辑必须可证伪，且双向）。

判据（方案书 §9 门 2）：
    同种子、同场景，只把补货点 factor 从 ×1 提到 ×3：
      stockout_count(×3) < stockout_count(×1)     # 补货更积极 → 缺货更少
      holding_cost(×3)  > holding_cost(×1)        # 库存更高 → 持有成本更高
    **只有一个动 → FAIL**：说明补货逻辑是死代码，或者持有成本没入账。

为什么必须做**库存压力**：正常库存（48 件/库位）下，1 小时 horizon 里根本不会缺货，
两组 stockout 都是 0，这道门会"永远绿"——那不是通过，是没测。所以这里显式
压库存（`--stock-scale`）并加压需求（`--demand-mult`），把机制逼到可观测区间，
参数写进报告，不许藏着。这与 `check_station.py`（并行位 4→1）、
`check_deadlock.py`（关掉退让）是同一套"强制实验"文化。

用法：
    python scripts/check_reorder.py
    python scripts/check_reorder.py --self-test   # 把 factor 写死成常量 → 必须 FAIL
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CONFIG, DATA, OUT = ROOT / "config", ROOT / "data", ROOT / "out"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def run_once(scenario, stock_scale, demand_mult, factor, constant_factor=False):
    import shutil
    import sim_engine as se
    # 实验产物必须写到 out/exp/，**绝不能落进 runs/<scenario>/**：
    # 那个目录是 step_baseline/kpi_baseline 取"最近一次运行"的地方，
    # 压力测试一旦写进去，逐字节基准就会去比一张缩放过的库存账（实测直接让
    # report_refreeze 判红）。实验与产线必须分开放。
    exp_dir = OUT / "exp" / ("reorder-%s-%s" % (factor, "const" if constant_factor else "var"))
    shutil.rmtree(exp_dir, ignore_errors=True)
    sim = se.Simulation(
        CONFIG / "scenarios" / ("%s.json" % scenario),
        jload(CONFIG / "sim_spec.json"), jload(CONFIG / "fleet_spec.json"),
        jload(CONFIG / "demand_spec.json"), jload(DATA / "layout.json"),
        jload(DATA / "nav_grid.json"), jload(DATA / "config.json"),
        out_dir=exp_dir,
    )
    for sku, rows in sim.inv.items():
        for loc in rows:
            sim.inv[sku][loc] = float(rows[loc]) * stock_scale
    if demand_mult != 1.0:
        sim.per_hour *= demand_mult
        sim.lam = sim.per_hour / 3600.0
    # constant_factor=True 用来造"补货点不随 factor 变"的假实现，供 --self-test
    sim.reorder_factor = 1.0 if constant_factor else factor
    m = sim.run()["metrics"]
    return {"stockout_count": m["stockout_count"]["value"],
            "holding_cost": m["holding_cost"]["value"],
            "fill_rate": m["fill_rate"]["value"],
            "reorder_triggers": m["reorder_triggers"]["value"],
            "orders_completed": m["orders_completed"]["value"]}


def compare(lo, hi, min_witness=3):
    """纯函数：返回 (ok, 说明)。lo=factor×1，hi=factor×3。

    `min_witness` 是防"空转通过"的：如果低因子那侧本来就没有缺货，
    「缺货下降」在 0→0 上也"成立"（0 < 0 为假，但 0≤0 的变体容易写成恒真），
    更常见的是两组都为 0，门看起来永远绿。所以要求观测到足够多的缺货样本。
    """
    ok_stock = hi["stockout_count"] < lo["stockout_count"]
    ok_hold = hi["holding_cost"] > lo["holding_cost"]
    notes = []
    if lo["stockout_count"] < min_witness:
        notes.append("实验区间不可观测：低因子侧只有 %s 次缺货（要求 ≥%d），"
                     "这道门等于没测，请调 --stock-scale/--demand-mult"
                     % (lo["stockout_count"], min_witness))
    if not ok_stock:
        notes.append("缺货没下降（%s → %s）：补货逻辑没生效"
                     % (lo["stockout_count"], hi["stockout_count"]))
    if not ok_hold:
        notes.append("持有成本没上升（%.4f → %.4f）：持有成本没入账，或补货没真加库存"
                     % (lo["holding_cost"], hi["holding_cost"]))
    return ok_stock and ok_hold and not notes, notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="门 2 · 补货可证伪（双向）")
    ap.add_argument("--scenario", default="baseline")
    # 标定记录（2026-11-12 实测）：压力太狠（scale 0.03）时补货量被"现有可用量"卡死，
    # 机制不可观测；需求太高（dm 3+）或因子太大（≥4）时补货单会挤占运力，
    # 出现"补货点越高、完成单越少"（77→37）。这台机器上唯一稳定的可观测窗口是
    # scale 0.10 / dm 2.0 / factor 1↔3：缺货 4→3、持有 0.255→0.274、完成 77→90。
    ap.add_argument("--stock-scale", type=float, default=0.10)
    ap.add_argument("--demand-mult", type=float, default=2.0)
    ap.add_argument("--self-test", action="store_true",
                    help="把补货点写死成常量，验证这道门会 FAIL")
    a = ap.parse_args(argv)

    lo = run_once(a.scenario, a.stock_scale, a.demand_mult, 1.0)
    hi = run_once(a.scenario, a.stock_scale, a.demand_mult, 3.0,
                  constant_factor=a.self_test)
    ok, notes = compare(lo, hi)

    report = {"schema_version": "1.0", "kind": "reorder_report",
              "scenario": a.scenario, "seed": 20261106,
              "stress": {"stock_scale": a.stock_scale, "demand_mult": a.demand_mult},
              "factor_1": lo, "factor_3": hi,
              "criterion": "stockout↓ AND holding↑",
              "verdict": "PASS" if ok else "FAIL", "notes": notes}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "reorder_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if a.self_test:
        # 自检期望 FAIL：补货点被写死后，缺货不会随 factor 下降
        if not ok:
            print("REORDER_SELFTEST OK  补货点写死后被判 FAIL（%s）" % "；".join(notes))
            return 0
        print("REORDER_SELFTEST FAIL  补货点写死了居然还通过 —— 这道门是假的")
        return 1

    print("REORDER_%s  stockout %s→%s · holding %.4f→%.4f · triggers %s→%s"
          % (report["verdict"], lo["stockout_count"], hi["stockout_count"],
             lo["holding_cost"], hi["holding_cost"],
             lo["reorder_triggers"], hi["reorder_triggers"]))
    for n in notes:
        print("  - " + n)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
