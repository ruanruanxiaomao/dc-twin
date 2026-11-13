#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""console/game.py —— 游戏服务层：实时会话、墙钟循环、命令与存读档。

边界（ADR-GAME-01 / ADR-GAME-02）：
  - 这里**不做仿真**，只负责"什么时候推进一步"。所有状态都在 `sim_engine.Simulation` 里。
  - 前端只发命令、收状态；时钟唯一权威在服务端（多一套时间逻辑必然漂移）。
  - 一个进程一个会话（和 `JOB` 单例同构）：离线 exe 是单机工具，不需要多租户。

实时循环用 Fix Your Timestep：墙钟增量 × 倍速累加进 `acc`，够了就推一步，
剩余量留到下一帧。这样倍速 8× 在慢机器上不会"跳帧丢时间"，暂停时 `acc` 也不会
偷偷攒成一大坨（暂停时直接清零，恢复后不会瞬间追帧）。
"""
from __future__ import annotations

import copy
import json
import sys
import threading
import time
from pathlib import Path

# scripts/ 里有 dc_root / sim_engine / game_*：直接 import 本模块时也要能找到它们
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from dc_root import project_root  # noqa: E402
ROOT = project_root(__file__)
CONFIG = ROOT / "config"
DATA = ROOT / "data"
RUNS = ROOT / "runs"
SESSIONS = RUNS / "game"

#: 允许的倍速档位（0 由 paused 表达，不进这里）
SPEEDS = (1, 2, 4, 8)
#: 控制循环目标频率：60 Hz。太快白烧 CPU，太慢输入手感发涩。
FRAME_HZ = 60.0
#: 单帧最多推多少仿真秒：防止某次卡顿后一口气补上几百步（画面会"瞬移"）。
MAX_CATCHUP_S = 2.0


def _jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


class GameError(Exception):
    """业务错误：带 HTTP 码，方便路由层直接回给前端。"""

    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def build_sim(scenario_id="baseline", session_dir=None, external_orders=None):
    """按场景 id 造一个**未运行**的 Simulation。

    刻意复用 `sim_engine` 的构造路径（同一套 spec 文件），而不是另写一份装配逻辑：
    装配分叉过一次就再也无法保证"直播跑出来的数和批跑一致"。
    """
    import sim_engine as se

    path = CONFIG / "scenarios" / ("%s.json" % scenario_id)
    if not path.exists():
        custom = CONFIG / "scenarios" / "custom" / ("%s.json" % scenario_id)
        if not custom.exists():
            raise GameError("没有这个场景：%s" % scenario_id, 404)
        path = custom
    out = Path(session_dir) if session_dir else None
    return se.Simulation(
        path,
        _jload(CONFIG / "sim_spec.json"),
        _jload(CONFIG / "fleet_spec.json"),
        _jload(CONFIG / "demand_spec.json"),
        _jload(DATA / "layout.json"),
        _jload(DATA / "nav_grid.json"),
        _jload(DATA / "config.json"),
        out_dir=out,
        external_orders=external_orders,
    )


class GameSession:
    """一局游戏。所有对 `sim` 的读写都必须持有 `self.lock`。"""

    def __init__(self, sim, scenario_id, level=None):
        self.sim = sim
        self.scenario_id = scenario_id
        self.level = level
        self.lock = threading.RLock()
        self.paused = False
        self.speed = 1
        self.running = True
        self.finished = False
        self.session_id = sim.run_id.replace("/", "-")
        self.created_at = time.time()
        self.wall_steps = 0
        self._thread = None
        # 现金流曲线（HUD 画线用）。只留最近 600 点，够画图又不吃内存。
        self.cash_history = []

    # ---- 生命周期
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        with self.lock:
            # 只在"没跑过"的时候初始化。读档会话的 `_started` 已是 True，
            # 无条件 `_init_run()` 会把 t 归零——存档看起来读成功了，其实从头再开一局。
            if not getattr(self.sim, "_started", False):
                self.sim._init_run()
        self._thread = threading.Thread(target=self._loop, name="game-tick", daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    # ---- 主循环（Fix Your Timestep）
    def _loop(self):
        acc = 0.0
        last = time.monotonic()
        budget = 1.0 / FRAME_HZ
        while self.running:
            now = time.monotonic()
            frame = now - last
            last = now
            with self.lock:
                if self.finished:
                    time.sleep(budget)
                    continue
                if self.paused or self.speed == 0:
                    # 暂停时清零累加器：否则恢复的一瞬间会把暂停期间的时间一次性补上
                    acc = 0.0
                else:
                    acc += min(frame, MAX_CATCHUP_S) * self.speed
                    scratch = self.sim.tick
                    n = 0
                    while acc >= scratch and not self.sim.done:
                        self.sim.step()
                        acc -= scratch
                        n += 1
                        if n > 5000:          # 防单帧无限推进（病态场景保护）
                            break
                    self.wall_steps += n
                    self._record_cash()
                    if self.sim.done:
                        self._settle_locked()
                    elif getattr(getattr(self.sim, "economy", None), "game_over", False):
                        self._settle_locked()
            time.sleep(budget)

    def _record_cash(self):
        econ = getattr(self.sim, "economy", None)
        if econ is None:
            return
        point = {"t_s": round(self.sim.t, 2), "cash": round(econ.cash, 2)}
        if not self.cash_history or self.cash_history[-1]["t_s"] != point["t_s"]:
            self.cash_history.append(point)
            if len(self.cash_history) > 600:
                del self.cash_history[:100]

    def _settle_locked(self):
        """仿真到头或破产：收尾（幂等）并做关卡判定。

        破产时 `sim.done` 仍为 False（引擎不知道经济层），所以这里显式写一条
        GAME_OVER 事件再收尾——否则事后看事件流只会看到"跑到一半停了"。
        """
        if self.finished:
            return
        econ = getattr(self.sim, "economy", None)
        if econ is not None and getattr(econ, "game_over", False):
            if "GAME_OVER" not in self.sim.log.counts:
                self.sim.log.emit(self.sim.t, "GAME_OVER",
                                  reason="bankrupt", cash=round(econ.cash, 2),
                                  t_s=round(self.sim.t, 3))
        self.sim._finalize()
        self.finished = True
        if self.level is not None:
            try:
                import game_levels
                self.result = game_levels.judge(self.sim, self.level)
            except Exception as exc:                     # noqa: BLE001
                self.result = {"ok": False, "error": repr(exc)}
        else:
            self.result = {"ok": True, "stars": 0, "detail": "自由模式（无关卡目标）"}

    # ---- 控制
    def pause(self, paused):
        with self.lock:
            self.paused = bool(paused)

    def set_speed(self, speed):
        speed = int(speed)
        if speed not in SPEEDS:
            raise GameError("倍速只能是 %s" % (SPEEDS,))
        with self.lock:
            self.speed = speed
            self.paused = False

    def command(self, cmd):
        with self.lock:
            queued = self.sim.enqueue_command(cmd)
            return {"ok": True, "queued": queued, "at_tick": self.sim.tick_i}

    def fast_forward(self, to="settle", max_s=None):
        """快进。`settle` 直接推到结算；`next_event` 推到下一条事件。"""
        with self.lock:
            if self.finished:
                return {"ok": True, "already": True}
            before_seq = self.sim.log.seq
            limit = max_s if max_s is not None else self.sim.horizon
            t_end = self.sim.t + float(limit)
            guard = 0
            while not self.sim.step():
                guard += 1
                if guard > 500_000:
                    raise GameError("快进步数超限，疑似死循环", 500)
                if to == "next_event" and self.sim.log.seq > before_seq:
                    break
                if self.sim.t >= t_end:
                    break
            self._record_cash()
            if self.sim.done:
                self._settle_locked()
            return {"ok": True, "t_s": self.sim.t, "tick_i": self.sim.tick_i,
                    "events": self.sim.log.seq - before_seq, "finished": self.finished}

    # ---- 状态
    def state(self):
        """HUD 轻量状态。**不返回**全部事件、不返回全部 KPI（那是一份 40KB 的报文）。"""
        with self.lock:
            sim = self.sim
            robots = {}
            for rid, r in sim.robots.items():
                robots[rid] = {
                    "pos": [round(r.pos[0], 3), round(r.pos[1], 3)],
                    "state": r.state,
                    "soc_pct": round(r.soc_pct, 2),
                    "order": (r.order or {}).get("order_id"),
                    "completed": r.completed,
                }
            out = {
                "ok": True,
                "session_id": self.session_id,
                "scenario_id": self.scenario_id,
                "t_s": round(sim.t, 2),
                "tick_i": sim.tick_i,
                "n_ticks": sim.n_ticks,
                "horizon_s": sim.horizon,
                "paused": self.paused,
                "speed": self.speed,
                "finished": self.finished,
                "queue_depth": len(sim.queue),
                "robots": robots,
                "counts": dict(sim.log.counts),
            }
            # 车型库：前端要能"选哪个车型再买"，否则 BUY_EQUIPMENT 只有 API 能用，
            # 玩家点到的永远是默认车型（功能存在但不可达）。
            models = list(getattr(sim, "models_by_id", {}).values())
            out["models"] = [{"id": m.get("id"),
                              "capex_cny": float(m.get("capex_cny") or 0.0),
                              "payload_kg": float(m.get("payload_kg") or 0.0),
                              "speed_mps": float(m.get("speed_mps") or 0.0)}
                             for m in models]
            # 效期/批次是**独立一块**而不是塞进 economy：报废不进 P&L
            # （ADR-GAME-10 决策三），把它放经济体里会诱导前端当成成本去加总。
            out["expiry"] = {
                "enabled": bool(getattr(sim, "lot_expiry_enabled", False)),
                "policy": getattr(sim, "lot_policy", None),
                "expired_units": round(float(getattr(sim, "lot_expired_units", 0.0)), 3),
                "expired_value": round(float(getattr(sim, "lot_expired_value", 0.0)), 2),
                "open_lots": sum(len(v) for v in getattr(sim, "lots", {}).values()),
                "fefo_violations": int(getattr(sim, "fefo_violations", 0)),
            }
            econ = getattr(sim, "economy", None)
            if econ is not None:
                out["economy"] = econ.snapshot()
                out["cash_history"] = list(self.cash_history)
            if self.finished:
                out["result"] = getattr(self, "result", None)
            if self.level is not None:
                out["level"] = {"id": self.level.get("id"),
                                "name": self.level.get("name"),
                                "objectives": self.level.get("objectives", [])}
            return out

    def save(self, slot):
        import game_save
        with self.lock:
            return game_save.save_session(self, slot)

    @classmethod
    def load(cls, slot):
        import game_save
        return game_save.load_session(cls, slot)


class GameManager:
    """单例会话管理（与 JOB/JOB_LOCK 同构）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.session = None

    def current(self):
        with self.lock:
            return self.session

    def start(self, scenario_id="baseline", mode="live", level_id=None, initial_capital=None):
        if mode not in ("live", "replay"):
            raise GameError("mode 只能是 live / replay")
        with self.lock:
            if self.session is not None and not self.session.finished:
                self.session.stop()
            level = None
            if level_id:
                import game_levels
                level = game_levels.load_level(level_id)
                scenario_id = level.get("scenario_id") or scenario_id
            session_dir = SESSIONS / ("%s-%d" % (scenario_id, int(time.time() * 1000)))
            sim = build_sim(scenario_id, session_dir=session_dir)
            # 经济层总是挂载：没有现金的经营游戏不是一个游戏（自由模式也有 50 万启动资金，
            # 只是没有胜负目标）。开局才挂，避免"跑到一半才开始记账"的口径断层。
            import game_economy
            game_economy.attach(sim, level, initial_capital=initial_capital)
            session = GameSession(sim, scenario_id, level=level)
            self.session = session
        if mode == "live":
            session.start()
        else:
            with session.lock:
                sim._init_run()
        return session

    def stop(self):
        with self.lock:
            if self.session:
                self.session.stop()
            self.session = None


MANAGER = GameManager()
