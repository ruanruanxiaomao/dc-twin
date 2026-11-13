#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D11-A 门 · 分型进关卡场景，出厂仍然冻结（``TYPED_VARIANT_OK``）。

D11 有两条路：A 是"分型只在关卡/编辑器场景启用，出厂保持冻结"，B 是"出厂默认
本身变 typed（要一次四锚受控重冻）"。本门守的是 **A 的全部承诺**：

* ``TYPED_SPEC_OK``     关卡 spec 是**脚本生成**的，手改立刻红（否则"变体"会慢慢
                        漂成第二份真相）。
* ``TYPED_LAYOUT_OK``   这一步真的把 9 类机理 / 4 型输送 / 分拣分型用上了，
                        且 0 issue、exit 0（能用，不只是"写了个键"）。
* ``FACTORY_FROZEN_OK`` 出厂布局**一个字节都没动**，而且 layout 里没有
                        `rack_type` / `style` / `sorter` 任何键——A 的核心承诺。
* ``PROBE_DISTINCT_OK`` 9 类货架的几何指纹两两不同（复用 R8.2 探针，不新造视觉门）。

自检把 A 最怕的两件事真做出来：出厂被偷偷启用分型、关卡 spec 被手改。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CONFIG, DATA, OUT = ROOT / "config", ROOT / "data", ROOT / "out"
TMP = OUT / "exp" / "typed"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _run(spec_path, tag):
    import subprocess as sp
    TMP.mkdir(parents=True, exist_ok=True)
    layout = TMP / ("%s_layout.json" % tag)
    rep = TMP / ("%s_report.json" % tag)
    r = sp.run([sys.executable, str(ROOT / "scripts" / "spec_layout.py"),
                "--spec", str(spec_path), "--out", str(TMP / ("%s_spec_layout.json" % tag)),
                "--report", str(rep), "--write", str(layout), "--quiet"],
               cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace")
    lay = jload(layout) if layout.exists() else (
        jload(TMP / ("%s_spec_layout.json" % tag))
        if (TMP / ("%s_spec_layout.json" % tag)).exists() else None)
    return r.returncode, lay, (jload(rep) if rep.exists() else {})


def _dispatch_counts(layout):
    types = Counter()
    styles = Counter()
    sorters = Counter()
    for w in layout["warehouses"]:
        for rk in w.get("racks") or []:
            types[rk.get("rack_type")] += 1
        for cv in w.get("conveyors") or []:
            styles[cv.get("style")] += 1
        for st in w.get("stations") or []:
            sorters[st.get("sorter")] += 1
    return types, styles, sorters


def check_spec_generated():
    """关卡 spec 必须与脚本生成结果逐字节一致（手改 = 第二份真相）。"""
    import build_typed_spec
    problems = []
    want = json.dumps(build_typed_spec.build(), ensure_ascii=False, indent=2) + "\n"
    p = CONFIG / "layout_spec_typed.json"
    if not p.exists():
        return False, {}, ["没有 config/layout_spec_typed.json（先跑 build_typed_spec.py）"]
    got = p.read_text(encoding="utf-8")
    if got != want:
        problems.append("关卡 spec 与脚本生成结果不一致（手改过？重跑 build_typed_spec.py）")
    doc = json.loads(got)
    groups = sum(len(w.get("racks") or []) for w in doc["warehouses"])
    return not problems, {"groups": groups}, problems


def check_typed_layout(rc, layout, report, factory_layout):
    problems = []
    issues = (report or {}).get("issues") or []
    if rc != 0:
        problems.append("关卡场景被校验拒绝（退出码 %s）——分型版不可用" % rc)
    if issues:
        problems.append("关卡场景有 %d 条 issue：%s"
                        % (len(issues), (issues[0].get("message") or "")[:60]))
    if layout is None:
        return False, {"issues": len(issues)}, problems
    types, styles, sorters = _dispatch_counts(layout)
    named = {k: v for k, v in types.items() if k}
    if len(named) != 9:
        problems.append("场景只用到 %d 类货架机理（要求 9）：%s" % (len(named), sorted(named)))
    if min(named.values() or [0]) < 1:
        problems.append("有机理只声明没建：%s" % [k for k, v in named.items() if v < 1])
    used_styles = {k for k in styles if k}
    if len(used_styles) != 4:
        problems.append("输送分型只用到 %d 型（要求 4）：%s" % (len(used_styles), sorted(used_styles)))
    if not any(k for k in sorters):
        problems.append("分拣站没有任何 sorter 分型声明")
    base_loc = (factory_layout or {}).get("summary", {}).get("locations") or 0
    loc = layout["summary"]["locations"]
    if loc <= base_loc:
        problems.append("分型版库位没有变化（%s → %s）——深位机理没生效" % (base_loc, loc))
    return not problems, {"rack_types": named, "styles": dict(styles),
                          "sorters": dict(sorters), "locations": loc,
                          "base_locations": base_loc}, problems


def check_factory_frozen(factory_layout, rc, report, same_bytes=None):
    """出厂布局一字节不动，且没有任何分型键。

    `same_bytes` 由调用方用**磁盘上的两份文件**比对得出，不在这里重新序列化——
    否则序列化参数（缩进/结尾换行）会冒充成"内容变了"，冤枉一次金标准。
    """
    problems = []
    if rc != 0:
        problems.append("出厂 spec 跑不过校验（退出码 %s）" % rc)
    p = DATA / "layout.json"
    if not p.exists():
        return False, {}, ["没有 data/layout.json"]
    disk = p.read_bytes()
    if factory_layout is None:
        return False, {}, ["出厂布局没有产出"]
    if same_bytes is False:
        problems.append("出厂布局重生成后与磁盘不一致（金标准被改动）")
    keys = Counter()
    for w in factory_layout["warehouses"]:
        for rk in w.get("racks") or []:
            if rk.get("rack_type"):
                keys["rack_type"] += 1
        for cv in w.get("conveyors") or []:
            if cv.get("style"):
                keys["style"] += 1
        for st in w.get("stations") or []:
            if st.get("sorter"):
                keys["sorter"] += 1
    if keys:
        problems.append("出厂布局里出现了分型键：%s——A 方案要求出厂冻结" % dict(keys))
    return not problems, {"sha256": hashlib.sha256(disk).hexdigest()[:8],
                          "bytes": len(disk), "typed_keys": dict(keys)}, problems


def check_probe_distinct():
    """9 类货架指纹两两不同：读 R8.2 探针产物，不新造视觉门。"""
    problems, detail = [], {}
    rp = OUT / "rack_mesh_probe.json"
    vp = OUT / "variant_probe.json"
    if not rp.exists() or not vp.exists():
        return False, {}, ["没有探针产物（out/rack_mesh_probe.json / variant_probe.json）"]
    r = jload(rp)
    want = hashlib.sha256((ROOT / "scripts" / "rack_assets.py").read_bytes()).hexdigest()
    if r.get("source_sha") != want:
        problems.append("货架探针过期：rack_assets.py 已改（重跑 check_asset_dispatch --probe）")
    geoms = [v.get("geom_sha") for v in (r.get("mechanisms") or {}).values()]
    detail["rack_mech"] = len(geoms)
    detail["rack_distinct"] = len(set(geoms))
    if len(geoms) != 9:
        problems.append("货架探针只覆盖 %d 类" % len(geoms))
    if len(set(geoms)) != len(geoms):
        problems.append("货架几何有重复指纹（%d 类 → %d 种）" % (len(geoms), len(set(geoms))))
    v = jload(vp)
    detail["conveyor_distinct"] = v.get("distinct_conveyor")
    detail["sorters"] = len(v.get("sorters") or {})
    if v.get("distinct_conveyor") != 4:
        problems.append("输送分型不是 4 种不同几何（%r）" % v.get("distinct_conveyor"))
    if len(v.get("sorters") or {}) != 7:
        problems.append("分拣探针只覆盖 %d 型" % len(v.get("sorters") or {}))
    return not problems, detail, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="D11-A 门：分型进关卡场景")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    # 只清本门自己写的 9 个文件，**不要 glob 整个目录再批量删**：
    # 一次 unlink 50 个文件会触发宿主的安全删除阈值（bulk delete），
    # 在 pytest 里表现为一条莫名其妙的 FAILED（实测 count=50 正好踩线）。
    for tag in ("typed", "factory", "factory_typed"):
        for suf in ("layout", "report", "spec_layout"):
            (TMP / ("%s_%s.json" % (tag, suf))).unlink(missing_ok=True)

    typed_rc, typed, typed_rep = _run(CONFIG / "layout_spec_typed.json", "typed")
    fac_rc, fac, fac_rep = _run(CONFIG / "layout_spec.json", "factory")

    if a.self_test:
        bad = copy.deepcopy(jload(CONFIG / "layout_spec.json"))
        bad["warehouses"][0]["racks"][0].setdefault("rack", {})["rack_type"] = "double_deep"
        p = TMP / "factory_typed.json"
        p.write_text(json.dumps(bad, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rc_bad, lay_bad, rep_bad = _run(p, "factory_typed")
        bad_path = TMP / "factory_typed_layout.json"
        ok_frozen, _, _ = check_factory_frozen(
            lay_bad, rc_bad, rep_bad,
            same_bytes=(bad_path.read_bytes() == (DATA / "layout.json").read_bytes())
            if bad_path.exists() else None)

        import build_typed_spec
        orig = build_typed_spec.build
        try:
            build_typed_spec.build = lambda *x, **k: {"warehouses": []}
            ok_spec, _, _ = check_spec_generated()
        finally:
            build_typed_spec.build = orig
        good = (not ok_frozen) and (not ok_spec)
        print("TYPED_SELF_TEST %s" % ("PASS" if good else "FAIL"))
        print("  factory_typed_leak  %s" % ("ok" if not ok_frozen else "MISSED"))
        print("  spec_hand_edited    %s" % ("ok" if not ok_spec else "MISSED"))
        return 0 if good else 1

    gates, failed = {}, []
    for name, fn in (("TYPED_SPEC_OK", check_spec_generated),
                     ("TYPED_LAYOUT_OK", lambda: check_typed_layout(typed_rc, typed, typed_rep, fac)),
                     ("FACTORY_FROZEN_OK", lambda: check_factory_frozen(
                         fac, fac_rc, fac_rep,
                         same_bytes=((TMP / "factory_layout.json").read_bytes()
                                     == (DATA / "layout.json").read_bytes())
                         if (TMP / "factory_layout.json").exists() else None)),
                     ("PROBE_DISTINCT_OK", check_probe_distinct)):
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

    rep = {"schema_version": "1.0", "kind": "typed_variant_report",
           "verdict": "TYPED_VARIANT_OK" if not failed else "TYPED_VARIANT_FAIL",
           "failed": failed, "gates": gates,
           "note": "D11-A：分型只在关卡/编辑器场景启用，出厂配置与布局逐字节冻结；"
                   "几何指纹复用 R8.2 探针（9 货架 / 4 输送 / 7 分拣），不新造视觉门。"}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "typed_variant_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.quiet:
        print("%s %s" % (rep["verdict"], ",".join(failed) if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
