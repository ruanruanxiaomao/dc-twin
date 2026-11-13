#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R5 门 · 库存策略闭式对账（``POLICY_CLOSED_FORM_OK``）。

库存策略的错法不是"算不出来"，是**错了没人看得见**：仿真里表现为"缺货多了一点"，
和"需求涨了一点"分不开。所以这门不看仿真，只看**闭式解对几张手算表**：

| 量 | 手算值（外部查/推导，不复用被测实现） |
|---|---|
| EOQ(12500, 180, 2.16) | 1443.376 |
| z(0.95) / z(0.975) / z(0.99) | 1.6449 / 1.9600 / 2.3263 |
| 报童 fractile (c=8,p=18,v=2) | 0.625 |
| 报童 Q（normal μ=100 σ=25） | 107.966 |
| ROP(d=12.5/h, L=0.5h, cv=0.3, SL=0.95) | 10.612 |

四类反例（自检）：z 取 1.0、fractile 取 c/p、σ_L 用 σ·L、σ<0 不抛错。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import inventory_policy as ip      # noqa: E402

CONFIG, OUT = ROOT / "config", ROOT / "out"
TOL = 5e-4

#: 手算对照表。数值来自外部推导（不是调用被测函数算出来的），所以能抓实现错误。
EXPECT = {
    "eoq_12500_180_2.16": 1443.376,
    "z_0.95": 1.6449,
    "z_0.975": 1.9600,
    "z_0.99": 2.3263,
    "fractile_8_18_2": 0.625,
    "newsvendor_q": 107.966,
    "rop": 10.612,
    "gmroi_42_30": 1.4,
}


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def check_closed_form():
    problems, detail = [], {}
    got = {
        "eoq_12500_180_2.16": ip.eoq(12500, 180, 2.16),
        "z_0.95": ip.z_from_service_level(0.95),
        "z_0.975": ip.z_from_service_level(0.975),
        "z_0.99": ip.z_from_service_level(0.99),
        "fractile_8_18_2": ip.critical_fractile(8, 18, 2),
        "newsvendor_q": ip.newsvendor_quantity({"kind": "normal", "mu": 100, "sigma": 25},
                                              ip.critical_fractile(8, 18, 2)),
        "rop": ip.rop_from_policy(12.5, 0.5, 0.95, demand_cv=0.3)["rop"],
        "gmroi_42_30": ip.gmroi(42, 30),
    }
    for k, want in EXPECT.items():
        v = got[k]
        detail[k] = round(v, 6)
        if abs(v - want) > TOL:
            problems.append("%s 对不上手算值：%.6f vs %.6f" % (k, v, want))
    # 单调性（闭式解应满足，不满足说明公式抄错）
    if not (ip.z_from_service_level(0.90) < ip.z_from_service_level(0.95)
            < ip.z_from_service_level(0.99)):
        problems.append("z 对服务水平不单调")
    if not (ip.eoq(1000, 50, 4) < ip.eoq(2000, 50, 4)):
        problems.append("EOQ 对需求不单调")
    return not problems, detail, problems


def check_boundary():
    problems, detail = [], {}

    def raises(fn, *a, **k):
        try:
            fn(*a, **k)
        except ip.PolicyError:
            return True
        return False

    cases = {
        "eoq_zero_demand": ip.eoq(0, 180, 2.16) == 0.0,
        "eoq_zero_holding_raises": raises(ip.eoq, 12500, 180, 0.0),
        "eoq_negative_holding_raises": raises(ip.eoq, 12500, 180, -1.0),
        "ss_sigma_zero": ip.safety_stock(1.6449, 0.0) == 0.0,
        "ss_sigma_negative_raises": raises(ip.safety_stock, 1.6449, -1.0),
        "z_at_half_is_zero": abs(ip.z_from_service_level(0.5)) < 1e-9,
        "sl_zero_raises": raises(ip.z_from_service_level, 0.0),
        "sl_one_raises": raises(ip.z_from_service_level, 1.0),
        "fractile_zero_when_price_le_cost": ip.critical_fractile(20, 18, 2) == 0.0,
        "salvage_above_price_raises": raises(ip.critical_fractile, 8, 18, 20),
        "gmroi_zero_cost_raises": raises(ip.gmroi, 42, 0.0),
        "uniform_hi_below_lo_raises": raises(ip.newsvendor_quantity,
                                             {"kind": "uniform", "lo": 10, "hi": 5}, 0.5),
        "unknown_dist_raises": raises(ip.newsvendor_quantity, {"kind": "poisson"}, 0.5),
    }
    detail.update(cases)
    for k, ok in cases.items():
        if not ok:
            problems.append("边界用例未成立：%s" % k)
    return not problems, detail, problems


