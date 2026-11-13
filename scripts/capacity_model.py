#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/capacity_model.py —— G4 解析库容能力层（R0：只算，不接主链）。

## 这是什么

纯解析、确定性、无随机流、无时钟的一层：输入「货格几何 + 巷道拼接 + 设备类型 +
需求」，输出「每巷道货位数/有效货位/能力/负荷/瓶颈/告警」与全局汇总。

它**不替代**离散事件仿真（那是 `sim_engine.py` 的事），两者是交叉验证关系：
解析层给「理论上限」，仿真给「跑出来多少」，差 >15% 就要查口径或交通瓶颈
（CROSS_VALIDATE_OK，R4 启用）。

## 为什么这个文件里有一堆 "bug_mode"

资料（`评价公式说明.html` + 布局工具）自带 5 处口径错误，方案书 §5.1 要求
**先修再用负测试钉住**。但如果只在代码里写「对的版本」，谁也证明不了错的那版
真的会更错。所以这里把两版公式都写出来，用 `FIX` / `LEGACY` 两组开关切换：

    B1 负荷率分母漏了 ×2      → load_rate(divisor=1)
    B2 叉车行走时间二次乘 d_f → distance(distance_power=2)
    B3 高度检查只查叉车       → height_warn(check_all=False)
    B4 承载字段存而不用       → overload_check(enabled=False)
    B5 nDepth 写死 1          → unit_capacity(unlock_depth=False)

`--self-test` 逐条跑「修正版 vs 资料原式」，要求两版结果**必须不同**，
且不同方向符合物理（原式更宽松或更悲观）。两版一样 = 这道负对照是假的。

用法：
    python scripts/capacity_model.py --self-test
    python scripts/capacity_model.py --json      # 打印默认 spec 的示例计算
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"

#: 修正版（本项目的口径）。任何一项改成 legacy 值，对应的负测试就必须能失败。
FIX = {
    "load_divisor": 2,        # B1：(in+out)/(2·C)，1 复合 = 1 存 + 1 取
    "distance_power": 1,      # B2：D̄ 已含 d_f，行走时间不再乘 d_f
    "height_check_all": True, # B3：所有设备类型都查可服务高度
    "load_capacity": True,    # B4：单托总重 > 货架载 要拒绝
    "unlock_depth": True,     # B5：nDepth 可 >1
}

#: 资料原式（负对照用）。**不要在产线路径里用**，只给 self-test 与 ADR 对照。
LEGACY = {
    "load_divisor": 1,
    "distance_power": 2,
    "height_check_all": False,
    "load_capacity": False,
    "unlock_depth": False,
}


def _sw(fix, key):
    return (fix or FIX)[key]


# ---------------------------------------------------------------- C.1 货格几何

def cell_geometry(g):
    """列节距 p / 深度占用 d / 层高 h_l。

    p = w + s_w + 2g ；d = d_c + s_d + 2g ；h_l = h_c + s_h
    间隙是**单侧** g，但左右各一个，所以是 2g（资料 C.1 如此，别漏）。
    """
    p = float(g["clearW"]) + float(g["structW"]) + 2.0 * float(g["gap"])
    d = float(g["clearD"]) + float(g["structD"]) + 2.0 * float(g["gap"])
    h_l = float(g["clearH"]) + float(g["structH"])
    return {"pitch": round(p, 6), "depth": round(d, 6), "level_height": round(h_l, 6)}


# ---------------------------------------------------------------- C.2 货位与单元

def unit_capacity(g, levels_left=4, levels_right=4, fix=None):
    """一个货架单元（背靠背左右两排）的货位数。

    C_cell = n_∥ × n_⊥ = nParallel × nDepth
    N      = L_lv·C_L + L_rv·C_R
    B5：nDepth 不再写死 1。写死会让双深/驶入式的"容量翻倍"根本无法表达。
    """
    depth = int(g.get("nDepth", 1))
    if not _sw(fix, "unlock_depth"):
        depth = 1                                   # 资料原式：input disabled max=1
    n_par = int(g.get("nParallel", 1))
    per_level = n_par * depth
    n = int(levels_left) * per_level + int(levels_right) * per_level
    return {"per_level_slots": per_level, "slots": n, "n_depth": depth}


def unit_pitch(g, levels_left=4, levels_right=4):
    """单元节距 P = max(p_L, p_R)。左右排有时不等宽（一边托盘一边料箱）。"""
    geo = cell_geometry(g)
    return {"pitch": round(max(geo["pitch"], geo["pitch"]), 6)}


