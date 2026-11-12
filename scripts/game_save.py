#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/game_save.py —— 存档槽（ADR-GAME-05）。

三件事必须同时成立，否则"能存档"是假的：
  1. **续玩**：`restore(snapshot())` 之后继续跑，事件流与"不中断跑"逐字节一致；
  2. **不重号**：续写接在同一份事件流的 seq 后面（append），不是另开一局；
  3. **可拒收**：损坏 / 版本不符 / 缺字段 / 日志对不上的快照必须报错，
     不许"尽力而为"地补默认值——那会让玩家读到一个错位的存档，几十 tick 后
     才以"车穿墙"的形式暴露，且无法定位。

写盘用「临时文件 + 原子替换」：写一半断电不会毁掉上一个能用的存档。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dc_root import project_root  # noqa: E402
ROOT = project_root(__file__)
SAVES = ROOT / "saves"
SLOTS = SAVES / "slots"
SCHEMA = "1.0"


class SaveError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def slot_path(slot):
    if not isinstance(slot, str) or not slot or "/" in slot or "\\" in slot or ".." in slot:
        raise SaveError("存档槽名非法：%r" % (slot,))
    return SLOTS / ("%s.json" % slot)


def _atomic_write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def save_session(session, slot):
    sim = session.sim
    snap = sim.snapshot()
    payload = {
        "schema_version": SCHEMA,
        "kind": "game_save",
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "slot": slot,
        "scenario_id": session.scenario_id,
        "level_id": (session.level or {}).get("id"),
        "speed": session.speed,
        "paused": session.paused,
        "finished": session.finished,
        "cash_history": session.cash_history[-200:],
        "snapshot": snap,
    }
    p = slot_path(slot)
    _atomic_write(p, payload)
    try:
        shown = p.relative_to(ROOT).as_posix()
    except ValueError:                     # 存档目录被指到项目外（测试/自定义数据根）
        shown = p.as_posix()
    return {"ok": True, "slot": slot, "path": shown,
            "bytes": p.stat().st_size, "t_s": snap["t_s"], "tick_i": snap["tick_i"]}


def list_slots():
    out = []
    if SLOTS.is_dir():
        for p in sorted(SLOTS.glob("*.json"), key=lambda x: -x.stat().st_mtime):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({"slot": p.stem, "saved_at": d.get("saved_at"),
                            "scenario_id": d.get("scenario_id"),
                            "level_id": d.get("level_id"),
                            "t_s": (d.get("snapshot") or {}).get("t_s"),
                            "bytes": p.stat().st_size})
            except Exception as exc:                   # noqa: BLE001
                out.append({"slot": p.stem, "error": repr(exc)})
    return out


def delete_slot(slot):
    p = slot_path(slot)
    if not p.exists():
        raise SaveError("没有这个存档：%s" % slot, 404)
    p.unlink()
    return {"ok": True, "slot": slot}


def read_save(slot):
    p = slot_path(slot)
    if not p.exists():
        raise SaveError("没有这个存档：%s" % slot, 404)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                           # noqa: BLE001
        raise SaveError("存档不是合法 JSON：%r" % (exc,))
    if d.get("kind") != "game_save":
        raise SaveError("不是游戏存档：%r" % (d.get("kind"),))
    if d.get("schema_version") != SCHEMA:
        raise SaveError("存档版本不符：%r" % (d.get("schema_version"),))
    if not isinstance(d.get("snapshot"), dict):
        raise SaveError("存档缺少快照")
    return d


def _scenario_path(sid):
    import game_save as _self  # noqa: F401  (保持自引用可读性)
    from console import game as game_mod
    p = game_mod.CONFIG / "scenarios" / ("%s.json" % sid)
    if not p.exists():
        p = game_mod.CONFIG / "scenarios" / "custom" / ("%s.json" % sid)
    if not p.exists():
        raise SaveError("存档引用的场景不存在：%s" % sid, 404)
    return p


def load_session(session_cls, slot):
    """读档并返回一个**已暂停**的 GameSession（调用方决定是否 start）。"""
    import game_levels
    import sim_engine as se
    from console import game as game_mod

    d = read_save(slot)
    snap = d["snapshot"]
    sid = d.get("scenario_id") or snap.get("scenario")
    scen = _scenario_path(sid)

    run_dir = snap.get("run_dir")
    if not run_dir:
        raise SaveError("快照没有记录运行目录，无法接着同一份事件流续写")
    out = Path(run_dir)
    if not out.is_absolute():
        out = ROOT / out
    if not (out / "sim_events.jsonl").exists():
        # 事件流是"事实来源"，缺了就不该假装能续玩（否则 seq 从头开始，台账全错）
        raise SaveError("存档对应的事件流已不存在：%s" % out, 410)

    sim = se.Simulation.restore(
        snap, scen,
        game_mod._jload(game_mod.CONFIG / "sim_spec.json"),
        game_mod._jload(game_mod.CONFIG / "fleet_spec.json"),
        game_mod._jload(game_mod.CONFIG / "demand_spec.json"),
        game_mod._jload(game_mod.DATA / "layout.json"),
        game_mod._jload(game_mod.DATA / "nav_grid.json"),
        game_mod._jload(game_mod.DATA / "config.json"),
        out_dir=out,
    )
    level = None
    if d.get("level_id"):
        level = game_levels.load_level(d["level_id"])
    session = session_cls(sim, sid, level=level)
    session.paused = True                       # 读档默认暂停：给玩家反应时间
    session.speed = int(d.get("speed") or 1)
    session.cash_history = list(d.get("cash_history") or [])
    session.finished = bool(d.get("finished"))
    return session
