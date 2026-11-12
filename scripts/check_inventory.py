#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_inventory.py —— 门 1 · STOCK_OK（库位级 SKU 守恒）。

判据（方案书 §4.3）：
  对每个 (sku, location)：期初 + Σ入库 − Σ出库 − Σ损耗 == 期末，残差 < 1e-9
  对每个 sku：Σ_库位 期末 == 期初 + Σ事件净额（跨库位再对一次）

**证据来自两份互相独立的东西**：
  - 期末账：`runs/<scenario>/<ts>/sim_state.json` 的 `inventory.stock`（引擎自己报的）
  - 事件流：`sim_events.jsonl` 重放（事实来源）
两者对上才算守恒。只算一边（只信状态或只信事件）永远"自证成功"。

`--self-test` 会把期末改 +1e-6，要求审计**必须失败**——否则这道门是装饰。

用法：
    python scripts/check_inventory.py                 # 审计最近一次 baseline
    python scripts/check_inventory.py --scenario peak
    python scripts/check_inventory.py --self-test
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT, RUNS = ROOT / "data", ROOT / "out", ROOT / "runs"
TOL = 1e-9

DEC = ("STOCK_DECREMENT",)
INC = ("STOCK_RECEIPT", "STOCK_REPLENISH_ARRIVED")


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def latest_run(scenario="baseline"):
    cands = sorted(glob.glob(str(RUNS / scenario / "*" / "sim_state.json")),
                   key=lambda p: Path(p).stat().st_mtime)
    if not cands:
        raise SystemExit("找不到 %s 的运行产物，先跑 `python -m dc_twin run --scope scenarios`"
                         % scenario)
    return Path(cands[-1]).parent


def replay(initial, events):
    """从期初 + 事件流重放期末。返回 (stock, receipts, decrements)。"""
    stock = {sku: {loc: float(q) for loc, q in rows.items()}
             for sku, rows in initial["stock"].items()}
    inc, dec = {}, {}
    for e in events:
        typ, sku, loc = e.get("type"), e.get("sku"), e.get("location")
        qty = float(e.get("qty") or 0.0)
        if typ in INC:
            stock.setdefault(sku, {})
            stock[sku][loc] = stock[sku].get(loc, 0.0) + qty
            inc[(sku, loc)] = inc.get((sku, loc), 0.0) + qty
        elif typ in DEC:
            stock.setdefault(sku, {})
            stock[sku][loc] = stock[sku].get(loc, 0.0) - qty
            dec[(sku, loc)] = dec.get((sku, loc), 0.0) + qty
        elif typ == "PICKFACE_REFILL":
            # 内部移库：源减、目标加。必须显式处理，否则重放会少一块库存，
            # 而"少的那块"恰好会被误判成"引擎多记了"。
            rs, pf = e.get("reserve_location"), e.get("pickface_location")
            stock.setdefault(sku, {})
            stock[sku][rs] = stock[sku].get(rs, 0.0) - qty
            stock[sku][pf] = stock[sku].get(pf, 0.0) + qty
            dec[(sku, rs)] = dec.get((sku, rs), 0.0) + qty
            inc[(sku, pf)] = inc.get((sku, pf), 0.0) + qty
    return stock, inc, dec