def aisle_aggregate(units):
    """巷道聚合：L_a = Σ P_i ；S_a = Σ N_i。units 是 [{pitch, slots}, ...]。"""
    return {"length": round(sum(u["pitch"] for u in units), 6),
            "slots": int(sum(u["slots"] for u in units))}


# ---------------------------------------------------------------- 目标货位 / 有效货位

def target_slots(units, occupancy, target=0):
    """目标货位 N*：给了 targetSlots 就用；否则 ceil(U/η)。"""
    if target and float(target) > 0:
        return int(target)
    occ = float(occupancy)
    if occ <= 0:
        raise ValueError("occupancy 必须 > 0，否则目标货位无意义")
    return int(math.ceil(float(units) / occ))


def effective_slots(nominal, tail_loss=0.0, batch_mix_factor=1.0,
                    frozen=0, maintenance=0, disabled=0):
    """有效货位（B5 配套）：名义 ≠ 有效。

    effective = nominal·(1−tail_loss)·batch_mix_factor − frozen − maintenance − disabled
    **每一项单独可审计**。把它们折成一个拍出来的系数，是这类模型最常见的假精确。
    """
    v = float(nominal) * (1.0 - float(tail_loss)) * float(batch_mix_factor)
    v -= float(frozen) + float(maintenance) + float(disabled)
    return max(0.0, round(v, 6))


# ---------------------------------------------------------------- C.5 共用量

def avg_travel_distance(aisle_length, dist_factor=1.0, fix=None):
    """平均水平行走距离 D̄ = (L_a/2)·d_f。

    B2：**这里已经乘过 d_f**，所以行走时间 t=D̄/v 不能再乘一次 d_f
    （资料原式 t=(D̄/v)·d_f，实际成了 d_f²，且与堆垛机口径不一致）。
    """
    power = int(_sw(fix, "distance_power"))
    if power not in (1, 2):
        raise ValueError("distance_power 只能是 1（修正）或 2（资料原式）")
    d = (float(aisle_length) / 2.0) * (float(dist_factor) ** power)
    return round(d, 6)


def avg_height(level_counts, level_height):
    """平均作业层高 H̄ = Σ n_ℓ·(ℓ−0.5)·h_ℓ / Σ n_ℓ（层号从 1 起，按货位数加权）。"""
    num = den = 0.0
    for level, n in level_counts:
        num += float(n) * (float(level) - 0.5) * float(level_height)
        den += float(n)
    if den <= 0:
        raise ValueError("层货位数为 0，无法算平均作业层高")
    return round(num / den, 6)


# ---------------------------------------------------------------- C.6 设备时间模型

def stacker_cycles(D, H, v_h, v_v, t_p, t_f=1.0):
    """堆垛机：T_s = D/v + H/v_v + t_p·t_f；
    T_c = 0.6·(D/v + H/v_v) + 2·t_p·t_f（1 存 + 1 取，行走×0.6）。"""
    for name, val in (("v_h", v_h), ("v_v", v_v)):
        if float(val) <= 0:
            raise ValueError("%s 必须 > 0" % name)
    t_h = float(D) / float(v_h)
    t_v = float(H) / float(v_v)
    t_pk = float(t_p) * float(t_f)
    return {"single_s": round(t_h + t_v + t_pk, 6),
            "composite_s": round(0.6 * (t_h + t_v) + 2.0 * t_pk, 6)}


def forklift_cycles(D, v, t_p, t_f=1.0):
    """叉车：T_s = 2·(D/v) + t_p·t_f；T_c = 1.5·(D/v) + 2·t_p·t_f。

    B2：D 由 `avg_travel_distance` 给出（已含 d_f）。**不要再乘 d_f**。
    """
    if float(v) <= 0:
        raise ValueError("v 必须 > 0")
    t_ride = float(D) / float(v)
    t_pk = float(t_p) * float(t_f)
    return {"single_s": round(2.0 * t_ride + t_pk, 6),
            "composite_s": round(1.5 * t_ride + 2.0 * t_pk, 6)}


def capacity_per_hour(composite_s):
    """单机复合能力 Q_m = 3600 / T_c（复合次/h）。"""
    if float(composite_s) <= 0:
        raise ValueError("复合周期必须 > 0")
    return round(3600.0 / float(composite_s), 6)


