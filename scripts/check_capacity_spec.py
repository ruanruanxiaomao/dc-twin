#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_capacity_spec.py —— R0 门：新 spec 的 schema 与口径自洽。

R0 是「只加护栏、不改行为」的一阶段，所以这道门要能独立回答三个问题：

  1. **R0_SCHEMA_OK**：四份新 spec 都能过自己的 schema（含 `level` 枚举）。
  2. **R0_BUGFIX_NEG_OK**：资料自带 5 处 bug 的修正版与资料原式**给出不同结果**
     （调用 `capacity_model.self_test()`；两版一致就判红）。
  3. **R0_SEMANTIC_OK**：配置之间不自相矛盾——驶入式必须 LIFO、压入式与托盘重力式
     必须分开、双深必须 depth=2 且后位阻挡、货架允许的设备必须在设备类型库里、
     `time_model` 的 B1/B2/B3 开关必须是修正值。

**不写任何产物**（R0 不接主链，不产生 out/*.json），所以它不会惊动 manifest 与
报告契约。接主链、出 capacity_report 是 R4 的事。

用法：
    python scripts/check_capacity_spec.py
    python scripts/check_capacity_spec.py --quiet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG, SCHEMAS = ROOT / "config", ROOT / "config" / "schemas"
SPECS = ("unit_load_spec", "rack_spec", "equipment_spec", "capacity_spec")


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def check_schemas():
    import jsonschema
    problems = []
    for kind in SPECS:
        inst_p = CONFIG / ("%s.json" % kind)
        sch_p = SCHEMAS / ("%s.schema.json" % kind)
        if not inst_p.exists():
            problems.append("%s 缺失" % inst_p.name)
            continue
        if not sch_p.exists():
            problems.append("%s 缺失" % sch_p.name)
            continue
        try:
            jsonschema.validate(jload(inst_p), jload(sch_p))
        except Exception as exc:                  # noqa: BLE001
            problems.append("%s 未通过 schema：%s" % (kind, str(exc).splitlines()[0]))
    return problems


def check_semantics():
    problems = []
    rack = jload(CONFIG / "rack_spec.json")["types"]
    equip = jload(CONFIG / "equipment_spec.json")
    equip_types = set(equip["types"])

    # 结构决定顺序：这两条反了，R2 的 FIFO/LIFO 强制就无从谈起
    if rack["drive_in"]["inventory_order"] != "LIFO" or not rack["drive_in"].get("single_end"):
        problems.append("drive_in 必须是单端 LIFO")
    if rack["drive_through"]["inventory_order"] != "FIFO" or not rack["drive_through"].get("double_end"):
        problems.append("drive_through 必须是双端 FIFO")
    if rack["push_back"]["inventory_order"] != "LIFO":
        problems.append("push_back（压入式）必须是 LIFO，不能和托盘重力式混为一谈")
    if rack["pallet_gravity"]["inventory_order"] != "FIFO":
        problems.append("pallet_gravity 必须是 FIFO")
    if rack["double_deep"]["n_depth"] != 2 or not rack["double_deep"].get("rear_blocked_by_front"):
        problems.append("double_deep 必须 n_depth=2 且 rear_blocked_by_front=true")

    # B5：n_depth 不许被"全部写死 1"
    if all(t["n_depth"] == 1 for t in rack.values()):
        problems.append("所有货架类型 n_depth 都是 1：B5 没生效，深位无从表达")

    allowed_levels = {"standard", "typical", "assumption", "placeholder", "gap"}
    for name, t in rack.items():
        if t.get("level") not in allowed_levels:
            problems.append("rack_spec.%s 缺 level" % name)
        if not t.get("source"):
            problems.append("rack_spec.%s 缺 source" % name)
        for e in t.get("equipment_allowed", []):
            if e not in equip_types and e not in {"manual_pick", "forklift_reach_doubledeep",
                                                  "telescopic_fork"}:
                problems.append("rack_spec.%s 引用了不存在的设备 %s" % (name, e))

    tm = equip["time_model"]
    if tm["load_rate_divisor"] != 2:
        problems.append("B1 被改回资料原式（load_rate_divisor=1，负荷高估一倍）")
    if tm.get("distance_factor_power", 1) != 1:
        problems.append("B2 被改回资料原式（distance_factor_power=2，叉车 d_f²）")
    if tm.get("height_check") != "all_types":
        problems.append("B3 被改回只查叉车（堆垛机高层不可达会漏报）")

    # D5：AST 耦合默认必须关闭。这条容易被"顺手打开看看效果"改掉，
    # 而它一开就会重算导航 grid、破金标准，所以要有一道门盯着。
    if equip.get("ast_drives_aisle_width") is not False:
        problems.append("AST 耦合默认被打开（ast_drives_aisle_width 应为 false，D5）")

    cap = jload(CONFIG / "capacity_spec.json")
    if cap["load_rate_divisor"] != 2 or cap["distance_factor_power"] != 1:
        problems.append("capacity_spec 的 B1/B2 开关不是修正值")
    if cap["cell_geometry"]["nDepth"] < 1:
        problems.append("cell_geometry.nDepth 非法")

    # 单元层：上架必须先验货架载（最严苛）
    ul = jload(CONFIG / "unit_load_spec.json")
    if ul["load_rating_order"][0] != "rack":
        problems.append("load_rating_order 第一位必须是 rack（货架载最严苛）")
    for name, p in ul["pallet_standards"].items():
        if p["rack_kg"] > p["dynamic_kg"]:
            problems.append("pallet_standards.%s 的货架载大于动载，通常反了" % name)
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R0 门：新 spec schema + 口径自洽")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    schema_bad = check_schemas()
    sem_bad = check_semantics()

    sys.path.insert(0, str(ROOT / "scripts"))
    import capacity_model as cm
    neg_rc = cm.self_test()

    if schema_bad:
        print("R0_SCHEMA_FAIL  %d 处" % len(schema_bad))
        for p in schema_bad:
            print("  - " + p)
    elif not a.quiet:
        print("R0_SCHEMA_OK  %d 份 spec 通过 schema" % len(SPECS))

    if sem_bad:
        print("R0_SEMANTIC_FAIL  %d 处" % len(sem_bad))
        for p in sem_bad:
            print("  - " + p)
    elif not a.quiet:
        print("R0_SEMANTIC_OK  货架/设备/单元/库容四份 spec 口径自洽")

    if neg_rc != 0:
        print("R0_BUGFIX_NEG_FAIL  五条负对照未通过（见上）")
    elif not a.quiet:
        print("R0_BUGFIX_NEG_OK  B1–B5 修正版与资料原式给出不同结果")

    ok = not schema_bad and not sem_bad and neg_rc == 0
    print("R0_%s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
