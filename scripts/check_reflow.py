#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R5 门 · 库容密度重排（``REFLOW_OK``）。

R3 的 AST 耦合只改巷道宽，行数是布局里写死的整数，于是 `on_locations == off_locations`
——买窄巷道车买不到库容。R5 把"同一块地、巷道窄一点、多排几行"这件事做成可计算、
可越界检查、可复现的三步：

1. 货架组声明**进深预算** `depth_budget`（不给就取当前声明行数实际占用的进深）；
2. `reflow_rows()` 在预算内按 `(深度 + 巷道)` 节距算行数——纯算术，不做搜索；
3. 结果交给既有 L2（越界）/L3（重叠）/L6（AP 不可走）/L8（通道过窄）校验。

四道子门：

* `REFLOW_OFF_OK`       默认关闭时布局与出厂逐项一致（不碰金标准）。
* `REFLOW_DENSITY_OK`   同预算下，巷道窄的设备排得更多（平衡重 4 行 vs 曲臂 5 行），
                        且两边都 **0 违规**——密度是合规拿到的，不是靠越界挤出来的。
* `REFLOW_BOUNDARY_NEG` 越界必须**报错**：预算过大、或巷道窄到 AGV 站不进去
                        （VNA 2.0 m），L2/L3/L6/L8 必须给出 issue；静默通过即判红。
* `REFLOW_BUDGET_OK`    每条重排记录都满足 `depth_used <= budget`，行数 ≥ 1。

自检：把派生行数强行 +2 再校验，L2/L3/L6 必须抓到——证明边界检查是活的。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CONFIG = ROOT / "config"
DATA = ROOT / "data"
OUT = ROOT / "out"

#: 密度对照用的进深预算。标定依据：A 区从 z=13 起， conveyor CV-W01 在 z=34.5，
#: 因此 20 m 是"4 行平衡重 / 5 行曲臂都合规"的窗口；26 m 会压到输送线。
BUDGET = 20.0


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _spec(equipment_type=None, budget=BUDGET, ast=True, reflow=True, group=0):
    """造一份布局 spec：只改 W 的第一个货架组，其余保持出厂。"""
    s = copy.deepcopy(jload(CONFIG / "layout_spec.json"))
    if ast or reflow:
        s["options"] = {}
        if ast:
            s["options"]["ast_drives_aisle_width"] = True
        if reflow:
            s["options"]["reflow_rows"] = True
    g = s["warehouses"][0]["racks"][group]
    if budget is not None:
        g["depth_budget"] = budget
    if equipment_type:
        g.setdefault("rack", {})["equipment_type"] = equipment_type
    return s


def _measure(spec):
    import spec_layout as sl
    lay = sl.expand(spec)
    w = lay["warehouses"][0]
    shelf = [x for x in w["locations"] if x.get("kind") == "SHELF"]
    issues, warnings, _mutex, _R = sl.validate(lay, spec)
    return {"layout": lay, "rows": (w.get("reflow") or [{}])[0].get("rows_derived"),
            "shelf": len(shelf), "issues": issues, "warnings": warnings,
            "reflow": w.get("reflow") or [], "ap_clamped": w.get("ap_clamped") or []}


# --------------------------------------------------------------------- 门
def check_off():
    import spec_layout as sl
    base = jload(CONFIG / "layout_spec.json")
    lay = sl.expand(copy.deepcopy(base))
    w = lay["warehouses"][0]
    shelf = [x for x in w["locations"] if x.get("kind") == "SHELF"]
    shipped = jload(DATA / "layout.json")
    shipped_shelf = [x for x in shipped["warehouses"][0]["locations"]
                     if x.get("kind") == "SHELF"]
    problems, detail = [], {}
    detail["shelf"] = len(shelf)
    detail["shipped_shelf"] = len(shipped_shelf)
    if "reflow" in w:
        problems.append("默认态出现 reflow 记录：选项没关掉")
    if len(shelf) != len(shipped_shelf):
        problems.append("默认态货位数 %d != 出厂 %d" % (len(shelf), len(shipped_shelf)))
    if len(w["racks"]) != len(shipped["warehouses"][0]["racks"]):
        problems.append("默认态货架数与出厂不一致")
    return (not problems), detail, problems


def check_density():
    cb = _measure(_spec("forklift_counterbalance"))
    reach = _measure(_spec("forklift_reach"))
    problems, detail = [], {
        "counterbalance": {"rows": cb["rows"], "shelf": cb["shelf"],
                           "aisle": cb["reflow"][0]["aisle_width"] if cb["reflow"] else None,
                           "issues": len(cb["issues"])},
        "reach": {"rows": reach["rows"], "shelf": reach["shelf"],
                  "aisle": reach["reflow"][0]["aisle_width"] if reach["reflow"] else None,
                  "issues": len(reach["issues"])},
    }
    if cb["issues"]:
        problems.append("平衡重方案本身有 %d 个违规：%s"
                        % (len(cb["issues"]), cb["issues"][:2]))
    if reach["issues"]:
        problems.append("曲臂方案本身有 %d 个违规：%s"
                        % (len(reach["issues"]), reach["issues"][:2]))
    if reach["rows"] is None or cb["rows"] is None:
        problems.append("没有产生 reflow 记录：重排没生效")
    else:
        if not reach["rows"] > cb["rows"]:
            problems.append("窄巷道（%.1f m）排了 %s 行，不宽于平衡重（%.1f m）的 %s 行"
                            % (detail["reach"]["aisle"], reach["rows"],
                               detail["counterbalance"]["aisle"], cb["rows"]))
        if not reach["shelf"] > cb["shelf"]:
            problems.append("行数变多但货位数没变（%d vs %d）"
                            % (reach["shelf"], cb["shelf"]))
    return (not problems), detail, problems


