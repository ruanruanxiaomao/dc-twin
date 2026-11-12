#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/game_levels.py —— 关卡：场景 + 经营目标 + 胜负 + 星级 + 元进度（ADR-GAME-07）。

关键区分（方案书 §7）：
  - 场景里的 `assertions` 是**工程不变量**（零碰撞、事件覆盖），跑挂了说明引擎有问题；
  - 关卡里的 `objectives` 是**经营目标**（利润、准时率、单量），不达标是"玩得不好"。
  两者分开评估，不混算——混算会让"玩家经营失败"看起来像"引擎出错"。

星级写成可判定的规则数组（`[{"stars":1,"require":[...]}]`），而不是方案书 §2.4.3 里
那种自然语言字符串：字符串没法判分。这是对原 schema 的有意收紧，已在 ADR 记录。
"""
from __future__ import annotations

import json
from pathlib import Path

from dc_root import project_root  # noqa: E402
ROOT = project_root(__file__)
LEVELS = ROOT / "config" / "levels"
SCHEMAS = ROOT / "config" / "schemas"
SAVES = ROOT / "saves"
META = SAVES / "meta.json"

OPS = ("==", "!=", ">=", "<=", ">", "<")
#: 关卡可直接引用的经济指标（其余一律取自 sim_kpi.json 的 tick 层指标）
ECON_METRICS = ("cash", "operating_profit", "profit", "revenue", "variable_cost",
                "fixed_cost", "capex")


class LevelError(Exception):
    pass


def _jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def level_path(level_id):
    if not isinstance(level_id, str) or "/" in level_id or "\\" in level_id or ".." in level_id:
        raise LevelError("关卡 id 非法：%r" % (level_id,))
    return LEVELS / ("%s.json" % level_id)


def validate(level):
    """关卡配置校验。返回 issue 列表（空 = 通过）。"""
    issues = []
    if level.get("schema_version") != "1.0":
        issues.append("schema_version 必须是 '1.0'")
    lid = level.get("id")
    if not isinstance(lid, str) or not lid:
        issues.append("id 必填")
    if not isinstance(level.get("name"), str):
        issues.append("name 必填")
    if not isinstance(level.get("scenario"), str):
        issues.append("scenario 必填（相对 config/scenarios 的路径）")
    cap = level.get("initial_capital")
    if not isinstance(cap, (int, float)) or cap <= 0:
        issues.append("initial_capital 必须是正数")
    objs = level.get("objectives")
    if not isinstance(objs, list) or not objs:
        issues.append("objectives 至少一条")
    else:
        for i, o in enumerate(objs):
            if not isinstance(o, dict):
                issues.append("objectives[%d] 必须是对象" % i)
                continue
            if o.get("op") not in OPS:
                issues.append("objectives[%d].op 非法" % i)
            if not isinstance(o.get("metric"), str):
                issues.append("objectives[%d].metric 必填" % i)
            if not isinstance(o.get("value"), (int, float)):
                issues.append("objectives[%d].value 必须是数字" % i)
        if not any(o.get("primary") for o in objs if isinstance(o, dict)):
            issues.append("至少要有一条 primary 目标，否则赢了也没法定")
    stars = level.get("stars")
    if not isinstance(stars, list) or not stars:
        issues.append("stars 必须是规则数组")
    else:
        for i, s in enumerate(stars):
            if not isinstance(s, dict) or not isinstance(s.get("stars"), int):
                issues.append("stars[%d] 缺 stars" % i)
                continue
            if not isinstance(s.get("require"), list):
                issues.append("stars[%d].require 必须是数组" % i)
    unl = level.get("unlocks_on_win", [])
    if not isinstance(unl, list):
        issues.append("unlocks_on_win 必须是数组")
    return issues


def load_level(level_id):
    p = level_path(level_id)
    if not p.exists():
        raise LevelError("没有这个关卡：%s" % level_id)
    level = _jload(p)
    issues = validate(level)
    if issues:
        raise LevelError("关卡 %s 配置非法：%s" % (level_id, "; ".join(issues)))
    level["scenario_id"] = Path(level["scenario"]).stem
    return level


def list_levels():
    out = []
    if LEVELS.is_dir():
        for p in sorted(LEVELS.glob("*.json")):
            try:
                lv = load_level(p.stem)
            except LevelError as exc:
                out.append({"id": p.stem, "ok": False, "error": str(exc)})
                continue
            out.append({"id": lv["id"], "name": lv["name"],
                        "scenario_id": lv["scenario_id"],
                        "initial_capital": lv["initial_capital"],
                        "objectives": lv["objectives"],
                        "unlocks_on_win": lv.get("unlocks_on_win", [])})
    return out


# ---------------------------------------------------------------- 元进度
def load_meta():
    if not META.exists():
        return {"schema_version": "1.0", "kind": "game_meta", "levels": {}, "unlocks": []}
    return _jload(META)


def save_meta(meta):
    SAVES.mkdir(parents=True, exist_ok=True)
    tmp = META.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(META)                       # 原子替换：写一半崩掉不会毁存档


def is_unlocked(level_id):
    """第一关永远开放；其余需要前序关卡 unlocks_on_win 命中。"""
    levels = [lv["id"] for lv in list_levels() if lv.get("id")]
    if not levels or level_id == levels[0]:
        return True
    return level_id in (load_meta().get("unlocks") or [])


# ---------------------------------------------------------------- 判定
def metric_value(sim, name):
    econ = getattr(sim, "economy", None)
    if name in ECON_METRICS:
        if econ is None:
            raise LevelError("关卡要求经济指标 %s，但这一局没有经济层" % name)
        return {"cash": econ.cash,
                "operating_profit": econ.operating_profit(),
                "profit": econ.profit(),
                "revenue": econ.revenue,
                "variable_cost": econ.variable,
                "fixed_cost": econ.fixed,
                "capex": econ.capex}[name]
    metrics = (getattr(sim, "metrics", None) or {}).get("metrics") or {}
    if name not in metrics:
        raise LevelError("未知指标：%s（tick 层 KPI 里没有它）" % name)
    return metrics[name]["value"]


def _cmp(value, op, target):
    return {"==": value == target, "!=": value != target, ">=": value >= target,
            "<=": value <= target, ">": value > target, "<": value < target}[op]


def check(rules, sim):
    rows, all_ok = [], True
    for r in rules:
        try:
            v = metric_value(sim, r["metric"])
        except LevelError as exc:
            rows.append({"metric": r["metric"], "op": r["op"], "target": r["value"],
                         "value": None, "ok": False, "error": str(exc)})
            all_ok = False
            continue
        ok = _cmp(v, r["op"], r["value"])
        all_ok = all_ok and ok
        rows.append({"metric": r["metric"], "op": r["op"], "target": r["value"],
                     "value": v, "ok": ok, "primary": bool(r.get("primary"))})
    return all_ok, rows


def judge(sim, level):
    """结算：胜负 + 星级 + 元进度。纯函数（除了写元存档）。"""
    econ = getattr(sim, "economy", None)
    objs = level.get("objectives", [])
    primaries = [o for o in objs if o.get("primary")]
    _, rows = check(objs, sim)
    primary_ok, _ = check(primaries, sim) if primaries else (False, [])

    bankrupt = bool(getattr(econ, "game_over", False)) if econ is not None else False
    won = primary_ok and not bankrupt

    stars = 0
    if won:
        for rule in sorted(level.get("stars", []), key=lambda x: x["stars"]):
            ok, _ = check(rule["require"], sim)
            if ok:
                stars = max(stars, int(rule["stars"]))
        stars = max(stars, 1)               # 赢了至少 1 星

    unlocks = []
    if won:
        unlocks = [u for u in level.get("unlocks_on_win", []) if is_unlocked_target(u)]

    result = {
        "ok": True,
        "won": won,
        "stars": stars,
        "bankrupt": bankrupt,
        "objectives": rows,
        "unlocks": unlocks,
        "level_id": level.get("id"),
    }
    _persist(level, result)
    return result


def is_unlocked_target(u):
    return isinstance(u, str) and bool(u)


def _persist(level, result):
    """元进度：只记最好的成绩，不覆盖更好的历史。"""
    if not level.get("id"):
        return
    if not (level.get("carry_over") or {}).get("unlocks", True) and not result["won"]:
        return
    meta = load_meta()
    cur = (meta.get("levels") or {}).get(level["id"]) or {"stars": 0, "wins": 0}
    if result["won"]:
        cur["wins"] = int(cur.get("wins", 0)) + 1
        cur["stars"] = max(int(cur.get("stars", 0)), int(result["stars"]))
    cur["last_stars"] = int(result["stars"])
    meta.setdefault("levels", {})[level["id"]] = cur
    unlock_set = set(meta.get("unlocks") or [])
    if result["won"]:
        unlock_set.update(result["unlocks"])
    meta["unlocks"] = sorted(unlock_set)
    save_meta(meta)
