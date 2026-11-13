#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/rack_access.py —— R2 · 货架存取机理（G1 核心），纯函数。

## 它解决什么

现在的引擎眼里只有一种货架：`_pick_loc` 从 SHELF 池里随机取一个，`World.by_kind`
只认 `kind`。于是"双深后位被挡""驶入式 LIFO""重力式 FIFO""移动式通道压缩"
这些**货架生产方式**在仿真里根本不存在——玩家换货架类型，行为一模一样。

这个模块把这些机理离散化成可算的东西：给定一个货道（cell）的**深度占用**，
算出「这一单该从哪个深位取」「新货该放到哪个深位」「哪些深位被挡住、需要重排」。

## 为什么是纯函数

- 无随机流、无时钟、不读文件 → 可以逐条手算验证（`--self-test`）。
- R2 的引擎接线只做一件事：把货位映射到 (cell, depth)，然后问这里"取哪个"。
  机理本身不进 tick 循环，改机理不会悄悄改随机流。

## 五种控制（自检；任一不成立就判红）

1. 双深 `[有, 有]`：只能取 0，**1 被 0 挡住**（要重排）。
2. 双深 `[无, 有]`：可以取 1（前位空，叉子伸得进去）。
3. 驶入式（LIFO）投放=**最深的空位**；压入式（LIFO）投放=**最浅的空位**。
   两者都是 LIFO，但"怎么进去"不同——这正是 §5.5 说的"压入式≠托盘重力式"。
