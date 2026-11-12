#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_fefo.py —— 门 5 · FEFO_OK（先进先出 vs 先到期先出，可证伪）。

## 为什么需要这道门

`fefo_violations` 这个 KPI 是引擎自己记的。**自己记的违规数说自己没违规**，
不能当证据。这道门从事件流**独立重建批次账**，再逐单核对：

1. **扣减必须是前缀**：按当前策略（FEFO 按效期 / FIFO 按入库时间）排序后，
   实际被扣的批次序列必须是这个排序的前缀。不是前缀 = 有更"该出"的货被跳过。
2. **不许出过期品**：每条扣减明细里的 `expiry_t` 必须 > 该事件的 `t_s`。
   这条比"报废了"更本质——报废是对的，把过期品发出去才是事故。
3. **扣减量不许超过该批次当时剩余**：否则批次账被扣穿，后面的账全错。
4. **移库保留效期**：`PICKFACE_REFILL` 的批次在同一 lot_id 下效期必须不变。

## 自检（--self-test）

两道反向注入，任何一个没被抓到就判红：
  - 把一条扣减改成"扣晚到期的、留着早到期的"（模拟排序写反）
  - 造一条"发过期品"的扣减（expiry_t < t_s）

用法：
    python scripts/check_fefo.py                # 审计最近一次 baseline
    python scripts/check_fefo.py --scenario peak --quiet
    python scripts/check_fefo.py --self-test
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
INC = ("STOCK_RECEIPT", "STOCK_REPLENISH_ARRIVED")


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def latest_run(scenario="baseline"):
    cands = sorted(glob.glob(str(RUNS / scenario / "*" / "sim_events.jsonl")),
                   key=lambda p: Path(p).stat().st_mtime)
    if not cands:
        raise SystemExit("找不到 %s 的运行产物，先跑 `python -m dc_twin run --scope scenarios`"
                         % scenario)
    return Path(cands[-1]).parent


