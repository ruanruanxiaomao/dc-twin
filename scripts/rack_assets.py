#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""程序化货架几何（R8.2）：按**存取机理**分型，纯数据、不 import bpy。

## 为什么是「纯数据」而不是直接建 bmesh

R8 的核心风险是「两张皮」：外观说双深、行为是横梁。如果几何只能在一个装了
Blender 的进程里建出来，那"双深和横梁长得不一样吗"就只能在渲染图里用眼睛比——
这正是 R4 那个「0 巷道却全绿」的同类错误（看着对，其实没算）。

所以这里只产**盒子列表**：

```python
{"mechanism": "double_deep", "levels": 4,
 "boxes": [((x0,y0,z0), (x1,y1,z1), slot), ...],
 "footprint": [1.0, 1.0]}
```

- 坐标是 `[0,1]^3` 归一化空间，与 `build_dc.unit_rack_mesh()` 同一口径；
  `build_dc` 拿到后按 `rk['size']` 缩放，几何本身与场景无关。
- `slot` 是材质槽：0 立柱、1 层梁、2 导轨/辊道/底座、3 车体/料箱/小车。
- `scripts/check_asset_dispatch.py` 直接 import 本模块算**几何指纹**，
  0.2 秒内能证明"8 类货架互不相同"，不需要渲染、不需要 Blender。

## 分型依据（方案书 §16.5 表）

