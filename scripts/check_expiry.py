#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_expiry.py —— 门 6 · EXPIRY_OK（效期报废的时机与账面一致）。

判据四条，缺一条这道门就是装饰：

1. **不许提前报废**：每条 `STOCK_EXPIRED` 的 `expiry_t` 必须 <= 该事件的 `t_s`。
   提前报废等于"把还没过期的货当废物扔掉"，账面会凭空少一批可用库存，
   而缺货率会因此变差——这种错误在汇总指标里看起来只是"效率低了"，查不出来。
2. **不许漏报**：跑完一局，所有到期时刻落在 [0, horizon] 内的期初批次，
   必须都有对应的报废事件（或已被更早拣出）。漏报意味着过期品还在货架上，
   而下一单会把它发出去。
3. **状态与事件一致**：`sim_state.inventory.summary.expired`（引擎自报）必须等于
   事件流里报废量的总和；期末总账也必须等于 期初 + 入 − 出 − 报废（由
   `check_inventory.py` 守，这里只对"报废这一项"单独对齐）。
4. **可观测**：效期开启时 `expired_units > 0`；关闭时 `STOCK_EXPIRED` 必须为 0。
   前者防"死代码"，后者防"开关无效"。

`--self-test` 注入两条反例：把一条报废的 expiry_t 改到未来（提前报废）、
把期末报废量 +1（账面不一致）。任一条没被抓到就判红。

用法：
    python scripts/check_expiry.py
    python scripts/check_expiry.py --scenario holiday --quiet
    python scripts/check_expiry.py --self-test
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
        raise SystemExit("找不到 %s 的运行产物，先跑 `python -m dc_twin run --scope scenarios`"
                         % scenario)
    return Path(cands[-1]).parent


