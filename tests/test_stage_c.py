"""Stage A/B/C regression + contract tests (COMPLETION_PLAN S/F/C series).

Every test must be able to FAIL: negative cases deliberately tamper with input
and assert that validation rejects it.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "adapters"))

import base as adapters  # noqa: E402
import generic_rest  # noqa: E402
import lif  # noqa: E402
import scenario_tools  # noqa: E402
import spec_layout  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ C6 LIF
def test_lif_roundtrip():
    layout = jload(ROOT / "data" / "layout.json")
    exported = lif.export_lif(layout)
    back = lif.import_lif(exported)
    assert back["ok"], back["issues"]
    assert {n["nodeId"] for n in exported["nodes"]} == \
           {n["nodeId"] for n in back["draft"]["nodes"]}
    assert all(e["synthetic"] for e in exported["edges"])
    assert back["draft"]["status"] == "DRAFT_ONLY"


def test_lif_import_rejects_dangling_edge():
    obj = lif.export_lif(jload(ROOT / "data" / "layout.json"))
    obj["edges"][0]["endNodeId"] = "DOES-NOT-EXIST"
    res = lif.import_lif(obj)
    assert not res["ok"]
    assert any("unknown node" in x for x in res["issues"])


# ------------------------------------------------------------------ C3 adapters
def _order(oid="ORD-1", **kw):
    o = {"order_id": oid, "order_type": "OUTBOUND", "warehouse": "W",
         "sku": "SKU-1", "qty": 2, "priority": "P2", "t_arrive": 0.0}
    o.update(kw)
    return o


def test_adapter_dedupe_and_dry_run_ack():
    a = adapters.StaticAdapter([_order(), _order(), _order("ORD-2")])
    out = a.pull()
    assert out["count"] == 2
    assert out["duplicates"] == ["ORD-1"]
    ack = a.ack_order("ORD-1", "DONE")
    assert ack["dry_run"] is True and ack["written"] is False


def test_adapter_rejects_invalid_order():
    a = adapters.StaticAdapter([_order(), _order("ORD-2", order_type="NOPE", qty=0)])
    out = a.pull()
    assert out["count"] == 1
    assert len(out["invalid"]) == 1
    assert out["invalid"][0]["issues"]


def test_generic_rest_against_local_mock():
    payload = {"orders": [
        {"id": "ORD-1", "type": "OUTBOUND", "site": "W", "sku": "SKU-1",
         "quantity": 2, "priority": "P2", "created_at": 1.0},
        {"id": "ORD-1", "type": "OUTBOUND", "site": "W", "sku": "SKU-1",
         "quantity": 2, "priority": "P2", "created_at": 1.0},
    ]}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        cfg = jload(ROOT / "config" / "integrations.json")
        spec = next(x for x in cfg["adapters"] if x["name"] == "generic_rest")
        spec["endpoint"] = "http://127.0.0.1:%d" % srv.server_address[1]
        a = generic_rest.GenericRestAdapter(spec, environ={"DC_WMS_TOKEN": "unit-test"})
        out = a.pull(since_s=0.0)
        assert out["count"] == 1 and out["duplicates"] == ["ORD-1"]
        assert a.ack_order("ORD-1", "DONE")["written"] is False
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------------ C1 custom scenarios
def test_scenario_validation_accepts_and_rejects():
    d = scenario_tools.default_template("pytest_case")
    issues, _ = scenario_tools.validate_scenario(d)
    assert not issues
    bad = json.loads(json.dumps(d))
    bad["overrides"]["fleet.size"] = 9999
    issues, _ = scenario_tools.validate_scenario(bad)
    assert any("fleet.size" in x for x in issues)
    bad2 = json.loads(json.dumps(d))
    bad2["id"] = "baseline"
    issues, _ = scenario_tools.validate_scenario(bad2)
    assert any("frozen builtin" in x for x in issues)


# ------------------------------------------------------------------ C5 fleet_spec
def test_fleet_spec_default_keeps_frozen_layout():
    spec = jload(ROOT / "config" / "layout_spec.json")
    current = jload(ROOT / "data" / "layout.json")
    expanded = spec_layout.expand(spec, {"W": 12, "F1": 3, "F2": 3, "F3": 3})
    assert json.dumps(expanded, ensure_ascii=False, sort_keys=True) == \
           json.dumps(current, ensure_ascii=False, sort_keys=True)
    assert expanded["summary"]["robots"] == 21


def test_fleet_spec_over_capacity_is_rejected():
    spec = jload(ROOT / "config" / "layout_spec.json")
    layout = spec_layout.expand(spec, {"W": 20, "F1": 4, "F2": 3, "F3": 3})
    issues, _, _, _ = spec_layout.validate(layout, spec)
    assert any(k == "L13" for k, _ in issues)
    assert layout["summary"]["robots"] == 21  # never spawn overlapping AGVs


# ------------------------------------------------------------------ A/B reports
#: A/B 报告由 scripts/validate_sim.py 生成（它会额外跑两轮仿真），CI 不跑它。
#: out/ 是 gitignore 的产物，干净检出时不存在——缺产物就跳过，与本仓
#: test_glb_canonical / test_asset_dispatch 同一惯例，而不是让 CI 报一个假红。
_AB_REPORTS = [ROOT / "out" / "sim_validation.json", ROOT / "out" / "pnl_validation.json"]
_needs_ab = pytest.mark.skipif(not all(p.exists() for p in _AB_REPORTS),
                               reason="需要先跑 scripts/validate_sim.py 生成 A/B 报告")


@_needs_ab
def test_sim_and_pnl_validation_reports_pass():
    s = jload(ROOT / "out" / "sim_validation.json")
    p = jload(ROOT / "out" / "pnl_validation.json")
    assert s["verdict"] == "PASS", [c for c in s["checks"] if c["status"] == "FAIL"]
    assert p["verdict"] == "PASS", [c for c in p["checks"] if c["status"] == "FAIL"]
    assert all(c["status"] == "PASS" for c in s["negative_tests"])
    assert all(c["status"] == "PASS" for c in p["negative_tests"])


@_needs_ab
@pytest.mark.parametrize("report", ["sim_validation.json", "pnl_validation.json"])
def test_report_has_no_failed_checks(report):
    d = jload(ROOT / "out" / report)
    assert d["summary"]["failed"] == 0
    assert d["summary"]["negative_failures"] == 0