def aisle_capacity(devices):
    """巷道总能力 C_a = Σ_e Q_m^(e)·n_e。devices = [{q_per_h, count}, ...]。

    `count` 允许小数：车队是按货位份额摊到巷道的，W 的 12 台摊到 16 条巷道
    就是每巷 0.75 台。用 int() 截断会让主仓能力变成 0，而总能力仍然非零。
    """
    return round(sum(float(d["q_per_h"]) * float(d["count"]) for d in devices), 6)


# ---------------------------------------------------------------- C.7 负荷

def load_rate(in_a, out_a, capacity, fix=None):
    """巷道负荷率 ρ_a = (in_a + out_a) / (divisor · C_a)。

    B1：**分母要 ×2**。C_a 是**复合作业**能力（1 复合 = 1 存 + 1 取）。
    资料把"单次指令数"直接除以"复合能力"，等于把负荷高估约一倍，
    于是同样的需求会被误判成瓶颈、逼玩家多买设备。

    语义约定（与方案书 B1 的例子有一处口径差，已记入 ADR-REFACTOR-04）：
    这里 C_a 的单位是「复合次/h」，所以 in=out=C_a 时恰好饱和（ρ=1.0），
    in=out=C_a/2 时 ρ=0.5。方案书写「in=out=C_a ⇒ ρ=0.5」，那要求 C_a 是
    「单次指令能力」；两种写法只差一个系数，但混用会让结论差 2 倍，故显式钉住。
    """
    cap = float(capacity)
    if cap <= 0:
        raise ValueError("能力为 0：这条巷道没有可服务的设备（应触发 no_equipment 告警）")
    div = int(_sw(fix, "load_divisor"))
    return round((float(in_a) + float(out_a)) / (div * cap), 6)


def global_load(in_total, out_total, capacity_total, fix=None):
    div = int(_sw(fix, "load_divisor"))
    if float(capacity_total) <= 0:
        raise ValueError("全局能力为 0")
    return round((float(in_total) + float(out_total)) / (div * float(capacity_total)), 6)


# ---------------------------------------------------------------- C.6 高度 / B4 承载

def height_warn(level_height, levels, max_height_m, kind="stacker", fix=None):
    """高层不可达告警。

    B3：资料只在**叉车**分支判 height，堆垛机漏检——而堆垛机恰恰是跑高层的那种。
    修正版对所有设备类型统一判：h_ℓ·level > maxHeight → 告警。
    """
    if not _sw(fix, "height_check_all") and kind != "forklift":
        return None                                  # 资料原式：非叉车不查
    if max_height_m in (None, 0):
        return None                                  # 没给能力高度就当没约束（不猜）
    top = float(level_height) * int(levels)
    if top > float(max_height_m) + 1e-9:
        return {"kind": "height_unreachable", "top_m": round(top, 3),
                "max_height_m": float(max_height_m), "device": kind}
    return None


def overload_check(gross_kg, rack_rating_kg, load_cap_kg=None, payload_kg=None, fix=None):
    """B4：承载字段存而不用 → 现在真的校验。

    判据取**最严**的那一个：货架载 / 货位额定 / 设备载荷（上架先验货架载，资料 F1:227）。
    """
    if not _sw(fix, "load_capacity"):
        return {"ok": True, "reason": None, "limit_kg": None}   # 资料原式：不校验
    limits = [(name, float(v)) for name, v in
              (("rack_rating", rack_rating_kg), ("load_cap", load_cap_kg),
               ("payload", payload_kg)) if v not in (None, 0)]
    if not limits:
        return {"ok": True, "reason": None, "limit_kg": None}
    name, limit = min(limits, key=lambda kv: kv[1])
    ok = float(gross_kg) <= limit + 1e-9
    return {"ok": ok, "reason": None if ok else "overload",
            "limit_kg": limit, "limit_by": name,
            "gross_kg": float(gross_kg)}


# ---------------------------------------------------------------- C.8 告警

