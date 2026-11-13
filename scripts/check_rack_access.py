#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_rack_access.py —— R2 门 · RACK_TAXONOMY_OK / EFFECTIVE_CAP_OK。

R2 的判据不是"能跑"，而是三件可证伪的事：

1. **机理规则成立**（调 `rack_access.self_test()`）：双深后位被挡、驶入与压入投放
   位置不同、重力式 FIFO、LIFO 货道与 FEFO 策略冲突、移动式通道合拢不可作业。
2. **默认布局一个货道都不许有**：`spec_layout` 只在声明 `rack_type` 时拆深位，
   默认没有 → 金标准字节不变（D4）。这条一旦破了，"默认关闭"就是空话。
3. **声明了就真的展开**：双深 → 每侧 2 个深位、cell 元数据齐全；
   驶入式 → 按显式 `lane_depth` 展开。展开不出来说明配置读不到或键名写错。

用法：
    python scripts/check_rack_access.py
    python scripts/check_rack_access.py --quiet
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG, DATA = ROOT / "config", ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import rack_access as ra      # noqa: E402
import spec_layout as sl      # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _locs(layout):
    return [l for w in layout["warehouses"] for l in w["locations"]]


def check_default_has_no_cells():
    layout = sl.expand(jload(CONFIG / "layout_spec.json"))
    with_cell = [l for l in _locs(layout) if "cell_id" in l]
    if with_cell:
        return ["默认布局出现了 %d 个带 cell_id 的库位：默认关闭被破了" % len(with_cell)]
    return []


def check_opt_in_expands(rack_type, expect_depth):
    spec = copy.deepcopy(jload(CONFIG / "layout_spec.json"))
    spec["warehouses"][0]["racks"][0]["rack"]["rack_type"] = rack_type
    if rack_type == "drive_in":
        spec["warehouses"][0]["racks"][0]["lane_depth"] = expect_depth
    layout = sl.expand(spec)
    cells = {}
    for l in _locs(layout):
        if "cell_id" in l:
            cells.setdefault(l["cell_id"], []).append(l)
    if not cells:
        return ["%s 声明了 rack_type 却没有展开任何货道" % rack_type]
    depth = max(len(v) for v in cells.values())
    if depth != expect_depth:
        return ["%s 展开深度 %d，期望 %d" % (rack_type, depth, expect_depth)]
    if rack_type == "drive_in" and depth != expect_depth:
        return ["驶入式未按 lane_depth 展开"]
    return []


def check_effective_capacity():
    problems = []
    nominal = 100.0
    eff = ra.effective_slots(nominal, tail_loss=0.05, batch_mix_factor=0.9,
                             frozen=2, maintenance=1, disabled=3)
    if not (eff < nominal):
        problems.append("有效货位 %s 不小于名义 %s：名义/有效没区分" % (eff, nominal))
    if ra.effective_slots(10, 0.5, 1.0, frozen=99) != 0.0:
        problems.append("扣到负数没有夹到 0")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R2 门 · 货架存取机理")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    problems = []
    problems += [("default_no_cells", p) for p in check_default_has_no_cells()]
    problems += [("double_deep", p) for p in check_opt_in_expands("double_deep", 2)]
    problems += [("drive_in", p) for p in check_opt_in_expands("drive_in", 5)]
    problems += [("effective_slot", p) for p in check_effective_capacity()]

    neg = ra.self_test()
    ok = not problems and neg == 0
    if problems:
        print("RACK_TAXONOMY_FAIL  %d 处" % len(problems))
        for kind, p in problems:
            print("  - [%s] %s" % (kind, p))
    elif not a.quiet:
        print("RACK_TAXONOMY_OK  默认布局无货道；双深/驶入式按声明展开；有效货位 < 名义")
    print("R2_%s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