def check_spec(spec_path=None):
    """配置必须过 schema、且 plan 出来的数与本门手算一致；默认 enabled=false。"""
    problems, detail = [], {}
    sp = Path(spec_path or (CONFIG / "inventory_policy_spec.json"))
    sch = jload(CONFIG / "schemas" / "inventory_policy_spec.schema.json")
    doc = jload(sp)
    try:
        import jsonschema
        jsonschema.validate(doc, sch)
    except Exception as e:                                    # noqa: BLE001
        problems.append("spec 不过 schema：%s" % str(e)[:120])
    if doc.get("enabled") is not False:
        problems.append("默认必须 enabled=false（收到 %r）：改默认等于改金标准"
                        % (doc.get("enabled"),))
    plan = ip.plan_from_spec(doc)
    detail["plan"] = plan
    if abs(plan.get("eoq", 0) - EXPECT["eoq_12500_180_2.16"]) > TOL:
        problems.append("spec 的 EOQ 与手算不一致")
    if abs(plan.get("rop", {}).get("rop", 0) - EXPECT["rop"]) > TOL:
        problems.append("spec 的 ROP 与手算不一致")
    if abs(plan.get("newsvendor", {}).get("qty", 0) - EXPECT["newsvendor_q"]) > TOL:
        problems.append("spec 的报童 Q 与手算不一致")
    if abs(plan.get("gmroi", 0) - EXPECT["gmroi_42_30"]) > TOL:
        problems.append("spec 的 GMROI 与手算不一致")
    return not problems, detail, problems


def check_determinism():
    problems = []
    a = ip.plan_from_spec(jload(CONFIG / "inventory_policy_spec.json"))
    b = ip.plan_from_spec(jload(CONFIG / "inventory_policy_spec.json"))
    if a != b:
        problems.append("同输入两次结果不同：闭式层不允许有隐藏状态")
    return not problems, {"identical": a == b}, problems


def _wrong(impl, name):
    """自检用：把某条公式换成常见错法，闭式对账必须红。"""
    saved = getattr(ip, name)
    setattr(ip, name, impl)
    try:
        ok, _d, _p = check_closed_form()
    finally:
        setattr(ip, name, saved)
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description="R5 门：库存策略闭式对账")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        # 1) z 取 1.0（"95% 就用 1 倍标准差"是常见口误）
        ok_z = _wrong(lambda sl: 1.0, "z_from_service_level")
        # 2) fractile 取 c/p（把临界分位当成成本占比）
        ok_f = _wrong(lambda c, p, v: c / p if p else 0.0, "critical_fractile")
        # 3) σ_L 用 σ_h·L 而不是 σ_h·√L（高估 1/√L 倍）——换掉整个 ROP 实现
        def _rop_wrong_lead(demand_per_hour, lead_time_h, service_level,
                            sigma_per_hour=None, demand_cv=None, floor=1.0):
            lt = float(lead_time_h)
            mu_h = float(demand_per_hour)
            sigma_h = float(sigma_per_hour) if sigma_per_hour is not None else \
                (0.0 if demand_cv is None else float(demand_cv) * mu_h)
            z = 1.6448536269514722          # 固定 0.95，避免再依赖被测 z
            ss = z * sigma_h * lt           # 错在这里：应乘 sqrt(lt)
            return {"z": z, "mu_lead": mu_h * lt, "ss": ss,
                    "rop": max(float(floor), mu_h * lt + ss)}
        ok_ss = _wrong(_rop_wrong_lead, "rop_from_policy")
        # 4) σ<0 被吞成 0（边界检查必须红，不是闭式对账）
        saved_ss = ip.safety_stock
        ip.safety_stock = lambda z, s: z * max(0.0, s)
        try:
            ok_neg, _, _ = check_boundary()
        finally:
            ip.safety_stock = saved_ss
        good = all(not x for x in (ok_z, ok_f, ok_ss, ok_neg))
        print("POLICY_SELF_TEST %s" % ("PASS" if good else "FAIL"))
        print("  z_constant_one     %s" % ("ok" if not ok_z else "MISSED"))
        print("  fractile_cost_ratio %s" % ("ok" if not ok_f else "MISSED"))
        print("  ss_sigma_times_lead %s" % ("ok" if not ok_ss else "MISSED"))
        print("  negative_sigma_swallowed %s" % ("ok" if not ok_neg else "MISSED"))
        return 0 if good else 1

    gates, failed = {}, []
    for name, fn in (("POLICY_CLOSED_FORM_OK", check_closed_form),
                     ("POLICY_BOUNDARY_OK", check_boundary),
                     ("POLICY_SPEC_OK", check_spec),
                     ("POLICY_DETERMINISM_OK", check_determinism)):
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

    rep = {"schema_version": "1.0", "kind": "inventory_policy_report",
           "verdict": "POLICY_CLOSED_FORM_OK" if not failed else "POLICY_CLOSED_FORM_FAIL",
           "failed": failed, "gates": gates,
           "note": "闭式层只看公式对账（手算表）、定义域与确定性；"
                   "仿真接线默认关（enabled=false 且 reorder.policy=reorder_point），"
                   "本轮不声称 ROP+SS 已在仿真里生效。"}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "inventory_policy_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.quiet:
        print("%s %s" % (rep["verdict"], ",".join(failed) if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
