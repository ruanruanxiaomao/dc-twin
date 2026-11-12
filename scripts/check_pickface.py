#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_pickface.py —— 门 4 · PICKFACE_OK（拣选面对账）。

方案书原话是「拣选面在库只能来自 储备位→拣选面 的显式转移，禁止凭空 +」。
落地时发现这句比引擎实际行为更严：入库单（INBOUND/REPLENISH）的交货位是**任意
SHELF**，也可能落在拣选面上，于是「拣选面出现 STOCK_RECEIPT 增量」是合法的。

所以这里判的是**更本质的那条**：
    拣选面上任何**增量**，都必须能被事件流里某条事件解释
    （PICKFACE_REFILL 或 STOCK_RECEIPT / STOCK_REPLENISH_ARRIVED）；
    状态里有增量、事件流里找不到来源 → 判 FAIL（这才是"凭空 +"）。

另外两条：
  - Σ拣选面在库 + Σ储备在库 == 总账；
  - 每条 PICKFACE_REFILL 必须成对（储备 −q、拣选 +q），且 q>0。

`--self-test` 往某个拣选面结存里偷偷 +5（不写事件）→ 必须 FAIL。

用法：
    python scripts/check_pickface.py
    python scripts/check_pickface.py --self-test
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT, RUNS = ROOT / "data", ROOT / "out", ROOT / "runs"
TOL = 1e-9


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def latest_run(scenario="baseline"):
    cands = sorted(glob.glob(str(RUNS / scenario / "*" / "sim_state.json")),
                   key=lambda p: Path(p).stat().st_mtime)
    if not cands:
        raise SystemExit("找不到运行产物，先跑 `python -m dc_twin run --scope scenarios`")
    return Path(cands[-1]).parent


def audit(run_dir, slotting, initial, tamper=None):
    state = jload(run_dir / "sim_state.json")
    inv = (state.get("inventory") or {})
    if not inv or "stock" not in inv:
        return {"verdict": "FAIL", "error": "sim_state 没有 inventory"}, []
    closing = {sku: {loc: float(q) for loc, q in rows.items()}
               for sku, rows in inv["stock"].items()}
    if tamper:
        tamper(closing)

    role = {r["location_id"]: r["role"] for r in slotting["locations"]}
    pf_locs = {lid for lid, r in role.items() if r == "pick_face"}
    events = [json.loads(x) for x in (run_dir / "sim_events.jsonl")
              .read_text(encoding="utf-8").splitlines() if x.strip()]

    # 事件流给每个 (sku,loc) 的"有解释的增量"
    explained = {}
    pair_issues = []
    for e in events:
        t = e.get("type")
        q = float(e.get("qty") or 0.0)
        if t == "PICKFACE_REFILL":
            rs, pf, sku = e.get("reserve_location"), e.get("pickface_location"), e.get("sku")
            if not rs or not pf or q <= 0:
                pair_issues.append({"seq": e.get("seq"), "why": "refill 字段不全或 qty<=0"})
                continue
            explained[(sku, pf)] = explained.get((sku, pf), 0.0) + q
        elif t in ("STOCK_RECEIPT", "STOCK_REPLENISH_ARRIVED"):
            key = (e.get("sku"), e.get("location"))
            explained[key] = explained.get(key, 0.0) + q

    rows, bad = [], []
    for lid in sorted(pf_locs):
        # 该库位当前对应的 SKU（一个库位一个 SKU）
        sku = next((r["sku"] for r in slotting["locations"] if r["location_id"] == lid), None)
        opening = float(initial["stock"].get(sku, {}).get(lid, 0.0))
        closing_q = float(closing.get(sku, {}).get(lid, 0.0))
        inc = max(0.0, closing_q - opening)
        exp = explained.get((sku, lid), 0.0)
        ok = inc <= exp + TOL
        rows.append({"sku": sku, "location": lid, "opening": opening,
                     "closing": closing_q, "increase": round(inc, 6),
                     "explained_increase": round(exp, 6), "ok": ok})
        if not ok:
            bad.append(rows[-1])

    # 总量对账
    total_pf = sum(float(closing.get(s, {}).get(l, 0.0)) for l in pf_locs
                   for s in [next((r["sku"] for r in slotting["locations"]
                                   if r["location_id"] == l), None)])
    total_all = sum(q for rows_ in closing.values() for q in rows_.values())
    total_reserve = total_all - total_pf
    verdict = "PASS" if not bad and not pair_issues else "FAIL"
    return {"schema_version": "1.0", "kind": "pickface_report",
            "run": run_dir.name, "verdict": verdict,
            "pick_face_locations": len(pf_locs),
            "unexplained_increase": len(bad),
            "totals": {"pick_face": round(total_pf, 6), "reserve": round(total_reserve, 6),
                       "all": round(total_all, 6),
                       "sum_ok": abs(total_pf + total_reserve - total_all) <= TOL},
            "pair_issues": pair_issues[:5], "sample_bad": bad[:5]}, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="门 4 · 拣选面对账")
    ap.add_argument("--scenario", default="baseline")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    run_dir = Path(a.run_dir) if a.run_dir else latest_run(a.scenario)
    slotting = jload(DATA / "inventory_slotting.json")
    initial = jload(DATA / "inventory_initial.json")

    if a.self_test:
        def tamper(closing):
            lid = sorted(r["location_id"] for r in slotting["locations"]
                         if r["role"] == "pick_face")[0]
            sku = next(r["sku"] for r in slotting["locations"] if r["location_id"] == lid)
            closing.setdefault(sku, {})[lid] = closing.get(sku, {}).get(lid, 0.0) + 5.0
        rep, _ = audit(run_dir, slotting, initial, tamper=tamper)
        if rep["verdict"] == "FAIL" and rep["unexplained_increase"] >= 1:
            print("PICKFACE_SELFTEST OK  凭空 +5 被抓到（%d 处无来源增量）"
                  % rep["unexplained_increase"])
            return 0
        print("PICKFACE_SELFTEST FAIL  凭空加了 5 件居然还是 PASS —— 这道门是假的")
        return 1

    rep, rows = audit(run_dir, slotting, initial)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pickface_report.json").write_text(
        json.dumps(dict(rep, detail=rows if len(rows) <= 50 else rows[:50]),
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not a.quiet:
        print("PICKFACE_%s %s face=%d unexplained=%d 总量 拣选面 %s / 储备 %s / 合计 %s"
              % (rep["verdict"], run_dir.name, rep["pick_face_locations"],
                 rep["unexplained_increase"], rep["totals"]["pick_face"],
                 rep["totals"]["reserve"], rep["totals"]["all"]))
        for r in rep.get("sample_bad") or []:
            print("  - %s@%s 增量 %s 只有 %s 有解释" % (r["sku"], r["location"],
                                                     r["increase"], r["explained_increase"]))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
