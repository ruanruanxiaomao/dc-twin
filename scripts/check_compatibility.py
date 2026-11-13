#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_compatibility.py —— R1 门 · UNITLOAD_OK / OVERLOAD_NEG / ID_READ_OK。

G3 要回答的是三个很具体的问题，不是"看起来合理"：

1. **单托总重 ≤ 三个载荷里最严的那个**（货架载 / 货位额定 / 设备载荷）。
   B4 说的就是"承载字段存而不用"，所以这里必须真的比较，不是把字段读出来打印。
2. **外廓（含异常突出）≤ 货格净尺寸**。注意是"实测最大值 + 异常突出"，不是名义
   托盘尺寸（货架参考:8）——两者差几十毫米，正好卡在能不能进格口上。
3. **标识首读率 ≥ 门槛**，且每个 SKU 都有识别方式与 SSCC。

## 可证伪

`--self-test` 造三个反例（超重 / 超外廓 / 首读率不达标），每一个都必须被判 FAIL。
三个都能"通过"的门等于没有门。

用法：
    python scripts/check_compatibility.py
    python scripts/check_compatibility.py --quiet
    python scripts/check_compatibility.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG, DATA, OUT = ROOT / "config", ROOT / "data", ROOT / "out"

#: 异常突出余量（mm）：托盘上货物超出托盘边的常见量。资料只给定性"要考虑异常突出"，
#: 数值取 50mm 作为保守假设（level: assumption），不是实测。
ENVELOPE_ALLOWANCE_MM = 50


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def load_specs():
    return (jload(DATA / "sku_master.json"),
            jload(CONFIG / "unit_load_spec.json"),
            jload(CONFIG / "rack_spec.json"),
            jload(CONFIG / "capacity_spec.json"),
            jload(CONFIG / "equipment_spec.json"))


def check(master, ul, rack, cap, equip, allowance=ENVELOPE_ALLOWANCE_MM):
    """逐 SKU 校验。返回 (报告, 问题列表)。纯函数，便于自检注入。"""
    geo = rack["defaults"]["geometry"]
    cell_mm = {"w": geo["clearW"] * 1000, "d": geo["clearD"] * 1000, "h": geo["clearH"] * 1000}
    load_cap = float(cap["cell_geometry"].get("loadCap_kg") or 0)
    read_min = float(ul["identification"]["gate_read_rate_min"])

    # 哪类设备能搬这种单元：只作为"有没有人能搬"的存在性检查，设备-巷道绑定是 R3
    payload_by_unit = {}
    for name, t in equip["types"].items():
        unit = "pallet" if t["class"] in ("forklift", "stacker", "shuttle") else (
            "tote" if t["class"] in ("agv_amr",) else None)
        if unit and t.get("payload_kg"):
            payload_by_unit[unit] = max(payload_by_unit.get(unit, 0.0), float(t["payload_kg"]))

    problems, rows = [], []
    for sku in master["skus"]:
        u = sku.get("unit_load")
        if not u:
            problems.append({"kind": "missing_unit_load", "sku": sku["id"],
                             "detail": "sku_master 里没有 unit_load"})
            continue
        # 1) 三载取最严
        limits = [(n, v) for n, v in (("rack_rating", u.get("rating_kg")),
                                      ("cell_loadcap", load_cap)) if v]
        by_unit = payload_by_unit.get(u["kind"])
        if by_unit:
            limits.append(("equipment_payload", by_unit))
        lim_name, lim = min(limits, key=lambda kv: kv[1])
        overload = float(u["gross_weight_kg"]) > float(lim) + 1e-9
        if overload:
            problems.append({"kind": "overload", "sku": sku["id"],
                             "detail": "毛重 %.2fkg > %s %.2fkg"
                                       % (u["gross_weight_kg"], lim_name, lim)})

        # 2) 外廓（含异常突出）。**允许 90° 旋转**：托盘进格口时长边走深度、
        # 短边走梁跨，两种朝向都合法；只按一种朝向判会把合法摆放判成超限
        # （第一版就是按 l→宽 判的，EUR 托盘全被判超限）。
        L = float(u["l_mm"]) + allowance
        W = float(u["w_mm"]) + allowance
        H = float(u["h_mm"]) + allowance
        fit_a = L <= cell_mm["w"] and W <= cell_mm["d"]
        fit_b = W <= cell_mm["w"] and L <= cell_mm["d"]
        over_dim = []
        if not (fit_a or fit_b):
            over_dim.append("%.0fx%.0f+%d 装不进 %.0fx%.0f"
                            % (float(u["l_mm"]), float(u["w_mm"]), allowance,
                               cell_mm["w"], cell_mm["d"]))
        if H > cell_mm["h"]:
            over_dim.append("h=%.0f+%d>%.0f" % (float(u["h_mm"]), allowance, cell_mm["h"]))
        if over_dim:
            problems.append({"kind": "envelope", "sku": sku["id"],
                             "detail": "；".join(over_dim)})

        # 3) 标识
        if not u.get("id_method"):
            problems.append({"kind": "no_id_method", "sku": sku["id"],
                             "detail": "没有识别方式"})
        if len(str(u.get("sscc") or "")) != int(ul["identification"]["sscc_digits"]):
            problems.append({"kind": "bad_sscc", "sku": sku["id"],
                             "detail": "SSCC 位数不是 %s" % ul["identification"]["sscc_digits"]})

        rows.append({"sku": sku["id"], "kind": u["kind"], "load": u["load"],
                     "units_per_load": u["units_per_load"],
                     "gross_kg": u["gross_weight_kg"], "limit_kg": lim,
                     "limit_by": lim_name,
                     "utilization": round(float(u["gross_weight_kg"]) / float(lim), 4)
                     if lim else None,
                     "dims_mm": [u["l_mm"], u["w_mm"], u["h_mm"]],
                     "id_method": u.get("id_method")})

    if read_min < 0.995:
        problems.append({"kind": "read_rate", "sku": "-",
                         "detail": "首读率门槛 %.4f 低于 0.995" % read_min})

    util = [r["utilization"] for r in rows if r["utilization"] is not None]
    report = {"schema_version": "1.0", "kind": "unitload_report",
              "verdict": "PASS" if not problems else "FAIL",
              "skus": len(rows), "problems": len(problems),
              "envelope_allowance_mm": allowance,
              "cell_mm": {"w": round(cell_mm["w"], 1), "d": round(cell_mm["d"], 1),
                          "h": round(cell_mm["h"], 1)},
              "read_rate_min": read_min,
              "utilization": {"max": max(util) if util else None,
                              "over_90pct": sum(1 for x in util if x > 0.9)},
              "sample_problems": problems[:8],
              "sample_rows": rows[:5]}
    return report, problems


