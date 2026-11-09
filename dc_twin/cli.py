"""dc_twin.cli - the `dc` command. Thin wrappers; no simulation logic here."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from . import paths, runner
from .paths import VERSION


def _py(*args):
    return subprocess.run([sys.executable, *map(str, args)], cwd=paths.ROOT)


def cmd_doctor(a):
    from . import doctor
    r = doctor.collect()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(doctor.render_text(r))
    return 0 if r["verdict"] == "PASS" else 1


def cmd_demo(a):
    """Data layer only: works without Blender, then serves the console."""
    paths.ensure_dirs()
    rc = runner.run(scope="data")
    if rc:
        return rc
    print(">> generating scenario snapshots for the console", flush=True)
    rc = _py("scripts/scenario_run.py", "--all").returncode
    if rc:
        return rc
    rc = _py("scripts/fleet_sim.py").returncode
    if rc:
        return rc
    rc = _py("scripts/reservation_check.py").returncode
    if rc:
        return rc
    rc = _py("scripts/manifest.py").returncode
    if rc:
        return rc
    if a.no_serve:
        print("DEMO_OK data layer + scenarios ready (console not started)")
        return 0
    return _serve(a.port, "127.0.0.1")


def cmd_run(a):
    return runner.run(scope=a.scope, scenario=a.scenario, dry_run=a.dry_run,
                      quiet=a.quiet, seed=a.seed, out=a.out, preview=a.preview)


def cmd_validate(a):
    args = ["scripts/validate.py", "--tier", a.tier]
    if a.json:
        args.append("--json")
    if a.junit:
        args += ["--junit", a.junit]
    if a.check:
        args.append("--check")
    return _py(*args).returncode


def _serve(port, host):
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("DC_CONSOLE_TOKEN"):
        print("DC-E4001 refusing non-loopback host without DC_CONSOLE_TOKEN", file=sys.stderr)
        return 2
    env = dict(os.environ, DC_CONSOLE_PORT=str(port), DC_CONSOLE_HOST=host)
    print(">> console at http://%s:%d/  (Ctrl+C to stop)" % (host, port), flush=True)
    return subprocess.run([sys.executable, "console/server.py"], cwd=paths.ROOT, env=env).returncode


def cmd_serve(a):
    return _serve(a.port, a.host)


def cmd_export(a):
    from . import export
    return export.export(a.target, a.out)


def cmd_compare(a):
    from . import compare
    return compare.compare(a.run_a, a.run_b, a.format, a.out)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="dc", description="Distribution-center digital twin (Blender + AGV simulation)")
    ap.add_argument("--version", action="version", version="dc-twin " + VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="environment diagnostics")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("demo", help="data layer + scenarios + console (no Blender required)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-serve", action="store_true", help="generate data but do not start the server")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("run", help="run the pipeline")
    p.add_argument("--scope", choices=sorted(runner.SCOPES), default="data")
    p.add_argument("--scenario", default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--preview", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("validate", help="acceptance checks")
    p.add_argument("--tier", choices=("data", "render", "full"), default="full")
    p.add_argument("--json", action="store_true")
    p.add_argument("--junit", default=None)
    p.add_argument("--check", action="store_true", help="compare reports against out/manifest.json")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("serve", help="start the local console")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("export", help="static demo or release bundle")
    p.add_argument("--target", choices=("demo", "release"), default="demo")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("compare", help="diff two scenario runs")
    p.add_argument("run_a")
    p.add_argument("run_b")
    p.add_argument("--format", choices=("md", "csv", "json"), default="md")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_compare)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