def alerts(aisles, total_slots, needed_slots, spec, fix=None):
    """五类告警：容量不足 / 巷道超负荷 / 无设备 / 高层不可达 / 超载。"""
    cfg = spec.get("alerts") or {}
    out = []
    if cfg.get("capacity_insufficient", True) and total_slots < needed_slots:
        out.append({"kind": "capacity_insufficient", "slots": total_slots,
                    "needed": needed_slots,
                    "short": needed_slots - total_slots})
    for a in aisles:
        if cfg.get("aisle_overload", True) and a.get("load", 0) > 1.0 + 1e-9:
            out.append({"kind": "aisle_overload", "aisle": a.get("id"),
                        "load": a["load"],
                        "capacity": a.get("capacity")})
        if cfg.get("no_equipment", True) and not a.get("devices"):
            out.append({"kind": "no_equipment", "aisle": a.get("id")})
        if cfg.get("height_unreachable", True) and a.get("height_warn"):
            out.append(dict(a["height_warn"], aisle=a.get("id")))
        if cfg.get("overload", True) and a.get("overload_warn"):
            out.append(dict(a["overload_warn"], aisle=a.get("id")))
    return out


# ---------------------------------------------------------------- C.9 交叉验证

def cross_validate(throughput_sim, q_stable, tolerance_pct):
    """解析 vs 仿真：|sim − 解析| / 解析 ≤ 容差。超差要能判红（不夹逼、不取大）。"""
    if float(q_stable) <= 0:
        raise ValueError("解析稳定吞吐必须 > 0")
    dev = abs(float(throughput_sim) - float(q_stable)) / float(q_stable) * 100.0
    return {"ok": dev <= float(tolerance_pct) + 1e-9,
            "deviation_pct": round(dev, 3),
            "tolerance_pct": float(tolerance_pct)}


# ---------------------------------------------------------------- 汇总（R4 才接主链）

def aisles_from_layout(layout):
    """真实布局 → 巷道行（R4 修的第一处：原实现遍历 zones[].racks，而真实布局里
    货架挂在 warehouse.racks 上、zones 只有多边形，于是解析层算出 0 条巷道）。

    一"行"= 同仓同区同 z 的货架序列。巷道长 = 这一行首尾端点间距（含货架本体）。
    货位 = 架数 × 层数 × 2（spec_layout 给每个货架两侧各生成一排货位）。
    """
    out = []
    for wh in layout.get("warehouses", []):
        rows = {}
        for rk in wh.get("racks", []):
            key = (str(rk.get("zone") or "-"), round(float(rk["position"][2]), 3))
            rows.setdefault(key, []).append(rk)
        for key in sorted(rows, key=lambda k: (k[0], k[1])):
            rs = rows[key]
            xs = [float(x["position"][0]) for x in rs]
            width = float(rs[0]["size"][0])
            levels = int(rs[0].get("levels") or 1)
            out.append({
                "id": "%s/%s/z%.1f" % (wh.get("id"), key[0], key[1]),
                "warehouse": wh.get("id"), "zone": key[0],
                "length": round(max(xs) + width - min(xs), 3),
                "racks": len(rs), "levels": levels,
                "faces": 2,
                "slots": len(rs) * levels * 2,
            })
    return out


def device_cycle_s(length, etype, equipment_spec, capacity_spec):
    """一台设备在该巷道完成一单的周期（秒）。**与仿真同一作业策略**。

    段数来自 `travel_policy.legs`（引擎每单回本位，所以是 3 段），作业时间来自
    `handling`（取/放各一次站台作业 + 各一次停靠）。把这两项显式写进配置、而不是
    留在代码里调常数——否则"解析比仿真快四倍"会被当成公式精度问题，实际上是
    少算了整整两段行程和两次作业。
    """
    row = (equipment_spec.get("types") or {}).get(etype)
    if row is None:
        raise ValueError("未知设备类型：%r" % (etype,))
    v = float(row.get("speed_mps") or 0.0)
    if v <= 0:
        return None                      # 堆垛机/输送机不是按 m/s 走，另算
    h = capacity_spec["handling"]
    pol = capacity_spec["travel_policy"]
    d_bar = avg_travel_distance(float(length),
                                float(capacity_spec["demand"].get("distFactor", 1.0)))
    travel = int(pol["legs"]) * d_bar / v
    # 取一次、放一次：站台作业算两遍。只算一遍会让周期少 45 s（约 35%），
    # 而那个偏差会被误读成"解析模型比仿真乐观"。
    return round(travel + 2.0 * float(h["station_s"]) + 2.0 * float(h["dwell_s"]), 3)


