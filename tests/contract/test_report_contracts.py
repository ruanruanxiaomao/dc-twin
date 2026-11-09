"""Output contract: sidecar envelopes + artifact manifest (OPEN_SOURCE_PLAN §4.1)."""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.contract
def test_every_report_has_a_valid_sidecar():
    skip = {"manifest.json", "manifest_check.json", "validate_data_check_report.json",
            "junit.xml"}
    reports = [p for p in OUT.glob("*.json")
               if not p.name.endswith(".meta.json") and p.name not in skip]
    assert reports, "run `python scripts/manifest.py` first"
    missing = []
    for p in reports:
        meta = OUT / (p.stem + ".meta.json")
        if not meta.exists():
            missing.append(p.name)
            continue
        d = json.loads(meta.read_text(encoding="utf-8"))
        assert d.get("schema_version") == "1.0", p.name
        assert d.get("kind") == p.stem, p.name
        assert d.get("generator", {}).get("name") == "dc-twin", p.name
        assert d.get("generated_at", "").endswith(("Z", "+00:00")), p.name
        assert d.get("data_sha256") == _sha(p), "stale sidecar for %s" % p.name
    assert not missing, "missing sidecars: %s" % missing


@pytest.mark.contract
def test_manifest_artifact_hashes_match_disk():
    p = OUT / "manifest.json"
    assert p.exists(), "run `python scripts/manifest.py` first"
    m = json.loads(p.read_text(encoding="utf-8"))
    assert m["schema_version"] == "1.0" and m["seed"] is not None
    assert m["counts"]["artifacts"] > 10
    bad = [a["path"] for a in m["artifacts"]
           if not (ROOT / a["path"]).exists()
           or _sha(ROOT / a["path"]) != a["sha256"]]
    assert not bad, "stale artifacts (re-run manifest.py): %s" % bad[:8]
