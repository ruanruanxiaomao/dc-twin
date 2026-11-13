# -*- coding: utf-8 -*-
"""实体登记册：一份现实，多处消费（R8.11）。

## 为什么要有它

R8.9 的洞：`areas` 不在 `spec_layout` 的碰撞集里，于是"把收货区拖进货架带"拿到
**0 issue 并写盘**。R8.10 的洞：宽度轴只看了东墙和兄弟组，**待命位不在上限里**，
厂房宽 +20 就炸出 6 条 `HOME-W-R07..R12 AP not walkable`。

这是同一个失败模式：**约束集是一份手写清单，每加一条轴就漏一类实体**。
更糟的是同一份现实被手写了两次——`build_grid` 的 solids 含输送线和室外装备，
`validate` 的 L2/L3 rects 不含；两份清单各活各的。

登记册不替校验器决定"该不该撞"（那是语义，必须显式写在 `guards` 里）。
它只保证**没有任何实体隐身**：一类实体若既没进碰撞、也没进导航、也没进任何一条轴的
约束，它就是一个孤儿，门会当场判红。

## 读法

`KINDS[kind]["guards"]` 就是"谁在管它"的清单。想知道"货架为什么会被宽度轴算进去"，
看这一行即可；想加一类新实体（穿梭车轨道、电梯、消防卷帘），先登记，门会告诉你
还差哪个消费者没接。
"""
from __future__ import annotations

#: 已知消费者。写成常量是为了让门能校验 `guards` 里没有笔误，
#: 也为了让"新增一个校验器"变成一次显式修改，而不是悄悄多一份清单。
GUARDS = {
    "layout.L2/L3": "spec_layout.validate：尺寸越界(L2) 与实体重叠(L3)",
    "layout.L4/L6": "spec_layout.validate：访问点落进实体(L4) / 不在可行走格(L6)",
    "layout.L7": "spec_layout.validate：待命位落进实体或间距不足",
    "nav.bake": "spec_layout.build_grid：膨胀后烘进导航网格",
    "areas.L23/L26": "spec_areas：功能区的边界与互斥",
    "depth_axis": "layout_solver.functional_band：进深预算的上限",
    "width_axis": "layout_solver.free_bays_for_group：跨数预算的上限",
    "area_param.equipment": "check_area_param._explain_residual：残留 L6 的因果归因",
}

#: 实体种类。`space` 决定几何形态（rect / 折线加宽 / 点），
#: `guards` 决定"谁消费它"——空元组会在门上变成孤儿。
KINDS = {
    "rack": {
        "space": "rect",
        "guards": ("layout.L2/L3", "layout.L4/L6", "nav.bake",
                   "depth_axis", "width_axis"),
        "note": "货架：碰撞、导航、两条轴的预算都直接吃它",
    },
    "dock": {
        "space": "rect",
        "guards": ("layout.L2/L3", "layout.L4/L6", "nav.bake"),
        "note": "月台锚南墙，不参与任何轴的平移",
    },
    "station": {
        "space": "rect",
        "guards": ("layout.L2/L3", "layout.L4/L6", "nav.bake", "depth_axis"),
        "note": "站（包装/分拣/质检）：深度轴功能带的成员",
    },
    "area": {
        "space": "rect",
        "guards": ("areas.L23/L26", "depth_axis"),
        "note": "功能区只存在于 data/areas.json，**不在** layout 的 warehouse 里——R8.9 的洞",
    },
    "charger": {
        "space": "rect",
        "guards": ("layout.L2/L3", "layout.L4/L6", "nav.bake", "depth_axis"),
        "note": "充电位盒 1.6×0.8，AP 落在 start 往场内 ap_dz 处",
    },
    "obstacle": {
        "space": "rect",
        "guards": ("layout.L2/L3", "nav.bake"),
        "note": "柱 / 墙垛；默认布局 4 根角柱",
    },
    "conveyor": {
        "space": "polyline",
        "guards": ("nav.bake",),
        "note": "输送线只按折线加宽烘进导航，**不进 L2/L3**——这是已知窄口，显式记账",
    },
    "spawn": {
        "space": "point",
        "guards": ("layout.L7", "width_axis"),
        "note": "待命位锚东墙；宽度轴把它当第三上限（R8.10）",
    },
    "outdoor_vehicle": {
        "space": "rect",
        "guards": ("nav.bake", "area_param.equipment"),
        "note": "室外叉车/卡车：来自 scene 配置，不在布局 spec 里",
    },
}

#: warehouse 的实体键 → kind。`unregistered_keys` 拿它做结构级防漏。
KEY_TO_KIND = {
    "racks": "rack",
    "docks": "dock",
    "stations": "station",
    "areas": "area",
    "charging_stations": "charger",
    "obstacles": "obstacle",
    "conveyors": "conveyor",
    "spawn": "spawn",
}

#: 非实体的结构键。列全是为了让"多出一个键"能被发现，而不是被静默忽略。
STRUCT_KEYS = {
    "id", "kind", "origin", "size", "grid", "zones", "locations",
    "area_reflow", "area_reflow_x", "area_solve", "area_solve_bays", "reflow",
    "ap_clamped", "fleet", "layout",
}

#: L2/L3 的对象集。**故意不含 conveyor**（见 KINDS 里的 note），
#: 也不含 outdoor_vehicle（那一层由导航与 area_param 归因管）。
#: 改动这个集合必须同步改 `check_solid_registry.py` 的断言，不许悄悄放宽。
L2L3_KINDS = ("rack", "dock", "station", "charger", "obstacle")