| mechanism | 结构差异（相对 selective） | 视觉 cue |
|---|---|---|
| selective_pallet | 立柱 + 前后层梁 + 横撑，单深 | 托盘直接可见（build_dc 原有实现，本模块不重写） |
| double_deep | 三排立柱 + 沿进深的支撑轨，后位半隐 | 前后双托、托盘落在轨上而不是层梁上 |
| drive_in | 只有两端立柱、无每层横梁，两侧导轨贯通 | 叉车能开进去；单端封闭 |
| drive_through | 同 drive_in 但两端都开口 | 两端可入，无端部拉杆 |
| pallet_shuttle | 贯通导轨 + 入口交接位 + 车体在轨 | 深位由车搬，入口有穿梭车 |
| mobile | 整块可移动底座 + 车厢式层板 | 密集排列，只留一条活动通道 |
| carton_flow | 流利条微斜 + 周转箱尺度 | 补货端高、拣选端低 |
| pallet_gravity | 密排辊道 + 坡度 | 高端入、低端出，天然 FIFO |
| push_back | 嵌套小车微斜，同侧推入 | 同侧存取，LIFO |
"""
from __future__ import annotations

POST = 0.045          # 立柱截面（归一化）
BEAM = 0.02           # 层梁厚度

MAT_POST, MAT_BEAM, MAT_RAIL, MAT_CARRIER = 0, 1, 2, 3


def _box(lo, hi, slot):
    return (tuple(round(v, 4) for v in lo), tuple(round(v, 4) for v in hi), slot)


def _uprights(xs, ys, z0=0.0, z1=1.0, t=POST):
    out = []
    for x in xs:
        for y in ys:
            out.append(_box((x, y, z0), (x + t, y + t, z1), MAT_POST))
    return out


def _rail(x0, x1, y0, y1, z, h, slot=MAT_RAIL):
    return _box((x0, y0, z), (x1, y1, z + h), slot)


# ------------------------------------------------------------------ 各机理


def selective_pallet(levels=4):
    """横梁式（基线）：立柱 + 每层前后层梁 + 三根横撑。

    与 ``build_dc.unit_rack_mesh`` 同一套结构、同一套比例。这里保留一份是为了让
    指纹门能拿"基线长什么样"当参照物——**不是**让 dispatcher 走这条（dispatcher
    对 selective 仍调用 build_dc 原函数，保证默认产出逐字节不变）。
    """
    boxes = _uprights((0.0, 0.478, 0.955), (0.0, 0.94))
    for sy in (0.0, 0.955):
        boxes.append(_rail(0.0, 1.0, sy, sy + 0.03, 0.05, 0.03))
        boxes.append(_rail(0.0, 1.0, sy, sy + 0.03, 0.5, 0.03))
    for i in range(levels):
        z = (i + 1) / levels - 0.02
        for sy in (0.0, 0.94):
            boxes.append(_rail(0.0, 1.0, sy, sy + 0.06, z - 0.02, 0.02, MAT_BEAM))
        for sx in (0.03, 0.487, 0.94):
            boxes.append(_rail(sx, sx + 0.025, 0.0, 1.0, z - 0.015, 0.015, MAT_BEAM))
    return boxes


def rack_double_deep(levels=4):
    """双深位：**三排立柱**（前/中/后），托盘落在沿进深的支撑轨上而非层梁上。

    单深货架的托盘是"横着一根层梁撑住"；双深因为要伸手取后位，托盘必须落在
    两根贯通进深的支撑轨上，后位被前位挡住。这两点都必须看得出来，否则调用
    方永远分不清它买的是双深还是单深。
    """
    boxes = _uprights((0.0, 0.478, 0.955), (0.0, 0.478, 0.955))
    for z_edge in (0.05, 0.5):
        boxes.append(_rail(0.0, 1.0, 0.0, 0.02, z_edge, 0.03))
        boxes.append(_rail(0.0, 1.0, 0.955, 0.975, z_edge, 0.03))
    for i in range(levels):
        z = (i + 1) / levels - 0.02
        # 支撑轨：沿 y（进深）贯通，两组（x 方向左右各一道），托盘压在轨上
        for x in (0.12, 0.86):
            boxes.append(_rail(x, x + 0.025, 0.0, 1.0, z - 0.02, 0.02))
        boxes.append(_rail(0.0, 1.0, 0.0, 0.02, z - 0.02, 0.02, MAT_BEAM))
        boxes.append(_rail(0.0, 1.0, 0.95, 0.97, z - 0.02, 0.02, MAT_BEAM))
    return boxes


def rack_drive_in(levels=4, single_end=True):
    """驶入式：**每层没有横梁**，托盘骑在两侧贯通导轨上；叉车整车开进通道。

    `single_end=True` 时后端有拉杆封闭（单端进出、LIFO），否则两端贯通。
    """
    boxes = _uprights((0.0, 0.478, 0.955), (0.0, 0.955))
    # 两侧导轨：贯通整个进深，这是"车开进去"的关键读法
    for x in (0.05, 0.905):
        for i in range(levels):
            z = (i + 1) / levels - 0.03
            boxes.append(_rail(x, x + 0.04, 0.0, 1.0, z, 0.025))
    # 顶部拉杆（结构必须），以及单端封闭的端部拉杆
    boxes.append(_rail(0.0, 1.0, 0.0, 1.0, 0.975, 0.02, MAT_BEAM))
    if single_end:
        boxes.append(_rail(0.0, 1.0, 0.955, 0.975, 0.0, 0.06, MAT_BEAM))
    return boxes


def rack_drive_through(levels=4):
    """贯通式：与驶入式同构，但**两端都不封**——两端可入，先进先出。"""
    return rack_drive_in(levels, single_end=False)


def rack_shuttle(levels=4):
    """穿梭式：贯通导轨 + 入口交接位 + 一台车停在入口轨上。"""
    boxes = rack_drive_in(levels, single_end=True)
    # 车轨（比导轨更贴地的一对细轨）
    for x in (0.1, 0.86):
        boxes.append(_rail(x, x + 0.03, 0.0, 1.0, 0.0, 0.02))
    # 穿梭车车体：矮、方、停在入口第一深位
    boxes.append(_box((0.1, 0.06, 0.02), (0.86, 0.22, 0.14), MAT_CARRIER))
    # 车上的升降销（两枚），视觉上区分"车"和"托盘"
    for x in (0.28, 0.64):
        boxes.append(_box((x, 0.1, 0.14), (x + 0.06, 0.18, 0.19), MAT_RAIL))
    return boxes


def rack_mobile(levels=4):
    """移动式：整块**可移动底座**，车厢式层板密集排布，只留一条活动通道。"""
    boxes = [_box((0.0, 0.0, 0.0), (1.0, 1.0, 0.05), MAT_RAIL)]      # 底座
    boxes += _uprights((0.0, 0.478, 0.955), (0.0, 0.955), z0=0.05)
    for i in range(levels):
        z = (i + 1) / levels - 0.02
        # 车厢式层板：整块薄板，不像横梁式能看穿——这正是移动货架的读法
        boxes.append(_box((0.0, 0.0, z - 0.02), (1.0, 1.0, z), MAT_BEAM))
        boxes.append(_rail(0.0, 1.0, 0.0, 0.03, z - 0.06, 0.03))
    # 底座侧面的驱动轮
    for y in (0.2, 0.75):
        boxes.append(_box((0.0, y, 0.0), (0.04, y + 0.08, 0.05), MAT_CARRIER))
    return boxes


def rack_carton_flow(levels=4):
    """流利式（料箱）：**流利条微斜** + 补货端高、拣选端低。"""
    boxes = _uprights((0.0, 0.478, 0.955), (0.0, 0.955), t=0.03)
    span = 0.9
    for i in range(levels):
        z0 = (i + 0.55) / levels
        # 三条流利条，从 y=1（补货端，高）斜到 y=0（拣选端，低）
        for x in (0.08, 0.44, 0.80):
            for k in range(3):
                ya, yb = 1.0 - k * 0.32, 1.0 - (k + 1) * 0.32
                za, zb = z0 + 0.03 + k * 0.008, z0 + 0.03 + (k + 1) * 0.008
                boxes.append(_box((x, yb, min(za, zb)), (x + 0.12, ya, max(za, zb) + 0.012),
                                  MAT_RAIL))
        # 拣选端挡板
        boxes.append(_rail(0.0, 1.0, 0.0, 0.02, z0, 0.05, MAT_BEAM))
    del span
    return boxes


def rack_gravity(levels=4):
    """重力式（托盘）：**密排辊道 + 坡度**，高端入、低端出，天然 FIFO。"""
    boxes = _uprights((0.0, 0.478, 0.955), (0.0, 0.955))
    for i in range(levels):
        z0 = (i + 0.5) / levels
        # 9 根辊子/层，沿 y 递增高度形成 3° 视觉坡度
        for k in range(9):
            y = k / 9.0
            z = z0 + 0.02 + k * 0.006
            boxes.append(_box((0.05, y, z), (0.95, y + 0.055, z + 0.022), MAT_RAIL))
        # 两侧挡边
        for x in (0.03, 0.93):
            boxes.append(_box((x, 0.0, z0 + 0.02), (x + 0.02, 1.0, z0 + 0.10), MAT_BEAM))
    return boxes


def rack_push_back(levels=4):
    """压入式：**嵌套小车微斜**，同侧推入、后车被前车推着走（LIFO）。"""
    boxes = _uprights((0.0, 0.478, 0.955), (0.0, 0.955))
    for i in range(levels):
        z0 = (i + 0.5) / levels
        # 每层 3 台嵌套小车：越靠里越高，形成"压入"的读法
        for k in range(3):
            y0 = 0.04 + k * 0.30
            lift = k * 0.035
            boxes.append(_box((0.06, y0, z0 + 0.01 + lift),
                              (0.94, y0 + 0.26, z0 + 0.05 + lift), MAT_CARRIER))
            boxes.append(_box((0.06, y0 + 0.02, z0 + 0.05 + lift),
                              (0.94, y0 + 0.05, z0 + 0.10 + lift), MAT_BEAM))
    return boxes


MECHANISMS = {
    "selective_pallet": selective_pallet,
    "double_deep": rack_double_deep,
    "drive_in": rack_drive_in,
    "drive_through": rack_drive_through,
    "pallet_shuttle": rack_shuttle,
    "mobile": rack_mobile,
    "carton_flow": rack_carton_flow,
    "pallet_gravity": rack_gravity,
    "push_back": rack_push_back,
}

#: asset_spec 里 ``procedural:<name>`` 的名字 —— 与 asset_spec.source 一一对应。
#: ``unit_rack_mesh`` 这个"基线外观"仍住在 build_dc 里（默认产出必须复用它，
#: 才谈得上逐字节不变），所以这里登记成外部函数，不复制一份几何。
EXTERNAL_FUNCTIONS = {"unit_rack_mesh": "scripts/build_dc.py"}
BY_FUNCTION = {
    "unit_rack_mesh": "selective_pallet",
    "rack_double_deep": "double_deep",
    "rack_drive_in": "drive_in",
    "rack_drive_through": "drive_through",
    "rack_shuttle": "pallet_shuttle",
    "rack_mobile": "mobile",
    "rack_carton_flow": "carton_flow",
    "rack_gravity": "pallet_gravity",
    "rack_push_back": "push_back",
}
FUNCTION_OF = {v: k for k, v in BY_FUNCTION.items()}


def geometry(rack_type, levels=4):
    """返回 ``{"mechanism","levels","boxes","footprint"}``；未知机理抛 KeyError。"""
    fn = MECHANISMS[rack_type]
    boxes = fn(levels) if rack_type != "selective_pallet" else selective_pallet(levels)
    return {"mechanism": rack_type, "levels": levels, "boxes": boxes,
            "footprint": [1.0, 1.0]}


def signature_from_boxes(mechanism, boxes):
    """几何指纹的纯函数版本：给定盒子列表算摘要（门要注入假几何，所以拆出来）。"""
    dims = []
    for lo, hi, slot in boxes:
        d = tuple(round(hi[i] - lo[i], 3) for i in range(3))
        dims.append((d, slot))
    dims.sort()
    return {"mechanism": mechanism, "boxes": len(boxes), "dims": tuple(dims)}


def signature(rack_type, levels=4):
    """几何指纹：盒子数 + 每个盒子的 (尺寸, 材质槽) 排序后取摘要。

    用它比较"双深和横梁到底是不是同一个东西"——不依赖渲染、不依赖 Blender。
    尺寸四舍五入到 1e-3，避免浮点尾差把同一个模型判成两个。
    """
    g = geometry(rack_type, levels)
    return signature_from_boxes(rack_type, g["boxes"])


def fingerprint(rack_type, levels=4):
    """短哈希，便于在报告里放一行。"""
    s = signature(rack_type, levels)
    return hash_of(s)


def hash_of(sig):
    import hashlib
    import json as _json
    raw = _json.dumps({"n": sig["boxes"], "d": [[list(d), m] for d, m in sig["dims"]]},
                      sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------ 派发决策

class DispatchError(RuntimeError):
    """资产目录与货架声明对不上——宁可炸，也不许静默画成横梁式。"""


def resolve(rack, spec):
    """纯函数：给定一个货架声明 + asset_spec，回答「该用哪个程序化函数」。

    返回 ``{"mode": "legacy"}`` 或 ``{"mode": "procedural", "mechanism", "function"}``。

    这是 R8 唯一需要"想"的地方，所以它不碰 bpy、可被门直接调：

    * 没声明 ``rack_type``（默认布局就是这样）→ legacy，走 ``unit_rack_mesh``，
      产出与现状逐字节一致；
    * 声明了 → 必须能在 asset_spec 里查到，且 ``source`` 必须是
      ``procedural:<函数名>``、函数名必须反查到**同一个** mechanism。
      少一步就抛 ``DispatchError``——这就是 §16.11「缺→报错不静默回退」。
    """
    rt = (rack or {}).get("rack_type")
    if not rt:
        return {"mode": "legacy", "reason": "no_rack_type"}
    entry = (spec.get("rack_assets") or {}).get(rt)
    if entry is None:
        raise DispatchError("货架声明 rack_type=%r，但 asset_spec.rack_assets 里没有这一条" % rt)
    src = entry.get("source") or ""
    if not src.startswith("procedural:"):
        raise DispatchError("%s 的 source=%r 不是程序化函数；glb 母版不能表达机理差异" % (rt, src))
    fn = src.split(":", 1)[1].split(".")[-1]
    mech = BY_FUNCTION.get(fn)
    if mech is None:
        raise DispatchError("%s 的 source 指向未知函数 %s()" % (rt, fn))
    if mech != rt:
        raise DispatchError("%s 的 source(%s) 反查得到 %s——外观与行为不是同一个机理"
                            % (rt, fn, mech))
    return {"mode": "procedural", "mechanism": mech, "function": fn}


def force_selective(spec):
    """负对照用：把所有货架 source 强行改回 unit_rack_mesh（继承 station_slots_override 套路）。"""
    import copy
    out = copy.deepcopy(spec)
    for v in (out.get("rack_assets") or {}).values():
        v["source"] = "procedural:unit_rack_mesh"
    return out