def assign_devices(aisles, fleet, equipment_spec, capacity_spec):
    """把车队按**货位份额**摊到各巷道，返回 aisle_id -> [{type, count, q_per_h}]。

    为什么按份额而不是"每巷道都放整套车队"：后者会把总能力按巷道数放大一遍，
    负荷率永远小于 1，瓶颈永远不出现——那等于把解析层变成一句安慰话。
    """
    if not fleet:
        return {}
    models = {m.get("id"): m for m in fleet.get("models", [])}
    per_wh = {}
    for dep in fleet.get("deployment", []):
        m = models.get(dep.get("model"))
        if not m or not m.get("equipment_type"):
            continue
        et = m["equipment_type"]
        bucket = per_wh.setdefault(dep.get("warehouse"), {})
        bucket[et] = bucket.get(et, 0) + int(dep.get("count", 0))

    by_wh = {}
    for a in aisles:
        by_wh.setdefault(a["warehouse"], []).append(a)

    out = {}
    for wid, rows in by_wh.items():
        total = sum(r["slots"] for r in rows) or 1
        mix = per_wh.get(wid) or {}
        for a in rows:
            share = a["slots"] / float(total)
            devs = []
            for et in sorted(mix):
                cnt = mix[et] * share
                if cnt <= 1e-9:
                    continue
                t = device_cycle_s(a["length"], et, equipment_spec, capacity_spec)
                if not t:
                    continue
                devs.append({"type": et, "count": round(cnt, 6),
                             "cycle_s": t, "q_per_h": round(3600.0 / t, 3)})
            out[a["id"]] = devs
    return out


def build_report(layout, rack_spec, equipment_spec, capacity_spec, fleet=None):
    """按巷道算一遍。**纯函数**：不改 layout、不写文件、不碰时钟。

    R4：巷道改为从真实布局取（`aisles_from_layout`），设备改为从车队部署摊
    （`assign_devices`）。此前两者都是空的，于是每条巷道能力=0、负荷=0、
    瓶颈永不出现——报告看着"全绿"，其实什么都没算。
    """
    cap = capacity_spec
    g = cap["cell_geometry"]
    geo = cell_geometry(g)
    n_slots = unit_capacity(g, g.get("levels_left", 4), g.get("levels_right", 4))

    aisles = aisles_from_layout(layout)
    devices = assign_devices(aisles, fleet, equipment_spec, cap)

    total_slots_nominal = sum(a["slots"] for a in aisles)
    in_total, out_total = cap["demand"]["inPerHr"], cap["demand"]["outPerHr"]
    need = target_slots(cap["demand_plan"]["units"], cap["demand_plan"]["occupancy"],
                        cap["demand_plan"].get("targetSlots", 0))
    eff_rules = cap.get("effective_capacity") or {}
    avail = float(cap.get("availability_factor", 1.0))

    total_eff = 0.0
    for a in aisles:
        share = (a["slots"] / total_slots_nominal) if total_slots_nominal else 0.0
        a["task_in"] = round(in_total * share, 6)
        a["task_out"] = round(out_total * share, 6)
        a["effective_slots"] = effective_slots(
            a["slots"], eff_rules.get("tail_loss", 0.0),
            eff_rules.get("batch_mix_factor", 1.0),
            eff_rules.get("frozen", 0), eff_rules.get("maintenance", 0),
            eff_rules.get("disabled", 0))
        total_eff += a["effective_slots"]
        a["devices"] = devices.get(a["id"], [])
        # 可用率要乘进去：解析层的"理论能力"不能当"能用能力"报出去
        a["capacity"] = round(aisle_capacity(a["devices"]) * avail, 6)
        a["load"] = (round(load_rate(a["task_in"], a["task_out"], a["capacity"]), 6)
                     if a["capacity"] > 0 else 0.0)
        a["bottleneck"] = bool(a["load"] > 1.0)
        a["no_equipment"] = not a["devices"]

    cap_total = round(sum(a["capacity"] for a in aisles), 6)
    rep = {"schema_version": "1.0", "kind": "capacity_report",
           "cell": geo, "unit": n_slots,
           "totalSlots": total_slots_nominal,
           "totalEffective": round(total_eff, 6),
           "needed": need,
           "totalCapacity": cap_total,
           "availability_factor": avail,
           "handling": dict(cap["handling"]),
           "travel_policy": dict(cap["travel_policy"]),
           "totalLoad": (round(global_load(in_total, out_total, cap_total), 6)
                         if cap_total > 0 else 0.0),
           "aisles": aisles}
    rep["warnings"] = alerts(aisles, rep["totalEffective"], need, cap)
    return rep