def self_test(master, ul, rack, cap, equip) -> int:
    """三个反例，任何一个没被抓到都判红。"""
    base, _ = check(master, ul, rack, cap, equip)
    if base["verdict"] != "PASS":
        print("UNITLOAD_SELFTEST SKIP  真实数据本身不通过（%d 处），先修数据"
              % base["problems"])
        return 1

    import copy
    heavy = copy.deepcopy(master)
    heavy["skus"][0]["unit_load"]["gross_weight_kg"] = 1500.0
    rep_h, _ = check(heavy, ul, rack, cap, equip)

    big = copy.deepcopy(master)
    big["skus"][0]["unit_load"]["l_mm"] = 2000
    rep_b, _ = check(big, ul, rack, cap, equip)

    bad_id = copy.deepcopy(ul)
    bad_id["identification"]["gate_read_rate_min"] = 0.90
    rep_i, _ = check(master, bad_id, rack, cap, equip)

    ok = (rep_h["verdict"] == "FAIL" and rep_b["verdict"] == "FAIL"
          and rep_i["verdict"] == "FAIL")
    if ok:
        print("UNITLOAD_SELFTEST OK  超重 / 超外廓 / 首读率不达标 三类反例都被抓到"
              "（真实 %d 个 SKU 全部合规，最高载荷利用率 %.1f%%）"
              % (base["skus"], (base["utilization"]["max"] or 0) * 100))
        return 0
    print("UNITLOAD_SELFTEST FAIL  反例未被抓住：超重 %s / 超外廓 %s / 首读率 %s"
          % (rep_h["verdict"], rep_b["verdict"], rep_i["verdict"]))
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R1 门 · 集装单元承载/外廓/标识")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    master, ul, rack, cap, equip = load_specs()
    if a.self_test:
        return self_test(master, ul, rack, cap, equip)

    report, _ = check(master, ul, rack, cap, equip)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "unitload_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not a.quiet:
        print("UNITLOAD_%s  SKU %d · 问题 %d · 最高载荷利用率 %.1f%% · 外廓余量 %dmm"
              % (report["verdict"], report["skus"], report["problems"],
                 (report["utilization"]["max"] or 0) * 100,
                 report["envelope_allowance_mm"]))
        for p in report["sample_problems"]:
            print("  - [%s] %s %s" % (p["kind"], p["sku"], p["detail"]))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
