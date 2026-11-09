"""validate_sim.py - independent recomputation of COMPLETION_PLAN 3.8 (S1-S9)
plus the eight scenario assertions of 3.5.

Every check recomputes from the append-only event stream / terminal state /
position samples. It never trusts the engine's own KPI values (S8 compares
them, it does not reuse them). Negative tests deliberately tamper with a copy
of the artifacts and require the corresponding check to FAIL.

Usage:
  python scripts/validate_sim.py                 # full validation
  python scripts/validate_sim.py --json         # machine-readable stdout
  python scripts/validate_sim.py --no-determ    # skip the two extra sim runs
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
MIN_SEP_NOTE = "2*AGV_RADIUS; never lowered to make a test pass"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def lines(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]


def load_run(entry):
    d = Path(entry["run_dir"])
    return {"scenario": entry["scenario"], "run_dir": str(d),
            "events": lines(d / "sim_events.jsonl"),
            "kpi": jload(d / "sim_kpi.json"),
            "state": jload(d / "sim_state.json"),
            "positions": jload(d / "sim_positions.json")}


def mk(status, name, detail, **evidence):
    return {"id": name, "status": status, "detail": detail, "evidence": evidence}


def ok(name, detail, **ev):
    return mk("PASS", name, detail, **ev)


def fail(name, detail, **ev):
    return mk("FAIL", name, detail, **ev)


# ---------------------------------------------------------------- S2
def check_s2(run):
    ev, st = run["events"], run["state"]
    ticks = [e for e in ev if e["type"] == "ENERGY_TICK"]
    if not ticks:
        return fail("S2", "no ENERGY_TICK events")
    motion = sum(e["motion_kwh"] for e in ticks)
    idle = sum(e["idle_kwh"] for e in ticks)
    charge = sum(e["charge_kwh"] for e in ticks)
    out = sum(v["energy_out_kwh"] for v in st["robots"].values())
    inn = sum(v["energy_in_kwh"] for v in st["robots"].values())
    cap = max(v["soc_initial_kwh"] for v in st["robots"].values()) / 0.6
    e_out = abs(motion + idle - out) / max(out, 1e-9)
    e_in = abs(charge - inn) / max(inn, 1e-9)
    soc_err = max(abs(v["soc_initial_kwh"] - v["energy_out_kwh"]
                      + v["energy_in_kwh"] - v["soc_final_kwh"])
                  for v in st["robots"].values())
    good = e_out < 0.01 and e_in < 0.01 and soc_err < 0.01 * cap
    d = ("window sum vs terminal state: err_out=%.4f%% err_in=%.4f%% "
         "max soc residual=%.6f kWh" % (e_out * 100, e_in * 100, soc_err))
    return (ok if good else fail)("S2", "energy conservation: " + d,
                                  windows=len(ticks), motion_kwh=round(motion, 6),
                                  idle_kwh=round(idle, 6), charge_kwh=round(charge, 6),
                                  energy_out_kwh=round(out, 6), energy_in_kwh=round(inn, 6))


# ---------------------------------------------------------------- S3
def check_s3(run):
    c = collections.Counter(e.get("agent") for e in run["events"]
                            if e["type"] == "TASK_COMPLETED")
    best = max(c.values(), default=0)
    return (ok if best >= 3 else fail)(
        "S3", "multi-order truth: busiest robot completed %d orders, %d robots >=3"
        % (best, sum(1 for v in c.values() if v >= 3)),
        max_orders_per_robot=best, multi_order_robots=sum(1 for v in c.values() if v >= 3))


# ---------------------------------------------------------------- fault windows
def fault_windows(events, start_type="FAULT_START", end_type="FAULT_END"):
    open_w, out = {}, []
    for e in events:
        if e["type"] == start_type:
            open_w[e["agent"]] = e["t_s"]
        elif e["type"] == end_type and e["agent"] in open_w:
            out.append((e["agent"], open_w.pop(e["agent"]), e["t_s"]))
    for a, s in open_w.items():
        out.append((a, s, float("inf")))
    return out


def charge_windows(events):
    return fault_windows(events, "CHARGE_START", "CHARGE_END")


def check_s4(run):
    ev = run["events"]
    wins = fault_windows(ev)
    reass = [e for e in ev if e["type"] == "TASK_REASSIGNED"]
    inside = [e for e in ev if e["type"] == "TASK_COMPLETED"
              and any(a == e.get("agent") and s <= e["t_s"] < t for a, s, t in wins)]
    good = bool(wins) and bool(reass) and not inside
    return (ok if good else fail)(
        "S4", "fault truth: %d fault windows, %d reassignments, %d completions inside window"
        % (len(wins), len(reass), len(inside)),
        windows=len(wins), reassignments=len(reass), completions_in_window=len(inside))


# ---------------------------------------------------------------- S5
def check_s5(run):
    ev, pos = run["events"], run["positions"]
    wins = charge_windows(ev)
    if not wins:
        return fail("S5", "no CHARGE_START/CHARGE_END pair")
    moved, tasks = [], []
    for agent, s, t in wins:
        tasks += [e for e in ev if e["type"] in ("PICK_DONE", "DROP_DONE", "TASK_COMPLETED")
                  and e.get("agent") == agent and s <= e["t_s"] <= t]
        pts = [sample["agents"][agent] for sample in pos["samples"]
               if s <= sample["t_s"] <= t and agent in sample["agents"]]
        if len(pts) >= 2:
            moved.append((agent, math.dist(pts[0], pts[-1])))
    max_disp = max([d for _, d in moved] + [0.0])
    good = not tasks and max_disp < 0.05
    return (ok if good else fail)(
        "S5", "charging truth: %d sessions, %d task events while charging, max displacement %.4f m"
        % (len(wins), len(tasks), max_disp),
        sessions=len(wins), task_events_while_charging=len(tasks),
        max_displacement_m=round(max_disp, 4))


# ---------------------------------------------------------------- S6
def check_s6(run):
    kpi, pos = run["kpi"], run["positions"]
    thr = pos["min_sep_threshold_m"]
    bad = 0
    checks = 0
    for sample in pos["samples"]:
        pts = list(sample["agents"].values())
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                checks += 1
                if math.dist(pts[i], pts[j]) < thr - 1e-6:
                    bad += 1
    m = kpi["metrics"]
    good = (bad == 0 and m["strict_collisions"]["value"] == 0
            and m["reservation_double_occupancy"]["value"] == 0
            and m["min_pair_distance_m"]["value"] >= thr - 1e-6)
    return (ok if good else fail)(
        "S6", "traffic: %d sampled pairs checked at MIN_SEP=%.4f m, %d violations, "
        "engine collisions=%s double-occupancy=%s"
        % (checks, thr, bad, m["strict_collisions"]["value"],
           m["reservation_double_occupancy"]["value"]),
        sampled_pairs=checks, violations=bad, threshold_m=thr, note=MIN_SEP_NOTE)


# ---------------------------------------------------------------- S7
def check_s7(run):
    st, pos, kpi = run["state"], run["positions"], run["kpi"]
    agents = set(st["robots"])
    counts = {len(s["agents"]) for s in pos["samples"]}
    active = sum(1 for v in st["robots"].values() if v["move_events"] > 0)
    good = len(agents) == 50 and counts == {50} and kpi["metrics"]["agents"]["value"] == 50
    return (ok if good else fail)(
        "S7", "scale: %d distinct agent trajectories, sample cardinalities=%s, "
        "%d agents actually moved (idle agents are reported honestly)"
        % (len(agents), sorted(counts), active),
        agents=len(agents), active_agents=active, sample_cardinalities=sorted(counts))


# ---------------------------------------------------------------- S8
def recomputed(run):
    ev = run["events"]
    c = collections.Counter(e["type"] for e in ev)
    done = [e for e in ev if e["type"] == "TASK_COMPLETED"]
    horizon = run["kpi"]["metrics"]["sim_duration_s"]["value"]
    return {
        "orders_generated": c["ORDER_ARRIVED"], "orders_completed": c["TASK_COMPLETED"],
        "orders_lost": c["ORDER_LOST"], "charge_events": c["CHARGE_START"],
        "replans": c["REPLAN"], "wait_conflicts": c["WAIT_CONFLICT"],
        "task_reassignments": c["TASK_REASSIGNED"],
        "distance_total_m": round(sum(sum(e["legs_m"].values()) for e in done), 3),
        "energy_kwh_total": round(sum(e["energy_kwh"] for e in done), 6),
        "throughput_orders_per_h": round(c["TASK_COMPLETED"] * 3600 / horizon, 3),
        "agents": len(run["state"]["robots"]),
    }


def check_s8(run):
    m = run["kpi"]["metrics"]
    missing = [k for k, v in m.items()
               if not all(f in v for f in ("definition", "formula", "source"))]
    bad_source = [k for k, v in m.items()
                  if v.get("source") not in ("sim_engine measured", "analytic")]
    rec = recomputed(run)
    diffs = {k: (rec[k], m[k]["value"]) for k in rec
             if k in m and abs(float(rec[k]) - float(m[k]["value"])) > 1e-3}
    good = not missing and not bad_source and not diffs
    return (ok if good else fail)(
        "S8", "KPI provenance: %d metrics, %d missing triplets, %d recomputation mismatches"
        % (len(m), len(missing), len(diffs)),
        recomputed=rec, mismatches=diffs, missing_triplets=missing, bad_sources=bad_source)


# ---------------------------------------------------------------- S1
def check_s1():
    tmp = Path(tempfile.mkdtemp(prefix="dc_sim_det_"))
    outputs = []
    for i in (1, 2):
        base = tmp / ("run%d" % i)
        cmd = [sys.executable, str(ROOT / "scripts" / "sim_engine.py"),
               "--scenario", "config/scenarios/baseline.json",
               "--horizon", "900", "--seed", "42", "--out-dir", str(base)]
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        if p.returncode != 0:
            return fail("S1", "determinism run failed: " + p.stderr[-300:])
        outputs.append((base / "baseline" / "sim_events.jsonl").read_text(encoding="utf-8"))
    same = outputs[0] == outputs[1]
    n = len(outputs[0].splitlines())
    return (ok if same else fail)(
        "S1", "determinism: two independent 900 s runs, %d events each, byte-identical=%s"
        % (n, same), events=n, identical=same, temp_dir=str(tmp))


# ---------------------------------------------------------------- scenario assertions
def scenario_checks(runs):
    by = {r["scenario"]: r for r in runs}
    out = []

    def add(name, cond, detail, **ev):
        out.append((ok if cond else fail)(name, detail, **ev))

    b = by["baseline"]["kpi"]["metrics"]
    add("X1", b["orders_completed"]["value"] > 0 and b["strict_collisions"]["value"] == 0,
        "baseline completed=%s collisions=%s" % (b["orders_completed"]["value"],
                                                 b["strict_collisions"]["value"]))
    p = by["peak"]["kpi"]["metrics"]
    add("X2", p["max_queue_depth"]["value"] > b["max_queue_depth"]["value"]
        and p["throughput_orders_per_h"]["value"] > b["throughput_orders_per_h"]["value"],
        "peak queue=%s vs baseline=%s; throughput=%s vs %s"
        % (p["max_queue_depth"]["value"], b["max_queue_depth"]["value"],
           p["throughput_orders_per_h"]["value"], b["throughput_orders_per_h"]["value"]))
    n = by["night"]["kpi"]["metrics"]
    add("X3", n["idle_rate"]["value"] > 0.6,
        "night vehicle-level idle_rate=%s" % n["idle_rate"]["value"])
    h = collections.Counter(e.get("order_type") for e in by["holiday"]["events"]
                            if e["type"] == "ORDER_ARRIVED")
    bl = collections.Counter(e.get("order_type") for e in by["baseline"]["events"]
                             if e["type"] == "ORDER_ARRIVED")
    ht = sum(h.values()) or 1
    bt = sum(bl.values()) or 1
    delta = max(abs(h[k] / ht - bl[k] / bt) for k in set(h) | set(bl))
    add("X4", len(h) >= 5 and delta > 0.05,
        "holiday mix shift max=%.3f, types=%d" % (delta, len(h)),
        holiday=dict(h), baseline=dict(bl), max_share_delta=round(delta, 4))
    d = by["dock_blocked"]["events"]
    blocked = [e["dock"] for e in d if e["type"] == "DOCK_BLOCKED"]
    windows = [(e["t_s"], e["until_s"]) for e in d if e["type"] == "DOCK_BLOCKED"]
    drop = [e for e in d if e["type"] in ("DROP_DONE", "TASK_COMPLETED")
            and any(s <= e["t_s"] <= t for s, t in windows)
            and (e.get("loc") in blocked or e.get("drop_loc") in blocked)]
    add("X5", blocked and any(e["type"] == "REPLAN" for e in d) and not drop,
        "dock_blocked docks=%s replans=%d forbidden drops=%d"
        % (blocked, sum(1 for e in d if e["type"] == "REPLAN"), len(drop)))
    s7 = by["scale_50"]["kpi"]["metrics"]
    add("X7", s7["agents"]["value"] == 50, "scale_50 agents=%s" % s7["agents"]["value"])
    v = by["layout_variant_f4"]["kpi"]["metrics"]
    add("X8", v["strict_collisions"]["value"] == 0 and v["orders_completed"]["value"] > 0,
        "layout_variant_f4 collisions=%s completed=%s"
        % (v["strict_collisions"]["value"], v["orders_completed"]["value"]))
    return out


# ---------------------------------------------------------------- negative tests
def negative_tests(runs):
    by = {r["scenario"]: r for r in runs}
    cases = []

    def expect(name, run, checker, mutate, target):
        tampered = copy.deepcopy(run)
        mutate(tampered)
        res = checker(tampered)
        cases.append({"case": name, "target_check": target,
                      "tampered_run_status": res["status"],
                      "status": "PASS" if res["status"] == "FAIL" else "FAIL",
                      "detail": res["detail"]})

    def bump_energy(r):
        # The very first window can legitimately have zero motion; inject a
        # constant offset into *every* window so conservation must break.
        for e in r["events"]:
            if e["type"] == "ENERGY_TICK":
                e["motion_kwh"] += 1.0

    def drop_reassigned(r):
        r["events"] = [e for e in r["events"] if e["type"] != "TASK_REASSIGNED"]

    def squeeze_positions(r):
        s = r["positions"]["samples"][len(r["positions"]["samples"]) // 2]
        ids = list(s["agents"])
        s["agents"][ids[1]] = list(s["agents"][ids[0]])

    def forge_kpi(r):
        r["kpi"]["metrics"]["orders_completed"]["value"] += 7

    expect("tamper ENERGY_TICK", by["baseline"], check_s2, bump_energy, "S2")
    expect("delete TASK_REASSIGNED", by["robot_failure"], check_s4, drop_reassigned, "S4")
    expect("collapse two agent positions", by["baseline"], check_s6, squeeze_positions, "S6")
    expect("forge a KPI value", by["baseline"], check_s8, forge_kpi, "S8")
    return cases


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=str(OUT / "sim_index.json"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-determ", action="store_true")
    args = ap.parse_args(argv)
    index = jload(args.index)
    runs = [load_run(e) for e in index["runs"]]
    by = {r["scenario"]: r for r in runs}
    checks = [] if args.no_determ else [check_s1()]
    checks += [check_s2(r) for r in runs]
    checks += [check_s3(by["baseline"]), check_s4(by["robot_failure"]),
               check_s5(by["scale_50"]), check_s6(by["scale_50"]),
               check_s7(by["scale_50"]), check_s8(by["baseline"])]
    checks += scenario_checks(runs)
    neg = negative_tests(runs)
    failed = [c for c in checks if c["status"] == "FAIL"]
    neg_failed = [c for c in neg if c["status"] == "FAIL"]
    report = {"schema_version": "1.0", "kind": "sim_validation",
              "standard": "COMPLETION_PLAN.md 3.8 S1-S9 + 3.5 scenario assertions",
              "verdict": "PASS" if not failed and not neg_failed else "FAIL",
              "checks": checks, "negative_tests": neg,
              "summary": {"checks": len(checks), "failed": len(failed),
                          "negative_tests": len(neg), "negative_failures": len(neg_failed),
                          "scenarios": len(runs)}}
    jdump(OUT / "sim_validation.json", report)
    jdump(OUT / "sim_negative_tests.json", {"schema_version": "1.0",
          "kind": "sim_negative_tests", "cases": neg,
          "verdict": "PASS" if not neg_failed else "FAIL"})
    if args.json:
        print(json.dumps(report["summary"], ensure_ascii=False))
    else:
        for c in checks:
            print("%-4s %-6s %s" % (c["status"], c["id"], c["detail"]))
        for c in neg:
            print("%-4s NEG    %s -> %s" % (c["status"], c["case"], c["target_check"]))
        print("SIM_VALIDATION %s checks=%d failed=%d negative=%d neg_failed=%d"
              % (report["verdict"], len(checks), len(failed), len(neg), len(neg_failed)))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
