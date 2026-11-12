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
    for name in ("index.html", "style.css", "app.js", "editor.js",
                 "twin3d.js", "manifest.json"):
        assert (sub / name).exists(), name
    man = jload(sub / "manifest.json")
    assert len(man["images"]) >= 10
    assert all((sub / x).exists() for x in man["images"])
    assert all((sub / x).exists() for x in man["binaries"])
    assert "cdn" not in (sub / "index.html").read_text(encoding="utf-8").lower(), \
        "no CDN allowed (offline app)"


def _snapshot():
    raw = (ROOT / "submission" / "data.js").read_text(encoding="utf-8")
    assert raw.startswith("window.DC_SUBMISSION = ")
    return json.loads(raw[len("window.DC_SUBMISSION = "):].rstrip().rstrip(";"))


def test_submission_full_showcase_snapshot():
    d = _snapshot()
    assert len(d["architecture"]) == 6
    assert d["deliverables"]["count"] >= 50
    assert len(d["contracts"]) >= 8
    # tick 层指标数从产物取：写死 39 会在指标增加后变成假失败
    import glob
    last = sorted(glob.glob(str(ROOT / "runs" / "baseline" / "*" / "sim_kpi.json")))[-1]
    want = len(json.loads(Path(last).read_text(encoding="utf-8"))["metrics"])
    assert len(d["kpi"]) == want
    assert d["events"]["total"] > 100 and d["events"]["sample"]
    assert d["ledger"]["total"] > 100 and d["ledger"]["sample"]
    assert len(d["checks"]["A"]) == 40
    assert len(d["media"]["renders"]) == 10
    assert len(d["media"]["binary"]) == 3
    # 区间只用来抓"场景被清空/炸开"，不钉死具体数字。v4.9 加了园区表现层
    # （围墙/雨棚/绿化/作业线），对象数从 ~1.8k 涨到 ~2.3k，上界要跟着放。
    assert 900 <= d["media"]["blend"]["objects"] <= 3400
    assert (ROOT / "submission" / d["project"]["hero_image"]).exists()
    assert d["project"]["site_url"].startswith("https://")


def test_submission_keyframe_count_matches_source_file():
    # Regression: `chosen = pts` aliased the source list and inflated the count by 72.
    d = _snapshot()
    lines = [x for x in (ROOT / "data" / "paths.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    assert d["paths"]["total_keyframes"] == len(lines)
    assert d["paths"]["inlined_keyframes"] <= len(lines)
    assert len(d["paths"]["agents"]) == 21


def test_submission_no_absolute_user_paths():
    raw = (ROOT / "submission" / "data.js").read_text(encoding="utf-8")
    assert "C:/Users" not in raw
    assert "C:\\\\Users" not in raw
    assert str(ROOT.resolve()).replace("\\", "/") not in raw


def test_submission_layout_entities_use_global_coordinates():
    # The 3D renderer must NOT add warehouse.origin to entity coordinates,
    # and dock rects are [x0,y0,x1,y1] corners, not [x,y,w,h].
    d = _snapshot()
    sw, sh = d["layout"]["size"]
    for w in d["layout"]["warehouses"]:
        ox, oy = w["origin"]
        for r in w["racks"]:
            assert ox <= r[0] <= ox + w["size"][0], (w["id"], r)
            assert oy <= r[1] <= oy + w["size"][1], (w["id"], r)
            assert r[2] < 20 and r[3] < 20
        for dk in w["docks"]:
            x0, y0, x1, y1 = dk["rect"]
            assert 0 <= x0 < x1 <= sw and 0 <= y0 < y1 <= sh, (w["id"], dk)
            assert x1 - x0 < sw / 2, "dock rect was misread as [x,y,w,h]"
        for z in w["zones"]:
            assert all(0 <= p[0] <= sw and 0 <= p[1] <= sh for p in z["polygon"])
    js3d = (ROOT / "submission" / "twin3d.js").read_text(encoding="utf-8")
    jsapp = (ROOT / "submission" / "app.js").read_text(encoding="utf-8")
    assert "ox + r[0]" not in js3d
    assert "w.origin[0] + r[0]" not in jsapp
    assert "var dw = x1 - x0" in js3d
