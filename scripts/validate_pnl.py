"""validate_pnl.py - independent recomputation of COMPLETION_PLAN 4.6 (F1-F7).

F7 negative tests copy a run into a temp directory, tamper with the ledger /
event stream / report badge, and require the corresponding check to FAIL.

Usage:
  python scripts/validate_pnl.py
  python scripts/validate_pnl.py --json
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Windows 的管道控制台默认不是 UTF-8，中文报告会直接 UnicodeEncodeError。
# 这里强制 UTF-8 并允许替换，保证 CI（以及 GBK 控制台）都能跑完。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
OUT, CONFIG = ROOT / "out", ROOT / "config"
sys.path.insert(0, str(ROOT / "scripts"))
import ledger  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lines(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]


def mk(name, good, detail, **ev):
    return {"id": name, "status": "PASS" if good else "FAIL", "detail": detail,
            "evidence": ev}


def context(d):
    d = Path(d)
    pnl = jload(d / "pnl.json")
    meta = jload(d / "ledger_meta.json")
    entries = lines(d / "ledger.jsonl")
    events = lines(meta["source_events"])
    cost = jload(CONFIG / "cost_spec.json")
    return {"dir": d, "pnl": pnl, "meta": meta, "entries": entries,
            "events": events, "cost": cost,
            "md": (d / "pnl.md").read_text(encoding="utf-8")}


def check_f1(c):
    acc = {}
    for e in c["entries"]:
        acc[e["account"]] = round(acc.get(e["account"], 0.0) + e["amount"], 6)
    p = c["pnl"]
    rev = round(sum(v for k, v in acc.items() if k.startswith("Revenue:")), 6)
    var = round(sum(v for k, v in acc.items() if k.startswith("Cost:Variable:")
                    or k in ("Cost:SLA_Penalty", "Cost:LostOrder")), 6)
    fix = round(sum(v for k, v in acc.items() if k.startswith("Cost:Fixed:")), 6)
    expected = {"accounts": acc, "revenue_total": rev, "variable_cost_total": var,
                "fixed_cost_total": fix, "total_cost": round(var + fix, 6),
                "contribution_margin": round(rev - var, 6),
                "operating_profit": round(rev - var - fix, 6),
                "ledger_amount_total": round(sum(e["amount"] for e in c["entries"]), 6)}
    diffs = {k: (v, p.get(k)) for k, v in expected.items() if v != p.get(k)}
    return mk("F1", not diffs, "pnl vs independent ledger aggregation: %d mismatches"
              % len(diffs), mismatches={k: str(v) for k, v in diffs.items()})


def check_f2(c):
    issues = ledger.validate(c["entries"], c["events"], c["cost"])
    sample = [e for e in c["entries"] if e["account"].startswith("Revenue:")][:10]
    return mk("F2", not issues, "posting traceability: %d postings, %d sampled revenue rows, %d issues"
              % (len(c["entries"]), len(sample), len(issues)), issues=issues[:10])


def check_f3(c):
    before = lines(c["dir"] / "ledger.jsonl")
    ledger.process(str(c["dir"]), c["cost"], source=c["meta"]["engine"], rebuild=False)
    after = lines(c["dir"] / "ledger.jsonl")
    return mk("F3", before == after,
              "idempotency: re-posting added %d rows (must be 0)" % (len(after) - len(before)),
              before=len(before), after=len(after))


def check_f4(c):
    tariff = c["cost"]["variable_costs"]["energy_tariff_per_kwh"]
    kwh_ev = sum(e["motion_kwh"] + e["idle_kwh"] for e in c["events"]
                 if e["type"] == "ENERGY_TICK")
    energy_cost = sum(e["amount"] for e in c["entries"]
                      if e["account"] == "Cost:Variable:Energy")
    kwh_ledger = energy_cost / tariff if tariff else 0.0
    st = c["dir"] / "sim_state.json"
    kwh_state = (sum(v["energy_out_kwh"] for v in jload(st)["robots"].values())
                 if st.exists() else kwh_ev)
    e1 = abs(kwh_ev - kwh_ledger) / max(kwh_ev, 1e-9)
    e2 = abs(kwh_ev - kwh_state) / max(kwh_ev, 1e-9)
    good = e1 < 0.01 and e2 < 0.01
    return mk("F4", good, "energy: events=%.6f kWh ledger=%.6f kWh state=%.6f kWh, "
              "err=%.4f%%/%.4f%%" % (kwh_ev, kwh_ledger, kwh_state, e1 * 100, e2 * 100),
              kwh_events=round(kwh_ev, 6), tariff=tariff,
              energy_cost=round(energy_cost, 6))


def check_f5(c):
    p = c["pnl"]
    be, unit, fixed = p["breakeven_orders"], p["per_order"]["contribution"], p["fixed_cost_total"]
    if unit <= 0 or be is None:
        return mk("F5", False, "breakeven undefined (contribution<=0)")
    import math
    good = be == math.ceil(fixed / unit) and abs(be * unit - fixed) <= unit * 1.001
    return mk("F5", good, "breakeven=%s orders x %.4f contribution vs %.2f fixed"
              % (be, unit, fixed), breakeven=be, contribution=unit, fixed=fixed)


def check_f6(c):
    demo = c["pnl"]["prices_source"] == "demo"
    has = "演示价格" in c["md"]
    good = (not demo) or has
    return mk("F6", good, "badge honesty: prices_source=%s, header contains 演示价格=%s"
              % (c["pnl"]["prices_source"], has))


CHECKS = {"F1": check_f1, "F2": check_f2, "F4": check_f4, "F5": check_f5, "F6": check_f6}


def temp_copy(run_dir):
    src, tmp = Path(run_dir), Path(tempfile.mkdtemp(prefix="dc_pnl_neg_"))
    for name in ("ledger.jsonl", "ledger_meta.json", "pnl.json", "pnl.md",
                 "sim_events.jsonl", "ledger_source_events.jsonl",
                 "sim_state.json", "sim_kpi.json", "cost_per_order.csv"):
        if (src / name).exists():
            shutil.copy2(src / name, tmp / name)
    meta = jload(tmp / "ledger_meta.json")
    src_name = Path(meta["source_events"]).name
    meta["source_events"] = str(tmp / src_name)
    jdump(tmp / "ledger_meta.json", meta)
    return tmp


def negative_tests(run_dir):
    cases = []

    def expect(name, mutate, target):
        tmp = temp_copy(run_dir)
        mutate(tmp)
        res = CHECKS[target](context(tmp))
        cases.append({"case": name, "target_check": target,
                      "tampered_status": res["status"],
                      "status": "PASS" if res["status"] == "FAIL" else "FAIL",
                      "detail": res["detail"]})

    def forge_amount(d):
        p = d / "ledger.jsonl"
        rows = lines(p)
        rows[0]["amount"] += 100.0
        p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows),
                     encoding="utf-8")

    def delete_source_event(d):
        p = d / Path(jload(d / "ledger_meta.json")["source_events"]).name
        rows = lines(p)
        target = next(e["seq"] for e in rows if e["type"] == "TASK_COMPLETED")
        rows = [e for e in rows if e["seq"] != target]
        p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows),
                     encoding="utf-8")

    def duplicate_posting(d):
        p = d / "ledger.jsonl"
        rows = lines(p)
        dup = copy.deepcopy(rows[0])
        rows.append(dup)
        p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows),
                     encoding="utf-8")

    def strip_badge(d):
        p = d / "pnl.md"
        p.write_text(p.read_text(encoding="utf-8").replace("演示价格", ""), encoding="utf-8")

    expect("forge a posting amount", forge_amount, "F1")
    expect("delete a source event", delete_source_event, "F2")
    expect("duplicate a posting", duplicate_posting, "F2")
    expect("strip the demo-price badge", strip_badge, "F6")
    return cases


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    idx = jload(OUT / "ledger_index.json")
    runs = [args.run] if args.run else [r["run_dir"] for r in idx["runs"]]
    checks = []
    for r in runs:
        c = context(r)
        for name in ("F1", "F2", "F4", "F5", "F6"):
            res = CHECKS[name](c)
            res["id"] = "%s/%s" % (Path(r).parent.name or Path(r).name, name)
            checks.append(res)
        f3 = check_f3(c)
        f3["id"] = "%s/F3" % (Path(r).parent.name or Path(r).name)
        checks.append(f3)
    neg = negative_tests(runs[0])
    failed = [c for c in checks if c["status"] == "FAIL"]
    nf = [c for c in neg if c["status"] == "FAIL"]
    report = {"schema_version": "1.0", "kind": "pnl_validation",
              "standard": "COMPLETION_PLAN.md 4.6 F1-F7",
              "verdict": "PASS" if not failed and not nf else "FAIL",
              "checks": checks, "negative_tests": neg,
              "summary": {"runs": len(runs), "checks": len(checks), "failed": len(failed),
                          "negative_tests": len(neg), "negative_failures": len(nf)}}
    jdump(OUT / "pnl_validation.json", report)
    jdump(OUT / "pnl_negative_tests.json", {"schema_version": "1.0",
          "kind": "pnl_negative_tests", "cases": neg,
          "verdict": "PASS" if not nf else "FAIL"})
    if args.json:
        print(json.dumps(report["summary"], ensure_ascii=False))
    else:
        for c in checks:
            print("%-4s %-24s %s" % (c["status"], c["id"], c["detail"]))
        for c in neg:
            print("%-4s NEG %-20s %s -> %s" % (c["status"], c["case"], "", c["target_check"]))
        print("PNL_VALIDATION %s runs=%d checks=%d failed=%d negative=%d neg_failed=%d"
              % (report["verdict"], len(runs), len(checks), len(failed), len(neg), len(nf)))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
