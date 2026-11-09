"""Data-chain integrity without Blender: derived files must agree with each other."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _j(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_tasks_match_events_and_agents():
    t = _j("data/tasks.json")
    s = t["summary"]
    assert s["pending"] == 0
    assert s["assigned"] == s["tasks"] == 21
    assert s["referential_integrity_errors"] == 0


def test_path_samples_match_reported_count():
    pm = _j("data/path_metrics.json")
    p = ROOT / "data" / "paths.jsonl"
    n = sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
    assert n == pm["samples"]
    assert pm["route_kind"] == "CLOSED_LOOP"


def test_every_route_is_a_closed_loop():
    r = _j("data/routes.json")
    lay = _j("data/layout.json")
    homes = {rb["id"]: (rb["position"][0], rb["position"][2])
             for w in lay["warehouses"] for rb in w["spawn"]["robots"]}
    assert set(r["bounds"]) == set(homes)
    for aid, b in r["bounds"].items():
        pts = r["routes"][aid]
        assert abs(pts[0][0] - homes[aid][0]) < 0.01
        assert abs(pts[0][1] - homes[aid][1]) < 0.01
        assert abs(pts[b["end"]][0] - pts[0][0]) < 0.01
        assert abs(pts[b["end"]][1] - pts[0][1]) < 0.01
