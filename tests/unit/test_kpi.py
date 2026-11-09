"""Every KPI must carry a definition, formula, unit and source."""
import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_all_kpis_have_provenance():
    files = sorted(glob.glob(str(ROOT / "runs" / "*" / "*" / "kpi.json")))
    assert files, "run `python scripts/scenario_run.py --all` first"
    d = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    metrics = d["metrics"]
    assert len(metrics) >= 10, "OPEN_SOURCE_PLAN §13.1 requires >=10 KPIs"
    for name, m in metrics.items():
        for field in ("value", "unit", "definition", "formula", "source"):
            assert field in m, "%s missing %s" % (name, field)


def test_analytic_and_measured_sources_are_labelled():
    files = sorted(glob.glob(str(ROOT / "runs" / "*" / "*" / "kpi.json")))
    d = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    assert "model" in d and "note" in d, "scenario KPI must state its model"
