"""build_docs_assets.py - generate lightweight README visuals from out/.

Source PNGs are 2-2.7 MB each and must not be linked directly from the README
(OPEN_SOURCE_PLAN §3.3). This script writes WebP (<500 KB each) plus a short
animated GIF, and a manifest with source SHA256 for traceability.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENDERS = ROOT / "out" / "renders"
ASSETS = ROOT / "docs" / "assets"
VIDEO = ROOT / "out" / "anim_dc.mp4"

HERO = ["01_overview", "08_corridor_plan_zoom", "05_slotting_heatmap",
        "03_dock", "09_time_space"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_webp() -> list[dict]:
    from PIL import Image
    ASSETS.mkdir(parents=True, exist_ok=True)
    rows = []
    for src in sorted(RENDERS.glob("*.png")):
        im = Image.open(src).convert("RGB")
        if im.width > 1600:
            h = round(im.height * 1600 / im.width)
            im = im.resize((1600, h), Image.LANCZOS)
        dst = ASSETS / (src.stem + ".webp")
        q = 82
        while True:
            im.save(dst, "WEBP", quality=q, method=6)
            if dst.stat().st_size <= 500_000 or q <= 40:
                break
            q -= 8
        rows.append({"file": dst.name, "source": src.name, "bytes": dst.stat().st_size,
                     "width": im.width, "height": im.height, "quality": q,
                     "source_sha256": sha(src), "hero": src.stem in HERO})
    return rows


def build_gif() -> dict:
    """4-second, 8 fps, 480px-wide GIF from the animation (no external tool needed)."""
    from PIL import Image
    import imageio_ffmpeg
    if not VIDEO.exists():
        return {"file": None, "error": "out/anim_dc.mp4 missing"}
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as td:
        pat = os.path.join(td, "f_%03d.jpg")
        cmd = [exe, "-y", "-ss", "6", "-t", "4", "-i", str(VIDEO),
               "-vf", "fps=8,scale=480:-1:flags=lanczos", "-q:v", "4", pat]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return {"file": None, "error": r.stderr[-300:]}
        frames = [Image.open(p).convert("P", palette=Image.ADAPTIVE, colors=128)
                  for p in sorted(Path(td).glob("f_*.jpg"))]
        if not frames:
            return {"file": None, "error": "no frames extracted"}
        dst = ASSETS / "demo.gif"
        frames[0].save(dst, save_all=True, append_images=frames[1:],
                       duration=125, loop=0, optimize=True)
    return {"file": dst.name, "source": VIDEO.name, "bytes": dst.stat().st_size,
            "frames": len(frames), "fps": 8, "duration_s": round(len(frames) / 8, 2),
            "source_sha256": sha(VIDEO)}


def main() -> int:
    rows = build_webp()
    gif = build_gif()
    manifest = {"schema_version": "1.0", "kind": "docs_assets_manifest",
                "generated_by": "scripts/build_docs_assets.py",
                "webp": rows, "gif": gif,
                "total_bytes": sum(r["bytes"] for r in rows) + gif.get("bytes", 0)}
    (ASSETS / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("DOCS_ASSETS_OK webp=%d largest=%dKB gif=%s total=%.2fMB"
          % (len(rows), max(r["bytes"] for r in rows) // 1024,
             gif.get("file"), manifest["total_bytes"] / 1_048_576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