def audit(run_dir, initial, tamper=None):
    state = jload(run_dir / "sim_state.json")
    inv = state.get("inventory")
    if not inv or "stock" not in inv:
        return {"verdict": "FAIL", "error": "sim_state 没有 inventory（库存层未启用？）"}, []
    reported = {sku: {loc: float(q) for loc, q in rows.items()}
                for sku, rows in inv["stock"].items()}
    if tamper:
        tamper(reported)
    events = [json.loads(x) for x in (run_dir / "sim_events.jsonl")
              .read_text(encoding="utf-8").splitlines() if x.strip()]
    stock, inc, dec = replay(initial, events)

    rows, bad = [], []
    for sku in sorted(set(stock) | set(reported)):
        for loc in sorted(set(stock.get(sku, {})) | set(reported.get(sku, {}))):
            opening = float(initial["stock"].get(sku, {}).get(loc, 0.0))
            got_in = inc.get((sku, loc), 0.0)
            got_out = dec.get((sku, loc), 0.0)
            expect = opening + got_in - got_out
            actual = reported.get(sku, {}).get(loc, 0.0)
            residual = actual - expect
            rows.append({"sku": sku, "location": loc, "opening": opening,
                         "in": round(got_in, 6), "out": round(got_out, 6),
                         "closing_expected": round(expect, 6),
                         "closing_reported": round(actual, 6),
                         "residual": residual})
            if abs(residual) > TOL:
                bad.append(rows[-1])
    # 跨库位再对一次（按 SKU 汇总）
    sku_bad = []
    for sku in sorted(set(stock) | set(reported)):
        e = sum(float(initial["stock"].get(sku, {}).get(l, 0.0)) for l in initial["stock"].get(sku, {}))
        e += sum(v for (s, _l), v in inc.items() if s == sku)
        e -= sum(v for (s, _l), v in dec.items() if s == sku)
        a = sum(reported.get(sku, {}).values())
        if abs(a - e) > TOL:
            sku_bad.append({"sku": sku, "expected": round(e, 6), "reported": round(a, 6)})
    verdict = "PASS" if not bad and not sku_bad else "FAIL"
    report = {"schema_version": "1.0", "kind": "inventory_report",
              "run": run_dir.name, "tolerance": TOL, "verdict": verdict,
              "pairs": len(rows), "mismatched_pairs": len(bad),
              "sku_mismatch": sku_bad[:20],
              "summary": {"initial_units": round(sum(
                  float(q) for rows_ in initial["stock"].values() for q in rows_.values()), 6),
                  "in_units": round(sum(inc.values()), 6),
                  "out_units": round(sum(dec.values()), 6),
                  "closing_units": round(sum(q for rows_ in reported.values()
                                             for q in rows_.values()), 6)},
              "sample_mismatch": bad[:5]}
    return report, rows


def write_report(report, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    thin = dict(report, pairs_detail=rows if len(rows) <= 50 else rows[:50])
    (OUT / "inventory_report.json").write_text(
        json.dumps(thin, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def self_test(run_dir, initial):
    """证明审计会失败：把某个期末结存改 +1e-6，必须被抓到。"""
    def tamper(stock):
        sku = sorted(stock)[0]
        loc = sorted(stock[sku])[0]
        stock[sku][loc] += 1e-6
    rep, _ = audit(run_dir, initial, tamper=tamper)
    if rep["verdict"] == "FAIL" and rep["mismatched_pairs"] >= 1:
        print("INVENTORY_SELFTEST OK  期末 +1e-6 被抓到（残差 %.1e）"
              % rep["sample_mismatch"][0]["residual"])
        return 0
    print("INVENTORY_SELFTEST FAIL  改了一位居然还是 PASS —— 这道门是假的")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="门 1 · 库位级 SKU 守恒")
    ap.add_argument("--scenario", default="baseline")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    run_dir = Path(a.run_dir) if a.run_dir else latest_run(a.scenario)
    initial = jload(DATA / "inventory_initial.json")
    if a.self_test:
        return self_test(run_dir, initial)

    report, rows = audit(run_dir, initial)
    write_report(report, rows)
    if not a.quiet:
        print("INVENTORY_%s %s pairs=%d mismatched=%d units(期初 %s / 入 %s / 出 %s / 期末 %s)"
              % (report["verdict"], run_dir.name, report["pairs"],
                 report["mismatched_pairs"], report["summary"]["initial_units"],
                 report["summary"]["in_units"], report["summary"]["out_units"],
                 report["summary"]["closing_units"]))
        for r in report.get("sample_mismatch") or []:
            print("  - %s@%s 期望 %s 实报 %s（残差 %.2e）"
                  % (r["sku"], r["location"], r["closing_expected"],
                     r["closing_reported"], r["residual"]))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