#: 烘进导航网格的对象集。
NAV_KINDS = ("rack", "dock", "station", "charger", "obstacle", "conveyor")


def rect_of_rack(rk):
    """与 `spec_layout.rect_of_rack` 同一口径。两份实现由门逐架比对，防漂移。"""
    x, _, z = rk["position"]
    rl, _, rd = rk["size"]
    return [x, z, x + rl, z + rd]


def charger_rect(ch):
    """充电位盒：与历史口径一致（±0.8 / ±0.4），不是新算的。"""
    x, _, z = ch["position"]
    return [x - 0.8, z - 0.4, x + 0.8, z + 0.4]


def conveyor_rects(cv):
    """折线按宽度切段；与 `build_grid` 的 +0.5 半宽一致（宽度不读 cv['width']）。"""
    out = []
    for a, b in zip(cv["path"], cv["path"][1:]):
        out.append([min(a[0], b[0]) - 0.5, min(a[1], b[1]) - 0.5,
                    max(a[0], b[0]) + 0.5, max(a[1], b[1]) + 0.5])
    return out


def layout_solids(w, kinds=None):
    """从**已展开**的 warehouse 取实体：返回 [(id, kind, rect)]。

    顺序（rack → dock → station → charger → obstacle → conveyor）与
    `spec_layout.build_grid` 的历史顺序一致——不是审美，是为了替换后
    导航网格逐字节不变。
    """
    on = set(KINDS if kinds is None else kinds)
    out = []
    if "rack" in on:
        out += [(rk["id"], "rack", rect_of_rack(rk)) for rk in w.get("racks") or []]
    if "dock" in on:
        out += [(d["id"], "dock", list(d["rect"])) for d in w.get("docks") or []]
    if "station" in on:
        out += [(s["id"], "station", list(s["rect"])) for s in w.get("stations") or []]
    if "charger" in on:
        out += [(ch["id"], "charger", charger_rect(ch))
                for ch in w.get("charging_stations") or []]
    if "obstacle" in on:
        out += [(o["id"], "obstacle", list(o["rect"])) for o in w.get("obstacles") or []]
    if "conveyor" in on:
        for cv in w.get("conveyors") or []:
            out += [(cv["id"], "conveyor", r) for r in conveyor_rects(cv)]
    return out


def area_solids(doc):
    """功能区（data/areas.json）。它们不在 layout 的 warehouse 里，所以单独取。"""
    out = []
    for a in (doc or {}).get("areas") or []:
        r = a.get("rect")
        if r:
            out.append((a.get("id"), "area", list(r)))
    return out


def outdoor_solids(scene):
    """室外叉车/卡车：来自 scene 配置，不在布局 spec 里。"""
    fp = (scene or {}).get("vehicle_footprint_m") or {}
    out = []
    for kind, key in (("forklift", "forklifts"), ("truck", "trucks")):
        fw, fd = fp.get(kind, [2.0, 1.0])
        for i, pt in enumerate((scene or {}).get(key) or [], 1):
            out.append(("%s_%02d" % (kind.upper(), i), "outdoor_vehicle",
                        [pt[0] - fw / 2, pt[1] - fd / 2, pt[0] + fw / 2, pt[1] + fd / 2]))
    return out


def spawn_points(wh):
    """待命位（spec 阶段，未展开）：[{'id','x','z'}]。

    宽度轴的 blockers 从这里取，而不是在 spec_layout 里就地列表推导——
    后者正是"每加一条轴手写一遍"的来源。
    """
    pts = ((wh.get("spawns") or {}).get("points") or [])
    return [{"id": "spawn_%02d" % (i + 1), "x": float(p[0]), "z": float(p[1])}
            for i, p in enumerate(pts)]


def entity_keys(w):
    """warehouse 里"装实体"的键 → 数量（只看非空）。"""
    out = {}
    for key, kind in KEY_TO_KIND.items():
        v = w.get(key)
        if isinstance(v, list) and v:
            out[key] = (kind, len(v))
        elif isinstance(v, dict) and v:
            n = len(v.get("robots") or v.get("points") or [])
            if n:
                out[key] = (kind, n)
    return out


def unregistered_keys(w):
    """出现却没人登记的实体键。空列表 = 册子覆盖了这份布局的全部实体。"""
    bad = []
    for key, v in (w or {}).items():
        if key in KEY_TO_KIND or key in STRUCT_KEYS:
            continue
        if isinstance(v, list) and v:
            bad.append((key, len(v)))
        elif isinstance(v, dict) and any(v.values()):
            bad.append((key, len(v)))
    return bad


def guards_matrix(kinds=None):
    """覆盖矩阵：{kind: [guard, ...]}，供门与报告直接输出。"""
    on = KINDS if kinds is None else {k: KINDS[k] for k in kinds}
    return {k: list(v["guards"]) for k, v in on.items()}


def orphan_kinds():
    """没有任何消费者的种类——登记了却没人管，等于隐身。"""
    return sorted(k for k, v in KINDS.items() if not v.get("guards"))


def unknown_guards():
    """`guards` 里写了不存在的消费者（笔误 / 改名没同步）。"""
    bad = sorted({g for v in KINDS.values() for g in v["guards"] if g not in GUARDS})
    return bad
