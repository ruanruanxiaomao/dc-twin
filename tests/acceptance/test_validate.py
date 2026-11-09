"""Behavioural acceptance: the validator must be able to fail, not just claim it."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.acceptance
def test_data_tier_exits_zero():
    r = subprocess.run([sys.executable, "scripts/validate_data.py"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-1200:] + r.stderr[-400:]


@pytest.mark.acceptance
@pytest.mark.blender
@pytest.mark.skipif(not (ROOT / "out" / "distribution_center.blend").exists(),
                    reason="full A1-A40 needs the Blender artifacts")
def test_full_acceptance_40_checks():
    r = subprocess.run([sys.executable, "scripts/validate.py", "--json"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-1200:] + r.stderr[-400:]
    d = json.loads(r.stdout)
    assert d["total"] == 40 and d["passed"] == 40


@pytest.mark.acceptance
def test_negative_suite_reports_nonzero_exits():
    reps = ["out/negative_tests.json", "out/spec_negative_tests.json",
            "out/scenario_negative_tests.json"]
    total = 0
    nonzero = 0
    for rel in reps:
        d = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        assert d["verdict"] == "PASS", rel
        for c in d["cases"]:
            assert c["ok"] is True, (rel, c.get("name"))
            # 15/17 cases must actually trigger a non-zero exit. The other two
            # are a WARN rule and a positive seed-sensitivity assertion.
            if c.get("exit_code") not in (0, None):
                nonzero += 1
            total += 1
    assert total >= 17
    assert nonzero >= 15, "only %d/17 negative cases returned non-zero" % nonzero
