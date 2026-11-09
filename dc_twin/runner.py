"""dc_twin.runner - thin orchestration layer over the existing scripts.

Design rule from OPEN_SOURCE_PLAN §3.2: do NOT rewrite the simulation chain.
Every step is an existing script called via subprocess with sys.executable,
so the interpreter is always consistent (fixes the PowerShell 3.14 / Bash 3.13 split).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import paths

# (relative script path, args..., kind)  kind = python | blender
DATA_CHAIN = [
    ("scripts/spec_layout.py", ("--spec", "config/layout_spec.json",
                                "--fleet-spec", "config/fleet_spec.json", "--quiet"), "python"),
    ("scripts/gen_data.py", (), "python"),
    ("scripts/layout_audit.py", (), "python"),
    ("scripts/nav_grid.py", (), "python"),
    ("scripts/optimize_slotting.py", (), "python"),
    ("scripts/gen_tasks.py", (), "python"),
    ("scripts/plan_paths.py", (), "python"),
    ("scripts/gen_cargo.py", (), "python"),
    ("scripts/traffic_check.py", (), "python"),
    ("scripts/check_geometry.py", (), "python"),
    ("scripts/plot_nav_plan.py", (), "python"),
]
SCENARIO_CHAIN = [
    ("scripts/scenario_run.py", ("--all",), "python"),
    ("scripts/adapters/csv_orders.py",
     ("--csv", "config/orders_sample.csv", "--out", "out/csv_events.jsonl"), "python"),
    ("scripts/fleet_sim.py", (), "python"),
    ("scripts/reservation_check.py", (), "python"),
    # COMPLETION_PLAN 1.2: stage A/B appended in the same registration style.
    ("scripts/sim_engine.py", ("--all",), "python"),
    ("scripts/ledger.py", ("--all", "--rebuild"), "python"),
    ("scripts/pnl_report.py", ("--all",), "python"),
    ("scripts/validate_sim.py", (), "python"),
    ("scripts/validate_pnl.py", (), "python"),
    ("scripts/manifest.py", (), "python"),
]
BLENDER_CHAIN = [
    ("scripts/build_dc.py", (), "blender"),
    ("scripts/animate.py", (), "blender"),
    ("scripts/inspect_blend.py", (), "blender"),
    ("scripts/export_glb.py", (), "blender"),
    ("scripts/render.py", (), "blender"),
]
POST_CHAIN = [
    ("scripts/annotate_heatmap.py", (), "python"),
    ("scripts/encode_video.py", (), "python"),
    ("scripts/parse_artifacts.py", (), "python"),
    ("scripts/build_docs_assets.py", (), "python"),
]
ACCEPT_CHAIN = [
    ("tests/run_all_negative.py", (), "python"),
    ("scripts/validate.py", (), "python"),
    ("scripts/validate_data.py", (), "python"),
    ("scripts/report_status.py", (), "python"),
    ("scripts/export_demo.py", (), "python"),
    ("scripts/manifest.py", (), "python"),   # last: hashes must cover every report above
]

SCOPES = {
    "data": DATA_CHAIN,
    "scenarios": DATA_CHAIN + SCENARIO_CHAIN,
    "full": DATA_CHAIN + SCENARIO_CHAIN + BLENDER_CHAIN + POST_CHAIN + ACCEPT_CHAIN,
}

DEPS = ("PIL", "imageio_ffmpeg", "jsonschema")


def log_path() -> Path:
    paths.RUNS.mkdir(parents=True, exist_ok=True)
    (paths.RUNS / "logs").mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return paths.RUNS / "logs" / (stamp + ".jsonl")


def _append_log(fp: Path, **kw) -> None:
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **kw}
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def ensure_deps() -> bool:
    code = "import " + ", ".join(DEPS)
    if subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0:
        return True
    print(">> installing pinned dependencies from requirements.txt", flush=True)
    r = subprocess.run([sys.executable, "-m", "pip", "install",
                        "--disable-pip-version-check", "-r",
                        str(paths.ROOT / "requirements.txt")])
    return r.returncode == 0


def set_seed(seed: int) -> None:
    """Persistently override the seed in both config files (must agree)."""
    cfg = paths.DATA / "config.json"
    spec = paths.CONFIG / "layout_spec.json"
    d = json.loads(cfg.read_text(encoding="utf-8"))
    d["seed"] = seed
    cfg.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    s = json.loads(spec.read_text(encoding="utf-8"))
    s["site"]["seed"] = seed
    spec.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(">> seed set to %s in config.json and config/layout_spec.json" % seed, flush=True)


def build_steps(scope: str, scenario: str | None):
    steps = list(SCOPES[scope])
    if scope in ("scenarios", "full") and scenario and scenario != "config/scenarios/baseline.json":
        steps.insert(len(DATA_CHAIN) + 1,
                     ("scripts/scenario_run.py", ("--scenario", scenario), "python"))
    return steps


def run(scope: str = "data", scenario: str | None = None, dry_run: bool = False,
        quiet: bool = False, seed: int | None = None, out: str | None = None,
        preview: bool = False) -> int:
    paths.ensure_dirs()
    if seed is not None and not dry_run:
        set_seed(seed)
    steps = build_steps(scope, scenario)
    if preview and scope == "full":
        os.environ["DC_RENDER_PREVIEW"] = "1"
    blender = paths.blender_exe()
    if scope == "full" and not blender and not dry_run:
        print("DC-E1001 Blender not found. Install Blender 5.2, set BLENDER_EXE,", file=sys.stderr)
        print("         or use `dc run --scope data|scenarios` (no Blender needed).", file=sys.stderr)
        return 2
    if not dry_run and not ensure_deps():
        print("DC-E1002 dependency installation failed", file=sys.stderr)
        return 2

    log = None if dry_run else log_path()
    t_all = time.time()
    for i, (script, args, kind) in enumerate(steps, 1):
        exe = blender if kind == "blender" else sys.executable
        cmd = ([exe, "-b", "--factory-startup", "-P"] if kind == "blender" else [exe]) \
            + [script] + list(args)
        label = "[%d/%d] %s %s" % (i, len(steps), script, " ".join(args))
        if dry_run:
            print("DRY-RUN " + label)
            continue
        print(">> " + label, flush=True)
        t0 = time.time()
        _append_log(log, level="info", step=script, event="start", args=list(args))
        r = subprocess.run(cmd, cwd=paths.ROOT)
        dur = round(time.time() - t0, 3)
        _append_log(log, level="info", step=script, event="end",
                    exit_code=r.returncode, duration_s=dur)
        if r.returncode != 0:
            print("DC-E2001 step failed: %s (exit %d)" % (script, r.returncode), file=sys.stderr)
            return r.returncode
        if not quiet:
            print("   ok %.2fs" % dur, flush=True)
    if dry_run:
        print("DRY-RUN %d steps, no files written" % len(steps))
        return 0
    if out:
        target = Path(out).resolve()
        target.mkdir(parents=True, exist_ok=True)
        for item in paths.OUT.iterdir():
            if item.is_file():
                shutil.copy2(item, target / item.name)
        print(">> copied out/ -> %s" % target)
    _append_log(log, level="info", step="run", event="complete", scope=scope,
                duration_s=round(time.time() - t_all, 3), steps=len(steps))
    print("RUN_OK scope=%s steps=%d duration=%.2fs log=%s"
          % (scope, len(steps), time.time() - t_all, log.name if log else "-"), flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cross-platform pipeline driver")
    ap.add_argument("--scope", choices=sorted(SCOPES), default="data")
    ap.add_argument("--scenario", default=None, help="extra scenario json, e.g. config/scenarios/peak.json")
    ap.add_argument("--seed", type=int, default=None, help="persistently override the global seed")
    ap.add_argument("--out", default=None, help="copy out/ to this directory after the run")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--preview", action="store_true", help="low-resolution render preview")
    a = ap.parse_args(argv)
    return run(a.scope, a.scenario, a.dry_run, a.quiet, a.seed, a.out, a.preview)


if __name__ == "__main__":
    sys.exit(main())
