"""scenario_tools.py - user-defined scenarios (COMPLETION_PLAN 5.2 / C1).

One module backs both the HTTP API and the CLI:
  list / validate / dry-run / save / delete / run

Safety model (same as console /api/config):
  - id is a strict slug, custom scenarios live ONLY in config/scenarios/custom/
    so they never silently join the 8 frozen baseline scenarios;
  - spec paths are relative, whitelisted, and contain no '..' or drive letters;
  - every numeric field has an explicit range;
  - dry-run uses a temp directory and never writes into config/ or data/;
  - save writes a *.bak.json backup first.

Usage:
  python scripts/scenario_tools.py list
  python scripts/scenario_tools.py validate --file draft.json
  python scripts/scenario_tools.py dry-run --file draft.json --horizon 600
  python scripts/scenario_tools.py save --file draft.json
  python scripts/scenario_tools.py run --id my_peak
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
SCEN = CONFIG / "scenarios"
CUSTOM = SCEN / "custom"
OUT = ROOT / "out"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
SPEC_FILES = {"../layout_spec.json", "../fleet_spec.json", "../demand_spec.json"}
MIX_KEYS = ("INBOUND", "OUTBOUND", "PICK", "REPLENISH", "TRANSFER")
BUILTIN = ("baseline", "peak", "night", "holiday", "dock_blocked",
           "robot_failure", "scale_50", "layout_variant_f4")
RANGES = {
    "demand.orders.per_hour": (1, 1000),
    "fleet.size": (1, 200),
    "sim.horizon_s": (60, 7200),
}


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _scenario_files(folder):
    # *.bak.json are automatic backups, never scenarios.
    return sorted(Path(p) for p in glob.glob(str(folder / "*.json"))
                  if not p.endswith(".bak.json"))


def builtin_paths():
    return _scenario_files(SCEN)


def custom_paths():
    return _scenario_files(CUSTOM)


def all_paths():
    return builtin_paths() + custom_paths()


def scenario_path(sid):
    """Resolve a scenario id to a whitelisted file, or None."""
    if not ID_RE.match(sid or ""):
        return None
    for p in all_paths():
        if p.stem == sid:
            return p
    return None


def custom_path(sid):
    return CUSTOM / (sid + ".json") if ID_RE.match(sid or "") else None


def get_scenario(sid):
    p = scenario_path(sid)
    if not p:
        return {"ok": False, "issues": ["unknown scenario: %s" % sid]}
    try:
        return {"ok": True, "scenario": jload(p),
                "kind": "custom" if p.parent == CUSTOM else "builtin",
                "path": str(p.relative_to(ROOT)).replace("\\", "/")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "issues": ["cannot read: %r" % (exc,)]}


def list_scenarios():
    rows = []
    for p, kind in ([(x, "builtin") for x in builtin_paths()]
                    + [(x, "custom") for x in custom_paths()]):
        try:
            d = jload(p)
        except Exception as exc:  # noqa: BLE001
            rows.append({"id": p.stem, "kind": kind, "error": str(exc), "path": str(p)})
            continue
        rows.append({
            "id": d.get("id", p.stem), "file": p.stem, "kind": kind,
            "name": d.get("name", ""), "seed": d.get("seed"),
            "per_hour": d.get("overrides", {}).get("demand.orders.per_hour"),
            "fleet_size": d.get("overrides", {}).get("fleet.size"),
            "mix": d.get("overrides", {}).get("demand.orders.mix"),
            "disruptions": d.get("disruptions", []),
            "assertions": d.get("assertions", []),
            "path": str(p.relative_to(ROOT)).replace("\\", "/"),
            "editable": kind == "custom",
        })
    return rows


def validate_scenario(obj):
    """Return (issues, warnings). issues non-empty => must not be saved/run."""
    issues, warnings = [], []
    if not isinstance(obj, dict):
        return ["scenario must be a JSON object"], []
    for k in ("schema_version", "id", "name", "layout_spec", "fleet_spec",
              "demand_spec", "seed", "simulation", "assertions"):
        if k not in obj:
            issues.append("missing required field: %s" % k)
    sid = obj.get("id")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        issues.append("id must match [A-Za-z0-9_-]{1,40} and start with alnum")
    elif sid in BUILTIN:
        issues.append("id '%s' is a frozen builtin scenario; choose another id" % sid)
    if obj.get("schema_version") != "1.0":
        issues.append("schema_version must be '1.0'")
    for k in ("layout_spec", "fleet_spec", "demand_spec"):
        v = obj.get(k)
        if not isinstance(v, str) or v not in SPEC_FILES or os.path.isabs(v):
            issues.append("%s must be one of %s (relative path only)" % (k, sorted(SPEC_FILES)))
    if not isinstance(obj.get("seed"), int) or isinstance(obj.get("seed"), bool):
        issues.append("seed must be an integer")
    elif not (1 <= obj["seed"] <= 2 ** 31 - 1):
        issues.append("seed out of range")

    sim = obj.get("simulation") or {}
    if not isinstance(sim, dict) or sim.get("duration_s", 0) <= 0 or sim.get("dt_s", 0) <= 0:
        issues.append("simulation.duration_s / dt_s must be positive numbers")

    ov = obj.get("overrides", {})
    if not isinstance(ov, dict):
        issues.append("overrides must be an object")
    else:
        allowed = {"demand.orders.per_hour", "fleet.size", "fleet.deployment",
                   "demand.orders.mix", "fleet.deployment.count"}
        for k, v in ov.items():
            if k not in allowed:
                issues.append("override '%s' is not whitelisted" % k)
            elif k in RANGES and (not isinstance(v, (int, float))
                                  or isinstance(v, bool)
                                  or not RANGES[k][0] <= v <= RANGES[k][1]):
                issues.append("override '%s' outside %s" % (k, RANGES[k]))
            if k == "demand.orders.mix":
                if not isinstance(v, dict) or set(v) != set(MIX_KEYS):
                    issues.append("mix must contain exactly %s" % list(MIX_KEYS))
                elif any(not isinstance(x, (int, float)) or x < 0 for x in v.values()) \
                        or sum(v.values()) <= 0:
                    issues.append("mix values must be non-negative and sum > 0")

    dis = obj.get("disruptions", [])
    if not isinstance(dis, list) or len(dis) > 20:
        issues.append("disruptions must be an array of at most 20 items")
    else:
        for i, d in enumerate(dis):
            if not isinstance(d, dict) or d.get("type") not in ("robot_down", "dock_blocked"):
                issues.append("disruption[%d].type invalid" % i)
                continue
            dur = d.get("duration_s", 0)
            if not isinstance(dur, (int, float)) or not 1 <= dur <= 7200:
                issues.append("disruption[%d].duration_s out of [1,7200]" % i)
            if not 0 <= d.get("start_s", 0) <= 7200:
                issues.append("disruption[%d].start_s out of [0,7200]" % i)
            if d["type"] == "robot_down" and not isinstance(d.get("agent"), str):
                issues.append("disruption[%d].agent required" % i)
            if d["type"] == "dock_blocked" and not isinstance(d.get("dock"), str):
                issues.append("disruption[%d].dock required" % i)

    ass = obj.get("assertions", [])
    if not isinstance(ass, list) or not ass:
        issues.append("at least one assertion is required (verifier must be able to fail)")
    else:
        for i, a in enumerate(ass):
            if not isinstance(a, dict) or a.get("op") not in ("==", "!=", ">=", "<=", ">", "<"):
                issues.append("assertion[%d].op invalid" % i)
            elif not isinstance(a.get("metric"), str) or a.get("value") is None:
                issues.append("assertion[%d] needs metric and value" % i)
    return issues, warnings


def default_template(sid="my_scenario", name="我的自定义场景"):
    return {
        "schema_version": "1.0", "id": sid, "name": name,
        "layout_spec": "../layout_spec.json",
        "fleet_spec": "../fleet_spec.json",
        "demand_spec": "../demand_spec.json",
        "seed": 20261109,
        "simulation": {"warmup_s": 0, "duration_s": 3600, "dt_s": 0.1,
                       "fps": 8, "max_wall_clock_s": 900},
        "overrides": {"demand.orders.per_hour": 60, "fleet.size": 24,
                      "demand.orders.mix": {"INBOUND": 0.2, "OUTBOUND": 0.25,
                                            "PICK": 0.25, "REPLENISH": 0.15,
                                            "TRANSFER": 0.15}},
        "disruptions": [],
        "assertions": [{"metric": "orders_completed", "op": ">", "value": 0},
                       {"metric": "strict_collisions", "op": "==", "value": 0}],
        "outputs": ["sim_kpi.json", "sim_events.jsonl", "pnl.json"],
    }


def dry_run(obj, horizon=600):
    """Run a SHORT tick simulation in a temp dir; never touch config/ or out/."""
    issues, _ = validate_scenario(obj)
    if issues:
        return {"ok": False, "issues": issues}
    tmp = Path(tempfile.mkdtemp(prefix="dc_scenario_dry_"))
    scen = tmp / (obj["id"] + ".json")
    jdump(scen, obj)
    cmd = [sys.executable, str(ROOT / "scripts" / "sim_engine.py"),
           "--scenario", str(scen), "--horizon", str(horizon),
           "--out-dir", str(tmp / "out")]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        return {"ok": False, "issues": ["dry-run failed: " + p.stderr[-500:]],
                "stdout": p.stdout[-500:]}
    idx = jload(tmp / "out" / "sim_index.json")
    run = idx["runs"][0]
    kpi = jload(run["run_dir"] + "/sim_kpi.json")
    metrics = {k: v["value"] for k, v in kpi["metrics"].items()}
    return {"ok": True, "horizon_s": horizon,
            "metrics": metrics, "temp_dir": str(tmp),
            "note": "short-horizon trial; full run uses sim_spec.horizon_s=3600"}


def save_scenario(obj):
    issues, warnings = validate_scenario(obj)
    if issues:
        return {"ok": False, "issues": issues}
    CUSTOM.mkdir(parents=True, exist_ok=True)
    target = CUSTOM / (obj["id"] + ".json")
    if target.exists():
        shutil.copyfile(target, CUSTOM / (obj["id"] + ".bak.json"))
    jdump(target, obj)
    return {"ok": True, "path": str(target.relative_to(ROOT)).replace("\\", "/"),
            "warnings": warnings, "id": obj["id"]}


def delete_scenario(sid):
    p = custom_path(sid)
    if not p or not p.exists():
        return {"ok": False, "issues": ["custom scenario not found: %s" % sid]}
    p.unlink()
    return {"ok": True, "deleted": sid}


def evaluate_assertions(obj, metrics):
    """A custom scenario is only useful if its assertions can actually FAIL."""
    ops = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b,
           ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
           ">": lambda a, b: a > b, "<": lambda a, b: a < b}
    rows = []
    for a in obj.get("assertions", []):
        val = metrics.get(a["metric"])
        expected = a["value"]
        ok = (val is not None and isinstance(expected, (int, float))
              and not isinstance(expected, bool)
              and ops[a["op"]](val, expected))
        rows.append({"metric": a["metric"], "op": a["op"], "expected": expected,
                     "actual": val, "status": "PASS" if ok else "FAIL"})
    return {"verdict": "PASS" if all(r["status"] == "PASS" for r in rows) else "FAIL",
            "assertions": rows}


def run_scenario(sid, horizon=None):
    """Persisted custom scenario: full sim + ledger + P&L, in its own run dir."""
    p = scenario_path(sid)
    if not p:
        return {"ok": False, "issues": ["unknown scenario: %s" % sid]}
    issues, _ = validate_scenario(jload(p)) if p.parent == CUSTOM else ([], [])
    if issues:
        return {"ok": False, "issues": issues}
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = ROOT / "runs" / "custom" / sid / stamp
    cmd = [sys.executable, str(ROOT / "scripts" / "sim_engine.py"),
           "--scenario", str(p), "--out-dir", str(base)]
    if horizon:
        cmd += ["--horizon", str(horizon)]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        return {"ok": False, "issues": ["sim failed: " + r.stderr[-500:]]}
    idx = jload(base / "sim_index.json")
    run_dir = Path(idx["runs"][0]["run_dir"])
    for script in ("ledger.py", "pnl_report.py"):
        q = subprocess.run([sys.executable, str(ROOT / "scripts" / script),
                            "--run", str(run_dir), "--no-index"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        if q.returncode != 0:
            return {"ok": False, "issues": ["%s failed: %s" % (script, q.stderr[-400:])],
                    "run_dir": str(run_dir)}
    pnl = jload(run_dir / "pnl.json")
    metrics = idx["runs"][0]["metrics"]
    assertions = evaluate_assertions(jload(p), metrics)
    jdump(run_dir / "custom_assertions.json",
          {"schema_version": "1.0", "kind": "custom_assertions",
           "scenario": sid, **assertions})
    return {"ok": True, "run_dir": str(run_dir), "metrics": metrics,
            "verdict": assertions["verdict"], "assertions": assertions["assertions"],
            "profit": pnl["operating_profit"],
            "prices_source": pnl["prices_source"],
            "note": "custom runs are isolated under runs/custom/ and never overwrite out/sim_index.json"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["list", "validate", "dry-run", "save", "delete", "run", "template"])
    ap.add_argument("--file", default=None)
    ap.add_argument("--id", default=None)
    ap.add_argument("--horizon", type=float, default=None)
    args = ap.parse_args(argv)
    if args.action == "list":
        print(json.dumps(list_scenarios(), ensure_ascii=False, indent=2))
        return 0
    if args.action == "template":
        print(json.dumps(default_template(args.id or "my_scenario"), ensure_ascii=False, indent=2))
        return 0
    if args.action == "delete":
        res = delete_scenario(args.id)
    elif args.action == "run":
        res = run_scenario(args.id, args.horizon)
    else:
        obj = jload(args.file) if args.file else json.load(sys.stdin)
        if args.action == "validate":
            issues, warnings = validate_scenario(obj)
            res = {"ok": not issues, "issues": issues, "warnings": warnings}
        elif args.action == "dry-run":
            res = dry_run(obj, args.horizon or 600)
        else:
            res = save_scenario(obj)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") and res.get("verdict", "PASS") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
