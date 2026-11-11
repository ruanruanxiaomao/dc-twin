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
    ("scripts/spec_areas.py", ("--quiet",), "python"),   # 功能区 L23–L26（需 nav_grid 判 AP）
    ("scripts/check_process.py", ("--quiet",), "python"),  # 过程配置 L14–L22（产出 process_report）

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
    ("scripts/check_process.py", (), "python"),      # 过程配置 L14–L22
    ("scripts/spec_areas.py", ("--quiet",), "python"),  # 功能区 L23–L26（验收阶段再核一次）
    ("scripts/check_studio.py", ("--quiet",), "python"),  # 驾驶舱 ↔ 生成器 ↔ 变体 spec 三方一致
    ("scripts/check_station.py", ("--quiet",), "python"),  # 工位排队可证伪（并行位 4→1）
    ("scripts/check_deadlock.py", ("--quiet",), "python"),  # 死锁退让可证伪（关闭退让必须更差）
    ("scripts/check_webapp.py", ("--quiet",), "python"),  # 静态 3D 网页快照断链/哈希
    ("scripts/report_status.py", (), "python"),      # 先重生成状态，再做口径比对
    ("scripts/check_doc_drift.py", (), "python"),    # 文档口径守卫
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


def build_steps(scope: str, scenario: str | None, spec: str | None = None):
    steps = [list(s) for s in SCOPES[scope]]
    if spec:
        # 变体布局（如 build_variant.py 生成的扩仓版）：只换 spec 路径，
        # 其余步骤仍读派生出的 data/layout.json，链路不变。
        for i, (script, args, kind) in enumerate(steps):
            if script == 'scripts/spec_layout.py':
                a = list(args)
                if '--spec' in a:
                    a[a.index('--spec') + 1] = spec
                steps[i] = [script, tuple(a), kind]
    if scope in ("scenarios", "full") and scenario and scenario != "config/scenarios/baseline.json":
        steps.insert(len(DATA_CHAIN) + 1,
                     ["scripts/scenario_run.py", ("--scenario", scenario), "python"])
    return [tuple(s) for s in steps]


def run(scope: str = "data", scenario: str | None = None, dry_run: bool = False,
        quiet: bool = False, seed: int | None = None, out: str | None = None,
        preview: bool = False, spec: str | None = None) -> int:
    paths.ensure_dirs()
    if seed is not None and not dry_run:
        set_seed(seed)
    if spec:
        # 变体布局：gen_data 等步骤通过环境变量读取，避免改动每个脚本的默认值
        os.environ["DC_LAYOUT_SPEC"] = str(Path(spec).resolve())
        print(">> layout spec = %s（变体）" % spec, flush=True)
        scene = paths.DATA / "scene_variant.json"
        if scene.exists():
            os.environ["DC_SCENE_CONFIG"] = str(scene)
            print(">> scene config = data/scene_variant.json（扩仓后园区已挪位）", flush=True)
    steps = build_steps(scope, scenario, spec)
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
    ap.add_argument("--spec", default=None,
                    help="变体布局 spec，如 config/layout_spec_areas.json（用户自选功能区后生成）")
    a = ap.parse_args(argv)
    return run(a.scope, a.scenario, a.dry_run, a.quiet, a.seed, a.out, a.preview, a.spec)


if __name__ == "__main__":
    sys.exit(main())
