#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""程序化设备车体（R8.5）：穿梭车 / CTU / 码垛机械臂。

GLB 里这 3 类**没有母版**（附录 G 明确写"无 shuttle/CTU/机械臂专用母版"），所以只能
程序化建。和 `rack_assets.py` 一样：只产**盒子数据**（`[0,1]^3` 归一化 + 材质槽），
不 import bpy —— 这样"三个车体是否真的有区别"可以用指纹在 0.2 秒内验证，而不是靠
渲染图用眼睛比（R4 的 0 巷道就是这么栽的）。

尺寸口径：归一化立方体由调用方按真实尺寸缩放。这里的比例是"看起来对"的工程近似，
不是资料实测值——`asset_spec` 里这三类都带 `glb_gap` / `level: gap`，不许当实测引用。

| 车体 | 关键结构 | 视觉 cue |
|---|---|---|
| shuttle_car | 矮车体 + 升降销 + 四轮在轨 | 能在货架导轨里跑的小车 |
| ctu_robot | 立式货叉 + 顶部料箱位 + 底盘 | 比穿梭车高，能取料箱 |
| robot_arm | 底座 + 转台 + 两节臂 + 夹爪 | 码垛机械臂，站在包装区 |
"""
from __future__ import annotations

MAT_BODY, MAT_STEEL, MAT_ACCENT = 0, 1, 2


def _box(lo, hi, slot):
    return (tuple(round(v, 4) for v in lo), tuple(round(v, 4) for v in hi), slot)


def shuttle_car(levels=None):
    """穿梭车：矮、方、四轮，顶部两枚升降销。"""
    b = [_box((0.05, 0.08, 0.10), (0.95, 0.92, 0.30), MAT_BODY)]
    # 升降销（顶起托盘用的）
    for x in (0.24, 0.64):
        b.append(_box((x, 0.30, 0.30), (x + 0.12, 0.70, 0.38), MAT_ACCENT))
    # 四轮
    for x in (0.07, 0.85):
        for y in (0.08, 0.86):
            b.append(_box((x, y, 0.0), (x + 0.08, y + 0.06, 0.10), MAT_STEEL))
    # 两端防撞条
    for y in (0.06, 0.90):
        b.append(_box((0.05, y, 0.10), (0.95, y + 0.04, 0.16), MAT_ACCENT))
    return b


def ctu_robot(levels=None):
    """CTU（料箱到人）：底盘 + 升降立柱 + 顶部料箱托 + 单叉。"""
    b = [_box((0.10, 0.10, 0.0), (0.90, 0.90, 0.14), MAT_BODY)]
    b.append(_box((0.30, 0.30, 0.14), (0.70, 0.70, 0.86), MAT_STEEL))     # 立柱
    b.append(_box((0.18, 0.18, 0.86), (0.82, 0.82, 0.94), MAT_ACCENT))    # 托板
    b.append(_box((0.18, 0.18, 0.94), (0.82, 0.82, 1.0), MAT_BODY))       # 料箱
    b.append(_box((0.82, 0.34, 0.60), (1.0, 0.66, 0.66), MAT_STEEL))      # 出箱叉
    for x in (0.12, 0.80):
        for y in (0.12, 0.80):
            b.append(_box((x, y, 0.0), (x + 0.08, y + 0.08, 0.06), MAT_STEEL))
    return b


def robot_arm(levels=None):
    """码垛机械臂：底座 + 转台 + 大臂 + 小臂 + 夹爪。"""
    b = [_box((0.28, 0.28, 0.0), (0.72, 0.72, 0.08), MAT_STEEL)]          # 底座
    b.append(_box((0.34, 0.34, 0.08), (0.66, 0.66, 0.22), MAT_ACCENT))    # 转台
    b.append(_box((0.44, 0.44, 0.22), (0.56, 0.56, 0.62), MAT_BODY))      # 大臂
    b.append(_box((0.40, 0.46, 0.58), (0.60, 0.58, 0.70), MAT_STEEL))     # 肘
    b.append(_box((0.20, 0.46, 0.64), (0.44, 0.58, 0.76), MAT_BODY))      # 小臂（前伸）
    b.append(_box((0.10, 0.40, 0.60), (0.20, 0.64, 0.80), MAT_ACCENT))    # 夹爪
    return b


MECHANISMS = {
    "shuttle_car": shuttle_car,
    "ctu_robot": ctu_robot,
    "robot_arm": robot_arm,
}

#: asset_spec 里 procedural:<名字> 与本模块函数名的对应
BY_FUNCTION = {name: name for name in MECHANISMS}


def geometry(name, levels=None):
    return {"name": name, "boxes": MECHANISMS[name]()}


def signature_from_boxes(name, boxes):
    dims = []
    for lo, hi, slot in boxes:
        dims.append((tuple(round(hi[i] - lo[i], 3) for i in range(3)), slot))
    dims.sort()
    return {"name": name, "boxes": len(boxes), "dims": tuple(dims)}


def fingerprint(name):
    import hashlib
    import json as _json
    s = signature_from_boxes(name, geometry(name)["boxes"])
    raw = _json.dumps({"n": s["boxes"], "d": [[list(d), m] for d, m in s["dims"]]},
                      sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