def check_boundary():
    """越界必须报出来；静默通过就是门坏了。"""
    problems, detail = [], {}

    # 1) 预算过大：一路排到输送线上
    over = _measure(_spec("forklift_counterbalance", budget=26.0))
    detail["over_budget"] = {"rows": over["rows"], "issues": len(over["issues"]),
                             "kinds": sorted({i[0] for i in over["issues"]})}
    if not over["issues"]:
        problems.append("预算 26 m（越过输送线）却没有报任何越界——边界检查失效")

    # 2) 巷道窄到 AGV 站不进去：VNA 的 AST 1.8 m → 2.0 m 巷道
    vna = _measure(_spec("forklift_vna", budget=BUDGET))
    detail["vna"] = {"rows": vna["rows"], "aisle": vna["reflow"][0]["aisle_width"]
                     if vna["reflow"] else None,
                     "shelf": vna["shelf"], "issues": len(vna["issues"]),
                     "kinds": sorted({i[0] for i in vna["issues"]}),
                     "ap_clamped": vna["ap_clamped"]}
    if not vna["issues"]:
        problems.append("VNA 把巷道压到 %.1f m 却没有报 AP 不可走——"
                        "窄巷道在这套 AGV 参数下不可行，必须报而不是默默通过"
                        % (detail["vna"]["aisle"] or 0))
    return (not problems), detail, problems


def check_budget():
    import spec_layout as sl
    budget = BUDGET
    spec = _spec("forklift_reach", budget=budget)
    lay = sl.expand(spec)
    w = lay["warehouses"][0]
    problems, detail = [], {"records": w.get("reflow") or []}
    for rec in w.get("reflow") or []:
        if rec["depth_used"] > rec["budget"] + 1e-9:
            problems.append("%s 用了 %.2f m，超过预算 %.2f m"
                            % (rec["zone"], rec["depth_used"], rec["budget"]))
        if rec["rows_derived"] < 1:
            problems.append("%s 行数为 %s" % (rec["zone"], rec["rows_derived"]))
    return (not problems), detail, problems


# --------------------------------------------------------------------- 自检
def self_test():
    """把派生行数强行 +2，L2/L3/L6 必须抓到。"""
    import spec_layout as sl
    spec = _spec("forklift_reach", budget=BUDGET)
    lay = sl.expand(spec)
    w = lay["warehouses"][0]
    shifted = copy.deepcopy(lay)
    ws = shifted["warehouses"][0]
    extra = 0
    for rk in list(ws["racks"]):
        if rk["zone"] == "A" and rk["id"].endswith("01"):
            rk2 = copy.deepcopy(rk)
            rk2["id"] = rk["id"] + "-X"
            rk2["position"][2] = round(rk["position"][2] + 2 * 3.1, 3)
            ws["racks"].append(rk2)
            extra += 1
    issues, _w, _m, _R = sl.validate(shifted, spec)
    if not issues:
        print("R5_SELFTEST FAIL 多塞了 %d 排却没报越界（门是死的）" % extra)
        return 1
    kinds = sorted({i[0] for i in issues})
    print("R5_SELFTEST OK 多塞 %d 排 → %d 个违规 %s" % (extra, len(issues), kinds))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="R5 库容密度重排门")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()

    gates = {}
    failed = []
    for name, fn in (("REFLOW_OFF_OK", check_off),
                     ("REFLOW_DENSITY_OK", check_density),
                     ("REFLOW_BOUNDARY_NEG", check_boundary),
                     ("REFLOW_BUDGET_OK", check_budget)):
        try:
            ok, detail, problems = fn()
        except Exception as e:                                   # noqa: BLE001
            ok, detail, problems = False, {}, ["门执行异常：%r" % (e,)]
        gates[name] = {"ok": ok, "detail": detail, "problems": problems}
        if not ok:
            failed.append(name)
        if not a.quiet:
            print("%-22s %s%s" % (name, "PASS" if ok else "FAIL",
                                  "" if ok else "  -> " + "; ".join(problems[:2])))
            if ok:
                print("                       %s" % json.dumps(detail, ensure_ascii=False)[:200])

    rep = {"schema_version": "1.0", "kind": "reflow_report",
           "verdict": "REFLOW_OK" if not failed else "REFLOW_FAIL",
           "failed": failed, "gates": gates,
           "note": "VNA 2.0 m 巷道在这套 AGV（R≈0.73 m）下不可行，L6 报出来是正确行为，"
                   "不是失败；密度兑现在曲臂（3.1 m）上体现。"}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "reflow_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.quiet:
        print("%s %s" % (rep["verdict"], ",".join(failed) if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
