#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""车型 → 设备类型的取参通路（R3）。

**为什么要有这一层，而不是各处直接读 `fleet["models"][0]`**：

R3 之前，引擎只有一个车型，所有车共享同一份参数（电量、速度、能耗、充电功率、
最小间隔），于是"车队"实际上等于"台数"。要支持多车型，就必须有一个地方回答
"这台车该用哪份参数"，而且这个回答要**确定、可校验、可快照**。

三条纪律：

1. **车型必须能落到一个设备类型**。`fleet_spec.models[].equipment_type` 指向
   `equipment_spec.types` 里的键。落不到就报错——否则"设备库"只是文档。
2. **部署决定车型分配，按 deployment 行顺序展开**。同一仓混编多车型时，
   按槽位序号轮转，避免依赖 `dict` 顺序这类隐式行为。
3. **失配当场 raise**。未知车型、数值缺失都不许退回默认值——退回默认会让
   "买了一台 500kg 的车"变成"多了一台 250kg 的车"，而报表上看不出来。

纯函数：不读时钟、不碰随机流、不改全局状态。
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config"

#: 车型参数里必须有、且被引擎真正用到的字段。缺任一即 raise（不让 None 渗进去）。
REQUIRED_MODEL_FIELDS = ("id", "length", "width", "payload_kg", "speed_mps",
                         "battery_kwh", "consumption_kwh_per_m", "idle_kw",
                         "charge_kw", "safety_margin_m")


def load_equipment(path=None):
    p = Path(path) if path else CONFIG / "equipment_spec.json"
    return json.loads(p.read_text(encoding="utf-8"))


def model_index(fleet):
    """车型 id → 车型 dict，并在这里做一次结构校验。"""
    out = {}
    for m in fleet.get("models", []):
        mid = m.get("id")
        if not mid:
            raise ValueError("fleet.models 里有车型没有 id")
        if mid in out:
            raise ValueError("车型 id 重复：%r" % (mid,))
        missing = [k for k in REQUIRED_MODEL_FIELDS if k not in m]
        if missing:
            raise ValueError("车型 %s 缺字段：%s" % (mid, ", ".join(missing)))
        if float(m.get("payload_kg") or 0) <= 0:
            raise ValueError("车型 %s 的 payload_kg 非法" % mid)
        out[mid] = m
    if not out:
        raise ValueError("fleet.models 为空：没有车型就没法跑车")
    return out


def check_against_equipment(fleet, equip=None):
    """车型 ↔ 设备类型库的一致性。返回问题列表（空 = 通过）。"""
    equip = equip or load_equipment()
    types = equip.get("types") or {}
    problems = []
    for m in fleet.get("models", []):
        et = m.get("equipment_type")
        if not et:
            problems.append("车型 %s 未声明 equipment_type" % m.get("id"))
            continue
        t = types.get(et)
        if t is None:
            problems.append("车型 %s 指向不存在的设备类型 %r" % (m.get("id"), et))
            continue
        if float(m.get("payload_kg") or 0) > float(t.get("payload_kg") or 0) + 1e-9:
            problems.append("车型 %s 载荷 %s 超过设备 %s 额定 %s"
                            % (m.get("id"), m.get("payload_kg"), et, t.get("payload_kg")))
    return problems


def deployment_models(fleet):
    """warehouse → [车型 dict, ...]，按 deployment 行的 count 展开。

    同一行重复 count 次；多行按出现顺序拼接。**不排序**：deployment 的书写顺序
    就是玩家的意图（先买什么、后买什么），排序会让"同一份配置换个写法换一批车"。
    """
    idx = model_index(fleet)
    out = {}
    for dep in fleet.get("deployment", []):
        wid = dep.get("warehouse")
        mid = dep.get("model")
        if wid is None:
            raise ValueError("deployment 行缺 warehouse")
        if mid not in idx:
            raise ValueError("deployment 引用了不存在的车型 %r（仓 %s）" % (mid, wid))
        n = int(dep.get("count", 0))
        if n < 0:
            raise ValueError("deployment.count 不能为负（仓 %s）" % wid)
        out.setdefault(wid, []).extend([idx[mid]] * n)
    return out


def deployed_model_ids(fleet):
    """实际被部署的车型 id 集合（决定要不要在状态里逐车标车型）。"""
    return sorted({m["id"] for ms in deployment_models(fleet).values() for m in ms})


def effective_params(model, equip=None):
    """一台车的有效参数：车队参数 + 设备类型侧的额定（用于校验与报表）。"""
    equip = equip or load_equipment()
    et = (equip.get("types") or {}).get(model.get("equipment_type")) or {}
    return {
        "model_id": model["id"],
        "equipment_type": model.get("equipment_type"),
        "payload_kg": float(model["payload_kg"]),
        "equipment_payload_kg": float(et.get("payload_kg") or 0.0),
        "ast_mm": et.get("ast_mm"),
        "lift_mm": et.get("lift_mm"),
        "capex_cny": float(model.get("capex_cny") or 0.0),
        "min_sep_m": _min_sep(model),
    }


def _min_sep(model):
    import math
    return round(2 * (math.hypot(float(model["length"]), float(model["width"])) / 2
                     + float(model["safety_margin_m"])), 4)
