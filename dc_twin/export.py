"""export.py - static GitHub Pages demo and checksum-verified release bundle.

The demo has NO backend and NO external JS dependency: data is inlined into
data.js, and the viewer is plain Canvas 2D (top-down + isometric projection),
so it works even when opened via file:// where fetch/ES modules are blocked.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import shutil
from pathlib import Path

from . import paths

TEMPLATE = Path(__file__).resolve().parent / "static_demo"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _j(rel):
    return json.loads((paths.ROOT / rel).read_text(encoding="utf-8"))


def _bootstrap():
    lay = _j("data/layout.json")
    whs = []
    for w in lay["warehouses"]:
        ox, oz = w["origin"]
        whs.append({
            "id": w["id"], "origin": [ox, oz],
            "size": [w["size"]["width"], w["size"]["depth"]],
            "racks": [[rk["position"][0], rk["position"][2],
                       rk["size"][0], rk["size"][2]] for rk in w["racks"]],
            "docks": [[d["rect"][0], d["rect"][1], d["rect"][2], d["rect"][3]]
                      for d in w["docks"]],
            "homes": [[loc["access_point"][0], loc["access_point"][1]]
                      for loc in w["locations"] if loc["kind"] == "HOME"],
        })
    pm = _j("data/path_metrics.json")
    return {"schema_version": "1.0", "kind": "demo_bootstrap",
            "seed": lay["seed"], "size": lay["size"],
            "warehouses": whs, "summary": lay["summary"],
            "agents": pm["agents"], "tasks": pm["tasks"],
            "event_types": pm["event_types_executed"],
            "duration_s": pm["duration_s"], "fps": pm["fps"],
            "total_path_m": pm["total_path_m"],
            "note": "Static snapshot of one simulation run; analytic scenario KPIs "
                     "are labelled separately."}


def _paths_sample(step=4):
    """Sub-sample paths.jsonl into compact per-agent tracks."""
    by = {}
    p = paths.DATA / "paths.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        by.setdefault(r["agent_id"], []).append(r)
    out, t_end = {}, 0.0
    for aid, rows in by.items():
        rows.sort(key=lambda r: r["t"])
        t_end = max(t_end, rows[-1]["t"])
        sel = rows[::step]
        if sel[-1] is not rows[-1]:
            sel.append(rows[-1])
        out[aid] = [{"t": round(r["t"], 3), "x": round(r["x"], 2),
                     "y": round(r["y"], 2), "ph": r.get("phase", "MOVE")}
                    for r in sel]
    return {"schema_version": "1.0", "kind": "demo_paths",
            "sample_stride": step, "t_end_s": t_end,
            "agents": out, "note": "Subsampled keyframes; positions linearly interpolated."}


def _scenarios():
    by_id = {}
    for kp in sorted(glob.glob(str(paths.RUNS / "*" / "*" / "kpi.json"))):
        d = json.loads(Path(kp).read_text(encoding="utf-8"))
        run = Path(kp).parent
        sc = json.loads((run / "scenario.json").read_text(encoding="utf-8"))
        m = d.get("metrics", {})
        sid = sc.get("id", run.parent.name)
        # sorted() walks timestamps in ascending order; keep the newest per id.
        by_id[sid] = {"id": sid, "timestamp": run.name,
                      "throughput_orders_per_h": m.get("throughput_orders_per_h", {}).get("value"),
                      "on_time_rate": m.get("on_time_rate", {}).get("value"),
                      "max_dock_queue": m.get("max_dock_queue", {}).get("value"),
                      "model": d.get("model"),
                      "verdict": json.loads((run / "assertions.json").read_text(
                          encoding="utf-8")).get("verdict", "n/a")}
    rows = sorted(by_id.values(), key=lambda r: r["id"])
    return {"schema_version": "1.0", "kind": "demo_scenarios",
            "engine": "analytic (M/M/c what-if, NOT full per-vehicle A*)",
            "scenarios": rows}


def export_demo(target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    (target / "assets").mkdir(exist_ok=True)
    data = {"bootstrap": _bootstrap(), "paths": _paths_sample(),
            "scenarios": _scenarios(),
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    (target / "data.js").write_text(
        "window.DC_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8")
    for f in TEMPLATE.glob("*"):
        if f.is_file():
            shutil.copy2(f, target / f.name)
    copied = []
    for src, name in ((paths.OUT / "dc_preview.glb", "dc_preview.glb"),
                      (paths.ROOT / "docs" / "assets" / "01_overview.webp", "overview.webp")):
        if src.exists():
            shutil.copy2(src, target / "assets" / name)
            copied.append(name)
    m = {"schema_version": "1.0", "kind": "demo_manifest",
         "generated_at": data["generated_at"], "engine": "static, no backend",
         "agents": len(data["paths"]["agents"]), "duration_s": data["paths"]["t_end_s"],
         "scenarios": len(data["scenarios"]["scenarios"]), "assets": copied,
         "files": {p.name: {"sha256": _sha(p), "bytes": p.stat().st_size}
                   for p in sorted(target.rglob("*")) if p.is_file()}}
    (target / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("DEMO_EXPORT_OK dir=%s agents=%d scenarios=%d duration=%.2fs files=%d"
          % (target, m["agents"], m["scenarios"], m["duration_s"], len(m["files"])))
    return 0


def export_release(target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    files = [paths.OUT / "distribution_center.blend", paths.OUT / "dc_preview.glb",
             paths.OUT / "anim_dc.mp4", paths.OUT / "CURRENT_STATUS.md",
             paths.OUT / "validate_report.json", paths.OUT / "manifest.json",
             paths.ROOT / "README.md", paths.ROOT / "LICENSE",
             paths.ROOT / "THIRD_PARTY_NOTICES.md", paths.ROOT / "CHANGELOG.md"]
    files += [Path(p) for p in sorted(glob.glob(str(paths.RENDERS / "*.png")))]
    rows = []
    for p in files:
        if not p.exists():
            print("  WARN missing", p.name)
            continue
        dst = target / ("renders/" + p.name if p.parent == paths.RENDERS else p.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        rows.append((dst.relative_to(target).as_posix(), _sha(dst), dst.stat().st_size))
    (target / "checksums.txt").write_text(
        "\n".join("%s  %s" % (h, rel) for rel, h, _ in rows) + "\n", encoding="utf-8")
    m = {"schema_version": "1.0", "kind": "release_manifest", "version": "0.1.0",
         "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         "git_commit": _git(), "files": [
             {"path": r, "sha256": h, "bytes": n} for r, h, n in rows]}
    (target / "release_manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("RELEASE_EXPORT_OK dir=%s files=%d" % (target, len(rows)))
    return 0


def _git():
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=paths.ROOT,
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "n/a"


def export(kind: str = "demo", out: str | None = None) -> int:
    if kind == "demo":
        target = Path(out).resolve() if out else paths.DEMO
        return export_demo(target)
    target = Path(out).resolve() if out else paths.ROOT / "dist" / "release"
    return export_release(target)
