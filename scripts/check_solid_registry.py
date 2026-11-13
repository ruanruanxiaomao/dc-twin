#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R8.11 门 · 实体登记册覆盖（``REGISTRY_COVERAGE_OK``）。

它要证的只有一件事：**没有任何实体是隐身的**。

R8.9（areas 不在 spec_layout 碰撞集）与 R8.10（待命位不在宽度轴上上限）是同一个
失败模式的两次发作——约束集是手写清单，加一条轴就漏一类实体。所以门不验"算得对
不对"，只验"册子是否盖住了现实"：

* ``REGISTRY_COVERAGE_OK``  布局里每个实体键都登记了；每种实体都至少有一个消费者；
                            `guards` 里没有笔误；没有孤儿种类。
* ``REGISTRY_RECT_PARITY_OK`` 登记册的几何与 `spec_layout` 的历史口径逐架一致
                            （两份实现不许漂移；顺序也要一致，否则导航网格会变）。
* ``REGISTRY_SCOPE_OK``     L2/L3 与导航的对象集是**显式**的：改集合必须改门。
* ``REGISTRY_AREAS_OK``     `data/areas.json` 的功能区一条不漏（它们不在 layout 里，
                            正是 R8.9 漏掉的那一类）。

自检把"漏一类"真的做出来：塞一个未登记的 `elevators` 键、把某类的 guards 清空、
给 guards 写个错名字、放宽 L2/L3 集合——四种都必须判红。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import solid_registry as reg          # noqa: E402
import spec_layout                    # noqa: E402

DATA, OUT = ROOT / "data", ROOT / "out"

#: L2/L3 的对象集**必须**是这几个。放宽（比如把 conveyor 塞进来）会让默认布局
#: 出现新的 L3，等于悄悄改金标准；所以这里写死，改它必须改门、重跑金标准。
EXPECT_L2L3 = {"rack", "dock", "station", "charger", "obstacle"}
#: 导航网格烘焙的对象集（含输送线，因为 blocks_grid=true）。
EXPECT_NAV = EXPECT_L2L3 | {"conveyor"}


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def check_coverage(whs):
    problems, detail = [], {"warehouses": {}, "kinds": {}, "orphans": [],
                            "unknown_guards": []}
    for w in whs:
        bad = reg.unregistered_keys(w)
        keys = reg.entity_keys(w)
        detail["warehouses"][w["id"]] = {k: v[1] for k, v in keys.items()}
        if bad:
            problems.append("%s 有未登记实体键：%s" % (w["id"], bad))
        unknown = [k for k, (kind, _n) in keys.items() if kind not in reg.KINDS]
        if unknown:
            problems.append("%s 的实体键映射到未登记种类：%s" % (w["id"], unknown))
    orph = reg.orphan_kinds()
    detail["orphans"] = orph
    if orph:
        problems.append("以下实体种类没有任何消费者（登记了等于隐身）：%s" % orph)
    ug = reg.unknown_guards()
    detail["unknown_guards"] = ug
    if ug:
        problems.append("guards 里有不存在的消费者：%s" % ug)
    detail["kinds"] = reg.guards_matrix()
    return not problems, detail, problems


def check_rect_parity(whs):
    """登记册 vs `spec_layout` 的历史几何：逐架一致、顺序一致。

    顺序也要断言，因为 `build_grid` 是按这个顺序烘格的——顺序变了，网格可能一样，
    但"为什么一样"就没人说得清了。宁可要求它严格相等。
    """
    problems, detail = [], {"racks": 0, "solids": 0}
    for w in whs:
        old = [(rk["id"], spec_layout.rect_of_rack(rk)) for rk in w["racks"]]
        new = [(sid, r) for sid, kind, r in
               reg.layout_solids(w, ("rack",))]
        if old != new:
            problems.append("%s 货架几何/顺序与 spec_layout 不一致（%d vs %d）"
                            % (w["id"], len(old), len(new)))
        detail["racks"] += len(old)
        # 全量：登记册输出的每个 rect 必须与"手写清单"逐项相等
        handwritten = [(rk["id"], spec_layout.rect_of_rack(rk)) for rk in w["racks"]]
        handwritten += [(d["id"], list(d["rect"])) for d in w["docks"]]
        handwritten += [(s["id"], list(s["rect"])) for s in w["stations"]]
        handwritten += [(ch["id"], [ch["position"][0] - 0.8, ch["position"][2] - 0.4,
                                    ch["position"][0] + 0.8, ch["position"][2] + 0.4])
                        for ch in w["charging_stations"]]
        handwritten += [(o["id"], list(o["rect"])) for o in w["obstacles"]]
        for cv in w["conveyors"]:
            for a, b in zip(cv["path"], cv["path"][1:]):
                handwritten.append((cv["id"],
                                    [min(a[0], b[0]) - 0.5, min(a[1], b[1]) - 0.5,
                                     max(a[0], b[0]) + 0.5, max(a[1], b[1]) + 0.5]))
        got = [(sid, r) for sid, _k, r in reg.layout_solids(w, reg.NAV_KINDS)]
        if got != handwritten:
            problems.append("%s 登记册全量几何与手写清单不一致（%d vs %d）"
                            % (w["id"], len(got), len(handwritten)))
        detail["solids"] += len(got)
    return not problems, detail, problems


def check_scope(l2l3=None, nav=None):
    """对象集必须与门里写死的预期相等；改集合必须改门。"""
    l2l3 = set(l2l3 if l2l3 is not None else reg.L2L3_KINDS)
    nav = set(nav if nav is not None else reg.NAV_KINDS)
    problems = []
    if l2l3 != EXPECT_L2L3:
        problems.append("L2/L3 对象集被改了：%s（预期 %s）"
                        % (sorted(l2l3), sorted(EXPECT_L2L3)))
    if nav != EXPECT_NAV:
        problems.append("导航对象集被改了：%s（预期 %s）"
                        % (sorted(nav), sorted(EXPECT_NAV)))
    return not problems, {"l2l3": sorted(l2l3), "nav": sorted(nav)}, problems