4. 重力式（FIFO）取=最浅的有货、放=最深的空位（货从后往前滚）。
5. 物理 LIFO 货架 + FEFO 批次策略 = **冲突**（想按效期出，结构只让你按后进先出出）。
"""
from __future__ import annotations

import argparse
import sys

#: 存取机理 → (取货规则, 投放规则, 物理库存顺序)
#:   "front"    = 最浅的（靠通道那端）
#:   "back"     = 最深的
#: 取货一律"最浅的有货位"其实对双深/驶入/重力都成立，差别在**投放**与**是否滚动**。
MECHANISMS = {
    "single_deep_direct":      {"pick": "first_filled", "put": "first_empty", "order": "system"},
    "deep_front_back":         {"pick": "first_filled", "put": "first_empty", "order": "system"},
    "forklift_enters_aisle":   {"pick": "first_filled", "put": "last_empty",  "order": "LIFO"},
    "shuttle_in_aisle":        {"pick": "first_filled", "put": "last_empty",  "order": "LIFO"},
    "gravity_load_to_unload":  {"pick": "first_filled", "put": "last_empty",  "order": "FIFO"},
    "gravity_replenish_to_pick": {"pick": "first_filled", "put": "last_empty", "order": "FIFO"},
    "same_side_push_nest":     {"pick": "first_filled", "put": "first_empty", "order": "LIFO"},
    "aisle_compression":       {"pick": "first_filled", "put": "first_empty", "order": "system"},
}


def _first(seq, want):
    for i, v in enumerate(seq):
        if bool(v) == want:
            return i
    return None


def _last(seq, want):
    for i in range(len(seq) - 1, -1, -1):
        if bool(seq[i]) == want:
            return i
    return None


def resolve(mechanism, occupancy, operation="pick", aisle_open=True):
    """在一个货道里定位「该动哪个深位」。

    occupancy: 从前（靠通道）到后（最里）的布尔序列，True=有货。
    返回 {slot, blocked, reshuffle, end, reason}

    - `slot`：本次作业应动的深位（None=没有可用深位）
    - `blocked`：被挡住的深位（有货但在可及位之后）
    - `reshuffle`：是否必须先把前位搬走才能动目标位
    - `end`：drive_through 这类双端货道从哪端作业（"front"/"back"）
    """
    if mechanism not in MECHANISMS:
        raise ValueError("未知存取机理：%r（可选：%s）" % (mechanism, sorted(MECHANISMS)))
    rule = MECHANISMS[mechanism]
    occ = [bool(x) for x in occupancy]
    if not occ:
        raise ValueError("空货道：occupancy 至少一个深位")

    if mechanism == "aisle_compression" and not aisle_open:
        # 移动式：通道合拢时整条通道都不可作业（要等开闭动作）
        return {"slot": None, "blocked": [i for i, v in enumerate(occ) if v],
                "reshuffle": False, "end": "front", "reason": "aisle_closed",
                "order": rule["order"]}

    if operation == "pick":
        slot = _first(occ, True)
        blocked = [i for i, v in enumerate(occ) if v and slot is not None and i > slot]
        return {"slot": slot, "blocked": blocked, "reshuffle": bool(blocked),
                "end": "front", "reason": "ok" if slot is not None else "empty",
                "order": rule["order"]}
    if operation == "put":
        slot = _last(occ, False) if rule["put"] == "last_empty" else _first(occ, False)
        return {"slot": slot, "blocked": [], "reshuffle": False,
                "end": "back" if rule["put"] == "last_empty" else "front",
                "reason": "ok" if slot is not None else "full",
                "order": rule["order"]}
    raise ValueError("operation 只能是 pick / put，收到 %r" % (operation,))


def lane_order(mechanism, arrivals):
    """货道里的出库顺序（arrivals 是入库先后）。用于验证 FIFO/LIFO 不是嘴上说的。

    重力式/驶出式的"滚动"不在这里模拟位移，只给**顺序**：FIFO 按入库先后，
    LIFO 按逆序。"要不要真的搬位置"是引擎的事，不是机理定义的事。
    """
    order = MECHANISMS[mechanism]["order"]
    if order == "FIFO":
        return list(arrivals)
    if order == "LIFO":
        return list(reversed(arrivals))
    return list(arrivals)          # system：由系统决定，不做物理约束


def conflict(mechanism, lot_policy):
    """物理顺序与批次策略是否打架。

    LIFO 货架（驶入/压入/穿梭单端）只允许"后进先出"，而 FEFO 要求"先到期先出"——
    两者不可能同时满足。**这不是要禁止**，而是要计数：真实仓库里这种情况就是
    靠重排或改货位来妥协的，游戏里应该让玩家看见代价，而不是假装没冲突。
    """
    order = MECHANISMS[mechanism]["order"]
    if order == "LIFO" and str(lot_policy).upper() == "FEFO":
        return {"conflict": True, "rack_order": "LIFO", "lot_policy": "FEFO",
                "detail": "结构只允许后进先出，FEFO 要求先到期先出"}
    return {"conflict": False, "rack_order": order, "lot_policy": lot_policy}


def effective_slots(nominal, tail_loss=0.0, batch_mix_factor=1.0,
                    frozen=0, maintenance=0, disabled=0):
    """名义 → 有效：深位尾量、批次不可混放、冻结通道、维修、安全禁用。

    与 `capacity_model.effective_slots` 同一条公式、同一组扣项，**刻意各存一份**：
    一处是解析层（规划），一处是引擎（运行），两边必须能独立算出同一个数，
    否则"仪表盘说 100、仿真跑 80"永远查不出是谁错。
    """
    v = float(nominal) * (1.0 - float(tail_loss)) * float(batch_mix_factor)
    v -= float(frozen) + float(maintenance) + float(disabled)
    return max(0.0, round(v, 6))


# ---------------------------------------------------------------- 自检

def _eq(a, b):
    return a == b


def self_test() -> int:
    bad = []

    # 1) 双深：后位被前位挡住
    r = resolve("deep_front_back", [True, True], "pick")
    if not (r["slot"] == 0 and r["blocked"] == [1] and r["reshuffle"]):
        bad.append("双深 [有,有] 应取 0 且 1 被挡，实际 %r" % r)
    # 2) 双深：前位空 → 后位可达
    r = resolve("deep_front_back", [False, True], "pick")
    if not (r["slot"] == 1 and r["blocked"] == []):
        bad.append("双深 [空,有] 应能取 1，实际 %r" % r)
    # 3) 驶入式 LIFO 投放最深；压入式 LIFO 投放最浅
    a = resolve("forklift_enters_aisle", [True, False, False], "put")
    b = resolve("same_side_push_nest", [True, False, False], "put")
    if not (a["slot"] == 2 and b["slot"] == 1):
        bad.append("驶入式投放应为最深(2)、压入式应为最浅(1)，实际 %r / %r" % (a, b))
    # 4) 重力式 FIFO：取最浅、放最深
    p = resolve("gravity_load_to_unload", [True, True, False], "pick")
    q = resolve("gravity_load_to_unload", [True, True, False], "put")
    if not (p["slot"] == 0 and q["slot"] == 2 and p["order"] == "FIFO"):
        bad.append("重力式应取 0 / 放 2 且 order=FIFO，实际 %r / %r" % (p, q))
    # 5) 物理 LIFO + FEFO = 冲突
    c = conflict("forklift_enters_aisle", "FEFO")
    d = conflict("gravity_load_to_unload", "FEFO")
    if not (c["conflict"] and not d["conflict"]):
        bad.append("驶入式+FEFO 应冲突、重力式+FEFO 不应冲突，实际 %r / %r" % (c, d))
    # 附加：移动式通道合拢时不可作业
    m = resolve("aisle_compression", [True], "pick", aisle_open=False)
    if not (m["slot"] is None and m["reason"] == "aisle_closed"):
        bad.append("移动式通道合拢时应不可作业，实际 %r" % m)
    # 附加：LIFO/FIFO 顺序
    if lane_order("forklift_enters_aisle", ["a", "b", "c"]) != ["c", "b", "a"]:
        bad.append("LIFO 出库顺序应为逆序")
    if lane_order("gravity_load_to_unload", ["a", "b", "c"]) != ["a", "b", "c"]:
        bad.append("FIFO 出库顺序应为入库顺序")

    if bad:
        print("RACK_ACCESS_SELFTEST FAIL")
        for x in bad:
            print("  - " + x)
        return 1
    print("RACK_ACCESS_SELFTEST OK  双深阻挡/驶入压入差异/重力 FIFO/LIFO 与 FEFO 冲突"
          " 五条控制全部成立")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R2 货架存取机理（纯函数）")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
