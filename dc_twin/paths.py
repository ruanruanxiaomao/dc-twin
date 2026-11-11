"""dc_twin.paths - one place for project paths and tool discovery.

No personal absolute paths. Everything is resolved relative to this file or
from the environment / PATH, so a fresh clone works on Windows, macOS, Linux.
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config"
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
OUT = ROOT / "out"
RENDERS = OUT / "renders"
RUNS = ROOT / "runs"
DOCS = ROOT / "docs"
DEMO = ROOT / "demo"
ASSETS = ROOT / "assets"
CONSOLE = ROOT / "console"

# 版本只有一个来源：dc_twin/__init__.py。这里再写一遍必然漂移（0.1.0 与 0.6.0
# 曾经同时存在过，doctor 打印的就是旧的那个）。
from . import __version__ as VERSION  # noqa: E402,F401  (对外再导出)


def python_exe() -> str:
    """Prefer the project venv, then the interpreter running this code."""
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python", "venv/bin/python"):
        p = ROOT / rel
        if p.exists():
            return str(p)
    return sys.executable or shutil.which("python3") or shutil.which("python") or "python"


def blender_exe() -> str | None:
    """BLENDER_EXE -> PATH -> common install locations (no user-specific path)."""
    env = os.environ.get("BLENDER_EXE")
    if env and Path(env).exists():
        return env
    found = shutil.which("blender")
    if found:
        return found
    candidates: list[str] = []
    if sys.platform == "win32":
        base = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        candidates += glob.glob(os.path.join(base, "Blender Foundation", "Blender*", "blender.exe"))
        candidates += glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Blender Foundation", "Blender*", "blender.exe"))
    elif sys.platform == "darwin":
        candidates += ["/Applications/Blender.app/Contents/MacOS/Blender",
                       "/Applications/Blender/Blender.app/Contents/MacOS/Blender"]
    else:
        candidates += ["/usr/bin/blender", "/usr/local/bin/blender",
                       "/opt/blender/blender", "/snap/bin/blender"]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def ensure_dirs() -> None:
    for d in (OUT, RENDERS, RUNS, RUNS / "logs", DATA, CONFIG):
        d.mkdir(parents=True, exist_ok=True)


def rel(path: str | Path) -> str:
    """Path relative to project root, using forward slashes (for reports)."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)
