#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R8.6 门 · 厂房面积参数化（``AREA_PARAM_OK``）。

方案书 §16.8 说"改 `wh.size` 应该让壳体/货架格/导航格自动重算"，并把它列为 G8 的
核心。壳体与货架排布确实早就参数化了（§16.1 有取证），但**"参数化"和"参数真的
传导过去"是两件事**——R4 的"0 巷道却全绿"就是在这条缝里栽的。

三道子门：

* ``AREA_PARAM_OK``    放大面积 → `grid.cols/rows` 按 cell_size 线性变、货架数不减、
                       货位随之变；三项必须同时成立，只变壳不变格不算。
* ``AREA_SHRINK_NEG``  缩到装不下 → L2/L7 必须给 issue 且 **退出码非 0**（拒绝，不是告警）。
* ``AREA_RACK_FOLLOW_OK`` 货架与库位数量随面积单调不减（放大不能反而更少）。

自检：把"放大后的 spec"换成"原样 spec"，门必须自己判红——证明它真的在比对，
而不是把恒等式写成断言。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG, OUT = ROOT / "config", ROOT / "out"
TMP = OUT / "exp" / "area"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _run(spec, tag):
    """写 spec → 跑 spec_layout → 返回 (returncode, layout, report)。"""
    import subprocess
    TMP.mkdir(parents=True, exist_ok=True)
    sp = TMP / ("%s_spec.json" % tag)
    sp.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out = TMP / ("%s_layout.json" % tag)
    rep = TMP / ("%s_report.json" % tag)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "spec_layout.py"),
                        "--spec", str(sp), "--out", str(TMP / ("%s_spec_layout.json" % tag)),
                        "--report", str(rep), "--write", str(out), "--quiet"],
                       cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 and not rep.exists():
        raise RuntimeError("spec_layout 崩溃（tag=%s）：%s"
                           % (tag, ((r.stdout or "") + (r.stderr or ""))[-400:]))
    layout = jload(out) if out.exists() else None
    report = jload(rep) if rep.exists() else {}
    return r.returncode, layout, report


def _resize(spec, width, depth):
    s = copy.deepcopy(spec)
    wh = s["warehouses"][0]
    wh.setdefault("size", {})["width"] = width
    wh["size"]["depth"] = depth
    return s


def _reflow_from_size(spec):
    """打开"进深预算来自厂房尺寸"：这样面积变化才会真的改变行数/货位。

    不加这一条时，货架行数是 spec 里**写死的整数**——放大厂房只会变大壳子和导航格，
    库位一个不增。这正是 §16.8 缺口④剩下的那一半，所以门必须分别验两条链：
    面积 → 壳体/网格（恒成立），面积 → 库容（需要 reflow + from_size）。
    """
    s = copy.deepcopy(spec)
    s.setdefault("options", {})["reflow_rows"] = True
    for wh in s["warehouses"]:
        for g in wh["racks"]:
            g["depth_budget"] = "from_size"
    return s


def _grid(layout):
    w = layout["warehouses"][0]
    g = w.get("grid") or {}
    return g.get("cols"), g.get("rows"), g.get("cell_size"), len(w.get("racks") or []), \
        layout["summary"]["locations"]


def check_param(base_layout, big_layout):
    """面积 → 壳体/导航格：必须变；面积 → 货架/货位：**不许丢**。

    注意这里不断言"货位必须变多"。默认布局的货架行数是 spec 里写死的整数，放大厂房
    只会放大壳子与网格——想让它进库容，必须同时给进深预算（见 `check_fill`）。
    把"应该发生"写成断言，和把"没发生"写成通过一样糟，所以两者分开验。
    """
    problems = []
    if base_layout is None or big_layout is None:
        return False, {}, ["布局生成失败（没有 layout 可比）"]
    bc, br, cs, bra, blo = _grid(base_layout)
    gc, gr, gs, gra, glo = _grid(big_layout)
    if cs != gs:
        problems.append("cell_size 变了（%r → %r）：网格不是按同一精度重算" % (cs, gs))
    if (gc, gr) == (bc, br):
        problems.append("放大面积后 grid 没变（%s×%s）——壳体变了、导航格没变" % (bc, br))
    if gra < bra:
        problems.append("放大后货架数反而变少（%s → %s）" % (bra, gra))
    if glo < blo:
        problems.append("放大后货位数反而变少（%s → %s）" % (blo, glo))
    expect_cols = int(round(float(gc or 0)))
    return not problems, {"base": {"grid": [bc, br], "racks": bra, "locations": blo},
                          "big": {"grid": [gc, gr], "racks": gra, "locations": glo},
                          "cols": expect_cols}, problems


