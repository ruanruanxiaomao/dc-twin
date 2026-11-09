"""validate_data.py - CI-friendly acceptance tier, no Blender required.

OPEN_SOURCE_PLAN §3.2/§3.4: `dc validate --tier data` must pass on a fresh
clone with only Python installed. This tier reads the data-layer reports that
`dc run --scope data` (plus scenarios/negative tests) produces, and re-checks
them independently. `validate.py` remains the full A1-A40 gate.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "out"
sys.path.insert(0, str(ROOT / "scripts"))

R = []


def jload(rel):
    p = ROOT / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def ck(name, ok, detail):
    R.append({"item": name, "status": "PASS" if ok else "FAIL", "detail": str(detail)})


def main(argv=None):
    ap = argparse.ArgumentParser(description="Data-layer acceptance tier (no Blender)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--junit", default=None)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)

    la, sp = jload("out/layout_audit.json"), jload("out/spec_layout_report.json")
    tr, ge = jload("out/traffic_report.json"), jload("out/geometry_report.json")
    rs, fl = jload("out/reservation_report.json"), jload("out/fleet_report.json")
    tk, cg = jload("data/tasks.json"), jload("data/cargo_state.json")
    pm, rt = jload("data/path_metrics.json"), jload("data/routes.json")
    lay = jload("data/layout.json")

    ck("D1 布局实体零冲突", la.get("verdict") == "PASS" and la.get("count") == 0,
       "issues=%s warnings=%s" % (la.get("count"), la.get("warnings")))
    ck("D2 spec 展开通过 L1-L12", sp.get("verdict") == "PASS" and not sp.get("issues"),
       "warehouses=%s racks=%s mutex=%s" %
       (sp.get("summary", {}).get("warehouses"), sp.get("summary", {}).get("racks"),
        sp.get("mutex_pairs")))

    try:
        from geometry import MIN_SEP
    except Exception:
        MIN_SEP = 1.4592
    ck("D3 交通零违规且最小车距 >= 2R",
       tr.get("verdict") == "PASS" and tr.get("strict_violations") == 0
       and (tr.get("min_pair_distance_m") or 0) >= MIN_SEP,
       "min_pair=%s threshold=%s strict=%s" %
       (tr.get("min_pair_distance_m"), MIN_SEP, tr.get("strict_violations")))
    ck("D4 路径几何零穿越",
       ge.get("verdict") == "PASS" and ge.get("segment_crossings_with_agv_radius") == 0,
       "segments=%s crossings=%s clearance=%s" %
       (ge.get("segments_checked"), ge.get("segment_crossings_with_agv_radius"),
        ge.get("min_path_to_obstacle_m")))

    summary = tk.get("summary", {})
    ck("D5 订单 100% 执行、引用完整",
       summary.get("pending") == 0 and summary.get("assigned") == summary.get("tasks")
       and summary.get("referential_integrity_errors") == 0 and summary.get("assigned", 0) > 0,
       json.dumps(summary, ensure_ascii=False)[:220])

    paths = DATA / "paths.jsonl"
    n_lines = sum(1 for line in paths.read_text(encoding="utf-8").splitlines()
                  if line.strip()) if paths.exists() else 0
    ck("D6 paths.jsonl 行数与 path_metrics 一致",
       n_lines > 0 and n_lines == pm.get("samples"),
       "lines=%s samples=%s" % (n_lines, pm.get("samples")))

    bounds, routes = rt.get("bounds", {}), rt.get("routes", {})
    homes = {(rb["id"]): (rb["position"][0], rb["position"][2])
             for w in lay.get("warehouses", []) for rb in w["spawn"]["robots"]}
    closed = all(
        b and {"pick", "drop", "end"} <= set(b)
        and abs(pts[0][0] - homes[aid][0]) < 0.01 and abs(pts[0][1] - homes[aid][1]) < 0.01
        and abs(pts[b["end"]][0] - pts[0][0]) < 0.01
        and abs(pts[b["end"]][1] - pts[0][1]) < 0.01
        for aid, b in bounds.items() for pts in [routes.get(aid, [])])
    ck("D7 每台车 HOME 出发并回到 HOME（闭环）",
       bool(bounds) and len(bounds) == len(homes) and closed,
       "bounds=%s homes=%s" % (len(bounds), len(homes)))

    csum = cg.get("summary", {})
    ck("D8 货物状态机与库存可追溯",
       csum.get("loaded") == csum.get("delivered") == summary.get("assigned")
       and csum.get("referential_integrity_errors") == 0
       and len(cg.get("state_transitions", [])) > 0,
       json.dumps(csum, ensure_ascii=False)[:220])

    ck("D9 预约表复核：顶点/边/AP 互斥零冲突",
       rs.get("verdict") == "PASS" and rs.get("vertex_conflicts") == 0
       and rs.get("edge_conflicts") == 0
       and rs.get("mutex_simultaneous_occupations") == 0,
       "vertex=%s edge=%s mutex=%s" %
       (rs.get("vertex_conflicts"), rs.get("edge_conflicts"),
        rs.get("mutex_simultaneous_occupations")))
    ck("D10 车队状态机无非法迁移且电量达标",
       fl.get("verdict") == "PASS" and fl.get("illegal_count") == 0
       and not fl.get("battery_below_reserve"),
       "illegal=%s min_soc=%s reserve=%s" %
       (fl.get("illegal_count"), fl.get("min_battery_pct"), fl.get("reserve_pct")))

    negs = {f: jload("out/" + f) for f in
            ("negative_tests.json", "spec_negative_tests.json",
             "scenario_negative_tests.json", "cleanroom_report.json")}
    neg_ok = all(v.get("verdict") == "PASS" for v in negs.values()) and \
        negs["negative_tests.json"].get("passed") == 7
    ck("D11 四组负测试全部能失败", neg_ok,
       ", ".join("%s=%s" % (k, v.get("verdict")) for k, v in negs.items()))

    mf = OUT / "manifest.json"
    ck("D12 输出契约 manifest 存在且可校验",
       mf.exists() and jload("out/manifest.json").get("schema_version") == "1.0",
       "manifest=%s" % mf.exists())

    if a.check:
        from manifest import check_manifest
        rc = check_manifest()
        ck("D13 manifest 与产物哈希一致（--check）", rc == 0,
           "exit=%s (see out/manifest_check.json)" % rc)

    fails = [r for r in R if r["status"] != "PASS"]
    # --check adds D13; write to a separate file so a check run never invalidates
    # the stable 12-item report recorded in out/manifest.json.
    report_name = "validate_data_check_report.json" if a.check else "validate_data_report.json"
    (OUT / report_name).write_text(
        json.dumps({"schema_version": "1.0", "kind": "validate_data_report",
                    "tier": "data", "checks": R, "passed": len(R) - len(fails),
                    "total": len(R), "verdict": "PASS" if not fails else "FAIL"},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.junit:
        suite = ET.Element("testsuite", name="dc-twin-data", tests=str(len(R)),
                           failures=str(len(fails)))
        for r in R:
            c = ET.SubElement(suite, "testcase", name=r["item"], classname="validate_data")
            if r["status"] != "PASS":
                ET.SubElement(c, "failure", message=r["detail"])
        ET.ElementTree(suite).write(a.junit, encoding="utf-8", xml_declaration=True)
    if a.json:
        print(json.dumps({"tier": "data", "checks": R, "passed": len(R) - len(fails),
                           "total": len(R)}, ensure_ascii=False))
    else:
        for r in R:
            print("[%s] %s - %s" % (r["status"], r["item"], r["detail"]))
        print("SUMMARY_DATA", len(R) - len(fails), "/", len(R), "PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