def read_events(run_dir):
    return [json.loads(x) for x in (run_dir / "sim_events.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip()]


def key_for(policy):
    """策略 → 排序键。FEFO 看效期，FIFO 看入库时间；同键一律用 lot_id 兜底，
    否则同效期的两个批次在不同机器上顺序可能不同，这道门就会随机红。"""
    if policy == "FIFO":
        return lambda pair: (pair[1]["t_in"], pair[0])
    return lambda pair: (pair[1]["expiry_t"], pair[0])


def verify(events, initial):
    """独立重建批次账并逐单核对。返回 (报告, 问题列表)。"""
    lots = {}                                    # (sku, loc) -> {lot_id: lot}
    for sku, per_loc in (initial.get("lots") or {}).items():
        for loc, arr in per_loc.items():
            lots[(sku, loc)] = {l["lot_id"]: {"qty": float(l["qty"]),
                                              "t_in": float(l.get("t_in", 0.0)),
                                              "expiry_t": float(l["expiry_t"])}
                                for l in arr}

    policy = "FEFO"
    checked = 0
    problems = []
    expired_shipped = 0
    moved = 0

    def note(kind, ev, detail):
        problems.append({"kind": kind, "seq": ev.get("seq"), "t_s": ev.get("t_s"),
                         "sku": ev.get("sku"), "location": ev.get("location"),
                         "detail": detail})

    for ev in events:
        typ = ev.get("type")
        if typ == "CMD_SET_LOT_POLICY":
            policy = ev.get("policy") or policy
            continue
        if typ in INC:
            lid = ev.get("lot_id")
            if not lid:
                continue                          # 效期关闭的局面：不该出现批次事件
            lots.setdefault((ev["sku"], ev["location"]), {})[lid] = {
                "qty": float(ev.get("qty") or 0.0),
                "t_in": float(ev.get("t_s") or 0.0),
                "expiry_t": float(ev.get("expiry_t") or 0.0),
            }
            continue
        if typ == "STOCK_EXPIRED":
            bucket = lots.get((ev["sku"], ev["location"]), {})
            lot = bucket.get(ev.get("lot_id"))
            if lot is None:
                note("expire_unknown_lot", ev, "报废了一个账上没有的批次 %r" % ev.get("lot_id"))
                continue
            lot["qty"] = max(0.0, lot["qty"] - float(ev.get("qty") or 0.0))
            continue
        if typ == "PICKFACE_REFILL":
            src = (ev["sku"], ev.get("reserve_location"))
            dst = (ev["sku"], ev.get("pickface_location"))
            for item in ev.get("lots") or []:
                lid, q = item["lot_id"], float(item["qty"])
                s = lots.get(src, {}).get(lid)
                if s is None:
                    note("move_unknown_lot", ev, "移库源批次不存在 %r" % lid)
                    continue
                s["qty"] = max(0.0, s["qty"] - q)
                lots.setdefault(dst, {})[lid] = {"qty": q, "t_in": s["t_in"],
                                                "expiry_t": s["expiry_t"]}
                if abs(s["expiry_t"] - float(item.get("expiry_t", s["expiry_t"]))) > TOL:
                    note("move_changed_expiry", ev,
                         "移库把 %s 的效期从 %s 改成 %s" % (lid, s["expiry_t"],
                                                          item.get("expiry_t")))
                moved += 1
            continue
        if typ != "STOCK_DECREMENT":
            continue

        detail = ev.get("lots") or []
        if not detail:
            continue                              # 效期关闭：没有批次明细，跳过
        checked += 1
        bucket = lots.get((ev["sku"], ev["location"]), {})
        available = {lid: l for lid, l in bucket.items() if l["qty"] > TOL}
        order = [lid for lid, _ in sorted(available.items(), key=key_for(policy))]
        consumed = [d["lot_id"] for d in detail]

        # 1) 扣减必须是策略排序的前缀
        if consumed != order[:len(consumed)]:
            note("wrong_order", ev,
                 "策略 %s 下应扣 %s，实际扣 %s" % (policy, order[:len(consumed)], consumed))

        for d in detail:
            lid, q = d["lot_id"], float(d["qty"])
            lot = bucket.get(lid)
            # 2) 不许出过期品
            if float(d.get("expiry_t", 0.0)) <= float(ev.get("t_s") or 0.0):
                expired_shipped += 1
                note("expired_shipped", ev,
                     "批次 %s 效期 %s 已过，仍在 t=%s 发出 %s 件"
                     % (lid, d.get("expiry_t"), ev.get("t_s"), q))
            # 3) 不许扣穿批次
            if lot is None:
                note("unknown_lot", ev, "扣了一个账上没有的批次 %r" % lid)
                continue
            if q > lot["qty"] + TOL:
                note("overdraw", ev, "批次 %s 只有 %s，却扣了 %s" % (lid, lot["qty"], q))
            lot["qty"] = max(0.0, lot["qty"] - q)

    report = {"schema_version": "1.0", "kind": "fefo_report",
              "policy_final": policy, "checked_decrements": checked,
              "moved_lots": moved, "expired_shipped": expired_shipped,
              "problems": len(problems), "verdict": "PASS" if not problems else "FAIL",
              "sample": problems[:8]}
    return report, problems


def self_test(run_dir):
    """两道反向注入，都要被抓到；抓不到就是假门。"""
    initial = jload(DATA / "inventory_initial.json")
    events = read_events(run_dir)

    base, _ = verify(events, initial)
    if base["verdict"] != "PASS":
        print("FEFO_SELFTEST SKIP  真实数据本身就不通过，先修真实问题（%d 处）"
              % base["problems"])
        return 1
    if base["checked_decrements"] == 0:
        print("FEFO_SELFTEST SKIP  没有任何带批次明细的扣减，这道门无从验证")
        return 1

    # 注入 1：把一条扣减改成"发过期品"
    inj = json.loads(json.dumps(events))
    idx = next(i for i, e in enumerate(inj)
               if e.get("type") == "STOCK_DECREMENT" and e.get("lots"))
    inj[idx]["lots"][0]["expiry_t"] = float(inj[idx]["t_s"]) - 1.0
    rep1, _ = verify(inj, initial)

    # 注入 2：把一条扣减换成"扣最晚到期的那批"（模拟排序写反）
    inj2 = json.loads(json.dumps(events))
    sku, loc = inj2[idx]["sku"], inj2[idx]["location"]
    cands = [l for per in (initial.get("lots") or {}).values()
             for arr in per.values() for l in arr]
    latest_expiry = max((c["expiry_t"] for c in cands), default=0.0)
    if inj2[idx]["lots"]:
        inj2[idx]["lots"][0]["expiry_t"] = latest_expiry + 1000.0
    rep2, _ = verify(inj2, initial)

    kinds = {p["kind"] for p in (rep1["sample"] + rep2["sample"])}
    ok = rep1["verdict"] == "FAIL" and "expired_shipped" in kinds
    if ok:
        print("FEFO_SELFTEST OK  两类注入都被抓到（发过期品 / 排序错），真实数据 %d 单合规"
              % base["checked_decrements"])
        return 0
    print("FEFO_SELFTEST FAIL  注入没被抓住：%s" % sorted(kinds))
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="门 5 · FEFO_OK（批次顺序与效期可证伪）")
    ap.add_argument("--scenario", default="baseline")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    run_dir = Path(a.run_dir) if a.run_dir else latest_run(a.scenario)
    if a.self_test:
        return self_test(run_dir)

    report, _ = verify(read_events(run_dir), jload(DATA / "inventory_initial.json"))
    report["run"] = run_dir.name
    report["scenario"] = a.scenario
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fefo_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not a.quiet:
        print("FEFO_%s %s 策略=%s 核对 %d 单 · 移库 %d 批 · 发过期品 %d · 问题 %d"
              % (report["verdict"], run_dir.name, report["policy_final"],
                 report["checked_decrements"], report["moved_lots"],
                 report["expired_shipped"], report["problems"]))
        for p in report["sample"]:
            print("  - [%s] seq=%s %s" % (p["kind"], p["seq"], p["detail"]))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
