"""Stage G1/G2 + submission app contract tests.

Everything here must be able to fail: DXF with unknown layers is rejected,
a missing data snapshot field is caught, and the static app must reference
only existing files.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import import_dxf  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def test_dxf_sample_import_and_calibration():
    mp = jload(ROOT / "config" / "layer_mapping.json")
    text = import_dxf.SAMPLE_DXF
    res = import_dxf.import_dxf(text, mp, [0, 0], [10, 0], 10.0)
    assert res["ok"], res["issues"]
    d = res["draft"]
    assert d["status"] == "DRAFT_ONLY"
    assert len(d["racks"]) == 2 and len(d["walls"]) == 1
    assert d["extent_m"] == [0.0, 0.0, 100.0, 70.0]
    assert d["never_overwrites"] == "config/layout_spec.json"


def test_dxf_unknown_layers_fail_loudly():
    mp = jload(ROOT / "config" / "layer_mapping.json")
    bad = "0\nSECTION\n2\nENTITIES\n0\nLINE\n8\nMYSTERY\n10\n0\n20\n0\n11\n1\n21\n1\n0\nENDSEC\n0\nEOF\n"
    res = import_dxf.import_dxf(bad, mp)
    assert not res["ok"]
    assert any("未识别" in x for x in res["issues"])


def test_dxf_incomplete_calibration_is_rejected():
    mp = jload(ROOT / "config" / "layer_mapping.json")
    res = import_dxf.import_dxf(import_dxf.SAMPLE_DXF, mp, [0, 0], None, None)
    assert any("标定参数不完整" in x for x in res["issues"])


def test_submission_snapshot_fields():
    # build_submission.py is run before publishing; verify its data.js contract.
    p = ROOT / "submission" / "data.js"
    assert p.exists(), "run python scripts/build_submission.py"
    raw = p.read_text(encoding="utf-8")
    assert raw.startswith("window.DC_SUBMISSION = ")
    data = json.loads(raw[len("window.DC_SUBMISSION = "):].rstrip().rstrip(";"))
    assert len(data["scenarios"]) == 8
    assert len(data["pnl"]) >= 9
    assert len(data["layout"]["warehouses"]) == 4
    assert len(data["paths"]["agents"]) == 21
    assert data["paths"]["total_keyframes"] > 1000
    assert data["validation"]["A"]["passed"] == 40
    assert data["project"]["stats"]["racks"] == 196


def test_submission_static_app_references_existing_files():
    sub = ROOT / "submission"
    html = (sub / "index.html").read_text(encoding="utf-8")
    for rel in re.findall(r'(?:src|href)="([^"]+)"', html):
        if rel.startswith(("http", "data:", "#")):
            continue
        assert (sub / rel).exists(), "missing asset: " + rel
    js = (sub / "app.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    refs = set(re.findall(r"\$\('([^']+)'\)", js))
    dynamic = {"lf_dis"} | {"pane-" + t for t in re.findall(r'data-tab="([^"]+)"', html)}
    assert not (refs - ids - dynamic), sorted(refs - ids - dynamic)


def test_submission_assets_are_local():
    sub = ROOT / "submission"
    for name in ("index.html", "style.css", "app.js", "editor.js", "manifest.json"):
        assert (sub / name).exists(), name
    images = jload(sub / "manifest.json")["images"]
    assert len(images) >= 6
    assert all((sub / x).exists() for x in images)
    assert "cdn" not in (sub / "index.html").read_text(encoding="utf-8").lower(), \
        "no CDN allowed (offline app)"
