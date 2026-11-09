"""compare.py - diff two scenario runs (KPI deltas + assertions + config)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from . import paths


def _resolve(s: str) -> Path:
    p = Path(s)
    return p if p.is_absolute() else paths.ROOT / p


def load(run: str) -> dict:
    p = _resolve(run)
    if not p.is_dir():
        raise SystemExit("DC-E5001 run directory not found: %s" % p)
    if (p / "kpi.json").exists():
        kpi = json.loads((p / "kpi.json").read_text(encoding="utf-8"))
    elif (p / "sim_kpi.json").exists():
        # Stage-A tick runs carry measured KPIs in sim_kpi.json.
        kpi = json.loads((p / "sim_kpi.json").read_text(encoding="utf-8"))
    else:
        kpi = {}
    sc = json.loads((p / "scenario.json").read_text(encoding="utf-8")) if (p / "scenario.json").exists() else {}
    asr = json.loads((p / "assertions.json").read_text(encoding="utf-8")) if (p / "assertions.json").exists() else {}
    pnl = json.loads((p / "pnl.json").read_text(encoding="utf-8")) if (p / "pnl.json").exists() else {}
    return {"path": p, "id": sc.get("id", p.parent.name), "seed": sc.get("seed"),
            "metrics": kpi.get("metrics", {}), "assertions": asr, "scenario": sc,
            "pnl": pnl}


def compare(run_a: str, run_b: str, fmt: str = "md", out: str | None = None) -> int:
    a, b = load(run_a), load(run_b)
    keys = sorted(set(a["metrics"]) | set(b["metrics"]))
    rows = []
    for k in keys:
        ma, mb = a["metrics"].get(k, {}), b["metrics"].get(k, {})
        va, vb = ma.get("value"), mb.get("value")
        delta = None
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta = round(vb - va, 4)
        rows.append({"metric": k, "unit": ma.get("unit") or mb.get("unit") or "",
                     "a": va, "b": vb, "delta": delta,
                     "source": ma.get("source") or mb.get("source") or ""})
    # B6: P&L delta, recomputed from each run's pnl.json (never from the
    # ledger of the other run). Missing pnl.json => empty, explicitly stated.
    pnl_fields = ("revenue_total", "variable_cost_total", "fixed_cost_total",
                  "contribution_margin", "operating_profit", "orders_completed",
                  "breakeven_orders")
    pnl_rows = []
    for k in pnl_fields:
        va, vb = a["pnl"].get(k), b["pnl"].get(k)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            pnl_rows.append({"metric": k, "a": va, "b": vb,
                             "delta": round(vb - va, 4)})
    pa, pb = a["pnl"].get("per_order", {}), b["pnl"].get("per_order", {})
    for k in ("revenue", "cost", "contribution", "profit"):
        va, vb = pa.get(k), pb.get(k)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            pnl_rows.append({"metric": "per_order." + k, "a": va, "b": vb,
                             "delta": round(vb - va, 4)})
    payload = {"schema_version": "1.0", "kind": "run_comparison",
               "run_a": str(a["path"]), "run_b": str(b["path"]),
               "id_a": a["id"], "id_b": b["id"], "seed_a": a["seed"], "seed_b": b["seed"],
               "assertions": {"a": a["assertions"].get("verdict"),
                              "b": b["assertions"].get("verdict")},
               "deltas": rows, "pnl_deltas": pnl_rows,
               "pnl_available": {"a": bool(a["pnl"]), "b": bool(b["pnl"])},
               "prices_source": {"a": a["pnl"].get("prices_source"),
                                 "b": b["pnl"].get("prices_source")}}
    if fmt == "json":
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    elif fmt == "csv":
        import io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["metric", "unit", "a", "b", "delta", "source"])
        w.writeheader()
        w.writerows(rows)
        text = buf.getvalue()
    else:
        lines = ["# Run comparison", "", "A = `%s` (%s, seed %s) · B = `%s` (%s, seed %s)" %
                 (a["id"], a["path"].name, a["seed"], b["id"], b["path"].name, b["seed"]),
                 "", "Assertion verdicts: A=%s / B=%s" %
                 (payload["assertions"]["a"], payload["assertions"]["b"]), "",
                 "| Metric | Unit | A | B | Δ (B−A) |", "|---|---|---:|---:|---:|"]
        for r in rows:
            lines.append("| %s | %s | %s | %s | %s |" %
                         (r["metric"], r["unit"], r["a"], r["b"], r["delta"]))
        lines += ["", "KPI provenance is per metric (`source`); analytic what-if values "
                  "are not full per-vehicle A* results.", ""]
        if pnl_rows:
            lines += ["## 盈亏对比 (CNY)", "",
                      "| 项目 | A | B | Δ (B−A) |", "|---|---:|---:|---:|"]
            lines += ["| %s | %s | %s | %s |" % (r["metric"], r["a"], r["b"], r["delta"])
                      for r in pnl_rows]
            lines += ["", "价格来源 A=%s / B=%s；管理会计口径，演示价格不可用于真实决策。" %
                      (payload["prices_source"]["a"], payload["prices_source"]["b"]), ""]
        else:
            lines += ["> 两个运行都没有 pnl.json；先跑 B 阶段 ledger/pnl_report。", ""]
        text = "\n".join(lines)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print("COMPARE_OK output=%s metrics=%d" % (out, len(rows)))
    else:
        print(text)
    return 0
