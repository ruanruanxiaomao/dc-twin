"""Stage C4/G2 - DXF -> layout_spec DRAFT importer.

Zero-dependency ASCII DXF reader for the entity subset that matters for a
warehouse floor plan (LWPOLYLINE / LINE / INSERT, group codes 8/10/20/11/21/2).
`ezdxf` (MIT, verified 2026-11-09) can be added later for binary/complex DXF;
this module deliberately works without it so the default chain stays bpy +
standard library.

Rules (COMPLETION_PLAN 5.5):
  - output is ALWAYS a DRAFT; it never writes config/layout_spec.json;
  - unknown layers are reported; if no rack/wall layer is recognised the import
    FAILS loudly and points at the background-tracing fallback (G2);
  - two-point calibration converts drawing units to metres.

Usage:
  python scripts/import_dxf.py --make-sample --out out/sample_layout.dxf
  python scripts/import_dxf.py --file out/sample_layout.dxf \
      --p1 0,0 --p2 10,0 --meters 10 --out out/dxf_layout_draft.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAP = ROOT / "config" / "layer_mapping.json"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pairs(text):
    """DXF ASCII is a flat stream of (group-code, value) pairs."""
    lines = text.splitlines()
    out = []
    i = 0
    while i + 1 < len(lines):
        code = lines[i].strip()
        val = lines[i + 1].rstrip("\r")
        try:
            out.append((int(code), val))
        except ValueError:
            pass
        i += 2
    return out


def read_entities(text):
    """Return a minimal entity list from the ENTITIES section."""
    p = pairs(text)
    ents, cur, in_entities = [], None, False
    for code, val in p:
        v = val.strip()
        if code == 0 and v == "SECTION":
            cur = {"_section": True}
            continue
        if code == 2 and cur and cur.get("_section"):
            in_entities = (v == "ENTITIES")
            cur = None
            continue
        if code == 0 and v == "ENDSEC":
            in_entities = False
            continue
        if not in_entities:
            continue
        if code == 0:
            if cur and cur.get("type"):
                ents.append(cur)
            cur = {"type": v, "vertices": [], "xs": [], "ys": []}
            continue
        if cur is None:
            continue
        if code == 8:
            cur["layer"] = v
        elif code == 2:
            cur["block"] = v
        elif code in (10, 11):
            try:
                cur["xs"].append(float(v))
            except ValueError:
                pass
        elif code in (20, 21):
            try:
                cur["ys"].append(float(v))
            except ValueError:
                pass
    if cur and cur.get("type"):
        ents.append(cur)
    return ents


def parse_dxf(text, layer_map):
    ents = read_entities(text)
    known = set(layer_map["map"])
    buckets = {"walls": [], "racks": [], "docks": [], "home": [], "annotations": []}
    unknown_layers, unsupported = set(), set()
    for e in ents:
        typ, layer = e["type"], e.get("layer", "0").upper()
        if typ not in ("LWPOLYLINE", "POLYLINE", "LINE", "INSERT", "CIRCLE", "TEXT", "MTEXT"):
            unsupported.add(typ)
            continue
        if layer not in known:
            unknown_layers.add(layer)
            continue
        bucket = layer_map["map"][layer]
        if bucket == "annotations":
            buckets["annotations"].append({"type": typ, "layer": layer})
            continue
        xs, ys = e["xs"], e["ys"]
        if typ == "LINE" and len(xs) >= 2 and len(ys) >= 2:
            buckets[bucket].append({"type": "LINE",
                                    "a": [xs[0], ys[0]], "b": [xs[1], ys[1]],
                                    "layer": layer})
        elif typ in ("LWPOLYLINE", "POLYLINE") and len(xs) >= 2:
            pts = [[xs[i], ys[i]] for i in range(min(len(xs), len(ys)))]
            buckets[bucket].append({"type": "POLYLINE", "points": pts,
                                    "closed": len(pts) >= 4, "layer": layer})
        elif typ == "INSERT" and xs and ys:
            buckets[bucket].append({"type": "INSERT", "at": [xs[0], ys[0]],
                                    "block": e.get("block", ""), "layer": layer})
    return {"entities": len(ents), "buckets": buckets,
            "unknown_layers": sorted(unknown_layers),
            "unsupported_types": sorted(unsupported)}


def bounds(items):
    pts = []
    for it in items:
        for key in ("a", "b", "at"):
            if key in it:
                pts.append(it[key])
        for p in it.get("points", []):
            pts.append(p)
    if not pts:
        return None
    return [min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts)]


def import_dxf(text, layer_map, p1=None, p2=None, meters=None):
    parsed = parse_dxf(text, layer_map)
    issues = []
    scale = 1.0
    calib = "none (drawing units assumed to be metres)"
    if p1 or p2 or meters is not None:
        if not (p1 and p2 and meters):
            issues.append("标定参数不完整：需同时给 --p1 --p2 --meters")
        else:
            d = math.dist(p1, p2)
            if d <= 0:
                issues.append("标定点重合，无法计算比例尺")
            else:
                scale = meters / d
                calib = "two-point: %.6f m per drawing unit" % scale
    b = parsed["buckets"]
    if not b["racks"] and not b["walls"]:
        issues.append("未识别到任何 RACK/WALL 图层，不能生成布局草稿；"
                      "请检查图层映射或改用背景描图（G2 降级）")
    unknown = parsed["unknown_layers"]
    if unknown:
        issues.append("未识别图层 %s；这些实体未导入，需人工核对" % unknown)
    ext = bounds(b["walls"] + b["racks"] + b["docks"] + b["home"])
    draft = {
        "schema_version": "1.0", "kind": "layout_spec_draft",
        "status": "DRAFT_ONLY", "source": "DXF ASCII import",
        "calibration": calib, "scale_m_per_unit": round(scale, 8),
        "extent_drawing_units": ext,
        "extent_m": ([round(v * scale, 3) for v in ext] if ext else None),
        "walls": b["walls"], "racks": b["racks"], "docks": b["docks"],
        "home": b["home"], "annotations": b["annotations"],
        "entity_count": parsed["entities"],
        "unknown_layers": unknown,
        "unsupported_types": parsed["unsupported_types"],
        "next_step": "人工核对 → 生成 layout_spec.json → spec_layout L1-L12 校验 → 显式保存",
        "never_overwrites": "config/layout_spec.json",
    }
    return {"ok": not issues, "draft": draft, "issues": issues}


SAMPLE_DXF = """0
SECTION
2
ENTITIES
0
LWPOLYLINE
8
WALL
10
0.0
20
0.0
10
100.0
20
0.0
10
100.0
20
70.0
10
0.0
20
70.0
0
LWPOLYLINE
8
RACK
10
6.0
20
13.0
10
9.0
20
13.0
10
9.0
20
14.2
10
6.0
20
14.2
0
LWPOLYLINE
8
RACK
10
13.2
20
13.0
10
16.2
20
13.0
10
16.2
20
14.2
10
13.2
20
14.2
0
INSERT
8
HOME
2
HOME-01
10
2.0
20
12.0
0
LINE
8
DOCK
10
2.0
20
0.0
11
23.0
21
0.0
0
ENDSEC
0
EOF
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--mapping", default=str(DEFAULT_MAP))
    ap.add_argument("--make-sample", action="store_true")
    ap.add_argument("--p1", default=None, help="drawing point x,y")
    ap.add_argument("--p2", default=None, help="drawing point x,y")
    ap.add_argument("--meters", type=float, default=None)
    args = ap.parse_args(argv)
    if args.make_sample:
        out = args.out or str(ROOT / "out" / "sample_layout.dxf")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(SAMPLE_DXF, encoding="utf-8")
        print("DXF_SAMPLE_OK -> %s" % out)
        return 0
    if not args.file:
        print("DXF_IMPORT_FAIL missing --file")
        return 1

    def xy(s):
        if not s:
            return None
        a, b = s.split(",", 1)
        return [float(a), float(b)]

    res = import_dxf(Path(args.file).read_text(encoding="utf-8", errors="replace"),
                     jload(args.mapping), xy(args.p1), xy(args.p2), args.meters)
    if args.out:
        jdump(args.out, res["draft"])
    print("DXF_IMPORT %s entities=%d racks=%d walls=%d issues=%s"
          % ("OK" if res["ok"] else "FAIL",
             res["draft"]["entity_count"], len(res["draft"]["racks"]),
             len(res["draft"]["walls"]), res["issues"][:4]))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
