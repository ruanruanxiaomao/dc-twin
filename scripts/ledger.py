"""ledger.py - event-sourced management ledger (COMPLETION_PLAN 4.3, stage B).

Consumes sim_events.jsonl (engine=sim) or, as the E1/B0 degradation path,
out/fleet_report.json replay data (engine=replay). Every posting carries the
source event seq; re-running never double-posts (F3).

Usage:
  python scripts/ledger.py --all
  python scripts/ledger.py --run runs/baseline/<ts>
  python scripts/ledger.py --source replay --run out
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG, OUT = ROOT / "config", ROOT / "out"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lines(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]


def area_m2():
    layout = jload(ROOT / "data" / "layout.json")
    return sum(w["size"]["width"] * w["size"]["depth"] for w in layout["warehouses"])


def replay_events(report_path, out_dir):
    """E1/B0: derive a minimal, explicitly-labelled event stream from the
    offline replay report. It is NOT a tick simulation."""
    rep = jload(report_path)
    ev, seq = [], 0

    def emit(t, typ, **kw):
        nonlocal seq
        seq += 1
        ev.append({"seq": seq, "t_s": t, "type": typ, **kw})

    kwh = sum(b["used_kwh"] for b in rep["battery"])
    for i, b in enumerate(rep["battery"]):
        emit(i * 10.0, "TASK_COMPLETED", agent=b["agent_id"],
             order_id="REPLAY-%03d" % (i + 1), order_type="OUTBOUND",
             legs_m={"empty_out": b["distance_m"] * 0.3,
                     "loaded": b["distance_m"] * 0.4,
                     "empty_back": b["distance_m"] * 0.3},
             energy_kwh=b["used_kwh"], tardiness_s=0.0,
             pick_loc="REPLAY", drop_loc="REPLAY", reassignments=0)
    emit(rep["agents"] * 10.0, "ENERGY_TICK", window_s=rep["agents"] * 10.0,
         motion_kwh=kwh, idle_kwh=0.0, charge_kwh=0.0)
    emit(rep["agents"] * 10.0, "RUN_SETTLED", horizon_s=3600.0,
         orders_completed=len(rep["battery"]), energy_out_kwh=kwh, energy_in_kwh=0.0)
    src = Path(out_dir) / "ledger_source_events.jsonl"
    src.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ev), encoding="utf-8")
    return ev, str(src)


def load_events(run_dir, source="auto"):
    d = Path(run_dir)
    sim = d / "sim_events.jsonl"
    if source in ("auto", "sim") and sim.exists():
        return lines(sim), "sim", str(sim), (jload(d / "sim_kpi.json")
                                             if (d / "sim_kpi.json").exists() else {})
    rep = d / "fleet_report.json" if d.name == "out" else OUT / "fleet_report.json"
    if not rep.exists():
        raise SystemExit("LEDGER_FAIL no sim_events.jsonl and no fleet_report.json in %s" % d)
    ev, src = replay_events(rep, d)
    return ev, "replay", src, {}


def fixed_costs(cost, hours, area, agents):
    f = cost["fixed_costs_per_day"]
    lab = f["labor"]
    dep = f["robot_depreciation"]
    capex_total = dep["capex_per_unit"] * agents
    return {
        "Labor": round(lab["operators"] * lab["hourly_rate"]
                       * (1 + lab["supervision_ratio"]) * hours, 6),
        "Depreciation": round(dep["capex_per_unit"] / dep["life_h"] * agents * hours, 6),
        "Maintenance": round(capex_total * f["maintenance_pct_of_capex_per_year"]
                             / (365 * 24) * hours, 6),
        "Rent": round(f["space_rent_per_m2_month"] * area / (30 * 24) * hours, 6),
        "IT": round(f["it_systems_per_day"] / 24 * hours, 6),
    }


def build_entries(events, cost, run_id, hours, area, agents):
    """Pure mapping: event seq -> postings. Deterministic and recomputable."""
    rev, var = cost["revenue"], cost["variable_costs"]
    out = []

    def post(e, account, amount, direction, **kw):
        out.append({"source_event_seq": e["seq"], "run_id": run_id,
                    "t_s": e["t_s"], "account": account,
                    "amount": round(amount, 6), "direction": direction,
                    "order_id": e.get("order_id"), "cost_center": e.get("warehouse"),
                    "memo": e["type"], **kw})

    for e in events:
        typ = e["type"]
        if typ == "TASK_COMPLETED":
            unit = rev["per_order"].get(e["order_type"], 0.0) + rev["per_line_extra"]
            post(e, "Revenue:" + e["order_type"], unit, "credit",
                 unit_price=unit)
            post(e, "Cost:Variable:Consumables", var["consumables_per_order"], "debit")
            if e.get("tardiness_s", 0) > 0:
                post(e, "Cost:SLA_Penalty",
                     e["tardiness_s"] * rev["sla_penalty_per_tardy_s"], "debit")
        elif typ == "ORDER_LOST":
            post(e, "Cost:LostOrder", rev["lost_order_penalty"], "debit")
        elif typ == "ENERGY_TICK":
            kwh = e["motion_kwh"] + e["idle_kwh"]
            post(e, "Cost:Variable:Energy",
                 kwh * var["energy_tariff_per_kwh"], "debit", kwh=kwh)
        elif typ == "RUN_SETTLED":
            for name, amount in fixed_costs(cost, hours, area, agents).items():
                post(e, "Cost:Fixed:" + name, amount, "debit")
    return out


def append_idempotent(path, entries):
    """F3: (source_event_seq, account) is the idempotency key."""
    seen = set()
    if path.exists():
        for x in lines(path):
            seen.add((x["source_event_seq"], x["account"]))
    fresh = [e for e in entries if (e["source_event_seq"], e["account"]) not in seen]
    with open(path, "a", encoding="utf-8") as f:
        for e in fresh:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return len(fresh), len(entries) - len(fresh)


def validate(entries, events, cost):
    """F2 + F3 core checks; returns issue list (empty = PASS)."""
    issues = []
    seqs = {e["seq"] for e in events}
    by_seq = {e["seq"]: e for e in events}
    keys = [(x["source_event_seq"], x["account"]) for x in entries]
    if len(keys) != len(set(keys)):
        issues.append("duplicate (source_event_seq, account) postings")
    for x in entries:
        if x["source_event_seq"] not in seqs:
            issues.append("orphan seq %s" % x["source_event_seq"])
            continue
        src = by_seq[x["source_event_seq"]]
        expected = None
        if x["account"].startswith("Revenue:"):
            expected = (cost["revenue"]["per_order"].get(src.get("order_type"), 0.0)
                        + cost["revenue"]["per_line_extra"])
        elif x["account"] == "Cost:Variable:Consumables":
            expected = cost["variable_costs"]["consumables_per_order"]
        elif x["account"] == "Cost:Variable:Energy":
            expected = ((src["motion_kwh"] + src["idle_kwh"])
                        * cost["variable_costs"]["energy_tariff_per_kwh"])
        elif x["account"] == "Cost:SLA_Penalty":
            expected = (src["tardiness_s"]
                        * cost["revenue"]["sla_penalty_per_tardy_s"])
        elif x["account"] == "Cost:LostOrder":
            expected = cost["revenue"]["lost_order_penalty"]
        if expected is not None and abs(x["amount"] - round(expected, 6)) > 1e-5:
            issues.append("amount mismatch seq=%s account=%s got=%s expected=%s"
                          % (x["source_event_seq"], x["account"], x["amount"], expected))
    return issues


def process(run_dir, cost, source="auto", rebuild=False):
    d = Path(run_dir)
    events, engine, src_path, kpi = load_events(d, source)
    settled = next((e for e in events if e["type"] == "RUN_SETTLED"), None)
    if not settled:
        raise SystemExit("LEDGER_FAIL no RUN_SETTLED event in %s" % src_path)
    hours = settled["horizon_s"] / 3600.0
    agents = (kpi.get("metrics", {}).get("agents", {}).get("value")
              or cost["fixed_costs_per_day"]["robot_depreciation"]["units"])
    area = area_m2()
    run_id = "replay/main" if engine == "replay" else "%s/%s" % (d.parent.name, d.name)
    entries = build_entries(events, cost, run_id, hours, area, agents)
    ledger = d / "ledger.jsonl"
    if rebuild and ledger.exists():
        ledger.unlink()
    added, skipped = append_idempotent(ledger, entries)
    meta = {"schema_version": "1.0", "kind": "ledger_meta", "run_id": run_id,
            "engine": engine, "source_events": src_path, "event_count": len(events),
            "postings": len(entries), "added": added, "skipped_duplicates": skipped,
            "mode": "single_entry", "currency": cost["currency"],
            "prices_source": cost["prices_source"],
            "cost_spec_sha256": hashlib.sha256(
                Path(CONFIG / "cost_spec.json").read_bytes()).hexdigest(),
            "run_hours": hours, "area_m2": area, "agents": agents,
            "fixed_costs": fixed_costs(cost, hours, area, agents),
            "energy_basis": cost.get("meta", {}).get("energy_basis", "vehicle-side measured kWh"),
            "degradation": ("E1: replay-derived estimates" if engine == "replay"
                            else "none (tick simulation events)")}
    jdump(d / "ledger_meta.json", meta)
    issues = validate(entries, events, cost)
    jdump(d / "ledger_validation.json",
          {"schema_version": "1.0", "kind": "ledger_validation",
           "verdict": "PASS" if not issues else "FAIL", "issues": issues[:50],
           "checked_postings": len(entries)})
    if issues:
        raise SystemExit("LEDGER_FAIL %s" % issues[:3])
    print("LEDGER_OK %s engine=%s postings=%d -> %s"
          % (run_id, engine, len(entries), ledger))
    return {"run_id": run_id, "run_dir": str(d), "engine": engine,
            "postings": len(entries), "ledger": str(ledger)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--source", default="auto", choices=["auto", "sim", "replay"])
    ap.add_argument("--cost-spec", default=str(CONFIG / "cost_spec.json"))
    ap.add_argument("--rebuild", action="store_true",
                    help="drop existing ledger.jsonl before posting (use after changing prices)")
    ap.add_argument("--no-index", action="store_true",
                    help="do not overwrite out/ledger_index.json (isolated custom runs)")
    args = ap.parse_args(argv)
    cost = jload(args.cost_spec)
    if args.all:
        idx = jload(OUT / "sim_index.json")
        runs = [e["run_dir"] for e in idx["runs"]]
    elif args.run:
        runs = [args.run]
    else:
        runs = [jload(OUT / "sim_latest.json")["run_dir"]]
    result = [process(r, cost, args.source, args.rebuild) for r in runs]
    if args.all:
        # B0/E1 degradation path kept alive and labelled.
        result.append(process(str(OUT), cost, "replay", args.rebuild))
    if not args.no_index:
        jdump(OUT / "ledger_index.json", {"schema_version": "1.0",
              "kind": "ledger_index", "runs": result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
