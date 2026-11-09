"""dc_twin.doctor - environment diagnostics, pasteable into a GitHub issue."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import socket
import subprocess
import sys

from . import paths
from .paths import VERSION


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=paths.ROOT, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "n/a"


def collect() -> dict:
    checks = []

    def add(name, ok, detail, fatal=False):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "fatal": fatal})

    add("python", sys.version_info >= (3, 10),
        "%s %s (%s)" % (platform.python_implementation(), sys.version.split()[0], sys.executable), True)
    for mod in ("PIL", "imageio_ffmpeg", "jsonschema"):
        try:
            m = __import__(mod)
            try:
                from importlib.metadata import version
                v = version("pillow" if mod == "PIL" else
                            "imageio-ffmpeg" if mod == "imageio_ffmpeg" else "jsonschema")
            except Exception:
                v = getattr(m, "__version__", "installed")
            add("dep:" + mod, True, v)
        except ImportError:
            add("dep:" + mod, False, "missing - run: pip install -r requirements.txt")
    bl = paths.blender_exe()
    add("blender", bl is not None,
        bl or "not found; data/scenarios scopes still work, full needs Blender 5.2")
    if bl:
        try:
            import re
            v = subprocess.check_output([bl, "-b", "--factory-startup", "--python-expr",
                                        "import bpy; print('DC_BLENDER_VERSION=' + bpy.app.version_string)"],
                                       text=True, stderr=subprocess.DEVNULL, timeout=90)
            m = re.search(r"DC_BLENDER_VERSION=(.+)", v)
            ver = m.group(1).strip() if m else "unknown"
            add("blender_version", bool(m) and "5.2" in ver, ver)
        except Exception as e:
            add("blender_version", False, "probe failed: %s" % type(e).__name__)
    for p in ("config/layout_spec.json", "config/fleet_spec.json",
              "config/demand_spec.json", "data/config.json", "scripts/validate.py",
              "console/server.py", "requirements.txt"):
        add("file:" + p, (paths.ROOT / p).exists(), p)
    try:
        (paths.OUT / ".write_test").write_text("ok", encoding="utf-8")
        (paths.OUT / ".write_test").unlink()
        add("write:out", True, str(paths.OUT))
    except Exception as e:
        add("write:out", False, str(e), True)
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        add("loopback", True, "ephemeral port %s OK" % port)
    except Exception as e:
        add("loopback", False, str(e))
    add("path_length", len(str(paths.ROOT)) < 120,
        "%d chars (Windows MAX_PATH 260)" % len(str(paths.ROOT)))
    seeds = {}
    for label, p, key in (("config.json", paths.DATA / "config.json", "seed"),
                          ("layout_spec.json", paths.CONFIG / "layout_spec.json", "site.seed")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            seeds[label] = d["seed"] if key == "seed" else d["site"]["seed"]
        except Exception:
            seeds[label] = None
    add("seed_consistent", len(set(seeds.values())) == 1 and None not in seeds.values(),
        json.dumps(seeds))
    return {
        "schema_version": "1.0", "kind": "doctor_report",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "generator": {"name": "dc-twin", "version": VERSION, "git_commit": _git_commit(),
                      "python": sys.version.split()[0], "platform": platform.platform()},
        "root": str(paths.ROOT), "os": sys.platform,
        "env": {"BLENDER_EXE": os.environ.get("BLENDER_EXE", ""),
                "DC_CONSOLE_HOST": os.environ.get("DC_CONSOLE_HOST", "127.0.0.1")},
        "checks": checks,
        "failed": [c["name"] for c in checks if not c["ok"]],
        "fatal": [c["name"] for c in checks if not c["ok"] and c["fatal"]],
        "verdict": "FAIL" if any(not c["ok"] and c["fatal"] for c in checks) else "PASS",
    }


def render_text(r: dict) -> str:
    lines = ["dc doctor  %s  (dc-twin %s, %s)" %
             (r["verdict"], r["generator"]["version"], r["generated_at"]),
             "root: %s" % r["root"]]
    for c in r["checks"]:
        lines.append("  [%s] %-24s %s" % ("OK" if c["ok"] else "!!", c["name"], c["detail"]))
    if r["failed"]:
        lines.append("")
        lines.append("Non-blocking: " + ", ".join(r["failed"]))
        lines.append("Blender missing is fine for `dc demo` / `dc run --scope data`.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Environment diagnostics")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = collect()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(render_text(r))
    return 0 if r["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
