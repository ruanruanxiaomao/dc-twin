"""Stage C4/C6 - LIF-like layout interchange (COMPLETION_PLAN 5.5 / 5.7 C6).

SELF-DESIGNED, no code derived from bekirbostanci/vda5050_lif_editor (GPL-3.0,
verified 2026-11-09); only the public node/edge/station *semantics* are aligned.
This is NOT VDA 5050 certified and NOT a real drivable road graph: edges are a
synthetic connectivity sketch. Every import produces a DRAFT and must be
reviewed before any layout_spec.json is written (never silently overwrite).

Usage:
  python scripts/lif.py export --out out/layout.lif.json
  python scripts/lif.py import --file out/layout.lif.json --out out/lif_draft.json
  python scripts/lif.py self-test
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LAYOUT = ROOT / "data" / "layout.json"
NODE_KINDS = ("HOME", "OUTBOUND", "INBOUND", "PACKING", "STATION", "DOCK")


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_sidecar(p):
    """Honour the out/*.json sidecar contract (tests/contract/test_report_contracts.py)."""
    f = Path(p)
    meta = {"schema_version": "1.0", "kind": f.stem,
            "generator": {"name": "dc-twin"},
            "generated_at": dt.datetime.now(dt.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"),
            "data_sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    Path(str(f.with_suffix(".meta.json"))).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export_lif(layout):
    nodes, edges = [], []
    for w in layout["warehouses"]:
        wid = w["id"]
        picked = []
        for loc in w["locations"]:
            kind = loc["kind"] if loc["kind"] in NODE_KINDS else "STATION"
            if kind == "STATION" and loc["kind"] not in NODE_KINDS:
                continue
            x, y = loc["access_point"][:2]
            picked.append({"nodeId": loc["id"], "x": round(float(x), 3),
                           "y": round(float(y), 3), "nodeType": kind,
                           "warehouse": wid})
        for dock in w.get("docks", []):
            for door in dock.get("doors", []):
                pos = door.get("position", [0, 0, 0])
                picked.append({"nodeId": door.get("id", "%s-DOOR" % dock["id"]),
                               "x": round(float(pos[0]), 3),
                               "y": round(float(pos[2] if len(pos) > 2 else pos[1]), 3),
                               "nodeType": "DOCK", "warehouse": wid})
        nodes.extend(picked)
        # Synthetic chain per warehouse: connectivity sketch only.
        for i in range(len(picked) - 1):
            a, b = picked[i], picked[i + 1]
            edges.append({"edgeId": "%s-%s" % (a["nodeId"], b["nodeId"]),
                          "startNodeId": a["nodeId"], "endNodeId": b["nodeId"],
                          "bidirectional": True, "synthetic": True})
    return {"schema_version": "1.0", "kind": "lif_draft",
            "format": "LIF-like (node/edge/station semantics)",
            "certification": "NOT VDA 5050 certified; synthetic sketch",
            "nodes": nodes, "edges": edges,
            "meta": {"source": "data/layout.json", "node_count": len(nodes),
                     "edge_count": len(edges),
                     "warning": "边为示意连通关系，不是真实可行驶路网；导入后必须人工核对"}}


def import_lif(obj):
    issues = []
    if not isinstance(obj, dict):
        return {"ok": False, "draft": {}, "issues": ["LIF must be an object"]}
    nodes = obj.get("nodes", [])
    edges = obj.get("edges", [])
    if not isinstance(nodes, list) or not nodes:
        issues.append("nodes must be a non-empty array")
    if not isinstance(edges, list):
        issues.append("edges must be an array")
    ids = set()
    for i, n in enumerate(nodes if isinstance(nodes, list) else []):
        if not isinstance(n, dict) or not n.get("nodeId"):
            issues.append("node[%d] missing nodeId" % i)
            continue
        if n["nodeId"] in ids:
            issues.append("duplicate nodeId %s" % n["nodeId"])
        ids.add(n["nodeId"])
        for k in ("x", "y"):
            v = n.get(k)
            if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                issues.append("node %s has invalid %s" % (n["nodeId"], k))
        if n.get("nodeType") not in NODE_KINDS:
            issues.append("node %s has unknown nodeType" % n["nodeId"])
    for i, e in enumerate(edges if isinstance(edges, list) else []):
        if not isinstance(e, dict):
            issues.append("edge[%d] not an object" % i)
            continue
        for k in ("startNodeId", "endNodeId"):
            if e.get(k) not in ids:
                issues.append("edge[%d] %s references unknown node" % (i, k))
    draft = {"schema_version": "1.0", "kind": "layout_draft",
             "status": "DRAFT_ONLY", "format": "LIF-like",
             "nodes": nodes, "edges": edges,
             "next_step": "人工核对 → 生成 layout_spec.json 草稿 → spec_layout L1-L12 校验 → 显式保存",
             "never_overwrites": "config/layout_spec.json"}
    return {"ok": not issues, "draft": draft, "issues": issues}


def self_test():
    layout = jload(DEFAULT_LAYOUT)
    lif = export_lif(layout)
    back = import_lif(lif)
    src_ids = {n["nodeId"] for n in lif["nodes"]}
    out_ids = {n["nodeId"] for n in back["draft"]["nodes"]}
    checks = [
        ("export_nonempty", len(lif["nodes"]) > 0),
        ("import_ok", back["ok"]),
        ("roundtrip_nodes", src_ids == out_ids),
        ("roundtrip_edges", len(lif["edges"]) == len(back["draft"]["edges"])),
        ("all_edges_synthetic", all(e["synthetic"] for e in lif["edges"])),
        ("draft_only", back["draft"]["status"] == "DRAFT_ONLY"),
    ]
    bad = [n for n, ok in checks if not ok]
    print("LIF_SELF_TEST %s nodes=%d edges=%d issues=%s failed=%s"
          % ("PASS" if not bad else "FAIL", len(lif["nodes"]), len(lif["edges"]),
             len(back["issues"]), bad))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["export", "import", "self-test"])
    ap.add_argument("--file", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--layout", default=str(DEFAULT_LAYOUT))
    args = ap.parse_args(argv)
    if args.action == "self-test":
        return self_test()
    if args.action == "export":
        lif = export_lif(jload(args.layout))
        out = args.out or str(ROOT / "out" / "layout.lif.json")
        jdump(out, lif)
        if Path(out).parent == ROOT / "out":
            write_sidecar(out)
        print("LIF_EXPORT_OK nodes=%d edges=%d -> %s"
              % (len(lif["nodes"]), len(lif["edges"]), out))
        return 0
    if not args.file:
        print("LIF_IMPORT_FAIL missing --file")
        return 1
    res = import_lif(jload(args.file))
    if args.out:
        jdump(args.out, res["draft"])
        if Path(args.out).parent == ROOT / "out":
            write_sidecar(args.out)
    print("LIF_IMPORT %s nodes=%d issues=%s"
          % ("OK" if res["ok"] else "FAIL", len(res["draft"].get("nodes", [])),
             res["issues"][:5]))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
