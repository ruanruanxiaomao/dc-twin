#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""v1.0 冻结门（``MILESTONE_V1_OK``）。

冻结不是一纸声明，是**三条锚 + 一组计数**：任何默认产物变了，这里先红，
而不是等到"怎么 v1 的图和上周不一样"才发现。

锚（`config/milestone_v1.json`）：

* `data/layout.json` — 默认布局，出厂未启用面积/分型/宽度轴；
* `out/build_report.json` — 默认构建报告（counts 与 .blend 实测一致）；
* `out/dc_preview.glb` — 默认 GLB（ADR-13 规整后两次导出逐字节相同）。

计数：4 仓 / 196 架 / 441 库位 / 2345 对象 / 394 mesh / 86 材质。

**重冻流程**（写进 MILESTONE_V1.md）：改 `config/milestone_v1.json` 的锚 → 写 ADR
说明"为什么变" → 同一次提交里跑 `--scope full` 与 A1–A40 → 再提交。不许只改锚不写原因。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG, DATA, OUT = ROOT / "config", ROOT / "data", ROOT / "out"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


#: 这两份锚是 **Blender 产物**（gitignore，需 --scope full 才生成）。CI 上没有
#: Blender（一帧 smoke 是 manual-only），缺失只说明"没跑 Blender"，不是默认产物被改。
#: 显式跳过并写进 detail，既不判红、也不假装验过。
BLENDER_ANCHORS = ("out/build_report.json", "out/dc_preview.glb")


def check_anchors(freeze):
    problems, detail, skipped = [], {}, []
    for rel, want in (freeze.get("anchors") or {}).items():
        p = ROOT / rel
        if not p.exists():
            if rel in BLENDER_ANCHORS:
                skipped.append(rel)
                continue
            problems.append("%s 缺失（先跑 --scope full 构建）" % rel)
            continue
        b = p.read_bytes()
        sha = hashlib.sha256(b).hexdigest()
        detail[rel] = {"sha": sha[:8], "bytes": len(b)}
        if not sha.startswith(want.get("sha256_prefix") or "?"):
            problems.append("%s 的 sha 变了：%s…（冻结 %s…）——默认产物被改动"
                            % (rel, sha[:8], want.get("sha256_prefix")))
        if want.get("bytes") is not None and len(b) != int(want["bytes"]):
            problems.append("%s 的字节数变了：%d（冻结 %d）"
                            % (rel, len(b), int(want["bytes"])))
    if skipped:
        detail["skipped_no_blender"] = skipped
    return not problems, detail, problems


def check_counts(freeze, report=None):
    """布局侧计数（racks/locations/warehouses）必验；对象/网格/材质来自
    Blender 的 build_report.json，CI 无该产物时只跳过这三项，不判红。"""
    problems, detail, skipped = [], {}, []
    want = freeze.get("counts") or {}
    lay = jload(DATA / "layout.json")
    if report is None:
        rp = OUT / "build_report.json"
        report = jload(rp) if rp.exists() else {}
    got = {
        "racks": lay["summary"]["racks"], "locations": lay["summary"]["locations"],
        "warehouses": lay["summary"]["warehouses"],
    }
    for k in ("objects", "meshes", "materials"):
        if k in report:
            got[k] = report.get(k)
        else:
            skipped.append(k)
    detail.update(got)
    if skipped:
        detail["skipped_no_blender"] = skipped
    for k, v in want.items():
        if k not in got:            # 跳过项不参与比对（已在 detail 里写明）
            continue
        if got.get(k) != v:
            problems.append("计数 %s 变了：%r（冻结 %r）" % (k, got.get(k), v))
    return not problems, detail, problems


def check_scope(freeze, chain_steps=None):
    """链步数、验收口径、backlog 记账都必须显式。"""
    problems, detail = [], {}
    want_steps = int(freeze.get("refactor_chain_steps") or 0)
    if chain_steps is None:
        try:
            sys.path.insert(0, str(ROOT))
            import dc_twin.runner as R
            chain_steps = len(R.REFACTOR_CHAIN)
        except Exception:                                    # noqa: BLE001
            chain_steps = 0
    detail["refactor_chain_steps"] = chain_steps
    if chain_steps != want_steps:
        problems.append("refactor 链步数 %d ≠ 冻结的 %d（加了门就同步改冻结件）"
                        % (chain_steps, want_steps))
    backlog = freeze.get("backlog_v1_1") or []
    detail["backlog_items"] = len(backlog)
    if len(backlog) < 1:
        problems.append("backlog 为空：v1.1 的欠账必须显式记账，不许口头承诺")
    if any(not str(x).strip() for x in backlog):
        problems.append("backlog 里有空条目")
    acc = freeze.get("acceptance") or {}
    if acc.get("total") != 40 or acc.get("passed") != 40:
        problems.append("验收口径应为 40/40（收到 %r/%r）"
                        % (acc.get("total"), acc.get("passed")))
    return not problems, detail, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="v1.0 冻结门")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    freeze = jload(CONFIG / "milestone_v1.json")

    if a.self_test:
        bad = json.loads(json.dumps(freeze))
        bad["anchors"]["data/layout.json"]["sha256_prefix"] = "00000000"
        ok_anchor, _, _ = check_anchors(bad)
        bad2 = json.loads(json.dumps(freeze))
        bad2["counts"]["racks"] = 999
        ok_counts, _, _ = check_counts(bad2)
        bad3 = json.loads(json.dumps(freeze))
        bad3["backlog_v1_1"] = []
        ok_scope, _, _ = check_scope(bad3)
        good = (not ok_anchor) and (not ok_counts) and (not ok_scope)
        print("MILESTONE_SELF_TEST %s" % ("PASS" if good else "FAIL"))
        print("  anchor_drift  %s" % ("ok" if not ok_anchor else "MISSED"))
        print("  count_drift   %s" % ("ok" if not ok_counts else "MISSED"))
        print("  empty_backlog %s" % ("ok" if not ok_scope else "MISSED"))
        return 0 if good else 1

    gates, failed = {}, []
    for name, fn in (("MILESTONE_ANCHORS_OK", lambda: check_anchors(freeze)),
                     ("MILESTONE_COUNTS_OK", lambda: check_counts(freeze)),
                     ("MILESTONE_SCOPE_OK", lambda: check_scope(freeze))):
        try:
            ok, detail, problems = fn()
        except Exception as e:                                # noqa: BLE001
            ok, detail, problems = False, {}, ["门执行异常：%r" % (e,)]
        gates[name] = {"ok": ok, "detail": detail, "problems": problems}
        if not ok:
            failed.append(name)
        if not a.quiet:
            print("%-24s %s%s" % (name, "PASS" if ok else "FAIL",
                                  "" if ok else "  -> " + "; ".join(problems[:2])))
            if ok:
                print("                         %s" % json.dumps(detail, ensure_ascii=False)[:200])

    rep = {"schema_version": "1.0", "kind": "milestone_v1_report",
           "verdict": "MILESTONE_V1_OK" if not failed else "MILESTONE_V1_FAIL",
           "failed": failed, "gates": gates,
           "note": "v1.0 冻结：三锚 + 计数 + 链步数 + backlog 记账。"
                   "重冻必须同一次提交改锚、写 ADR、跑 full 与 A1–A40。"}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "milestone_v1_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.quiet:
        print("%s %s" % (rep["verdict"], ",".join(failed) if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