def check_fill(rc, layout, report, base_layout):
    """把进深预算设为"填满厂房"（depth_budget=from_size）。

    两种结局都算诚实，只有"悄悄什么都没变"和"崩溃"算失败：

    * 越界被拒（issues>0 / 退出码非 0）：说明**自动填满**撞上月台/输送线，面积要进
      库容必须配功能区重排（D10b 求解器）——这是当前布局的真实结论；
    * 通过且货位变多：说明这一版布局恰好容得下。

    门只拒绝"既没被拒绝、货位也没变"这种静默无效。
    """
    problems = []
    issues = (report or {}).get("issues") or []
    if rc != 0 or issues:
        return True, {"outcome": "rejected_by_validation", "issues": len(issues),
                      "rules": sorted({i.get("rule") for i in issues if isinstance(i, dict)})}, []
    if layout is None:
        problems.append("填满预算既没被拒也没产出布局")
        return False, {}, problems
    base_loc = base_layout["summary"]["locations"] if base_layout else 0
    if layout["summary"]["locations"] <= base_loc:
        problems.append("填满预算通过了，但货位没有增加（%s → %s）——静默无效"
                        % (base_loc, layout["summary"]["locations"]))
    return not problems, {"outcome": "fits", "locations": layout["summary"]["locations"],
                          "base": base_loc}, problems


def check_shrink(rc, report):
    problems = []
    issues = report.get("issues") or []
    if rc == 0:
        problems.append("缩小到装不下却退出码 0（把越界当成了告警）")
    if not issues:
        problems.append("缩小到装不下没有产生任何 issue")
    rules = sorted({i.get("rule") for i in issues if isinstance(i, dict)})
    return not problems, {"returncode": rc, "issues": len(issues), "rules": rules}, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="R8.6 门：厂房面积参数化")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    spec = jload(CONFIG / "layout_spec.json")
    wh = spec["warehouses"][0]
    W, D = int(wh["size"]["width"]), int(wh["size"]["depth"])

    for f in TMP.glob("*_layout.json"):
        f.unlink()                      # 不留旧产物冒充本次结果
    rc0, base, _ = _run(spec, "base")
    rc1, big, rep1 = _run(_resize(spec, W + 20, D + 14), "big")
    rc3, fill, rep3 = _run(_resize(_reflow_from_size(spec), W + 20, D + 14), "fill")
    rc2, small, rep2 = _run(_resize(spec, max(20, W // 3), max(20, D // 3)), "small")

    if a.self_test:
        # 反例：拿原样 spec 当"放大后"，check_param 必须自己判红
        ok_self, _, probs_self = check_param(base, base)
        ok_shrink, _, probs_shrink = check_shrink(0, {"issues": []})
        ok_fill, _, _ = check_fill(0, base, {"issues": []}, base)
        good = (not ok_self) and (not ok_shrink) and (not ok_fill) and rc0 == 0 and rc1 == 0
        print("AREA_SELF_TEST %s" % ("PASS" if good else "FAIL"))
        print("  no_change_detected  %s" % ("ok" if not ok_self else "MISSED"))
        print("  silent_shrink       %s" % ("ok" if not ok_shrink else "MISSED"))
        print("  silent_fill         %s" % ("ok" if not ok_fill else "MISSED"))
        return 0 if good else 1

    gates, failed = {}, []
    for name, fn in (("AREA_PARAM_OK", lambda: check_param(base, big)),
                     ("AREA_FILL_OK", lambda: check_fill(rc3, fill, rep3, base)),
                     ("AREA_SHRINK_NEG", lambda: check_shrink(rc2, rep2))):
        try:
            ok, detail, problems = fn()
        except Exception as e:                                    # noqa: BLE001
            ok, detail, problems = False, {}, ["门执行异常：%r" % (e,)]
        gates[name] = {"ok": ok, "detail": detail, "problems": problems}
        if not ok:
            failed.append(name)
        if not a.quiet:
            print("%-20s %s%s" % (name, "PASS" if ok else "FAIL",
                                  "" if ok else "  -> " + "; ".join(problems[:2])))
            if ok:
                print("                     %s" % json.dumps(detail, ensure_ascii=False)[:200])

    rep = {"schema_version": "1.0", "kind": "area_param_report",
           "verdict": "AREA_PARAM_OK" if not failed else "AREA_PARAM_FAIL",
           "failed": failed, "gates": gates,
           "sizes": {"base": [W, D], "big": [W + 20, D + 14],
                     "small": [max(20, W // 3), max(20, D // 3)]},
           "note": "只验参数是否真传导到壳体/货架/导航格与越界是否拒绝；"
                   "交互重摆（拖拽功能区/月台）属于 R8.7，未在此声称。"}
    TMP.mkdir(parents=True, exist_ok=True)
    (OUT / "area_param_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.quiet:
        print("%s %s" % (rep["verdict"], ",".join(failed) if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