def check_areas(doc):
    """功能区必须全部在册：它们不在 layout 的 warehouse 里（R8.9 的洞）。

    注意 `areas.json` 里 **21 条只有 3 条是物理区**（`physical=true` 且有 rect），
    另外 18 条逻辑区（拣选面/补货面/暂存合流…）本来就不占物理空间。门如果把逻辑区
    也要求有 rect，就会逼着人去给它们编一个矩形——那是给审美写约束。
    所以这里只对物理区硬要求，并**反过来**检查"逻辑区有没有偷偷带 rect"。
    """
    problems = []
    items = (doc or {}).get("areas") or []
    phys = [a for a in items if a.get("physical")]
    logical = [a for a in items if not a.get("physical")]
    solids = reg.area_solids(doc)
    ids = {i for i, _k, _r in solids}
    no_rect = [a.get("id") for a in phys if not a.get("rect")]
    if no_rect:
        problems.append("物理区缺 rect（占位却没法碰撞）：%s" % no_rect[:5])
    missing = [a.get("id") for a in phys if a.get("id") not in ids]
    if missing:
        problems.append("物理区没进登记册：%s" % missing[:5])
    sneaky = [a.get("id") for a in logical if a.get("rect")]
    if sneaky:
        problems.append("逻辑区带了 rect（该不该占位要先说清）：%s" % sneaky[:5])
    if len(solids) != len(phys):
        problems.append("登记册功能区数 %d ≠ 物理区数 %d" % (len(solids), len(phys)))
    if "area" not in reg.KINDS:
        problems.append("area 没登记进 KINDS")
    return not problems, {"physical": len(phys), "logical": len(logical),
                          "in_registry": len(solids)}, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="R8.11 门：实体登记册覆盖")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    layout = jload(DATA / "layout.json")
    whs = layout["warehouses"]
    areas = jload(DATA / "areas.json") if (DATA / "areas.json").exists() else {}

    if a.self_test:
        # 1) 塞一个未登记实体键 → 覆盖门必须红
        fake = copy.deepcopy(whs)
        fake[0]["elevators"] = [{"id": "E1", "rect": [0, 0, 2, 2]}]
        ok_unknown, _, _ = check_coverage(fake)
        # 2) 某类没有消费者 → 孤儿门必须红
        saved = reg.KINDS["sprayer"] if "sprayer" in reg.KINDS else None
        reg.KINDS["sprayer"] = {"space": "rect", "guards": (), "note": "自检注入"}
        ok_orphan, _, _ = check_coverage(whs)
        if saved is None:
            del reg.KINDS["sprayer"]
        # 3) guards 写错名字 → 必须红
        reg.KINDS["sprayer"] = {"space": "rect", "guards": ("nobody.checks",), "note": "自检注入"}
        ok_typo, _, _ = check_coverage(whs)
        del reg.KINDS["sprayer"]
        # 4) 放宽 L2/L3 集合（把输送线塞进去）→ 门必须红
        ok_scope, _, _ = check_scope(l2l3=list(reg.L2L3_KINDS) + ["conveyor"])
        # 5) 物理区缺 rect（占位却没法碰撞）→ 必须红
        ok_areas, _, _ = check_areas({"areas": [{"id": "X", "physical": True, "rect": None}]})
        good = ((not ok_unknown) and (not ok_orphan) and (not ok_typo)
                and (not ok_scope) and (not ok_areas))
        print("REGISTRY_SELF_TEST %s" % ("PASS" if good else "FAIL"))
        print("  unknown_entity_key  %s" % ("ok" if not ok_unknown else "MISSED"))
        print("  orphan_kind         %s" % ("ok" if not ok_orphan else "MISSED"))
        print("  guard_typo          %s" % ("ok" if not ok_typo else "MISSED"))
        print("  scope_widened       %s" % ("ok" if not ok_scope else "MISSED"))
        print("  area_missing        %s" % ("ok" if not ok_areas else "MISSED"))
        return 0 if good else 1

    gates, failed = {}, []
    for name, fn in (("REGISTRY_COVERAGE_OK", lambda: check_coverage(whs)),
                     ("REGISTRY_RECT_PARITY_OK", lambda: check_rect_parity(whs)),
                     ("REGISTRY_SCOPE_OK", lambda: check_scope()),
                     ("REGISTRY_AREAS_OK", lambda: check_areas(areas))):
        try:
            ok, detail, problems = fn()
        except Exception as e:                                    # noqa: BLE001
            ok, detail, problems = False, {}, ["门执行异常：%r" % (e,)]
        gates[name] = {"ok": ok, "detail": detail, "problems": problems}
        if not ok:
            failed.append(name)
        if not a.quiet:
            print("%-24s %s%s" % (name, "PASS" if ok else "FAIL",
                                  "" if ok else "  -> " + "; ".join(problems[:2])))
            if ok:
                print("                         %s" % json.dumps(detail, ensure_ascii=False)[:200])

    rep = {"schema_version": "1.0", "kind": "solid_registry_report",
           "verdict": "REGISTRY_COVERAGE_OK" if not failed else "REGISTRY_COVERAGE_FAIL",
           "failed": failed, "gates": gates,
           "note": "登记册只保证实体不隐身，不替校验器决定该不该撞；"
                   "L2/L3 与导航是同一份现实的两个语义子集，差异写在 KINDS.note 里。"}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "registry_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.quiet:
        print("%s %s" % (rep["verdict"], ",".join(failed) if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