def read_events(run_dir):
    return [json.loads(x) for x in (run_dir / "sim_events.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip()]


def audit(run_dir, initial, tamper=None):
    """返回 (报告, 问题列表)。tamper 是给自检用的注入钩子。"""
    events = read_events(run_dir)
    state = jload(run_dir / "sim_state.json")
    inv = state.get("inventory") or {}
    summary = inv.get("summary") or {}
    expiry_enabled = bool(summary.get("expiry_enabled", summary.get("enabled", False)))

    exp = [e for e in events if e.get("type") == "STOCK_EXPIRED"]
    problems = []

    def note(kind, detail, **kw):
        problems.append(dict({"kind": kind, "detail": detail}, **kw))

    # 1) 不许提前报废
    for e in exp:
        expiry = float(e.get("expiry_t") or 0.0)
        t = float(e.get("t_s") or 0.0)
        if expiry > t + 1e-9:
            note("premature_writeoff",
                 "批次 %s 效期 %s 还没到，就在 t=%s 报废了 %s 件"
                 % (e.get("lot_id"), expiry, t, e.get("qty")), seq=e.get("seq"))
        if float(e.get("qty") or 0.0) <= 0:
            note("nonpositive_writeoff", "报废量为 %s" % e.get("qty"), seq=e.get("seq"))

    # 2) 不许漏报：期初批次里到期时刻在窗口内的，要么被报废，要么已被拣出
    horizon = float(state.get("horizon_s") or 0.0)
    expired_ids = {e.get("lot_id") for e in exp}
    consumed_ids = {d["lot_id"] for e in events if e.get("type") == "STOCK_DECREMENT"
                    for d in (e.get("lots") or [])}
    missed = []
    if expiry_enabled and horizon > 0:
        for sku, per_loc in (initial.get("lots") or {}).items():
            for loc, arr in per_loc.items():
                for lot in arr:
                    if float(lot["expiry_t"]) > horizon:
                        continue
                    lid = lot["lot_id"]
                    if lid not in expired_ids and lid not in consumed_ids:
                        missed.append("%s@%s/%s（效期 %s）" % (sku, loc, lid, lot["expiry_t"]))
    if missed:
        note("missed_writeoff", "有 %d 个到期批次既没报废也没被拣出：%s"
             % (len(missed), missed[:5]))

    # 3) 状态与事件一致
    event_units = sum(float(e.get("qty") or 0.0) for e in exp)
    event_value = sum(float(e.get("value") or 0.0) for e in exp)
    reported_units = float(summary.get("expired_units", event_units))
    reported_value = float(summary.get("expired_value", event_value))
    if tamper:
        reported_units, reported_value = tamper(reported_units, reported_value)
    if abs(reported_units - event_units) > TOL:
        note("units_mismatch", "状态报报废 %s，事件流合计 %s" % (reported_units, event_units))
    if abs(reported_value - event_value) > 1e-6:
        note("value_mismatch", "状态报报废价值 %s，事件流合计 %s"
             % (reported_value, event_value))

    # 4) 可观测 / 开关有效
    if expiry_enabled and event_units <= 0:
        note("unobservable", "效期开着却一件都没过期：这道门在这个场景里等于没测")
    if not expiry_enabled and exp:
        note("switch_ignored", "效期关着却产生了 %d 条报废事件" % len(exp))

    report = {"schema_version": "1.0", "kind": "expiry_report",
              "run": run_dir.name, "expiry_enabled": expiry_enabled,
              "writeoff_events": len(exp), "expired_units": round(event_units, 6),
              "expired_value": round(event_value, 2),
              "missed_lots": len(missed),
              "problems": len(problems), "sample": problems[:8],
              "verdict": "PASS" if not problems else "FAIL"}
    return report, problems


def self_test(run_dir):
    initial = jload(DATA / "inventory_initial.json")
    base, _ = audit(run_dir, initial)
    if base["verdict"] != "PASS":
        print("EXPIRY_SELFTEST SKIP  真实数据本身不通过（%d 处），先修真实问题"
              % base["problems"])
        return 1
    if base["writeoff_events"] == 0:
        print("EXPIRY_SELFTEST SKIP  这个场景没有报废事件，无法证明门会失败")
        return 1

    # 注入 1：提前报废 —— 把状态里的报废量 +1（模拟"多扔了一件没到期的货"）
    rep1, _ = audit(run_dir, initial,
                    tamper=lambda u, v: (u + 1.0, v))
    # 注入 2：把一条报废事件的效期改到未来（提前报废，事件层面）
    rep2 = None
    events = read_events(run_dir)
    idx = next((i for i, e in enumerate(events) if e.get("type") == "STOCK_EXPIRED"), None)
    if idx is not None:
        bad = json.loads(json.dumps(events))
        bad[idx]["expiry_t"] = float(bad[idx]["t_s"]) + 3600.0
        # 写到临时目录，**不要写进 runs/**：那里是基准取"最近一次运行"的地方，
        # 自检产物混进去会让别的门读到伪造数据（P6a 已经踩过一次，见 ADR-GAME-09）。
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="expiry_selftest_"))
        tmp.mkdir(exist_ok=True)
        (tmp / "sim_events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in bad) + "\n", encoding="utf-8")
        (tmp / "sim_state.json").write_text(
            (Path(run_dir) / "sim_state.json").read_text(encoding="utf-8"), encoding="utf-8")
        rep2, _ = audit(tmp, initial)

    ok1 = rep1["verdict"] == "FAIL"
    ok2 = rep2 is not None and rep2["verdict"] == "FAIL"
    if ok1 and ok2:
        print("EXPIRY_SELFTEST OK  账面不一致与提前报废都被抓到（真实 %d 条报废、%s 件）"
              % (base["writeoff_events"], base["expired_units"]))
        return 0
    print("EXPIRY_SELFTEST FAIL  注入未被抓住（账面 %s / 提前报废 %s）"
          % ("ok" if ok1 else "漏", "ok" if ok2 else "漏"))
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="门 6 · 效期报废可证伪")
    ap.add_argument("--scenario", default="baseline")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    run_dir = Path(a.run_dir) if a.run_dir else latest_run(a.scenario)
    if a.self_test:
        return self_test(run_dir)

    report, _ = audit(run_dir, jload(DATA / "inventory_initial.json"))
    report["scenario"] = a.scenario
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "expiry_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not a.quiet:
        print("EXPIRY_%s %s 效期=%s 报废 %d 批 / %s 件 / ¥%s · 漏报 %d · 问题 %d"
              % (report["verdict"], run_dir.name,
                 "on" if report["expiry_enabled"] else "off",
                 report["writeoff_events"], report["expired_units"],
                 report["expired_value"], report["missed_lots"], report["problems"]))
        for p in report["sample"]:
            print("  - [%s] %s" % (p["kind"], p["detail"]))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
