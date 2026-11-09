"""pnl_report.py - management P&L from the event-sourced ledger (COMPLETION_PLAN 4.4).

Every figure is an aggregation of ledger.jsonl; the validator (validate_pnl.py)
recomputes it independently. Scope is internal management accounting, never an
external financial statement.

Usage:
  python scripts/pnl_report.py --all
  python scripts/pnl_report.py --run runs/baseline/<ts>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG, OUT = ROOT / "config", ROOT / "out"
SCOPE = ("内部管理会计口径（运营决策用），非对外财务报表、非税务合规；"
         "固定成本按运行时长/班次分摊，分摊口径 basis 见 cost_spec.allocation")


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lines(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]


def aggregate(entries):
    accounts = {}
    for e in entries:
        accounts[e["account"]] = round(accounts.get(e["account"], 0.0) + e["amount"], 6)
    return accounts


def report(run_dir, cost):
    d = Path(run_dir)
    entries = lines(d / "ledger.jsonl")
    meta = jload(d / "ledger_meta.json")
    kpi = jload(d / "sim_kpi.json") if (d / "sim_kpi.json").exists() else {}
    accounts = aggregate(entries)
    rev = round(sum(v for k, v in accounts.items() if k.startswith("Revenue:")), 6)
    variable = round(sum(v for k, v in accounts.items()
                         if k.startswith("Cost:Variable:") or k in
                         ("Cost:SLA_Penalty", "Cost:LostOrder")), 6)
    fixed = round(sum(v for k, v in accounts.items() if k.startswith("Cost:Fixed:")), 6)
    margin = round(rev - variable, 6)
    profit = round(margin - fixed, 6)
    completed = (kpi.get("metrics", {}).get("orders_completed", {}).get("value")
                 or sum(1 for e in entries if e["memo"] == "TASK_COMPLETED"
                        and e["account"].startswith("Revenue:")))
    n = max(completed, 1)
    per = {"revenue": round(rev / n, 6), "cost": round((variable + fixed) / n, 6),
           "contribution": round(margin / n, 6), "profit": round(profit / n, 6)}
    be = math.ceil(fixed / per["contribution"]) if per["contribution"] > 0 else None

    energy = accounts.get("Cost:Variable:Energy", 0.0)
    var_ex_energy = variable - energy
    sens = {
        "energy_tariff_pm20": {
            "low": round(profit + energy * 0.2, 6), "base": profit,
            "high": round(profit - energy * 0.2, 6), "note": "电价 -20% / +20%"},
        "order_volume_pm20": {
            "low": round(profit - (rev - var_ex_energy) * 0.2, 6), "base": profit,
            "high": round(profit + (rev - var_ex_energy) * 0.2, 6),
            "note": "单量 -20% / +20%，固定成本不变"},
        "fleet_pm2": {
            "low": round(profit + (accounts.get("Cost:Fixed:Depreciation", 0.0)
                                   + accounts.get("Cost:Fixed:Maintenance", 0.0))
                         * 2 / max(meta["agents"], 1), 6),
            "base": profit,
            "high": round(profit - (accounts.get("Cost:Fixed:Depreciation", 0.0)
                                    + accounts.get("Cost:Fixed:Maintenance", 0.0))
                          * 2 / max(meta["agents"], 1), 6),
            "note": "车数 -2 / +2，只影响折旧与维护"},
    }
    pnl = {"schema_version": "1.0", "kind": "pnl", "run_id": meta["run_id"],
           "engine": meta["engine"], "currency": cost["currency"],
           "prices_source": cost["prices_source"], "scope": SCOPE,
           "degradation": meta["degradation"], "mode": meta["mode"],
           "run_hours": meta["run_hours"], "agents": meta["agents"],
           "orders_completed": completed,
           "accounts": accounts,
           "revenue_total": rev, "variable_cost_total": variable,
           "fixed_cost_total": fixed, "total_cost": round(variable + fixed, 6),
           "contribution_margin": margin, "operating_profit": profit,
           "per_order": per, "breakeven_orders": be, "sensitivity": sens,
           "ledger_postings": len(entries),
           "ledger_amount_total": round(sum(e["amount"] for e in entries), 6)}
    jdump(d / "pnl.json", pnl)

    # cost_per_order.csv - one row per delivered order
    energy_each = energy / n
    with open(d / "cost_per_order.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "order_type", "revenue", "consumables",
                    "energy_allocated", "penalty", "contribution"])
        for e in entries:
            if e["account"].startswith("Revenue:") and e["memo"] == "TASK_COMPLETED":
                seq = e["source_event_seq"]
                pen = sum(x["amount"] for x in entries
                          if x["source_event_seq"] == seq
                          and x["account"] in ("Cost:SLA_Penalty", "Cost:LostOrder"))
                cons = sum(x["amount"] for x in entries
                           if x["source_event_seq"] == seq
                           and x["account"] == "Cost:Variable:Consumables")
                w.writerow([e["order_id"], e["account"].split(":", 1)[1],
                            e["amount"], round(cons, 6), round(energy_each, 6),
                            round(pen, 6),
                            round(e["amount"] - cons - energy_each - pen, 6)])

    badge = ("**⚠️ 演示价格（prices_source=demo）**：全部单价为演示值，"
             "禁止直接用于真实经营决策。\n\n"
             if cost["prices_source"] == "demo" else
             "价格来源：`%s`\n\n" % cost["prices_source"])
    md = ["# 运营损益表 · %s" % meta["run_id"], "",
          badge,
          "> %s" % SCOPE, "> 引擎：`%s`；%s" % (meta["engine"], meta["degradation"]), "",
          "| 项目 | 金额 (%s) |" % cost["currency"], "|---|---:|",
          "| 收入合计 | %.2f |" % rev,
          "| 变动成本与罚金 | %.2f |" % variable,
          "| 边际贡献 | %.2f |" % margin,
          "| 固定成本分摊 | %.2f |" % fixed,
          "| 运营利润 | **%.2f** |" % profit, "",
          "## 明细", "",
          "| 账户 | 金额 |", "|---|---:|"]
    md += ["| %s | %.4f |" % (k, v) for k, v in sorted(accounts.items())]
    md += ["", "## 单均与盈亏平衡", "",
           "- 完成单量：%d；单均收入 %.2f；单均成本 %.2f；单均边际贡献 %.2f；单均利润 %.2f"
           % (completed, per["revenue"], per["cost"], per["contribution"], per["profit"]),
           "- 盈亏平衡点：%s 单/班（固定成本 %.2f ÷ 单均边际贡献 %.2f）"
           % (be if be is not None else "不可达（边际贡献≤0）", fixed, per["contribution"]),
           "", "## 敏感性", "",
           "| 因素 | -20% / -2 | 基准 | +20% / +2 |", "|---|---:|---:|---:|"]
    for k, v in sens.items():
        md.append("| %s | %.2f | %.2f | %.2f |" % (v["note"], v["low"], v["base"], v["high"]))
    md += ["", "> 数据来源：`ledger.jsonl`（%d 笔分录）；金额合计 %.4f；"
           "可用 `scripts/validate_pnl.py` 独立复算。" % (len(entries), pnl["ledger_amount_total"]), ""]
    (d / "pnl.md").write_text("\n".join(md), encoding="utf-8")
    print("PNL_OK %s profit=%.2f breakeven=%s -> %s"
          % (meta["run_id"], profit, be, d / "pnl.json"))
    return {"run_id": meta["run_id"], "run_dir": str(d),
            "operating_profit": profit, "revenue": rev, "breakeven_orders": be}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--cost-spec", default=str(CONFIG / "cost_spec.json"))
    ap.add_argument("--no-index", action="store_true",
                    help="do not overwrite out/pnl_index.json (isolated custom runs)")
    args = ap.parse_args(argv)
    cost = jload(args.cost_spec)
    if args.all:
        idx = jload(OUT / "ledger_index.json")
        runs = [e["run_dir"] for e in idx["runs"]]
    elif args.run:
        runs = [args.run]
    else:
        runs = [jload(OUT / "ledger_index.json")["runs"][-1]["run_dir"]]
    result = [report(r, cost) for r in runs]
    if not args.no_index:
        jdump(OUT / "pnl_index.json", {"schema_version": "1.0", "kind": "pnl_index",
                                       "runs": result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