# ---------------------------------------------------------------- 自检

def _eq(a, b, tol=1e-9):
    return abs(float(a) - float(b)) <= tol


def self_test() -> int:
    """五条负对照：修正版必须与资料原式不同，且方向符合物理。"""
    problems = []

    g = {"clearW": 1.1, "clearD": 1.1, "clearH": 1.3, "structW": 0.07,
         "structD": 0.05, "structH": 0.1, "gap": 0.1, "nParallel": 1, "nDepth": 2}

    # B1 负荷率：分母 ×2
    c, i, o = 100.0, 50.0, 50.0
    fixed = load_rate(i, o, c)
    legacy = load_rate(i, o, c, fix=LEGACY)
    if not (_eq(fixed, 0.5) and _eq(legacy, 1.0)):
        problems.append("B1 负荷率：修正 %.3f / 原式 %.3f（应 0.5 / 1.0）" % (fixed, legacy))

    # B2 距离：原式是平方
    d_fix = avg_travel_distance(100.0, 2.0)
    d_bug = avg_travel_distance(100.0, 2.0, fix=LEGACY)
    if not (_eq(d_fix, 100.0) and _eq(d_bug, 200.0)):
        problems.append("B2 距离：修正 %.3f / 原式 %.3f（应 100 / 200）" % (d_fix, d_bug))

    # B3 高度：堆垛机也要查
    w_fix = height_warn(1.5, 6, max_height_m=6.0, kind="stacker")
    w_bug = height_warn(1.5, 6, max_height_m=6.0, kind="stacker", fix=LEGACY)
    if not (w_fix and w_bug is None):
        problems.append("B3 高度：修正 %r / 原式 %r（修正应告警、原式漏检）" % (w_fix, w_bug))

    # B4 承载：1500kg 超过 1000kg 货架载要拒绝
    o_fix = overload_check(1500, rack_rating_kg=1000)
    o_bug = overload_check(1500, rack_rating_kg=1000, fix=LEGACY)
    if not (o_fix["ok"] is False and o_bug["ok"] is True):
        problems.append("B4 承载：修正 %r / 原式 %r（修正应拒绝）" % (o_fix["ok"], o_bug["ok"]))

    # B5 深度：nDepth=2 容量必须翻倍
    n_fix = unit_capacity(g)
    n_bug = unit_capacity(g, fix=LEGACY)
    if not (n_fix["slots"] == 16 and n_bug["slots"] == 8):
        problems.append("B5 深度：修正 %d / 原式 %d（应 16 / 8）"
                        % (n_fix["slots"], n_bug["slots"]))

    # C.1 几何：p=1.37 / d=1.35 / h=1.4
    geo = cell_geometry(g)
    if not (_eq(geo["pitch"], 1.37) and _eq(geo["depth"], 1.35)
            and _eq(geo["level_height"], 1.4)):
        problems.append("C.1 几何：%r" % geo)

    # C.9 交叉验证：20% 偏差在 15% 容差下必须判红
    cv_bad = cross_validate(120, 100, 15)
    cv_ok = cross_validate(108, 100, 15)
    if not (cv_bad["ok"] is False and cv_ok["ok"] is True):
        problems.append("C.9 交叉验证：%r / %r" % (cv_bad["ok"], cv_ok["ok"]))

    if problems:
        print("CAPACITY_SELFTEST FAIL")
        for p in problems:
            print("  - " + p)
        return 1
    print("CAPACITY_SELFTEST OK  B1/B2/B3/B4/B5 五条负对照与 C.1/C.9 正对照全部符合预期")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="G4 解析库容能力层（R0：不接主链）")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true", help="用默认 spec 算一遍示例并打印")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    if not a.json:
        ap.print_help()
        return 0

    layout = json.loads((ROOT / "data" / "layout.json").read_text(encoding="utf-8"))
    rack = json.loads((CONFIG / "rack_spec.json").read_text(encoding="utf-8"))
    equip = json.loads((CONFIG / "equipment_spec.json").read_text(encoding="utf-8"))
    cap = json.loads((CONFIG / "capacity_spec.json").read_text(encoding="utf-8"))
    rep = build_report(layout, rack, equip, cap)
    print(json.dumps({k: v for k, v in rep.items() if k != "aisles"},
                     ensure_ascii=False, indent=2))
    print("aisles=%d warnings=%d" % (len(rep["aisles"]), len(rep["warnings"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
