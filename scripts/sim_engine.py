"""sim_engine.py - tick-level discrete-event simulation (COMPLETION_PLAN §3, stage A).

Pure standard library. Deterministic PRNG (objectified Mulberry32, deepcopy-able),
continuous order arrivals, online dispatching, 9-state robot FSM, four-rate battery
model, fault injection, L1 space-time reservation, append-only event log and
measured KPIs. All outputs are derived data; never edit them by hand.

Usage:
  python scripts/sim_engine.py --scenario config/scenarios/baseline.json
  python scripts/sim_engine.py --all
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import glob
import heapq
import json
import math
import os
import sys
import time
from pathlib import Path

from dc_root import project_root  # noqa: E402
ROOT = project_root(__file__)
CONFIG, DATA, OUT, RUNS = ROOT / "config", ROOT / "data", ROOT / "out", ROOT / "runs"

# 死锁退让参数：被同一台车挡住这么久，且我方 id 更大，就后退让路。
# DC_YIELD_AFTER_S 给守卫做对照实验（调成很大即等于"关闭退让"）。
YIELD_AFTER_S = float(os.environ.get("DC_YIELD_AFTER_S", "8"))
# 绕行阈值：被同一台车挡这么久还没让开（对方可能是停着的 IDLE/充电车），
# 就重新规划一条绕开它的路线。退让解决面对面，绕行解决"前面停着一台车"。
DETOUR_AFTER_S = 20.0
DETOUR_COOLDOWN_S = 30.0      # 同一台车的绕行重试间隔
# 兜底阈值：单向窄巷里迎面顶死时，退让与绕行都无解（没有第二条路）。
# 真实仓库中这是"呼叫人工把车挪开"。这里做成显式事件并计数，而不是假装没有。
JAM_CLEAR_AFTER_S = 300.0
YIELD_BACK_M = 1.5

EVENT_TYPES = (
    "ORDER_ARRIVED", "ORDER_LOST", "TASK_ASSIGNED", "TASK_REASSIGNED",
    "STATE_CHANGED", "PICK_DONE", "DROP_DONE", "TASK_COMPLETED",
    "CHARGE_START", "CHARGE_END", "BATTERY_CRITICAL", "FAULT_START", "FAULT_END",
    "DOCK_BLOCKED", "DOCK_REOPENED", "DOCK_REFUSED", "DOCK_RESERVED",
    "DOCK_HANDOFF", "REPLAN", "WAIT_CONFLICT", "ENERGY_TICK",
    "STATION_ENTER", "STATION_QUEUED", "STATION_EXIT", "DEADLOCK_YIELD",
    "JAM_CLEARED",
    # P6a 库存层（ADR-GAME-09）：库位级 SKU 账。这些事件必须真写进
    # sim_events.jsonl，否则 refreeze 的 `--check=v1` 不会变红，归因就无从谈起。
    "STOCK_DECREMENT", "STOCK_RECEIPT", "STOCK_REPLENISH_ARRIVED", "STOCKOUT",
    "REORDER_TRIGGERED", "PICKFACE_REFILL", "HOLDING_ACCRUED",
    "RUN_SETTLED",
)


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Mulberry32:
    """Objectified PRNG: deepcopy gives an identical independent random stream."""

    def __init__(self, seed):
        self.s = seed & 0xFFFFFFFF

    def next(self):
        self.s = (self.s + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.s
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF) ^ t) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    def expovariate(self, lam):
        return -math.log(max(self.next(), 1e-12)) / lam if lam > 0 else float("inf")

    def choice(self, seq):
        return seq[int(self.next() * len(seq))]

    # ---- 快照原语（ADR-GAME-05）----
    # 内部状态只有一个 32 位整数，所以"存档"就是存这个数。为它单开读写口子，
    # 是为了让存档代码不必碰私有字段，也让单元测试能直接断言"同一状态 → 同一序列"。
    def state(self):
        return self.s

    @classmethod
    def from_state(cls, s):
        obj = cls(0)
        obj.s = int(s) & 0xFFFFFFFF
        return obj


class World:
    """Read-only layout + navigation grid, with an 8-direction no-corner-cut A*."""

    def __init__(self, layout, grid):
        self.layout = layout
        self.cell = grid["cell_size_m"]
        self.cols, self.rows = grid["cols"], grid["rows_count"]
        self.rows_walk = grid["walkable_rows"]
        self.warehouses = {w["id"]: w for w in layout["warehouses"]}
        self.locs = {}
        self.by_kind = {}
        self.chargers = {}
        self.homes = {}
        for wid, w in self.warehouses.items():
            self.locs[wid] = {loc["id"]: loc for loc in w["locations"]}
            kinds = {}
            for loc in w["locations"]:
                kinds.setdefault(loc["kind"], []).append(loc)
            self.by_kind[wid] = kinds
            self.chargers[wid] = [c["access_point"] for c in w.get("charging_stations", [])]
            self.homes[wid] = {r["id"]: (r["position"][0], r["position"][2])
                               for r in w["spawn"]["robots"]}

    def to_cell(self, xy):
        return (min(self.cols - 1, max(0, int(xy[0] / self.cell))),
                min(self.rows - 1, max(0, int(xy[1] / self.cell))))

    def center(self, cr):
        return ((cr[0] + 0.5) * self.cell, (cr[1] + 0.5) * self.cell)

    def walkable(self, cr):
        c, r = cr
        return 0 <= c < self.cols and 0 <= r < self.rows and self.rows_walk[r][c] == "1"

    def nearest_walkable(self, xy):
        c, r = self.to_cell(xy)
        if self.walkable((c, r)):
            return (c, r)
        for rad in range(1, 12):
            best = None
            for dc in range(-rad, rad + 1):
                for dr in (-rad, rad):
                    for cr in ((c + dc, r + dr), (c + dr, r + dc)):
                        if self.walkable(cr):
                            p = self.center(cr)
                            d = (p[0] - xy[0]) ** 2 + (p[1] - xy[1]) ** 2
                            if best is None or d < best[0]:
                                best = (d, cr)
            if best:
                return best[1]
        raise RuntimeError("no walkable cell near %s" % (xy,))

    def astar(self, a_xy, b_xy, extra_blocked=None):
        a, b = self.nearest_walkable(a_xy), self.nearest_walkable(b_xy)
        blocked = extra_blocked or set()
        def h(cr):
            return (max(abs(cr[0] - b[0]), abs(cr[1] - b[1]))
                    + (math.sqrt(2) - 2) * min(abs(cr[0] - b[0]), abs(cr[1] - b[1]))) * self.cell
        open_heap = [(0, 0, a)]
        g = {a: 0.0}
        parent = {}
        seq = 1
        while open_heap:
            _, _, cur = heapq.heappop(open_heap)
            if cur == b:
                path = [cur]
                while path[-1] in parent:
                    path.append(parent[path[-1]])
                path.reverse()
                return [self.center(cr) for cr in path]
            for dc, dr, cost in ((1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1),
                                 (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)),
                                 (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2))):
                nxt = (cur[0] + dc, cur[1] + dr)
                if nxt in blocked or not self.walkable(nxt):
                    continue
                if dc and dr and (not self.walkable((cur[0] + dc, cur[1]))
                                  or not self.walkable((cur[0], cur[1] + dr))):
                    continue    # no corner cutting
                ng = g[cur] + cost * self.cell
                if ng < g.get(nxt, 1e18) - 1e-9:
                    g[nxt] = ng
                    parent[nxt] = cur
                    heapq.heappush(open_heap, (ng + h(nxt), seq, nxt))
                    seq += 1
        return None


class EventLog:
    def __init__(self, path, ring=5000, append=False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # append=True 只给 restore 用：快照续玩要接着写，不能把已落盘的事件清空。
        self.f = open(self.path, "a" if append else "w", encoding="utf-8")
        self.seq = 0
        self.ring = ring
        self.recent = []
        self.counts = {}

    def emit(self, t_s, typ, **kw):
        self.seq += 1
        e = {"seq": self.seq, "t_s": round(t_s, 3), "type": typ}
        # core fields are reserved: an order's business type must never
        # overwrite the event type (it is carried as `order_type`).
        e.update({k: v for k, v in kw.items() if k not in ("seq", "t_s", "type")})
        self.f.write(json.dumps(e, ensure_ascii=False) + "\n")
        self.counts[typ] = self.counts.get(typ, 0) + 1
        self.recent.append(e)
        if len(self.recent) > self.ring:
            self.recent.pop(0)
        return e

    def close(self):
        self.f.close()

    # ---- 快照原语（ADR-GAME-05 / 方案书 §4.3）----
    def snapshot(self):
        # 必须先 flush：Python 的文件对象有缓冲，存档瞬间最后几条事件可能还在内存里。
        # 不 flush 的后果是 restore 校验读到"磁盘最后 seq=0、快照 seq=4"，看起来像
        # 日志被截断——实际只是没落盘。（打包后的 exe 里更容易命中：事件少、缓冲没满。）
        try:
            self.f.flush()
        except Exception:                      # noqa: BLE001  已关闭的文件不用管
            pass
        return {"seq": self.seq, "counts": dict(self.counts), "recent": list(self.recent)}

    @classmethod
    def restore(cls, path, ring, snap):
        """以 append 重开日志并恢复 seq/counts/recent。

        硬校验：**磁盘上最后一条事件的 seq 必须等于快照里的 seq**。
        这条检查是给"截断重开"这一类事故上的锁——一旦有人图省事用 `open(...,"w")`
        重开，已写事件被清空，后续 seq 从 1 重号，ledger 的幂等键（seq, account）
        会把新事件当成旧事件去重，最后算出来的利润是错的且查不出来。
        """
        path = Path(path)
        want = int(snap["seq"])
        last = 0
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last = json.loads(line)["seq"]
        elif want > 0:
            raise ValueError("事件日志缺失但快照 seq=%d：不能凭空续写" % want)
        if path.exists() and last != want:
            raise ValueError("事件日志与快照不一致：磁盘最后 seq=%d，快照 seq=%d"
                             "（历史被截断或快照过期）" % (last, want))
        obj = cls(path, ring, append=True)
        obj.seq = want
        obj.counts = dict(snap.get("counts") or {})
        obj.recent = list(snap.get("recent") or [])[-ring:]
        return obj


#: 派单规则表。键就是 config/scenarios/*.json 里 overrides["dispatch.policy"] 的取值。
#: 加一个键 = 多一种可保存、可对比的策略；引擎里不再写死"最近的车"。
DISPATCH_RULES = {
    "nearest_first":   lambda r, o, sim: math.dist(r.pos, o["pick_xy"]),
    "farthest_first":  lambda r, o, sim: -math.dist(r.pos, o["pick_xy"]),
    "highest_soc":     lambda r, o, sim: -r.soc_pct,
    "lowest_soc":      lambda r, o, sim: r.soc_pct,
    "least_completed": lambda r, o, sim: r.completed,
}
CHARGE_POLICIES = ("opportunistic", "threshold")

#: 订单类型（与 demand_spec.orders.mix 的键一致）。定价只能定这五种。
ORDER_TYPES = ("INBOUND", "OUTBOUND", "PICK", "REPLENISH", "TRANSFER")

#: 玩家命令登记表（方案书 §2.4.1 / ADR-GAME-03）。加一个动作 = 在这里登记 + 写一个
#: `_cmd_<type小写>` 方法；`_handler_for` 按命名约定取处理器，不做第二份注册表
#: （两份表必然分叉，最后会出现"登记了但没有人接"的命令）。
COMMANDS = ("BUY_AGV", "SELL_AGV", "SET_PRICE", "SET_POLICY",
            "SET_DEMAND", "INJECT_FAULT", "BUILD_STATION", "MANUAL_ASSIGN",
            "SET_REORDER_POINT", "SET_LOT_POLICY")
#: 已登记但尚未实现：入队即抛，不静默空转（静默 = 玩家点了没反应还以为生效了）。
NOT_IMPLEMENTED = ("BUILD_STATION", "MANUAL_ASSIGN")


class Robot:
    STATES = ("IDLE", "ASSIGNED", "MOVING_PICK", "PICKING", "MOVING_DROP",
              "DROPPING", "RETURNING", "TO_CHARGER", "CHARGING", "FAULT", "WAITING")

    def __init__(self, rid, wid, home, soc, model):
        self.id, self.wid, self.home = rid, wid, home
        self.pos = home
        self.soc = soc                      # kWh
        self.soc_initial = soc              # kept for S2 conservation recompute
        self.cap = model["battery_kwh"]
        self.speed = model["speed_mps"]
        self.cons = model["consumption_kwh_per_m"]
        self.idle_kw = model["idle_kw"]
        self.charge_kw = model["charge_kw"]
        self.state = "IDLE"
        self.order = None
        self.legs = []
        self.leg = 0
        self.route = []
        self.arc = 0.0
        self.route_len = 0.0
        self.dwell_until = 0.0
        self.wait_s = 0.0
        self.fault_until = 0.0
        self.charging_since = None
        # 工位占用/排队（M1-02）：station 非空表示正占着一个工位
        self.station = None
        self.next_station_state = None
        self.queued_since = 0.0
        # 死锁退让：blocked_by 记录当前挡路的车，blocked_s 累计被同一台车挡住的时长
        self.blocked_by = None
        self.blocked_s = 0.0
        self.yields = 0
        self.detours = 0
        self.blocked_total = 0.0      # 累计受阻时长（跨机器人求和后更有区分度）
        self.detour_try_t = -1e9      # 上次尝试绕行的时刻（冷却用）
        self.completed = 0
        self.distance_m = 0.0
        self.energy_out = 0.0
        self.energy_in = 0.0
        self.move_events = 0
        self.leg_m = [0.0, 0.0, 0.0]

    @property
    def soc_pct(self):
        return 100.0 * self.soc / self.cap

    # ---- 快照原语（ADR-GAME-05 / 方案书 §4.2）----
    #: 跨 tick 会变的全部字段。模型常量（cap/speed/cons/idle_kw/charge_kw）**不入快照**：
    #: 它们由 model 重建，存两份必然漂移；模型改版后旧快照也不该带着旧参数复活。
    SNAPSHOT_FIELDS = (
        "id", "wid", "home", "pos", "soc", "soc_initial", "state", "order", "legs", "leg",
        "route", "arc", "route_len", "dwell_until", "wait_s", "fault_until", "charging_since",
        "station", "next_station_state", "queued_since", "blocked_by", "blocked_s",
        "yields", "detours", "blocked_total", "detour_try_t", "completed",
        "distance_m", "energy_out", "energy_in", "move_events", "leg_m",
        # 附录 A.3 漏了这一个：_make_robots 里给每台车配了充电桩，充电去哪台
        # 靠它决定。漏存的后果是续玩后全车队挤同一台桩。
        "charger_id",
    )

    def serialize(self):
        """JSON-able 全量状态。坐标元组统一降为列表（JSON 没有元组）。"""
        def conv(v):
            if isinstance(v, (list, tuple)):
                return [conv(x) for x in v]
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            return v
        return {k: conv(getattr(self, k)) for k in self.SNAPSHOT_FIELDS}

    @classmethod
    def deserialize(cls, d, model):
        """逐字段恢复。缺字段直接报错——不许"尽力而为"地补默认值。

        补默认值看起来友好，实际会让缺状态的快照静默续跑，然后在几十个 tick 之后
        以"轨迹莫名偏了"的形式暴露，极难定位。
        """
        missing = [k for k in cls.SNAPSHOT_FIELDS if k not in d]
        if missing:
            raise ValueError("robot 快照缺字段：%s" % ", ".join(missing))
        r = cls.__new__(cls)
        r.cap = model["battery_kwh"]
        r.speed = model["speed_mps"]
        r.cons = model["consumption_kwh_per_m"]
        r.idle_kw = model["idle_kw"]
        r.charge_kw = model["charge_kw"]
        r.id = d["id"]
        r.wid = d["wid"]
        r.home = tuple(d["home"])
        r.pos = tuple(d["pos"])
        r.soc = d["soc"]
        r.soc_initial = d["soc_initial"]
        r.state = d["state"]
        if r.state not in cls.STATES:
            raise ValueError("robot %s 快照状态非法：%r" % (r.id, r.state))
        # 订单里的 pick_xy/drop_xy 必须还原成元组：下游 math.dist 与
        # "同一坐标"的判断都按元组语义走的，列表会让相等判断悄悄失真。
        od = d["order"]
        r.order = None
        if od is not None:
            r.order = dict(od)
            for k in ("pick_xy", "drop_xy"):
                if k in r.order and r.order[k] is not None:
                    r.order[k] = tuple(r.order[k])
        r.legs = [[tuple(p) for p in leg] for leg in d["legs"]]
        r.leg = d["leg"]
        r.route = [tuple(p) for p in d["route"]]
        r.arc = d["arc"]
        r.route_len = d["route_len"]
        r.dwell_until = d["dwell_until"]
        r.wait_s = d["wait_s"]
        r.fault_until = d["fault_until"]
        r.charging_since = d["charging_since"]
        r.station = d["station"]
        r.next_station_state = d["next_station_state"]
        r.queued_since = d["queued_since"]
        r.blocked_by = d["blocked_by"]
        r.blocked_s = d["blocked_s"]
        r.yields = d["yields"]
        r.detours = d["detours"]
        r.blocked_total = d["blocked_total"]
        r.detour_try_t = d["detour_try_t"]
        r.completed = d["completed"]
        r.distance_m = d["distance_m"]
        r.energy_out = d["energy_out"]
        r.energy_in = d["energy_in"]
        r.move_events = d["move_events"]
        r.leg_m = list(d["leg_m"])
        r.charger_id = d["charger_id"]
        return r

    def set_state(self, new, log, t):
        old = self.state
        if old != new:
            self.state = new
            log.emit(t, "STATE_CHANGED", agent=self.id, **{"from": old, "to": new})

    def route_to(self, world, target, log, t, extra_blocked=None):
        p = world.astar(self.pos, target, extra_blocked)
        if not p:
            return False
        self.route = p
        self.route_len = sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1))
        self.arc = 0.0
        return True

    def point_at(self, arc):
        if not self.route:
            return self.pos
        left = arc
        for i in range(len(self.route) - 1):
            a, b = self.route[i], self.route[i + 1]
            d = math.dist(a, b)
            if left <= d:
                f = left / d if d > 1e-9 else 0
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            left -= d
        return self.route[-1]

    def advance(self, dt_s, t, log, world, positions, min_sep):
        """L1 space-time reservation: a move is allowed only if the new centre
        stays >=2*AGV_RADIUS from every other robot's (already updated) centre.
        Cell occupancy is recorded separately for the double-occupancy check."""
        if self.state in ("MOVING_PICK", "MOVING_DROP", "RETURNING", "TO_CHARGER"):
            step = min(self.speed * dt_s, max(0.0, self.route_len - self.arc))
            if step <= 1e-9:
                return 0.0
            # 电量硬约束：没有电就走不了。以前是 soc 夹到 0 但照常行驶，
            # 会让"能量守恒"残差凭空多出来（0.157 kWh 就是这么来的）。
            if self.cons > 0:
                step = min(step, max(0.0, self.soc) / self.cons)
                if step <= 1e-9:
                    return 0.0
            target_arc = self.arc + step
            cand = self.point_at(target_arc)
            blocked = next((oid for oid, opos in positions.items()
                            if oid != self.id and math.dist(cand, opos) < min_sep - 1e-6), None)
            if blocked is not None:
                self.wait_s += dt_s
                self.blocked_total += dt_s
                if blocked == self.blocked_by:
                    self.blocked_s += dt_s
                else:
                    self.blocked_by, self.blocked_s = blocked, dt_s
                if self.wait_s <= dt_s * 1.5:
                    log.emit(t, "WAIT_CONFLICT", agent=self.id,
                             cell=list(world.to_cell(cand)), blocked_by=blocked)

                # ---- 死锁退让 ----
                # 纯反应式距离门控在窄巷道会让你等我、我等你，双方永远不动。
                # 规则：被同一台车挡住超过 YIELD_AFTER 秒，且**我方 id 较大**（确定性
                # 仲裁，避免两边同时退让来回抖），就沿自己的路线后退让出空间。
                # 退让只在“后方同样满足最小间距”时执行，不会撞上第三台车。
                if self.blocked_s >= YIELD_AFTER_S and self.id > blocked:
                    back_arc = max(0.0, self.arc - YIELD_BACK_M)
                    if back_arc < self.arc - 1e-9:
                        back_pt = self.point_at(back_arc)
                        clear = all(
                            oid == self.id or math.dist(back_pt, opos) >= min_sep - 1e-6
                            for oid, opos in positions.items())
                        if clear:
                            moved = self.arc - back_arc
                            self.arc = back_arc
                            self.pos = back_pt
                            self.distance_m += moved
                            self.energy_out += moved * self.cons
                            self.soc = max(0.0, self.soc - moved * self.cons)
                            self.yields += 1
                            self.blocked_s = 0.0
                            self.blocked_by = None
                            log.emit(t, "DEADLOCK_YIELD", agent=self.id,
                                     yielded_to=blocked, back_m=round(moved, 3))
                            return -moved
                return 0.0
            self.wait_s = 0.0
            self.blocked_by, self.blocked_s = None, 0.0
            self.arc = target_arc
            self.pos = cand
            self.distance_m += step
            self.energy_out += step * self.cons
            self.soc = max(0.0, self.soc - step * self.cons)
            self.move_events += 1
            if self.leg < 3:
                self.leg_m[self.leg] += step
            return step
        return 0.0


class Simulation:
    station_slots_override = None       # 验证排队用：把所有工位并行位强制为 N
    #: 验证"效期开关"用：覆写 inventory_spec（只给测试/实验，产线走 config 文件）。
    #: 和 station_slots_override 同一套路——可证伪必须能构造反例，否则只能读代码相信。
    inventory_spec_override = None
    dock_slots_override = None          # 同上，但只改月台门（工位不动）
    # 验证"跳 tick 分支真的在起作用"用：置 False 时关闭空闲跳 tick。
    # 这不是给人用的开关，是给 tests/test_step_gate.py 证明 STEP_OK 门会失败用的
    # ——沿用 station_slots_override / dock_slots_override 的可证伪套路。
    jump_ticks_override = None

    def __init__(self, scenario_path, sim_spec, fleet, demand, layout, grid, cfg,
                 out_dir=None, external_orders=None, append_log=False):
        self.sc = jload(scenario_path)
        self.spec = copy.deepcopy(sim_spec)
        self.fleet, self.demand, self.layout, self.grid = fleet, demand, layout, grid
        self.cfg = cfg
        self.world = World(layout, grid)
        self.tick = self.spec["tick_s"]
        self.horizon = self.spec["horizon_s"]
        self.seed = self.sc.get("seed", self.spec["seed"])
        self.rng = Mulberry32(self.seed)
        # 服务时长采样用独立随机流：否则会打乱订单到达序列，
        # 让"只改了过程层"变成"整个需求分布都变了"，无法归因。
        self.svc_rng = Mulberry32((self.seed ^ 0x9E3779B9) & 0xFFFFFFFF)
        self.model = fleet["models"][0]
        self.reserve_pct = self.spec["dispatch"]["battery_reserve_pct"]
        self.critical_pct = self.spec["battery"]["critical_pct"]
        self.radius = self.spec["traffic"]["reserve_radius_cells"]
        self.min_sep = round(2 * (math.hypot(self.model["length"], self.model["width"]) / 2
                                  + self.model["safety_margin_m"]), 4)
        self.dwell = cfg["agv"]["dwell_pick_s"]
        self.overrides = self.sc.get("overrides", {})
        # 策略层：派单规则 / 充电策略 / 电量保留。全部可由场景 overrides 覆盖，
        # 因为场景本来就能保存，所以"自定义策略"= 存一个场景，不需要第二套存档。
        self.dispatch_rule = str(self.overrides.get(
            "dispatch.policy", self.spec["dispatch"].get("policy", "nearest_first")))
        if self.dispatch_rule not in DISPATCH_RULES:
            raise ValueError("unknown dispatch.policy: %s (known: %s)"
                             % (self.dispatch_rule, ", ".join(sorted(DISPATCH_RULES))))
        self.charge_policy = str(self.overrides.get(
            "battery.charge_policy", self.spec["battery"].get("charge_policy", "opportunistic")))
        if self.charge_policy not in CHARGE_POLICIES:
            raise ValueError("unknown battery.charge_policy: %s (known: %s)"
                             % (self.charge_policy, ", ".join(CHARGE_POLICIES)))
        self.reserve_pct = float(self.overrides.get(
            "dispatch.battery_reserve_pct", self.reserve_pct))
        self.per_hour = float(self.overrides.get("demand.orders.per_hour",
                                                 self.demand["orders"]["per_hour"]))
        self.n_robots = self._fleet_size()
        self.run_id = "%s/%s" % (self.sc["id"], dt.datetime.now(dt.timezone.utc)
                                 .strftime("%Y%m%dT%H%M%SZ"))
        self.out = Path(out_dir) if out_dir else RUNS / self.sc["id"] / Path(self.run_id).name
        self.out.mkdir(parents=True, exist_ok=True)
        self.log = EventLog(self.out / "sim_events.jsonl", self.spec["output"]["event_ring"],
                            append=append_log)
        self.robots = self._make_robots()
        self.queue = []
        self.reserved = {}
        self.blocked_docks = set()
        self.stations = self._load_stations()
        # 两个覆盖项互不干扰：做"工位产能"对照实验时，不该顺手把月台门也改小，
        # 否则观察到的排队分不清是工位堵的还是月台堵的。
        if Simulation.station_slots_override:
            for v in self.stations.values():
                if v.get("kind") == "DOCK":
                    continue
                v["slots"] = int(Simulation.station_slots_override)
        if Simulation.dock_slots_override:
            for v in self.stations.values():
                if v.get("kind") == "DOCK":
                    n = max(1, int(Simulation.dock_slots_override))
                    v["slots"] = n
                    # 门的时间窗数组必须跟着改：只改 slots 不改数组，容量约束会
                    # 悄悄留在装载时的门数上（三组对照实验曾因此给出完全相同的
                    # 队列数字，等于这个开关没生效）。
                    cur = list(v.get("door_free_at") or [])
                    v["door_free_at"] = (cur + [0.0] * n)[:n]
        self.faults = self._disruptions()
        # 外部订单流（企业 CSV / WMS 适配器产出）：给了就不再合成订单。
        # 这样"真实数据接入"才是真的跑一遍，而不是拿 CSV 生成个差不多的泊松。
        self.ext = list(external_orders or [])
        self.ext_i = 0
        self.metrics = {}
        self.samples = []
        self.position_samples = []
        self.min_pair_m = float("inf")
        self.sep_violations = 0
        self.reservation_double = 0
        self.next_arrival = 0.0
        # ENERGY_TICK is a *window delta*, not a cumulative total: keeping the
        # previous cumulative counters makes S2 energy conservation recomputable.
        self._last_energy_out = 0.0
        self._last_motion = 0.0
        self._last_energy_in = 0.0
        self.max_queue_depth = 0
        # 死锁与电量守卫的可观测计数（要给 KPI 与守卫看，不能只存在日志里）
        self.yield_n = 0
        self.detour_n = 0
        self.jam_clear_n = 0
        self.max_blocked_s = 0.0
        self._max_blocked_agent = None
        self.energy_guard_n = 0
        self.wall0 = time.time()
        # step 化（ADR-GAME-01）：以下状态原是 run() 的局部变量，升为实例字段，
        # 才能让实时会话逐帧推进、让快照读到"现在是第几 tick"。初值仍由
        # _init_run() 统一重置，避免"__init__ 与 run 两处初始化"再次分叉。
        self.tick_i = 0
        self.t = 0.0
        self.n_ticks = int(self.horizon / self.tick)
        self.lam = self.per_hour / 3600.0
        self._order_n = 0
        self.done = False
        # step() 是否已经初始化过。没有这个标记时，"先 step 再 run"会把下一次
        # 到达时间用成 __init__ 的 0.0（不是随机抽样），首单凭空插在 t=0。
        self._started = False
        # 玩家命令队列（ADR-GAME-03）：批跑模式下恒为空，等价于空操作。
        self._cmd_queue = []
        # 定价与需求弹性（P3）：订单需求原本是外生的，改单价不影响单量，
        # "经营反馈环"就断了。这里把 per_hour 拆成 基准 × 需求倍数 × 价格因子，
        # 让 SET_DEMAND 与 SET_PRICE 各自可叠加、且都可复现。
        self._base_per_hour = float(self.demand["orders"]["per_hour"])
        self.demand_multiplier = (self.per_hour / self._base_per_hour
                                  if self._base_per_hour > 0 else 1.0)
        self.price_overrides = {}
        self.price_elasticity_k = 1.2
        self.min_demand_factor = 0.1
        self.sell_refund_pct = 0.5
        self.cost_spec = {}
        for name, attr in (("cost_spec.json", "cost_spec"), ("economy_spec.json", "econ_spec")):
            p = CONFIG / name
            if p.exists():
                setattr(self, attr, jload(p))
        es = getattr(self, "econ_spec", {}) or {}
        self.price_elasticity_k = float(es.get("price_elasticity_k", self.price_elasticity_k))
        self.min_demand_factor = float(es.get("min_demand_factor", self.min_demand_factor))
        self.sell_refund_pct = float(es.get("sell_refund_pct", self.sell_refund_pct))
        # 游戏营收口径的市场价倍率：只影响"按什么价收钱"，不改进货/成本/会计科目。
        # 默认为 1，批跑（演示会计口径）完全不受影响。
        self.tariff_multiplier = float(es.get("tariff_multiplier", 1.0))
        # 库存层（P6a / ADR-GAME-09）。数据与配置都由 gen_inventory.py 生成；
        # 缺文件时不假装有库存（那会静默把每单都判成缺货），而是置 inv_enabled=False，
        # 由 KPI 明确报 stockout_enabled=0。
        self._init_inventory()

    # ---------- 库存层（P6a）----------
    def _init_inventory(self):
        """装载库位级 SKU 账。**不消耗随机流、不读时钟**（ADR-GAME-09 决策四）。"""
        self.inv_enabled = False
        self.inv = {}            # sku -> {location: qty}
        self.inv_loc = {}        # location -> {"sku","role","warehouse"}
        self.inv_sku = {}        # sku -> {"reserve":[...], "pick_face":[...]}
        self.inv_cost = {}       # sku -> unit_cost
        self.inv_par = {}        # (sku, location) -> 期初水位（拣选面 par）
        self.inv_reorder = {}    # sku -> 基础补货点（factor 前）
        self.inv_pending = {}    # order_id -> {"sku","qty","location"}
        self.inv_hold_t = 0.0
        self.inv_stockout_n = 0
        self.inv_reorder_n = 0
        self.inv_refill_n = 0
        self.inv_units_out = 0
        self.inv_units_req = 0
        self.inv_holding = 0.0
        self.reorder_factor = 1.0
        self.reorder_factor_by_sku = {}
        # ---- P6b 批次层：inv 仍是"聚合结存"，lots 是它的明细。
        # 两套账同时存在就有对不上的风险，所以每次写都在 `_inv_lot_check()` 里断言
        # sum(lots) == inv —— 宁可当场 raise，也不要让期末对账时才发现差 3 件。
        self.lots = {}           # (sku, location) -> [{lot_id,qty,t_in,expiry_t}] 按 expiry 升序
        self.lot_heap = []       # (expiry_t, lot_id, sku, location)：到期扫描用，可惰性删除
        self.lot_seq = 0         # 新批次编号（新收货用 LOT-N……，期初用 LOT-I……，不撞号）
        self.lot_policy = "FEFO"  # 玩家可用 SET_LOT_POLICY 切到 FIFO
        self.lot_opened_n = 0
        self.lot_expired_n = 0
        self.lot_expired_units = 0.0
        self.lot_expired_value = 0.0
        self.fefo_violations = 0

        sp = CONFIG / "inventory_spec.json"
        spec_override = Simulation.inventory_spec_override
        slot_f = DATA / "inventory_slotting.json"
        init_f = DATA / "inventory_initial.json"
        master_f = DATA / "sku_master.json"
        if not (sp.exists() and slot_f.exists() and init_f.exists() and master_f.exists()):
            return False
        spec = spec_override if spec_override else jload(sp)
        slotting = jload(slot_f)
        initial = jload(init_f)
        master = jload(master_f)

        self.inv_spec = spec
        self.inv_hold_period_s = float(spec["holding"].get("period_s", 600))
        for row in slotting["locations"]:
            self.inv_loc[row["location_id"]] = {"sku": row["sku"], "role": row["role"],
                                                "warehouse": row["warehouse_id"]}
        self.inv_sku = {s: {"reserve": list(v["reserve"]),
                            "pick_face": list(v["pick_face"])}
                        for s, v in slotting["by_sku"].items()}
        self.inv_cost = {s["id"]: float(s["unit_cost"]) for s in master["skus"]}
        self.inv_shelf_life = {s["id"]: float(s.get("shelf_life_s") or 0.0) for s in master["skus"]}
        vel = {s["id"]: float(s.get("velocity") or 0.0) for s in master["skus"]}
        for sku, rows in initial["stock"].items():
            self.inv[sku] = {loc: float(q) for loc, q in rows.items()}
            for loc, q in rows.items():
                self.inv_par[(sku, loc)] = float(q)      # 期初水位 = 拣选面 par

        # ---- 期初批次（P6b）。expiry.enabled=false 时整层不装载，行为退回 P6a。
        exp = spec.get("expiry") or {}
        self.lot_expiry_enabled = bool(exp.get("enabled", False))
        self.lot_policy = str(exp.get("policy", "FEFO"))
        self.lot_waste_posting = str(exp.get("waste_posting", "none"))
        if self.lot_expiry_enabled:
            for sku, per_loc in (initial.get("lots") or {}).items():
                for loc, lst in per_loc.items():
                    for lot in lst:
                        self._lot_push(sku, loc, {"lot_id": lot["lot_id"],
                                                  "qty": float(lot["qty"]),
                                                  "t_in": float(lot.get("t_in", 0.0)),
                                                  "expiry_t": float(lot["expiry_t"])})
                        self.lot_opened_n += 1
            self._inv_lot_check(fatal=True)

        # 补货点：按"本 SKU 的期望小时出库量"推，不用"周均"——这套引擎的 horizon
        # 是 1 小时，周均在跑动中不可观测。写死成常量就等于没有补货逻辑。
        mix = self.overrides.get("demand.orders.mix", self.demand["orders"]["mix"])
        dec_share = sum(w for k, w in mix.items()
                        if k in ("OUTBOUND", "PICK", "TRANSFER", "REPLENISH"))
        total_w = sum(mix.values()) or 1.0
        out_share = dec_share / total_w
        safety_f = float(spec["reorder"]["safety_stock_factor"])
        rp_f = float(spec["reorder"]["reorder_point_factor"])
        for sku in sorted(self.inv):
            exp_h = self.per_hour * out_share * vel.get(sku, 0.0)
            self.inv_reorder[sku] = max(1.0, safety_f * exp_h + rp_f * exp_h)
        self.inv_enabled = True
        return True

    def inv_on_hand(self, sku):
        return sum(self.inv.get(sku, {}).values())

    def inv_value(self):
        return sum(q * self.inv_cost.get(sku, 0.0)
                   for sku, rows in self.inv.items() for q in rows.values())

    def inv_reorder_point(self, sku):
        factor = float(self.reorder_factor_by_sku.get(sku, self.reorder_factor))
        return self.inv_reorder.get(sku, 0.0) * factor

    # ---------- 批次层（P6b）：FEFO / 到期报废 ----------
    # 设计要点：`inv`（聚合结存）仍是唯一被广泛读取的口径，`lots` 是它的明细。
    # 两套账必然有对不上的风险，所以每次写都断言 sum(lots) == inv；宁可当场 raise，
    # 也不要等到期末对账时才发现少了 3 件（那时已经无法归因）。
    def _lot_push(self, sku, loc, lot):
        arr = self.lots.setdefault((sku, loc), [])
        arr.append(lot)
        arr.sort(key=lambda l: (l["expiry_t"], l["lot_id"]))
        heapq.heappush(self.lot_heap, (lot["expiry_t"], lot["lot_id"], sku, loc))

    def _inv_lot_check(self, fatal=False):
        """sum(lots) 必须等于聚合结存。返回不一致项，fatal=True 时直接 raise。"""
        bad = []
        for (sku, loc), arr in self.lots.items():
            s = sum(l["qty"] for l in arr)
            agg = self.inv.get(sku, {}).get(loc, 0.0)
            if abs(s - agg) > 1e-9:
                bad.append((sku, loc, round(s, 6), round(agg, 6)))
        if bad and fatal:
            raise ValueError("批次明细与聚合结存不一致（%d 处），前 3 处：%s"
                             % (len(bad), bad[:3]))
        return bad

    def _lot_order(self, arr):
        """FEFO 按效期、FIFO 按入库时间。同键用 lot_id 兜底，保证确定性。"""
        if self.lot_policy == "FIFO":
            return sorted(arr, key=lambda l: (l["t_in"], l["lot_id"]))
        return sorted(arr, key=lambda l: (l["expiry_t"], l["lot_id"]))

    def _lot_consume(self, sku, loc, qty, t):
        """按当前策略扣批次，返回 [(lot_id, qty, expiry_t)]；不足额即 raise。"""
        arr = self.lots.get((sku, loc)) or []
        need, out = float(qty), []
        for lot in self._lot_order(arr):
            if need <= 1e-9:
                break
            take = min(need, lot["qty"])
            if take <= 1e-9:
                continue
            lot["qty"] -= take
            need -= take
            out.append((lot["lot_id"], take, lot["expiry_t"]))
        if need > 1e-9:
            # 聚合账说够、批次账说不够：两套账已经分叉，不许硬扣成负数
            raise ValueError("批次不足以扣减：%s@%s 缺 %s" % (sku, loc, round(need, 6)))
        self.lots[(sku, loc)] = [l for l in arr if l["qty"] > 1e-9]
        # FEFO 下"先扣晚到期的、却留着早到期的"就是违规，计数而不是静默。
        # 正常实现应恒为 0；一旦排序写错，这个 KPI 会立刻非零（check_fefo 断言它）。
        if out and self.lot_policy == "FEFO":
            first = min(e for _, _, e in out)
            if any(l["expiry_t"] < first - 1e-9 for l in self.lots[(sku, loc)]):
                self.fefo_violations += 1
        return out

    def _inv_expire_sweep(self, t):
        """把到期批次报废。用最小堆 + 惰性删除，不遍历全库（392 库位 × 3600 tick 太贵）。

        "到期"按 `expiry_t <= t` 判：批次在到期那一刻就不可再发，而不是等下一次
        盘点。否则同一 tick 里"刚过期"的货还能被拣出去，效期就成了装饰。
        """
        if not self.lot_expiry_enabled:
            return
        while self.lot_heap and self.lot_heap[0][0] <= t + 1e-9:
            expiry, lot_id, sku, loc = heapq.heappop(self.lot_heap)
            arr = self.lots.get((sku, loc)) or []
            hit = next((l for l in arr if l["lot_id"] == lot_id), None)
            if hit is None or hit["qty"] <= 1e-9:
                continue                       # 已扣完/已报废：堆里的惰性条目
            q = hit["qty"]
            hit["qty"] = 0.0
            self.lots[(sku, loc)] = [l for l in arr if l["qty"] > 1e-9]
            self.inv.setdefault(sku, {})
            before = self.inv[sku].get(loc, 0.0)
            self.inv[sku][loc] = max(0.0, before - q)
            value = q * self.inv_cost.get(sku, 0.0)
            self.lot_expired_n += 1
            self.lot_expired_units += q
            self.lot_expired_value += value
            self.log.emit(t, "STOCK_EXPIRED", sku=sku, location=loc, lot_id=lot_id,
                          qty=q, expiry_t=expiry, value=round(value, 6),
                          on_hand_after=round(self.inv[sku][loc], 6),
                          waste_posting=self.lot_waste_posting)

    def _inv_dec(self, sku, loc, qty, order, cause):
        cur = self.inv.setdefault(sku, {}).get(loc, 0.0)
        if cur + 1e-9 < qty:
            # 先扣减再发事件，扣不动就是逻辑错，不许"扣成负数"糊过去
            raise ValueError("库存不足却要求扣减：%s@%s 现有 %s 需 %s"
                             % (sku, loc, cur, qty))
        self.inv[sku][loc] = cur - qty
        self.inv_units_out += qty
        extra = {}
        if self.lot_expiry_enabled:
            consumed = self._lot_consume(sku, loc, qty, self.t)
            # 批次明细写进事件：这样"这一单从哪批货出的"可查，FEFO 才不只是口头承诺
            extra["lots"] = [{"lot_id": lid, "qty": round(q, 6), "expiry_t": e}
                             for lid, q, e in consumed]
        self.log.emit(self.t, "STOCK_DECREMENT", sku=sku, location=loc, qty=qty,
                      order_id=(order or {}).get("order_id"), cause=cause,
                      on_hand_after=round(self.inv[sku][loc], 6), **extra)
        return self.inv[sku][loc]

    def _inv_inc(self, sku, loc, qty, order_id=None, cause="inbound",
                 etype="STOCK_RECEIPT"):
        self.inv.setdefault(sku, {})
        self.inv[sku][loc] = self.inv[sku].get(loc, 0.0) + qty
        extra = {}
        if self.lot_expiry_enabled:
            # 新批次：效期 = 现在 + 该 SKU 的保质期。**不再发 LOT_OPENED**——
            # STOCK_RECEIPT 自带 lot_id/expiry_t，事件流已经足以重建批次，多发一条纯噪音。
            shelf = float(self.inv_shelf_life.get(sku, 0.0))
            self.lot_seq += 1
            lot = {"lot_id": "LOT-N%06d" % self.lot_seq, "qty": float(qty),
                   "t_in": round(self.t, 3),
                   "expiry_t": round(self.t + shelf if shelf > 0 else float("inf"), 3)}
            self._lot_push(sku, loc, lot)
            self.lot_opened_n += 1
            extra = {"lot_id": lot["lot_id"], "expiry_t": lot["expiry_t"]}
        self.log.emit(self.t, etype, sku=sku, location=loc, qty=qty,
                      order_id=order_id, cause=cause,
                      on_hand_after=round(self.inv[sku][loc], 6), **extra)
        return self.inv[sku][loc]

    def _inv_move_lots(self, sku_src, src, sku_dst, dst, qty):
        """内部移库：批次跟着走，**但不重算效期**。

        这是仓储里一个经典错：货搬到拣选面就当成新收货、重新给一个保质期，
        结果临期货永远不过期。这里显式保留 expiry_t。
        """
        if not self.lot_expiry_enabled:
            return []
        moved = self._lot_consume(sku_src, src, qty, self.t)
        for lid, q, e in moved:
            self._lot_push(sku_dst, dst, {"lot_id": lid, "qty": float(q),
                                          "t_in": round(self.t, 3), "expiry_t": e})
        return moved

    def _inv_settle(self, order):
        """完成时结算库存；返回 (delivered, ok)。ok=False 表示缺货不得完成。

        **先看清这套引擎里各类型到底怎么走**（读 `new_order` 的 rules 才知道，
        方案书 §5.2 的抽象描述对不上具体实现）：
          - OUTBOUND：货架 → 出库月台    → 只在取货位**扣**
          - PICK：货架 → 打包            → 只在取货位**扣**
          - INBOUND / REPLENISH：月台 → 货架 → 只在交货位**加**（补货是从月台补货架，
            不是"储备位补拣选面"——后者由内部 TRANSFER 单承担）
          - TRANSFER：货架 → 货架        → 源扣、目标加
        订单**不带 sku 字段**：SKU 由库位反查（ADR-GAME-09 决策四），订单结构与
        CSV 适配器都不用动，也不会多消耗一次 rng。
        """
        if not self.inv_enabled:
            return 1.0, True
        typ = order["type"]
        need = float(order.get("qty") or 1.0)

        # 内部移库（储备位 → 拣选面）：只发一条 PICKFACE_REFILL，不再发通用收/发事件。
        # 这样"拣选面库存只能由显式转移改变"这条不变量在事件流里是**一眼可查**的。
        if order.get("internal") and typ == "TRANSFER":
            src, dst = order["pick_loc"], order["drop_loc"]
            si, di = self.inv_loc.get(src), self.inv_loc.get(dst)
            if si is None or di is None:
                raise ValueError("内部补货单 %s 的库位没有映射：%r → %r"
                                 % (order.get("order_id"), src, dst))
            avail = self.inv.get(si["sku"], {}).get(src, 0.0)
            qty = min(need, avail)
            if qty <= 1e-9:
                self.log.emit(self.t, "STOCKOUT", sku=si["sku"], location=src,
                              order_id=order.get("order_id"), requested=need,
                              available=avail, shortfall=need, policy="refill_empty")
                self.inv_stockout_n += 1
                self.inv_pending.pop(order.get("order_id"), None)
                return 0.0, False
            self.inv[si["sku"]][src] = avail - qty
            self.inv.setdefault(di["sku"], {})
            self.inv[di["sku"]][dst] = self.inv[di["sku"]].get(dst, 0.0) + qty
            moved = self._inv_move_lots(si["sku"], src, di["sku"], dst, qty)
            self.inv_refill_n += 1
            extra = {}
            if self.lot_expiry_enabled:
                # 移库的批次明细：验"搬过去还是原来的效期"，不重算
                extra["lots"] = [{"lot_id": lid, "qty": round(q, 6), "expiry_t": e}
                                 for lid, q, e in moved]
            self.log.emit(self.t, "PICKFACE_REFILL", sku=si["sku"],
                          reserve_location=src, pickface_location=dst, qty=qty,
                          order_id=order.get("order_id"), **extra)
            self.inv_pending.pop(order.get("order_id"), None)
            self._inv_reorder_check(si["sku"], src)
            return qty, True

        dec = typ in ("OUTBOUND", "PICK", "TRANSFER")
        inc = typ in ("INBOUND", "REPLENISH", "TRANSFER")
        sku_src = None
        if dec:
            src = order["pick_loc"]
            info = self.inv_loc.get(src)
            if info is None:
                raise ValueError("订单 %s 的取货位 %r 没有库存映射（未知即抛错）"
                                 % (order.get("order_id"), src))
            sku_src = info["sku"]
            self.inv_units_req += need
            avail = self.inv.get(sku_src, {}).get(src, 0.0)
            if avail + 1e-9 >= need:
                self._inv_dec(sku_src, src, need, order, "pick")
                delivered = need
            elif avail > 1e-9 and self.inv_spec["stockout"].get("partial_fill"):
                self._inv_dec(sku_src, src, avail, order, "pick_partial")
                self.log.emit(self.t, "STOCKOUT", sku=sku_src, location=src,
                              order_id=order.get("order_id"), requested=need,
                              available=avail, shortfall=need - avail,
                              policy="partial_fill")
                self.inv_stockout_n += 1
                delivered = avail
            else:
                self.log.emit(self.t, "STOCKOUT", sku=sku_src, location=src,
                              order_id=order.get("order_id"), requested=need,
                              available=avail, shortfall=need,
                              policy=self.inv_spec["stockout"].get("policy", "fail_order"))
                self.inv_stockout_n += 1
                return 0.0, False
        else:
            delivered = need
        if inc:
            dst = order["drop_loc"]
            info2 = self.inv_loc.get(dst)
            if info2 is None:
                raise ValueError("订单 %s 的交货位 %r 没有库存映射（未知即抛错）"
                                 % (order.get("order_id"), dst))
            sku_dst = info2["sku"]
            etype = "STOCK_REPLENISH_ARRIVED" if typ == "REPLENISH" else "STOCK_RECEIPT"
            self._inv_inc(sku_dst, dst, delivered, order.get("order_id"),
                          typ.lower(), etype)
        if dec and sku_src:
            self._inv_reorder_check(sku_src, order["pick_loc"])
        return delivered, True

    def _inv_make_order(self, oid, src, dst, qty, typ="TRANSFER", internal=True):
        """造一张内部补货单。**不碰 rng**：多抽一次随机数会打乱后续需求流。"""
        # 一端可能是月台（REPLENISH 从月台补货架）：月台不在库存映射里，
        # 所以"哪个端有映射就用哪个端的仓"，不能两端都要求有映射。
        src_info, dst_info = self.inv_loc.get(src), self.inv_loc.get(dst)
        if src_info is None and dst_info is None:
            raise ValueError("内部补货单两端都没有库存映射：%r → %r" % (src, dst))
        wid = (src_info or dst_info)["warehouse"]
        if src_info and dst_info and dst_info["warehouse"] != src_info["warehouse"]:
            # P6a 不做多仓调拨：跨仓补货单在引擎里没有合理路径语义，直接拒绝
            raise ValueError("内部补货单跨仓（%s→%s），P6a 不支持多仓调拨" % (src, dst))
        s_loc = self.world.locs[wid][src]
        d_loc = self.world.locs[wid][dst]
        return {"order_id": oid, "type": typ, "warehouse": wid,
                "pick_loc": src, "drop_loc": dst,
                "pick_xy": tuple(s_loc["access_point"]),
                "drop_xy": tuple(d_loc["access_point"]),
                # 内部补货是后台任务：必须让客户订单先派（P3 最低优先级）。
                # 用 P2 时补货单会和客户单抢运力，出现"补货点越高、完成单越少"
                # 的反常现象（实测 90→37），那是把内部作业当成了主营业务。
                "t_arrive": self.t, "priority": "P3",
                # 内部移库不该产生"迟到"：deadline 拉到无限远
                "deadline": float("inf"), "reassignments": 0,
                "qty": qty, "internal": True}

    def _inv_reorder_check(self, sku, loc):
        """补货判定。**只对本次被扣减的 SKU/库位做**（不遍历无序集合，§5.3）。

        这里踩过一个设计坑，记下来：最初把"补货"实现成"储备位 → 拣选面"的内部移库，
        结果 REORDER_OK 反着走——补货点越高、缺货越多。原因是订单从 `pick_loc`
        取货，而 `pick_loc` 绝大多数是储备位：把库存搬去拣选面，等于把货搬离了
        真正被取的地方。现在按引擎既有语义改成**从月台补货到被扣减的那个库位**
        （`REPLENISH` 单本来就是 月台 → 货架），补货才真的降缺货。
        拣选面只做"跌破水位时从同仓储备位补"的小批量前置，且不跨仓。
        """
        if not self.inv_enabled:
            return
        if self.inv_spec["reorder"].get("reorder_check") != "on_stock_change":
            return
        on_hand = self.inv.get(sku, {}).get(loc, 0.0)
        info = self.inv_loc.get(loc) or {}
        wid = info.get("warehouse")
        role = info.get("role")

        # 1) 拣选面：跌破最低水位 → 从同仓储备位补（内部移库，不发收入）
        if role == "pick_face":
            base = self.inv_par.get((sku, loc), 0.0)
            ratio = float(self.inv_spec["pick_face"].get("min_fill_ratio", 0.3))
            reserve = [l for l in self.inv_sku.get(sku, {}).get("reserve", [])
                       if self.inv_loc.get(l, {}).get("warehouse") == wid]
            if reserve and on_hand < max(1.0, base * ratio):
                if not any(p.get("sku") == sku and p.get("kind") == "refill"
                           for p in self.inv_pending.values()):
                    src = reserve[0]
                    if self.inv.get(sku, {}).get(src, 0.0) > 1.0:
                        qty = max(1.0, base - on_hand)
                        oid = "RPL-%05d" % (self.inv_reorder_n + 1)
                        order = self._inv_make_order(oid, src, loc, qty)
                        self.queue.append(order)
                        self.inv_pending[oid] = {"sku": sku, "qty": qty, "location": loc,
                                                 "kind": "refill"}
                        self.inv_reorder_n += 1
                        self.log.emit(self.t, "REORDER_TRIGGERED", sku=sku, location=loc,
                                      on_hand=round(on_hand, 6),
                                      reorder_point=round(base * ratio, 6),
                                      replenish_qty=qty, replenish_order_id=oid,
                                      kind="pickface")
            return

        # 2) 储备位：跌破补货点 → 从月台补货到本位（REPLENISH：月台 → 货架）
        rp = self.inv_reorder_point(sku)
        if on_hand > rp:
            return
        if any(p.get("sku") == sku and p.get("kind") == "dock"
               for p in self.inv_pending.values()):
            return
        docks = [d["id"] for d in self.world.by_kind.get(wid, {}).get("INBOUND", [])]
        if not docks:
            return
        qty = max(1.0, round(2.0 * rp - on_hand))
        oid = "RPL-%05d" % (self.inv_reorder_n + 1)
        order = self._inv_make_order(oid, sorted(docks)[0], loc, qty, typ="REPLENISH")
        self.queue.append(order)
        self.inv_pending[oid] = {"sku": sku, "qty": qty, "location": loc, "kind": "dock"}
        self.inv_reorder_n += 1
        self.log.emit(self.t, "REORDER_TRIGGERED", sku=sku, location=loc,
                      on_hand=round(on_hand, 6), reorder_point=round(rp, 6),
                      replenish_qty=qty, replenish_order_id=oid, kind="dock")

    def _inv_accrue_holding(self, t_end):
        """按仿真小时计提持有成本。用 tick 时间，不读墙钟（§5.6）。"""
        if not self.inv_enabled:
            return
        dt_h = (t_end - self.inv_hold_t) / 3600.0
        if dt_h <= 1e-12:
            return
        value = self.inv_value()
        rate = float(self.inv_spec["holding"]["holding_rate_per_year"])
        amount = value * rate * dt_h / 8760.0
        self.inv_holding += amount
        self.log.emit(t_end, "HOLDING_ACCRUED", t0=round(self.inv_hold_t, 3),
                      t1=round(t_end, 3), hours=round(dt_h, 6),
                      on_hand_value=round(value, 6), amount=round(amount, 6))
        self.inv_hold_t = t_end


    # ---------- 工位层（M1-02）：process_spec 的服务时间真的进 tick ----------
    def _load_stations(self):
        """把 process_spec 的工位读成"服务台"：容量 + 服务时间分布。

        id 与 data/layout.json 的 PACKING/SORTING 库位完全同名（已核对），
        所以可以直接用订单的 pick_loc/drop_loc 查表，不需要额外映射表。
        缺文件时返回空字典 → 引擎退回旧的固定 dwell 行为，旧产物仍可复现。
        """
        path = CONFIG / "process_spec.json"
        if not path.exists():
            return {}
        try:
            ps = jload(path)
        except Exception:
            return {}
        out = {}
        for st in ps.get("stations", []):
            # process_spec.stations 是扁平结构：service_time_s / parallel_slots /
            # buffer 都在工位对象本层（不是嵌套在 process 里）。
            svc = st.get("service_time_s") or {}
            mean = svc.get("mean")
            if not mean or mean <= 0:
                continue
            out[st["id"]] = {
                "kind": st.get("kind") or "STATION",
                "mean": float(mean),
                "cv": float(svc.get("cv", 0.0) or 0.0),
                "slots": max(1, int(st.get("parallel_slots") or 1)),
                "buffer": int((st.get("buffer") or {}).get("capacity") or 0),
                "busy": 0,
                "queue": [],
                "served": 0,
                "busy_s": 0.0,
                "wait_sum": 0.0,
                "wait_n": 0,
                "qmax": 0,
            }

        # 拣选池：SHELF 取货没有独立工位（392 个货架），但拣选人力是共享资源。
        # 用 data/areas.json 的 PICK_FACE 区（W 有 4 个并行位 / 45 s）当服务台，
        # 这样"每次取货都要占一个拣选位"就能被排队与利用率真实反映。
        areas_path = DATA / "areas.json"
        if areas_path.exists():
            try:
                for a in jload(areas_path).get("areas", []):
                    if a.get("kind") != "PICK_FACE":
                        continue
                    proc = a.get("process") or {}
                    svc = proc.get("service_time_s") or {}
                    mean = svc.get("mean")
                    wid = a.get("warehouse") or str(a.get("id", "")).split("-")[0]
                    if not mean or not wid:
                        continue
                    out["@PICK:" + wid] = {
                        "kind": "PICKING",
                        "mean": float(mean),
                        "cv": float(svc.get("cv", 0.0) or 0.0),
                        "slots": max(1, int(proc.get("parallel_slots") or 1)),
                        "buffer": int((proc.get("buffer") or {}).get("capacity") or 0),
                        "busy": 0, "queue": [], "served": 0, "busy_s": 0.0,
                        "wait_sum": 0.0, "wait_n": 0, "qmax": 0,
                    }
            except Exception:
                pass
        self._load_docks(out)
        return out

    def _dock_times(self):
        """装卸工时直接取 process_spec 的 DOCK_IN/DOCK_OUT 段，不另抄一份数字。

        过程规格里已经写着"叉车卸一车 180 s、装一车 150 s"，在引擎里再写一遍
        就必然两边漂移（这正是 check_claims 一直在防的那类问题）。
        """
        times = {"INBOUND": 180.0, "OUTBOUND": 150.0}   # 无过程规格时的兜底
        cv = 0.2
        path = CONFIG / "process_spec.json"
        if not path.exists():
            return times, cv
        try:
            ps = jload(path)
        except Exception:
            return times, cv
        conf = ps.get("docks") or {}
        if conf.get("cv") is not None:
            cv = float(conf["cv"])
        for step in ps.get("process_steps") or []:
            t = step.get("time_s")
            if not t:
                continue
            if step.get("from") == "DOCK_IN":
                times["INBOUND"] = float(t)
            elif step.get("to") == "DOCK_OUT":
                times["OUTBOUND"] = float(t)
        return times, cv

    def _dock_slot(self, st, t):
        """找一扇此刻空着的门；没有返回 None。"""
        for i, free_at in enumerate(st["door_free_at"]):
            if free_at <= t + 1e-9:
                return i
        return None

    def _load_docks(self, out):
        """月台门 = 服务台：并行位就是这区的实际门数。

        以前月台只有"能被封"这一个行为：车到了、停 dwell、走人，装卸从来不需要
        时间，也不需要占用一扇门。于是 5 扇门和 1 扇门在仿真里毫无区别，月台
        永远不是瓶颈。这里让每次装卸真的占一扇门、真的花 150–180 s，门外没位
        就排队——和工位走同一套 STATION_ENTER/QUEUED/EXIT 事件。
        """
        times, cv = self._dock_times()
        for wid, wh in self.world.warehouses.items():
            for dk in wh.get("docks") or []:
                did = dk.get("id")
                if not did:
                    continue
                kind = (dk.get("kind") or "OUTBOUND").upper()
                mean = times.get(kind, times["OUTBOUND"])
                doors = len(dk.get("doors") or [])
                out[did] = {
                    "kind": "DOCK",
                    "dock_kind": kind,
                    "warehouse": wid,
                    # 门是"时间窗"资源：一次装卸占用 150–180 s，期间这扇门不能再
                    # 接一单。用空闲时刻数组表示，而不是让车在门口干等——车在门口
                    # 干等会把 1.5 m 宽的月台走廊堵死（实测 peak 59→35 单）。
                    "door_free_at": [0.0] * max(1, len(dk.get("doors") or [])),
                    "mean": float(mean),
                    "cv": float(cv),
                    "slots": max(1, doors),     # 一扇门一个并行位
                    "doors": max(1, doors),
                    "buffer": 0,
                    "busy": 0, "queue": [], "served": 0, "busy_s": 0.0,
                    "wait_sum": 0.0, "wait_n": 0, "qmax": 0,
                    "closed": False,            # 封锁时置位：门外排队的人要改派
                }

    def _station_id(self, r, next_state):
        """订单点位 → 服务台 id。精确工位优先，否则拣选单落到本仓拣选池。"""
        if not r.order:
            return None
        loc = r.order["pick_loc"] if next_state == "PICKING" else r.order["drop_loc"]
        if loc in self.stations:
            return loc
        wid = r.order.get("warehouse")
        if next_state == "PICKING" and wid and ("@PICK:" + wid) in self.stations:
            return "@PICK:" + wid
        return None

    def _sample_service(self, st):
        """对数正态采样（mean/cv 参数化）。cv=0 时退化为确定值。"""
        mean, cv = st["mean"], st["cv"]
        if cv <= 1e-9:
            return mean
        sig2 = math.log(1.0 + cv * cv)
        mu = math.log(mean) - 0.5 * sig2
        u1 = max(self.svc_rng.next(), 1e-12)
        u2 = self.svc_rng.next()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        return max(1e-3, math.exp(mu + math.sqrt(sig2) * z))

    def _clear_jam(self, r, t):
        """把顶死的车挪到最近的空闲待命点（模拟人工清障）。

        只在"退让 + 绕行都失败"且已顶死 5 分钟时触发，一次运行通常 0–2 次。
        选点要求：距其他所有车 >= min_sep，避免清障本身造成碰撞。
        """
        others = [(o.id, o.pos) for o in self.robots.values() if o.id != r.id]
        homes = [loc["access_point"]
                 for loc in self.world.by_kind.get(r.wid, {}).get("HOME", [])]
        cands = sorted(homes, key=lambda p: math.dist(p, r.pos))
        spot = next((p for p in cands
                     if all(math.dist(p, q) >= self.min_sep for _, q in others)), None)
        if spot is None:
            return
        old = [round(v, 2) for v in r.pos]
        self._release_order(r, t, "JAM_CLEAR")
        r.pos = tuple(spot)
        r.route, r.arc, r.route_len = [], 0.0, 0.0
        r.blocked_s = 0.0
        r.blocked_by = None
        r.set_state("IDLE", self.log, t)
        self.jam_clear_n += 1
        self.log.emit(t, "JAM_CLEARED", agent=r.id, blocked_by=None,
                      from_pos=old, to_pos=[round(v, 2) for v in spot],
                      reason="single_lane_head_on")

    def _leg_goal(self, r):
        """当前这一段行程的终点（绕行时用）。"""
        if r.state == "MOVING_PICK" and r.order:
            return r.order["pick_xy"]
        if r.state == "MOVING_DROP" and r.order:
            return r.order["drop_xy"]
        if r.state == "RETURNING":
            return r.home
        return None

    def _station_of(self, r):
        if not r.order:
            return None
        loc = r.order["pick_loc"] if r.state == "MOVING_PICK" else r.order["drop_loc"]
        return self.stations.get(loc)

    def _enter_station(self, r, t, next_state):
        """到达工位点：有空位就服务，没空位就排队（真的会等）。"""
        loc = self._station_id(r, next_state)
        st = self.stations.get(loc) if loc else None
        if st is not None and st.get("kind") == "DOCK" and not st.get("closed"):
            # 门已在派单时占好：这里只做交接（把货放到门口），不占门、不排队。
            r.station = None
            r.set_state(next_state, self.log, t)
            r.dwell_until = t + self.dwell
            self.log.emit(t, "DOCK_HANDOFF", agent=r.id, dock=loc,
                          order_id=(r.order or {}).get("order_id"))
            return
        if st is not None and st.get("closed"):
            # 门被封锁：真实现场是让车改走别的门，而不是在门口排到封锁解除。
            # 订单退回队列，_assign 里的 _replan_blocked_docks 会换一个开着的门。
            self.log.emit(t, "DOCK_REFUSED", agent=r.id, dock=loc,
                          order_id=(r.order or {}).get("order_id"))
            self._release_order(r, t, "DOCK_BLOCKED")
            return
        if st is None:                      # 无工位定义 → 旧行为
            r.station = None
            r.set_state(next_state, self.log, t)
            r.dwell_until = t + self.dwell
            return

        if st["busy"] < st["slots"]:
            st["busy"] += 1
            st["served"] += 1
            r.station = loc
            dur = self._sample_service(st)
            st["busy_s"] += dur
            r.set_state(next_state, self.log, t)
            r.dwell_until = t + dur
            st["qmax"] = max(st["qmax"], len(st["queue"]))
            self.log.emit(t, "STATION_ENTER", agent=r.id, station=loc,
                          service_s=round(dur, 4), queue=len(st["queue"]))
            return

        r.station = None
        r.next_station_state = next_state
        r.queued_since = t
        st["queue"].append(r.id)
        st["qmax"] = max(st["qmax"], len(st["queue"]))
        r.set_state("WAITING", self.log, t)
        self.log.emit(t, "STATION_QUEUED", agent=r.id, station=loc,
                      queue_len=len(st["queue"]), slots=st["slots"])

    def _station_retry(self, r, t):
        """排队中的机器人每 tick 争一次：严格 FIFO，前一个没进就不许插队。"""
        if not r.order or not r.next_station_state:
            return
        next_state = r.next_station_state
        loc = self._station_id(r, next_state)
        st = self.stations.get(loc) if loc else None
        if st is None:
            r.set_state(next_state, self.log, t)
            r.dwell_until = t + self.dwell
            return
        if st.get("closed"):
            self.log.emit(t, "DOCK_REFUSED", agent=r.id, dock=loc,
                          order_id=(r.order or {}).get("order_id"))
            self._release_order(r, t, "DOCK_BLOCKED")
            return
        if not st["queue"] or st["queue"][0] != r.id:
            return
        if st["busy"] >= st["slots"]:
            return
        st["queue"].pop(0)
        st["busy"] += 1
        st["served"] += 1
        waited = max(0.0, t - r.queued_since)
        st["wait_sum"] += waited
        st["wait_n"] += 1
        dur = self._sample_service(st)
        st["busy_s"] += dur
        r.station = loc
        r.next_station_state = None
        r.set_state(next_state, self.log, t)
        r.dwell_until = t + dur
        self.log.emit(t, "STATION_ENTER", agent=r.id, station=loc,
                      service_s=round(dur, 4), queue=len(st["queue"]),
                      waited_s=round(waited, 4))

    def _release_station(self, r, t):
        if not r.station:
            return
        st = self.stations.get(r.station)
        if st and st["busy"] > 0:
            st["busy"] -= 1
        self.log.emit(t, "STATION_EXIT", agent=r.id, station=r.station)
        r.station = None

    def _drop_from_queue(self, r):
        if not r.next_station_state:
            return
        next_state = r.next_station_state
        loc = self._station_id(r, next_state)
        st = self.stations.get(loc) if loc else None
        if st and r.id in st["queue"]:
            st["queue"].remove(r.id)
        r.next_station_state = None

    def _fleet_size(self):
        for key in ("fleet.size", "fleet.deployment"):
            v = self.overrides.get(key)
            if isinstance(v, int):
                return v
        return sum(d["count"] for d in self.fleet["deployment"])

    def _make_robots(self):
        homes = []
        for wid, w in self.world.warehouses.items():
            for r in w["spawn"]["robots"]:
                homes.append((r["id"], wid, (r["position"][0], r["position"][2]),
                              r.get("battery", 85)))
        # scale up/down by cloning home positions (keeps them distinct via an offset ring)
        if self.n_robots > len(homes):
            base = list(homes)
            i = 0
            while len(homes) < self.n_robots:
                rid, wid, xy, soc = base[i % len(base)]
                k = len(homes) // len(base)
                homes.append(("%s-X%02d" % (rid, k), wid,
                              (xy[0] + 2.0 * k * (1 if xy[0] < 50 else -1),
                               xy[1] + 1.6 * k), soc))
                i += 1
        else:
            homes = homes[:self.n_robots]
        b = self.spec["battery"]
        soc0 = b["initial_soc_pct"] / 100.0 * self.model["battery_kwh"]
        low = b.get("low_soc_pct", 22) / 100.0 * self.model["battery_kwh"]
        every = b.get("low_soc_robot_every", 7)
        robots = {}
        for i, (rid, wid, xy, _) in enumerate(homes):
            r = Robot(rid, wid, xy, low if i % every == 0 else soc0, self.model)
            r.charger_id = "%s-CHG-%02d" % (wid, (i % 6) + 1)
            robots[rid] = r
        return robots

    def _disruptions(self):
        out = []
        for d in self.sc.get("disruptions", []):
            typ = d.get("type")
            if typ == "robot_down":
                out.append({"type": typ, "agent": d.get("agent"),
                            "start": float(d.get("start_s", 0)),
                            "end": float(d.get("start_s", 0)) + float(d.get("duration_s", 600))})
            elif typ == "dock_blocked":
                out.append({"type": typ, "dock": d.get("dock"),
                            "start": float(d.get("start_s", 0)),
                            "end": float(d.get("start_s", 0)) + float(d.get("duration_s", 1800))})
        return out

    # ---------- demand ----------
    def _pick_loc(self, wid, kind, rng):
        pool = self.world.by_kind[wid].get(kind) or self.world.by_kind[wid].get("SHELF", [])
        if not pool:
            return None
        return rng.choice(pool)

    def new_order(self, t):
        mix = self.overrides.get("demand.orders.mix", self.demand["orders"]["mix"])
        keys, weights = list(mix), [mix[k] for k in mix]
        p = self.rng.next() * sum(weights)
        typ = keys[-1]
        for k, wgt in zip(keys, weights):
            p -= wgt
            if p <= 0:
                typ = k
                break
        wid = self.rng.choice(list(self.world.warehouses))
        rules = {"INBOUND": ("INBOUND", "SHELF"), "REPLENISH": ("INBOUND", "SHELF"),
                 "OUTBOUND": ("SHELF", "OUTBOUND"), "PICK": ("SHELF", "PACKING"),
                 "TRANSFER": ("SHELF", "SHELF")}
        fk, tk = rules.get(typ, ("SHELF", "SHELF"))
        src = self._pick_loc(wid, fk, self.rng)
        pool = [loc for loc in self.world.by_kind[wid].get(tk, [])
                if loc["id"] != (src or {}).get("id")]
        dst = self.rng.choice(pool) if pool else None
        if not src or not dst:
            return None
        p = self.rng.next()
        pri = "P1" if p < 0.1 else ("P2" if p < 0.7 else "P3")
        return {"order_id": "ORD-%05d" % (self._order_n + 1), "type": typ,
                "warehouse": wid, "pick_loc": src["id"], "drop_loc": dst["id"],
                "pick_xy": tuple(src["access_point"]), "drop_xy": tuple(dst["access_point"]),
                "t_arrive": t, "priority": pri,
                "deadline": t + self.demand["orders"]["deadline_s"][pri],
                "reassignments": 0}

    # ---------- dispatch ----------
    def _start_charge(self, r, t, mode="opportunistic_inplace"):
        """就地充电（项目原有的 opportunistic 模式），抽出来给两处复用。"""
        if r.state == "CHARGING":
            return
        r.set_state("CHARGING", self.log, t)
        r.charging_since = t
        self.log.emit(t, "CHARGE_START", agent=r.id, soc_pct=round(r.soc_pct, 3),
                      charger_id=getattr(r, "charger_id", "inplace"), mode=mode,
                      pos=[round(v, 2) for v in r.pos])

    def _replan_blocked_docks(self, t, order):
        """A blocked dock is a real obstacle: reroute delivery to an open one."""
        if order["drop_loc"] not in self.blocked_docks:
            return True
        wid = order["warehouse"]
        pool = [loc for loc in (self.world.by_kind[wid].get("OUTBOUND", [])
                                + self.world.by_kind[wid].get("INBOUND", []))
                if loc["id"] not in self.blocked_docks]
        if not pool:
            return False
        new = self.rng.choice(pool)
        old = order["drop_loc"]
        order["drop_loc"] = new["id"]
        order["drop_xy"] = tuple(new["access_point"])
        self.log.emit(t, "REPLAN", order_id=order["order_id"], reason="dock_blocked",
                      from_loc=old, to_loc=new["id"])
        return True

    def _assign(self, t):
        if not self.queue:
            return
        idle = [r for r in self.robots.values()
                if r.state == "IDLE" and r.soc_pct >= self.reserve_pct
                and r.fault_until <= t]
        self.queue.sort(key=lambda o: (o["deadline"], o["priority"]))
        for order in list(self.queue):
            if not self._replan_blocked_docks(t, order):
                continue
            # 月台订单先要一扇门：没有空门就留在派单队列里等，**不要**先开过去
            # 在门口排队——那条走廊只有一条车道，车停在那里会堵死整片月台。
            dock_req = None
            for key in ("pick_loc", "drop_loc"):
                st = self.stations.get(order[key])
                if st and st.get("kind") == "DOCK":
                    dock_req = (key, st)
                    break
            dock_slot = None
            if dock_req is not None:
                key, st = dock_req
                dock_slot = self._dock_slot(st, t)
                if dock_slot is None:
                    if order["order_id"] not in st["queue"]:
                        st["queue"].append(order["order_id"])
                        st["qmax"] = max(st["qmax"], len(st["queue"]))
                    if order.get("dock_wait_since") is None:
                        order["dock_wait_since"] = t
                    continue
            cand = [r for r in idle if r.wid == order["warehouse"]]
            if not cand:
                continue
            rule = DISPATCH_RULES[self.dispatch_rule]
            r = min(cand, key=lambda x: rule(x, order, self))
            # An explicit, independently recomputable preemption fact: the
            # nearest robot in the warehouse is unavailable (FAULT), so this
            # order is handed to the next-best idle robot. Without this event
            # robot_failure would show 0 reassignments (plan S4).
            pool = [x for x in self.robots.values() if x.wid == order["warehouse"]]
            nearest = min(pool, key=lambda x: math.dist(x.pos, order["pick_xy"]))
            if nearest.state == "FAULT" and nearest.id != r.id:
                self.log.emit(t, "TASK_REASSIGNED", agent=nearest.id,
                              reassigned_to=r.id, order_id=order["order_id"],
                              reason="FAULT_PREEMPT")
            legs = [self.world.astar(r.pos, order["pick_xy"]),
                    self.world.astar(order["pick_xy"], order["drop_xy"]),
                    self.world.astar(order["drop_xy"], r.home)]
            if any(p is None for p in legs):
                continue
            # 派单要算得动：三段行程总长 × 单位电耗。以前只看当前 soc ≥ 20%，
            # 而 20% 只够跑 400 m，一趟 500 m 的活必然半路没电。
            trip_m = sum(math.dist(p[i], p[i + 1])
                         for p in legs for i in range(len(p) - 1))
            need_kwh = trip_m * r.cons
            avail_kwh = r.soc - self.reserve_pct / 100.0 * r.cap
            if need_kwh > avail_kwh:
                self._start_charge(r, t, mode="energy_guard")
                self.energy_guard_n += 1
                continue

            if dock_req is not None:
                key, st = dock_req
                dur = self._sample_service(st)
                # 从"预计到达门口"开始占用，到装卸完成为止
                travel = (sum(math.dist(legs[0][i], legs[0][i + 1])
                              for i in range(len(legs[0]) - 1))
                          + sum(math.dist(legs[1][i], legs[1][i + 1])
                                for i in range(len(legs[1]) - 1))) / max(r.speed, 1e-6)
                st["door_free_at"][dock_slot] = t + travel + dur
                st["served"] += 1
                st["busy_s"] += dur
                if order["order_id"] in st["queue"]:
                    st["queue"].remove(order["order_id"])
                if order.get("dock_wait_since") is not None:
                    st["wait_sum"] += max(0.0, t - order["dock_wait_since"])
                    st["wait_n"] += 1
                    order["dock_wait_since"] = None
                order["dock_slot"] = dock_slot
                self.log.emit(t, "DOCK_RESERVED", agent=r.id, dock=order[key],
                              order_id=order["order_id"], door=dock_slot,
                              service_s=round(dur, 4),
                              free_at=round(st["door_free_at"][dock_slot], 3))

            r.order = order
            r.legs = legs
            r.leg = 0
            r.route, r.arc, r.route_len = legs[0], 0.0, sum(
                math.dist(legs[0][i], legs[0][i + 1]) for i in range(len(legs[0]) - 1))
            r.leg_m = [0.0, 0.0, 0.0]
            r.set_state("MOVING_PICK", self.log, t)
            idle.remove(r)
            self.queue.remove(order)
            self.log.emit(t, "TASK_ASSIGNED", agent=r.id, order_id=order["order_id"],
                          order_type=order["type"], pick_loc=order["pick_loc"],
                          drop_loc=order["drop_loc"], priority=order["priority"])
        while len(self.queue) > self.spec["dispatch"]["max_waiting"]:
            lost = self.queue.pop()
            self.log.emit(t, "ORDER_LOST", order_id=lost["order_id"],
                          order_type=lost["type"], reason="queue_full")

    def _release_order(self, r, t, reason):
        if not r.order:
            return
        self._drop_from_queue(r)
        self._release_station(r, t)
        o = r.order
        o["reassignments"] += 1
        self.queue.append(o)
        self.log.emit(t, "TASK_REASSIGNED", agent=r.id, order_id=o["order_id"],
                      reason=reason)
        r.order = None
        r.legs = []
        r.set_state("IDLE", self.log, t)

    def _emit_energy_window(self, t, window_s, final=False):
        robots = list(self.robots.values())
        out_total = sum(r.energy_out for r in robots)
        motion_total = sum(r.cons * r.distance_m for r in robots)
        in_total = sum(r.energy_in for r in robots)
        out_d = out_total - self._last_energy_out
        motion_d = motion_total - self._last_motion
        in_d = in_total - self._last_energy_in
        self._last_energy_out, self._last_motion, self._last_energy_in = \
            out_total, motion_total, in_total
        self.log.emit(t, "ENERGY_TICK", window_s=round(window_s, 3),
                      idle_kwh=round(out_d - motion_d, 8),
                      motion_kwh=round(motion_d, 8),
                      charge_kwh=round(in_d, 8),
                      cumulative_out_kwh=round(out_total, 8),
                      cumulative_in_kwh=round(in_total, 8),
                      **({"final": True} if final else {}))

    # ---------- main loop ----------
    def _handler_for(self, typ):
        fn = getattr(self, "_cmd_" + typ.lower(), None)
        if fn is None:
            raise ValueError("unknown command: %r (known: %s)" % (typ, ", ".join(COMMANDS)))
        return fn

    def enqueue_command(self, cmd):
        """校验并入队。校验前置是**故意的**：入队通过、应用时炸掉，异常会落在
        实时 tick 线程里，直接把会话打死；前置校验能把 400 报给前端。"""
        if not isinstance(cmd, dict):
            raise ValueError("command must be an object")
        typ = cmd.get("type")
        if typ not in COMMANDS:
            raise ValueError("unknown command: %r (known: %s)" % (typ, ", ".join(COMMANDS)))
        self._handler_for(typ)                              # 命名约定必须对得上
        if typ in NOT_IMPLEMENTED:
            raise NotImplementedError("%s 已登记但尚未实现（不静默空转）" % typ)
        payload = cmd.get("payload") or {}
        self._validate_payload(typ, payload)
        self._cmd_queue.append({"type": typ,
                                "t_request_s": float(cmd.get("t_request_s", self.t)),
                                "payload": dict(payload)})
        return len(self._cmd_queue)

    def _validate_payload(self, typ, p):
        if not isinstance(p, dict):
            raise ValueError("%s payload must be an object" % typ)
        if typ == "SET_POLICY":
            known = {"dispatch.policy", "battery.charge_policy", "dispatch.battery_reserve_pct"}
            unknown = sorted(set(p) - known)
            if unknown:
                raise ValueError("SET_POLICY 不认识的键：%s" % unknown)
            if not p:
                raise ValueError("SET_POLICY 需要至少一个策略键")
            if "dispatch.policy" in p and p["dispatch.policy"] not in DISPATCH_RULES:
                raise ValueError("unknown dispatch.policy: %r (known: %s)"
                                 % (p["dispatch.policy"], ", ".join(sorted(DISPATCH_RULES))))
            if "battery.charge_policy" in p and p["battery.charge_policy"] not in CHARGE_POLICIES:
                raise ValueError("unknown battery.charge_policy: %r (known: %s)"
                                 % (p["battery.charge_policy"], ", ".join(CHARGE_POLICIES)))
            if "dispatch.battery_reserve_pct" in p:
                v = p["dispatch.battery_reserve_pct"]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 95:
                    raise ValueError("dispatch.battery_reserve_pct 必须在 [0,95]")
        elif typ == "SET_DEMAND":
            v = p.get("orders.per_hour")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 1 <= v <= 1000:
                raise ValueError("SET_DEMAND 需要 orders.per_hour ∈ [1,1000]")
            extra = sorted(set(p) - {"orders.per_hour"})
            if extra:
                raise ValueError("SET_DEMAND 不认识的键：%s" % extra)
        elif typ == "INJECT_FAULT":
            kind = p.get("type")
            if kind not in ("robot_down", "dock_blocked"):
                raise ValueError("INJECT_FAULT.type 只能是 robot_down / dock_blocked")
            dur = p.get("duration_s")
            if isinstance(dur, bool) or not isinstance(dur, (int, float)) or not 1 <= dur <= 7200:
                raise ValueError("INJECT_FAULT.duration_s 必须在 [1,7200]")
            if kind == "robot_down":
                ag = p.get("agent")
                if not isinstance(ag, str) or not ag:
                    raise ValueError("robot_down 需要 agent")
                if ag != "ANY_BUSY" and ag not in self.robots:
                    raise ValueError("没有这台车：%r" % ag)
            else:
                dock = p.get("dock")
                if not isinstance(dock, str) or dock not in self.stations:
                    raise ValueError("没有这个月台：%r" % (dock,))
        elif typ == "BUY_AGV":
            wid = p.get("wid")
            if wid not in self.world.warehouses:
                raise ValueError("没有这个仓：%r" % (wid,))
            model = p.get("model")
            if model is not None and model != self.model.get("id", "AMR-250"):
                raise ValueError("本布局只支持车型 %r" % self.model.get("id", "AMR-250"))
            home = p.get("home")
            if home is not None:
                if (not isinstance(home, (list, tuple)) or len(home) != 2
                        or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in home)):
                    raise ValueError("home 必须是 [x, y] 两个数字")
        elif typ == "SELL_AGV":
            rid = p.get("agent")
            if not isinstance(rid, str) or rid not in self.robots:
                raise ValueError("没有这台车：%r" % (rid,))
        elif typ == "SET_REORDER_POINT":
            sku = p.get("sku")
            if not isinstance(sku, str) or not sku:
                raise ValueError("SET_REORDER_POINT 需要 sku（'ALL' 或具体 id）")
            if sku != "ALL" and sku not in self.inv_sku:
                raise ValueError("没有这个 SKU：%r" % (sku,))
            f = p.get("factor")
            if isinstance(f, bool) or not isinstance(f, (int, float)) or not 0.1 <= f <= 10:
                raise ValueError("factor 必须在 [0.1, 10]")
        elif typ == "SET_LOT_POLICY":
            pol = p.get("policy")
            if pol not in ("FEFO", "FIFO"):
                raise ValueError("SET_LOT_POLICY 只接受 FEFO / FIFO，收到 %r" % (pol,))
            if not self.lot_expiry_enabled:
                raise ValueError("本局没有启用批次效期，无法设置批次策略")
        elif typ == "SET_PRICE":
            typ_ = p.get("order_type")
            if typ_ not in ORDER_TYPES:
                raise ValueError("order_type 只能是 %s" % list(ORDER_TYPES))
            price = p.get("price")
            if isinstance(price, bool) or not isinstance(price, (int, float)) or not 0 < price <= 1000:
                raise ValueError("price 必须在 (0, 1000]")

    def _apply_commands(self):
        """在 tick 边界应用玩家命令。批跑模式下队列恒为空，是空操作。

        这里**不做 try/except**：handler 的异常要冒出去让人看见。吞掉异常等于
        "玩家的操作悄悄没生效"，比崩掉更难查。
        """
        if not self._cmd_queue:
            return
        while self._cmd_queue:
            cmd = self._cmd_queue.pop(0)
            self._handler_for(cmd["type"])(cmd)

    def _cmd_set_policy(self, cmd):
        p = cmd["payload"]
        changed = {}
        if "dispatch.policy" in p:
            self.dispatch_rule = p["dispatch.policy"]
            changed["dispatch.policy"] = p["dispatch.policy"]
        if "battery.charge_policy" in p:
            self.charge_policy = p["battery.charge_policy"]
            changed["battery.charge_policy"] = p["battery.charge_policy"]
        if "dispatch.battery_reserve_pct" in p:
            self.reserve_pct = float(p["dispatch.battery_reserve_pct"])
            changed["dispatch.battery_reserve_pct"] = self.reserve_pct
        # 策略同时写回 overrides：存档时"策略就是场景的一部分"这条不变量继续成立。
        self.overrides.update(changed)
        self.log.emit(self.t, "CMD_SET_POLICY", **changed)

    def _cmd_set_demand(self, cmd):
        per_hour = float(cmd["payload"]["orders.per_hour"])
        self.demand_multiplier = (per_hour / self._base_per_hour
                                  if self._base_per_hour > 0 else 1.0)
        self._refresh_demand()
        self.overrides["demand.orders.per_hour"] = per_hour
        self.log.emit(self.t, "CMD_SET_DEMAND", orders_per_hour=per_hour)

    # ---- 定价与需求弹性（P3）----
    # 需求在引擎里（确定性、可单测），钱在经济层（game_economy）。这样"改价真的
    # 改变单量"不必依赖经济层是否挂载，也不会让账本逻辑污染仿真核心。
    def _price_factor(self):
        """按订单结构加权：只给 OUTBOUND 涨价，不该让 PICK 的量也一起掉。

        factor_i = clamp(1 − k × (price_i − base_i) / base_i, min_factor, ∞)
        线性弹性是最粗的一版，但可解释、可调参、能让"薄利多销 vs 高价少单"成立。
        """
        mix = self.overrides.get("demand.orders.mix", self.demand["orders"]["mix"])
        total_w = sum(mix.values()) or 1.0
        base_prices = (self.cost_spec.get("revenue", {}).get("per_order") or {})
        acc = 0.0
        for typ, w in mix.items():
            base = float(base_prices.get(typ, 0.0)) * self.tariff_multiplier
            if base <= 0:
                acc += w
                continue
            price = float(self.price_overrides.get(typ, base))
            f = 1.0 - self.price_elasticity_k * (price - base) / base
            acc += w * max(self.min_demand_factor, f)
        return acc / total_w

    def _refresh_demand(self):
        self.per_hour = max(0.0, self._base_per_hour * self.demand_multiplier
                            * self._price_factor())
        self.lam = self.per_hour / 3600.0

    def _cmd_set_lot_policy(self, cmd):
        """切 FEFO / FIFO。这是关卡里可玩的杠杆：FIFO 更"公平"但会先出掉
        早入库的货而不顾效期，于是过期报废上升——两个指标同时动，才叫决策。"""
        pol = cmd["payload"]["policy"]
        before = self.lot_policy
        self.lot_policy = pol
        self.log.emit(self.t, "CMD_SET_LOT_POLICY", policy=pol, before=before)

    def _cmd_set_reorder_point(self, cmd):
        p = cmd["payload"]
        sku, f = p["sku"], float(p["factor"])
        if not self.inv_enabled:
            raise ValueError("本局没有装载库存层，无法设置补货点")
        if sku == "ALL":
            self.reorder_factor = f
            self.reorder_factor_by_sku.clear()
        else:
            self.reorder_factor_by_sku[sku] = f
        self.log.emit(self.t, "CMD_SET_REORDER_POINT", sku=sku, factor=f)

    def _cmd_set_price(self, cmd):
        p = cmd["payload"]
        typ = p["order_type"]
        price = float(p["price"])
        self.price_overrides[typ] = price
        self._refresh_demand()
        self.log.emit(self.t, "CMD_SET_PRICE", order_type=typ, price=price,
                      orders_per_hour=round(self.per_hour, 6))

    def _cmd_sell_agv(self, cmd):
        p = cmd["payload"]
        rid = p["agent"]
        r = self.robots.get(rid)
        if r is None:
            raise ValueError("没有这台车：%r" % (rid,))
        if r.order is not None:
            raise ValueError("车 %s 正在执行任务，不能卖（会丢单）" % rid)
        del self.robots[rid]
        self.n_robots = len(self.robots)
        self.log.emit(self.t, "CMD_SELL_AGV", agent=rid, wid=r.wid,
                      refund_pct=self.sell_refund_pct)

    def _cmd_inject_fault(self, cmd):
        p = cmd["payload"]
        end = self.t + float(p["duration_s"])
        f = {"type": p["type"], "start": self.t, "end": end}
        if p["type"] == "robot_down":
            f["agent"] = p["agent"]
        else:
            f["dock"] = p["dock"]
        self.faults.append(f)
        extra = {"agent": f["agent"]} if "agent" in f else {"dock": f["dock"]}
        self.log.emit(self.t, "CMD_INJECT_FAULT", fault_type=p["type"], until_s=end, **extra)

    def _next_robot_id(self, wid):
        n = 1
        while True:
            rid = "%s-BUY%02d" % (wid, n)
            if rid not in self.robots:
                return rid
            n += 1

    def _cmd_buy_agv(self, cmd):
        p = cmd["payload"]
        wid = p["wid"]
        home = p.get("home")
        if home is None:
            spawns = [(s["position"][0], s["position"][2])
                      for s in self.world.warehouses[wid]["spawn"]["robots"]]
            base = spawns[0] if spawns else (10.0, 10.0)
            k = sum(1 for r in self.robots.values() if r.wid == wid)
            home = (base[0] + 2.0 * k, base[1] + 1.6 * k)
        # 吸附到最近可走格中心：玩家给的坐标可能落在货架里，车不能生成在墙上。
        cell = self.world.nearest_walkable((float(home[0]), float(home[1])))
        pos = self.world.center(cell)
        b = self.spec["battery"]
        soc = b["initial_soc_pct"] / 100.0 * self.model["battery_kwh"]
        rid = self._next_robot_id(wid)
        r = Robot(rid, wid, pos, soc, self.model)
        r.charger_id = "%s-CHG-01" % wid
        self.robots[rid] = r
        self.n_robots = len(self.robots)
        self.log.emit(self.t, "CMD_BUY_AGV", agent=rid, wid=wid,
                      model=self.model.get("id", "AMR-250"), home=list(pos))

    def _cmd_build_station(self, cmd):
        raise NotImplementedError("BUILD_STATION 尚未实现（P4 落地）")

    def _cmd_manual_assign(self, cmd):
        raise NotImplementedError("MANUAL_ASSIGN 尚未实现（P4 落地）")

    def _init_run(self):
        """把批跑的一次性初值收敛到一处。

        原来这些初值写在 run() 开头，实时会话（每帧只推进一步）没有地方可放，
        只能复制一遍——复制就会漂移。所以升为方法，run() 与实时循环共用同一套起点。
        """
        self._order_n = 0
        self.tick_i = 0
        self.t = 0.0
        self.n_ticks = int(self.horizon / self.tick)
        self.lam = self.per_hour / 3600.0
        self.done = False
        self._started = True
        if self.ext:
            self.next_arrival = self.ext[0]["t_arrive"]
        else:
            self.next_arrival = self.rng.expovariate(self.lam) if self.lam > 0 else float("inf")

    def step(self):
        """推进一个循环迭代（空闲时可能跳 tick），返回是否到达 horizon。

        这是 run() 循环体的**逐行搬运**，语义一字不改：仍保留"空闲事件驱动跳 tick"，
        否则批跑事件流会变、STEP_OK 会红。变量升格：局部 `tick` → `self.tick_i`，
        `t` → `self.t`，供实时模式与快照读取。
        """
        self._apply_commands()
        if not self._started:
            # 防误用：直接 step() 也要有正确的首次到达时间（不是 __init__ 的 0.0）
            self._init_run()
        if self.tick_i >= self.n_ticks:
            return True
        t = self.tick_i * self.tick
        self.t = t
        # Event-driven tick jump. Fault start/end times are scheduled events
        # too: without them a t=0 fault was silently skipped to the first
        # order arrival and the recovery never fired on an idle fleet.
        busy = any(r.state not in ("IDLE", "FAULT", "CHARGING") for r in self.robots.values())
        # An arrival / fault edge that is already due but not yet consumed
        # must be processed in THIS tick; never jump over it.
        due = self.next_arrival <= t + 1e-9
        scheduled = [self.next_arrival]
        for f in self.faults:
            was = f.get("was_active", False)
            if not was and f["start"] <= t + 1e-9:
                due = True
            elif not was:
                scheduled.append(f["start"])
            elif was and f["end"] <= t + 1e-9:
                due = True
            else:
                scheduled.append(f["end"])
        # 到期批次也是**调度事件**：不把它算进 next_t，空闲跳 tick 会直接跨过到期时刻，
        # 于是批次永远不会过期。这和"t=0 的故障被跳过"是同一类 bug（见上面的注释），
        # 区别只是这次被跳过的是效期，而效期是 P6b 的全部意义。
        if self.lot_expiry_enabled and self.lot_heap:
            nxt = self.lot_heap[0][0]
            if nxt <= t + 1e-9:
                due = True
            else:
                scheduled.append(nxt)
        future = [x for x in scheduled if x > t + 1e-9]
        next_t = min(future) if future else float("inf")
        if Simulation.jump_ticks_override is not False \
                and not busy and not self.queue and not due:
            if next_t == float("inf"):
                self.tick_i = self.n_ticks      # no future event: jump to horizon end
                return True
            if next_t > t + self.tick:
                jump = int((next_t - t) / self.tick)
                self.tick_i = min(self.n_ticks, self.tick_i + jump)
                return self.tick_i >= self.n_ticks

        # 0) 到期报废：必须在派单前做，否则"刚到期的货"还能被拣出去
        self._inv_expire_sweep(t)

        # 1) demand
        if self.ext:
            # 外部订单：到达时间由数据决定，不消耗随机流——同一个 CSV 跑两遍
            # 结果必须一致，否则"真实数据接入"就没法当基准。
            while (self.ext_i < len(self.ext)
                   and self.ext[self.ext_i]["t_arrive"] <= t + 1e-9):
                o = self.ext[self.ext_i]
                self.ext_i += 1
                self._order_n += 1
                self.queue.append(o)
                self.log.emit(o["t_arrive"], "ORDER_ARRIVED",
                              order_id=o["order_id"], order_type=o["type"],
                              warehouse=o["warehouse"], priority=o["priority"],
                              source="csv")
            self.next_arrival = (self.ext[self.ext_i]["t_arrive"]
                                 if self.ext_i < len(self.ext) else float("inf"))
        else:
            while self.next_arrival <= t:
                self._order_n += 1
                o = self.new_order(self.next_arrival)
                if o:
                    self.queue.append(o)
                    self.log.emit(self.next_arrival, "ORDER_ARRIVED",
                                  order_id=o["order_id"], order_type=o["type"],
                                  warehouse=o["warehouse"], priority=o["priority"])
                self.next_arrival += self.rng.expovariate(self.lam) if self.lam > 0 else float("inf")

        # 2) faults
        self._apply_faults(t)

        # 3) dispatch
        self._assign(t)

        # 4) robots: sequential updates with an immediately-updated
        #    position map keep the 2*AGV_RADIUS reservation consistent.
        positions = {r0.id: r0.pos for r0 in self.robots.values()}
        for r in self.robots.values():
            self._robot_step(r, t, positions)
            positions[r.id] = r.pos
        cell_owner = {}
        for r0 in self.robots.values():
            cr0 = self.world.to_cell(r0.pos)
            if cr0 in cell_owner and cell_owner[cr0] != r0.id:
                self.reservation_double += 1
            cell_owner[cr0] = r0.id

        # 4b) independent separation sampling (1 s) for S6
        if self.tick_i % 10 == 0:
            pos = [(r.id, r.pos) for r in self.robots.values()]
            for i in range(len(pos)):
                for j in range(i + 1, len(pos)):
                    d = math.dist(pos[i][1], pos[j][1])
                    if d < self.min_pair_m:
                        self.min_pair_m = d
                    if d < self.min_sep - 1e-6:
                        self.sep_violations += 1

        # 5) energy accounting + KPI sampling (window deltas, one aggregate
        #    event per KPI window; S2 recomputes from these + sim_state)
        if self.tick_i % self.spec["output"]["kpi_every_ticks"] == 0:
            self._emit_energy_window(t, self.spec["output"]["kpi_every_ticks"] * self.tick)
            self.max_queue_depth = max(self.max_queue_depth, len(self.queue))
            robots = list(self.robots.values())
            self.position_samples.append({"t_s": round(t, 2), "agents": {
                rid: [round(v, 3) for v in r.pos] for rid, r in self.robots.items()}})
            busy = sum(1 for r in robots
                       if r.state not in ("IDLE", "FAULT", "CHARGING"))
            self.samples.append({"t_s": round(t, 2),
                                 "queue": len(self.queue),
                                 "active": busy,
                                 "fleet": len(robots),
                                 "idle_robots": len(robots) - busy,
                                 "mean_soc": round(sum(r.soc_pct for r in robots)
                                                   / len(robots), 3)})
        self.tick_i += 1
        # 持有成本按仿真小时计提（tick 时间，不读墙钟）
        if self.inv_enabled and t - self.inv_hold_t >= self.inv_hold_period_s - 1e-9:
            self._inv_accrue_holding(t)
        return self.tick_i >= self.n_ticks

    def _finalize(self):
        """收尾：尾窗能量 + RUN_SETTLED + 关日志 + KPI/状态落盘。

        幂等：实时模式可能在多个窗口里碰到 done，重复收尾会写出两份 RUN_SETTLED
        并让 ledger 重复结算固定成本。
        """
        if self.done:
            return self.metrics
        self.done = True
        every = self.spec["output"]["kpi_every_ticks"]
        # 尾段持有成本要补到 horizon，否则"计提"会差最后一段（与 ENERGY_TICK 的
        # 尾窗同理：不补就会略微小于实际占用时间，守恒对不上）
        if self.inv_enabled:
            self._inv_accrue_holding(self.horizon)
        # Flush the partial tail window so SUM(ENERGY_TICK) closes exactly with
        # sim_state energy_out/energy_in (S2 requires <1% error).
        self._emit_energy_window(self.horizon, (self.n_ticks % every or every) * self.tick,
                                 final=True)
        # Settlement event: fixed-cost allocation in ledger.py needs a real
        # source event (F2 traceability), not a synthetic entry.
        self.log.emit(self.horizon, "RUN_SETTLED", horizon_s=self.horizon,
                      orders_completed=self.log.counts.get("TASK_COMPLETED", 0),
                      energy_out_kwh=round(sum(r.energy_out for r in self.robots.values()), 8),
                      energy_in_kwh=round(sum(r.energy_in for r in self.robots.values()), 8))
        self.log.close()
        dur = time.time() - self.wall0
        self.metrics = self._kpi(dur)
        jdump(self.out / "sim_kpi.json", self.metrics)
        jdump(self.out / "sim_state.json", self._state())
        jdump(self.out / "sim_positions.json", {"schema_version": "1.0",
              "kind": "sim_positions", "min_sep_threshold_m": self.min_sep,
              "samples": self.position_samples})
        return self.metrics

    def run(self):
        """批跑模式：行为与 step 化之前逐字节一致（STEP_OK 门守着）。

        只在**没跑过**的时候初始化。这样"读档后 run() 续跑到尾"才成立——
        如果无条件 `_init_run()`，读档续跑会被悄悄重置成 t=0（存档看起来"成功了"，
        实际从头再跑一遍，事件流 seq 还会翻倍）。
        """
        if not self._started:
            self._init_run()
        while not self.step():
            pass
        return self._finalize()

    def _read_events(self):
        return [json.loads(line) for line in (self.out / "sim_events.jsonl")
                .read_text(encoding="utf-8").splitlines() if line.strip()]

    def _apply_faults(self, t):
        for f in self.faults:
            active = f["start"] <= t < f["end"]
            f.setdefault("was_active", False)
            if active and not f["was_active"]:
                f["was_active"] = True
                if f["type"] == "robot_down":
                    r = self.robots.get(f["agent"])
                    # agent="ANY_BUSY"：砸当前正在执行任务的车。故障场景要证明的
                    # 是"任务被打断→改派"，砸中一台闲置车什么也证明不了；没有车
                    # 在干活时这一 tick 不生效（was_active 不置位，下一 tick 重试），
                    # 而不是凭空记一条故障。
                    if f["agent"] == "ANY_BUSY":
                        pool = [x for x in self.robots.values()
                                if x.order is not None and x.state not in ("FAULT", "CHARGING")]
                        if not pool:
                            continue
                        r = min(pool, key=lambda x: x.id)
                    # "preempt_busy": a scenario-owned policy (declared in the
                    # scenario JSON) that moves the fault onto a same-warehouse
                    # robot with an order in flight, so task preemption is a
                    # real event rather than a no-op on an idle chassis.
                    if (r is not None and r.order is None
                            and self.sc.get("fault_target_policy") == "preempt_busy"):
                        busy = [x for x in self.robots.values()
                                if x.wid == r.wid and x.order is not None
                                and x.state not in ("FAULT", "CHARGING")]
                        if busy:
                            r = min(busy, key=lambda x: x.id)
                    if r:
                        f["actual_agent"] = r.id
                        self._release_order(r, t, "FAULT_START")
                        r.fault_until = f["end"]
                        r.set_state("FAULT", self.log, t)
                        extra = {"requested_agent": f["agent"]} if r.id != f["agent"] else {}
                        self.log.emit(t, "FAULT_START", agent=r.id,
                                      until_s=f["end"], **extra)
                elif f["type"] == "dock_blocked":
                    self.blocked_docks.add(f["dock"])
                    # 已经占用着门的车照常装完（门里那台车不会凭空消失），
                    # 但门外排队的和之后到达的必须改派。
                    st = self.stations.get(f["dock"])
                    if st is not None:
                        st["closed"] = True
                    self.log.emit(t, "DOCK_BLOCKED", dock=f["dock"], until_s=f["end"])
            elif not active and f["was_active"]:
                f["was_active"] = False
                if f["type"] == "robot_down":
                    r = self.robots.get(f.get("actual_agent", f["agent"]))
                    if r:
                        r.fault_until = 0
                        r.set_state("IDLE", self.log, t)
                        self.log.emit(t, "FAULT_END", agent=r.id)
                elif f["type"] == "dock_blocked":
                    self.blocked_docks.discard(f["dock"])
                    st = self.stations.get(f["dock"])
                    if st is not None:
                        st["closed"] = False
                    self.log.emit(t, "DOCK_REOPENED", dock=f["dock"])

    def _robot_step(self, r, t, positions):
        dt = self.tick
        if r.state == "FAULT":
            if r.fault_until <= t:
                r.set_state("IDLE", self.log, t)
            return
        if r.state == "WAITING":
            self._station_retry(r, t)
            return
        if r.state in ("PICKING", "DROPPING"):
            self._dwell_step(r, t)
        if r.state == "CHARGING":
            gain = r.charge_kw * dt / 3600.0
            r.soc = min(r.cap, r.soc + gain)
            r.energy_in += gain
            if r.soc_pct >= self.spec["battery"]["charge_to_pct"]:
                r.set_state("IDLE", self.log, t)
                self.log.emit(t, "CHARGE_END", agent=r.id, soc_pct=round(r.soc_pct, 3),
                              energy_in_kwh=round(r.energy_in, 6))
                r.charging_since = None
            return
        if r.state == "IDLE":
            r.soc = max(0.0, r.soc - r.idle_kw * dt / 3600.0)
            r.energy_out += r.idle_kw * dt / 3600.0
            # opportunistic（默认）：掉到 critical 才充，任务优先；
            # threshold：低于 low_soc 就找空档充，牺牲一点可用率换"不趴窝"。
            floor_pct = (self.spec["battery"]["low_soc_pct"]
                         if self.charge_policy == "threshold" else self.critical_pct)
            if r.soc_pct < floor_pct:
                if r.soc_pct < self.critical_pct:
                    self.log.emit(t, "BATTERY_CRITICAL", agent=r.id,
                                  soc_pct=round(r.soc_pct, 3))
                self._start_charge(r, t, mode=self.charge_policy)
                return
        before = r.yields
        before_d = r.detours
        r.advance(dt, t, self.log, self.world, positions, self.min_sep)
        if r.yields != before:
            self.yield_n += r.yields - before
        if r.blocked_s >= JAM_CLEAR_AFTER_S:
            self._clear_jam(r, t)
            return

        if r.blocked_s > self.max_blocked_s:
            self.max_blocked_s = r.blocked_s
            self._max_blocked_agent = (r.id, r.state, r.blocked_by,
                                       [round(v, 2) for v in r.pos])

        # ---- 绕行：退让解决"面对面"，绕行解决"前方停着一辆车" ----
        if (r.blocked_s >= DETOUR_AFTER_S and r.blocked_by in positions
                and r.state in ("MOVING_PICK", "MOVING_DROP", "RETURNING")
                and t - r.detour_try_t >= DETOUR_COOLDOWN_S):
            # 冷却很关键：A* 目标不可达时会扫完整个栅格，若每 tick 都试一次，
            # 28 台车 × 7200 tick 直接把仿真拖死（这一步踩过）。
            r.detour_try_t = t
            goal = self._leg_goal(r)
            if goal is not None:
                avoid = {self.world.to_cell(positions[r.blocked_by])}
                p_new = self.world.astar(r.pos, goal, avoid)
                if p_new and len(p_new) >= 2:
                    r.route = p_new
                    r.route_len = sum(math.dist(p_new[i], p_new[i + 1])
                                      for i in range(len(p_new) - 1))
                    r.arc = 0.0
                    r.blocked_s = 0.0
                    r.detours += 1
                    self.log.emit(t, "REPLAN", agent=r.id, reason="deadlock_detour",
                                  avoid=str(r.blocked_by),
                                  remaining_m=round(r.route_len, 2))
        if r.detours != before_d:
            self.detour_n += r.detours - before_d
        if r.state in ("MOVING_PICK", "MOVING_DROP", "RETURNING", "TO_CHARGER") \
                and r.arc >= r.route_len - 1e-6:
            self._arrive(r, t)

    def _arrive(self, r, t):
        if r.state == "MOVING_PICK":
            self._enter_station(r, t, "PICKING")
        elif r.state == "MOVING_DROP":
            self._enter_station(r, t, "DROPPING")
        elif r.state == "RETURNING":
            o = r.order
            # 库存结算必须在发 TASK_COMPLETED **之前**：缺货就不许"完成"，
            # 否则缺货被悄悄算成已交付（方案书 §5.2 / 门 3）。
            delivered, ok = self._inv_settle(o)
            if not ok:
                self.log.emit(t, "ORDER_LOST", order_id=o["order_id"],
                              order_type=o["type"], reason="stockout",
                              pick_loc=o["pick_loc"],
                              sku=self.inv_loc.get(o["pick_loc"], {}).get("sku"))
                r.order = None
                r.set_state("IDLE", self.log, t)
                return
            self.log.emit(t, "TASK_COMPLETED", agent=r.id, order_id=o["order_id"],
                          order_type=o["type"], warehouse=o["warehouse"],
                          legs_m={"empty_out": round(r.leg_m[0], 3),
                                  "loaded": round(r.leg_m[1], 3),
                                  "empty_back": round(r.leg_m[2], 3)},
                          energy_kwh=round(r.leg_m[0] * r.cons + r.leg_m[1] * r.cons
                                           + r.leg_m[2] * r.cons, 6),
                          tardiness_s=round(max(0.0, t - o["deadline"]), 3),
                          delivered=delivered,
                          internal=bool(o.get("internal")),
                          pick_loc=o["pick_loc"], drop_loc=o["drop_loc"],
                          reassignments=o["reassignments"])
            r.completed += 1
            r.order = None
            r.set_state("IDLE", self.log, t)
        elif r.state == "TO_CHARGER":
            r.set_state("CHARGING", self.log, t)

    def _dwell_step(self, r, t):
        if r.state in ("PICKING", "DROPPING") and t >= r.dwell_until:
            self._release_station(r, t)
            if r.state == "PICKING":
                self.log.emit(t, "PICK_DONE", agent=r.id,
                              order_id=r.order["order_id"], loc=r.order["pick_loc"])
                r.leg = 1
                r.route, r.arc = r.legs[1], 0.0
                r.route_len = sum(math.dist(r.legs[1][i], r.legs[1][i + 1])
                                  for i in range(len(r.legs[1]) - 1))
                r.set_state("MOVING_DROP", self.log, t)
            else:
                self.log.emit(t, "DROP_DONE", agent=r.id,
                              order_id=r.order["order_id"], loc=r.order["drop_loc"])
                r.leg = 2
                r.route, r.arc = r.legs[2], 0.0
                r.route_len = sum(math.dist(r.legs[2][i], r.legs[2][i + 1])
                                  for i in range(len(r.legs[2]) - 1))
                r.set_state("RETURNING", self.log, t)

    def _kpi(self, wall_s):
        ev = self._read_events()
        done = [e for e in ev if e["type"] == "TASK_COMPLETED"]
        lost = [e for e in ev if e["type"] == "ORDER_LOST"]
        charge = [e for e in ev if e["type"] == "CHARGE_START"]
        faults = [e for e in ev if e["type"] == "FAULT_START"]
        replans = [e for e in ev if e["type"] == "REPLAN"]
        waits = [e for e in ev if e["type"] == "WAIT_CONFLICT"]
        reassign = [e for e in ev if e["type"] == "TASK_REASSIGNED"]
        dock_blocks = [e for e in ev if e["type"] == "DOCK_BLOCKED"]
        dock_handoff = [e for e in ev if e["type"] == "DOCK_HANDOFF"]
        dock_refused = [e for e in ev if e["type"] == "DOCK_REFUSED"]
        dock_reroutes = [e for e in replans if e.get("reason") == "dock_blocked"]
        dist = sum(sum(e["legs_m"].values()) for e in done)
        energy = sum(e["energy_kwh"] for e in done)
        tardy = [e["tardiness_s"] for e in done]
        per_robot = [r.completed for r in self.robots.values()]
        # Vehicle-level idle share (not "was any robot busy"): the fleet-level
        # boolean made idle_rate identically 0 even on the night shift.
        idle_rate = (sum(s["idle_robots"] for s in self.samples)
                     / max(sum(s["fleet"] for s in self.samples), 1))
        max_queue = max([s["queue"] for s in self.samples] + [self.max_queue_depth])

        def M(value, unit, definition, formula, source="sim_engine measured"):
            return {"value": value, "unit": unit, "definition": definition,
                    "formula": formula, "source": source}

        m = {}
        m["orders_generated"] = M(self._order_n, "orders", "Poisson arrivals",
                                  "count(ORDER_ARRIVED)")
        m["orders_completed"] = M(len(done), "orders", "Delivered orders",
                                  "count(TASK_COMPLETED)")
        m["orders_lost"] = M(len(lost), "orders", "Orders dropped at queue cap",
                             "count(ORDER_LOST)")
        m["throughput_orders_per_h"] = M(round(len(done) * 3600 / self.horizon, 3),
                                         "orders/h", "Measured throughput",
                                         "orders_completed * 3600 / horizon_s")
        m["on_time_rate"] = M(round(sum(1 for x in tardy if x <= 0) / max(len(done), 1), 4),
                              "ratio", "On-time share", "count(tardiness_s=0)/completed")
        m["avg_tardiness_s"] = M(round(sum(tardy) / max(len(done), 1), 3), "s",
                                 "Mean tardiness", "mean(TASK_COMPLETED.tardiness_s)")
        m["max_tardiness_s"] = M(round(max(tardy, default=0), 3), "s", "Max tardiness",
                                 "max(TASK_COMPLETED.tardiness_s)")
        m["distance_total_m"] = M(round(dist, 3), "m", "All three legs",
                                  "sum(TASK_COMPLETED.legs_m)")
        m["energy_kwh_total"] = M(round(energy, 6), "kWh", "Motion energy",
                                  "sum(TASK_COMPLETED.energy_kwh)")
        m["energy_per_order_kwh"] = M(round(energy / max(len(done), 1), 6), "kWh/order",
                                      "Motion energy per order", "energy_kwh_total/completed")
        m["charge_events"] = M(len(charge), "events", "Charging sessions started",
                               "count(CHARGE_START)")
        m["fault_downtime_s"] = M(round(sum(f["until_s"] - f["t_s"] for f in faults), 2),
                                  "s", "Injected fault windows", "sum(FAULT_START.until_s - t_s)")
        m["recovery_s"] = M(round(sum(e["t_s"] - f["t_s"]
                                      for f in faults
                                      for e in ev if e["type"] == "FAULT_END"
                                      and e["agent"] == f["agent"] and e["t_s"] >= f["t_s"]), 2),
                             "s", "Window covered by fault ends", "sum(FAULT_END.t_s - FAULT_START.t_s)")
        m["replans"] = M(len(replans), "events", "Traffic replans", "count(REPLAN)")
        m["wait_conflicts"] = M(len(waits), "events", "Reservation waits",
                                "count(WAIT_CONFLICT)")
        m["task_reassignments"] = M(len(reassign), "events", "Fault/battery reassignments",
                                    "count(TASK_REASSIGNED)")
        m["multi_order_robots"] = M(sum(1 for x in per_robot if x >= 3), "robots",
                                    "Robots completing >=3 orders", "count(completed>=3)")
        m["max_orders_per_robot"] = M(max(per_robot, default=0), "orders",
                                      "Busiest robot", "max(completed)")
        m["availability"] = M(round(1 - m["fault_downtime_s"]["value"]
                                    / (self.horizon * max(self.n_robots, 1)), 6),
                              "ratio", "Fleet availability", "1 - downtime/(horizon*agents)")
        m["idle_rate"] = M(round(idle_rate, 4), "ratio", "Sampled vehicle-level idle share",
                           "sum(samples.idle_robots)/sum(samples.fleet)")
        m["max_queue_depth"] = M(max_queue, "orders", "Peak waiting queue",
                                 "max(samples.queue, max_queue_depth)")
        m["agents"] = M(self.n_robots, "robots", "Fleet size actually simulated",
                        "len(robots)")
        m["reservation_double_occupancy"] = M(self.reservation_double, "ticks",
                                              "Cell occupied by two robots",
                                              "count(duplicate cell owner per tick)")
        m["strict_collisions"] = M(self.sep_violations, "samples", "Separation violations (1 s sampling)",
                                   "count(pair_distance < 2*AGV_RADIUS)")
        m["min_pair_distance_m"] = M(round(self.min_pair_m, 4), "m",
                                     "Minimum sampled centre distance",
                                     "min over all robot pairs at 1 s samples")
        # ---- 工位层（M1-02）：服务时间与排队是实测的，不是公式推的 ----
        # 月台也走同一套服务台机制，但指标分开统计：混在一起算利用率，
        # "工位利用率"会被 38 扇月台门稀释成毫无意义的小数。
        srv = [v for v in self.stations.values() if v.get("kind") != "DOCK"]
        st_total = len(srv)
        st_served = sum(v["served"] for v in srv)
        st_busy_s = sum(v["busy_s"] for v in srv)
        st_wait_n = sum(v["wait_n"] for v in srv)
        st_wait_s = sum(v["wait_sum"] for v in srv)
        st_qmax = max([v["qmax"] for v in srv] + [0])
        st_service_mean = (st_busy_s / st_served) if st_served else 0.0
        st_util = (st_busy_s / (st_total * self.horizon)) if st_total and self.horizon else 0.0
        m["stations_defined"] = M(st_total, "stations",
                                  "process_spec 工位接入 tick 的数量",
                                  "count(process_spec.stations)")
        m["station_served"] = M(st_served, "visits", "工位服务次数",
                                "count(STATION_ENTER)")
        m["station_service_mean_s"] = M(round(st_service_mean, 3), "s",
                                        "工位实测平均服务时长",
                                        "sum(service_s)/count(STATION_ENTER)")
        m["station_utilization"] = M(round(st_util, 4), "ratio",
                                     "工位占用率（服务时长 / 工位数×时长）",
                                     "sum(service_s)/(stations*horizon_s)")
        m["station_queue_wait_avg_s"] = M(
            round(st_wait_s / st_wait_n, 3) if st_wait_n else 0.0, "s",
            "排队进入服务的平均等待（未排队的单不计入）",
            "sum(waited_s)/count(waited_s)")
        m["station_queue_max"] = M(st_qmax, "robots", "单工位最长队列",
                                   "max(STATION_QUEUED.queue_len)")

        # ---- 月台层（v4.7）：装卸真的占门、真的花时间、门外真的会排队 ----
        dk = [v for v in self.stations.values() if v.get("kind") == "DOCK"]
        dk_doors = sum(v["slots"] for v in dk)
        dk_served = sum(v["served"] for v in dk)
        dk_busy_s = sum(v["busy_s"] for v in dk)
        dk_wait_n = sum(v["wait_n"] for v in dk)
        dk_wait_s = sum(v["wait_sum"] for v in dk)
        dk_qmax = max([v["qmax"] for v in dk] + [0])
        m["docks_defined"] = M(len(dk), "docks", "接入 tick 的月台区数",
                               "count(layout.warehouses[].docks)")
        m["dock_doors"] = M(dk_doors, "doors", "月台门总数（一扇门一个并行位）",
                            "sum(len(dock.doors))")
        m["dock_served"] = M(dk_served, "visits", "月台装卸次数（进即占用一门）",
                             "count(STATION_ENTER.station in docks)")
        m["dock_service_mean_s"] = M(
            round(dk_busy_s / dk_served, 3) if dk_served else 0.0, "s",
            "月台实测平均装卸时长（进=卸货/装货，来源 process_steps）",
            "sum(service_s)/count(STATION_ENTER.station in docks)")
        m["dock_utilization"] = M(
            round(dk_busy_s / (dk_doors * self.horizon), 4) if dk_doors and self.horizon else 0.0,
            "ratio", "月台门占用率（装卸时长 / 门数×时长）",
            "sum(service_s)/(sum(doors)*horizon_s)")
        m["dock_queue_wait_avg_s"] = M(
            round(dk_wait_s / dk_wait_n, 3) if dk_wait_n else 0.0, "s",
            "门外排队进入装卸的平均等待（没排队的单不计入）",
            "sum(waited_s)/count(waited_s where station in docks)")
        m["dock_queue_max"] = M(dk_qmax, "orders", "单个月台区最长等门订单数",
                                "max(queue_len where station in docks)")
        m["dock_handoffs"] = M(len(dock_handoff), "visits",
                               "AGV 在门口实际完成交接的次数",
                               "count(DOCK_HANDOFF)")
        m["dock_blocked_events"] = M(len(dock_blocks), "times", "月台封锁事件数",
                                     "count(DOCK_BLOCKED)")
        m["dock_refused"] = M(len(dock_refused), "times",
                              "因门被封锁而被劝返改派的到达/排队次数",
                              "count(DOCK_REFUSED)")
        m["dock_reroutes"] = M(len(dock_reroutes), "times",
                               "因封锁把落货点改到别的门的次数",
                               "count(REPLAN.reason=dock_blocked)")
        m["deadlock_yields"] = M(self.yield_n, "times",
                                 "死锁退让次数（被同一台车挡住超阈值后主动后退）",
                                 "count(DEADLOCK_YIELD)")
        if os.environ.get("DC_DEBUG_BLOCK"):
            for rr in self.robots.values():
                if rr.blocked_s > 60:
                    bb = self.robots.get(rr.blocked_by)
                    print("BLOCKED", rr.id, rr.state, "by", rr.blocked_by,
                          (bb.state if bb else "-"),
                          "for %.0fs" % rr.blocked_s,
                          "pos", [round(v, 2) for v in rr.pos], flush=True)
        m["deadlock_detours"] = M(self.detour_n, "times",
                                  "为绕开停驶车辆而重新规划的路线数",
                                  "count(REPLAN.reason=deadlock_detour)")
        m["blocked_time_total_s"] = M(
            round(sum(r.blocked_total for r in self.robots.values()), 2), "s",
            "全员累计受阻时长（退让/绕行生效后会显著下降）",
            "sum(robot.blocked_total)")
        m["jam_clears"] = M(self.jam_clear_n, "times",
                            "人工清障次数（单向窄巷顶死，退让与绕行都无解）",
                            "count(JAM_CLEARED)")
        m["max_blocked_s"] = M(round(self.max_blocked_s, 3), "s",
                               "单台车最长连续受阻时长（死锁不退让时会非常大）",
                               "max(robot.blocked_s)")
        m["energy_guard_charges"] = M(self.energy_guard_n, "times",
                                      "因电量不足以跑完行程而就地充电的次数",
                                      "count(CHARGE_START.mode=energy_guard)")
        m["sim_duration_s"] = M(self.horizon, "s", "Simulation horizon", "sim_spec.horizon_s")
        # ---- 库存层（P6a）：tick 层指标，每项带 definition/formula/source ----
        m["stockout_count"] = M(self.inv_stockout_n, "times",
                                "缺货次数（库存不足导致订单未完成）",
                                "count(STOCKOUT)")
        m["fill_rate"] = M(round(min(1.0, (self.inv_units_out / self.inv_units_req)
                                     if self.inv_units_req else 1.0), 6),
                           "ratio", "实发件数 / 请求件数（缺货与部分交付都会拉低）",
                           "sum(STOCK_DECREMENT.qty)/sum(requested)")
        m["holding_cost"] = M(round(self.inv_holding, 6), "CNY",
                              "持有成本（按仿真小时计提，不含采购）",
                              "Σ on_hand_value × holding_rate_per_year × dt/8760")
        m["inventory_value"] = M(round(self.inv_value(), 2), "CNY",
                                 "库存占用资金（资产侧展示，不进 P&L）",
                                 "Σ qty × unit_cost")
        m["reorder_triggers"] = M(self.inv_reorder_n, "times",
                                  "补货触发次数", "count(REORDER_TRIGGERED)")
        m["inventory_enabled"] = M(1 if self.inv_enabled else 0, "bool",
                                   "本场景是否装载了库存层（缺数据时为 0）",
                                   "inventory_slotting.json 存在且已装载")
        m["expiry_enabled"] = M(1 if self.lot_expiry_enabled else 0, "bool",
                                "是否启用批次效期（P6b）", "inventory_spec.expiry.enabled")
        m["expired_units"] = M(round(self.lot_expired_units, 6), "units",
                               "期内到期报废数量", "STOCK_EXPIRED 求和")
        m["expired_value"] = M(round(self.lot_expired_value, 2), "CNY",
                               "到期报废的账面价值（按 unit_cost 估值，不进 P&L）",
                               "STOCK_EXPIRED.value 求和")
        m["lots_opened"] = M(self.lot_opened_n, "lots", "期内批次总数（期初批次 + 新收货）",
                             "inventory_initial.lots + STOCK_RECEIPT 派生")
        m["open_lots"] = M(sum(len(v) for v in self.lots.values()), "lots",
                           "期末未结批次", "lots 中 qty>0 的条目数")
        m["fefo_violations"] = M(self.fefo_violations, "times",
                                 "FEFO 违规次数（应恒为 0）",
                                 "扣减后仍有更早到期批次却已扣晚到期批次")
        m["wall_clock_s"] = M(round(wall_s, 3), "s", "Wall clock", "time.time() delta")
        return {"schema_version": "1.0", "kind": "sim_kpi", "engine": "sim",
                "scenario": self.sc["id"], "seed": self.seed,
                "run_id": self.run_id, "model": "measured",
                "metrics": m, "samples": self.samples[-200:],
                "note": "measured from sim_events.jsonl; analytic M/M/c KPIs are separate"}

    def _state(self):
        return {"schema_version": "1.0", "kind": "sim_state", "scenario": self.sc["id"],
                # horizon 进状态：check_expiry 要判"到期时刻是否落在窗口内"，
                # 不能靠读配置猜——场景可以覆盖 horizon，读配置会判错一整批。
                "horizon_s": self.horizon,
                "robots": {r.id: {"state": r.state, "pos": [round(v, 3) for v in r.pos],
                                  "soc_initial_kwh": round(r.soc_initial, 6),
                                  "soc_final_kwh": round(r.soc, 6),
                                  "soc_pct": round(r.soc_pct, 3), "completed": r.completed,
                                  "distance_m": round(r.distance_m, 3),
                                  "energy_out_kwh": round(r.energy_out, 6),
                                  "energy_in_kwh": round(r.energy_in, 6),
                                  "move_events": r.move_events}
                           for r in self.robots.values()},
                "queue_depth": len(self.queue), "blocked_docks": sorted(self.blocked_docks),
                # 库存期末账：给 check_inventory 做"状态 vs 事件流"的独立对账
                # （只有状态没有事件、或只有事件没有状态，都不足以证明守恒）
                "inventory": ({"stock": {sku: {loc: q for loc, q in rows.items()}
                                        for sku, rows in self.inv.items()},
                               "summary": {"enabled": self.inv_enabled,
                                           "units": round(self.inv_units_out, 6),
                                           "value": round(self.inv_value(), 2),
                                           "holding": round(self.inv_holding, 6),
                                           "stockout": self.inv_stockout_n,
                                           # 效期这一块要给 check_expiry 独立核对：
                                           # 只有状态没有事件、或只有事件没有状态，都不算证据。
                                           "expiry_enabled": self.lot_expiry_enabled,
                                           "expired_units": round(self.lot_expired_units, 6),
                                           "expired_value": round(self.lot_expired_value, 2),
                                           "expired_lots": self.lot_expired_n,
                                           "open_lots": sum(len(v) for v in self.lots.values()),
                                           "fefo_violations": self.fefo_violations}}
                              if self.inv_enabled else None)}

    # ---------- 快照（ADR-GAME-05 / 方案书 §2.4.2）----------
    # `_state()` 是给 KPI 对账用的**终态摘要**，故意保持原样不动（A 系列验收依赖它）。
    # 下面是另一个东西：可恢复的全量状态。字段必须穷举——少一块，续跑要几十个 tick
    # 之后才会"莫名偏了"，那时候已经查不出是哪块缺了。
    SNAPSHOT_SCHEMA = "3.0"

    def _lots_by_sku_loc(self):
        out = {}
        for (sku, loc), arr in sorted(self.lots.items()):
            if not arr:
                continue
            out.setdefault(sku, {})[loc] = [dict(l) for l in arr]
        return out

    def _inv_snapshot(self):
        """库存状态入快照。schema 1.0 的旧档没有这一块，restore 会显式拒绝
        （ADR-GAME-09 决策二）：补空账比报错危险得多。"""
        return {
            # 1.0 → 2.0：加入批次明细。旧档没有 lots，restore 会显式拒绝
            # （补一份"每个库位一个批次"的假账看起来能跑，但效期全是错的）。
            "schema": "2.0",
            "enabled": bool(self.inv_enabled),
            "stock": {sku: {loc: q for loc, q in rows.items()}
                      for sku, rows in self.inv.items()},
            # self.lots 的键是 (sku, loc) 元组，快照里按 sku → loc 两层更好读，
            # 也和第 1 层的 stock 结构对齐（对账时可以直接 zip 两层）。
            "lots": self._lots_by_sku_loc(),
            "lot_seq": self.lot_seq,
            "lot_policy": self.lot_policy,
            "lot_counters": {"opened": self.lot_opened_n,
                             "expired": self.lot_expired_n,
                             "expired_units": self.lot_expired_units,
                             "expired_value": self.lot_expired_value,
                             "fefo_violations": self.fefo_violations},
            "pending_replenish": {oid: dict(v) for oid, v in self.inv_pending.items()},
            "holding_accrued": self.inv_holding,
            "hold_t": self.inv_hold_t,
            "counters": {"stockout": self.inv_stockout_n,
                         "reorder": self.inv_reorder_n,
                         "refill": self.inv_refill_n,
                         "units_out": self.inv_units_out,
                         "units_req": self.inv_units_req},
            "reorder_factor": self.reorder_factor,
            "reorder_factor_by_sku": dict(self.reorder_factor_by_sku),
        }

    def snapshot(self):
        """全量可恢复状态（JSON-able）。

        `position_samples` 不入快照：它只喂 sim_positions.json（3D 位置抽样），
        不入任何 KPI，体积却有几百 KB。`samples` 必须入——`_kpi` 用它算
        idle_rate 与吞吐曲线，丢了续跑的 KPI 就和全程跑不一致了。
        """
        def conv(v):
            if isinstance(v, (list, tuple)):
                return [conv(x) for x in v]
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            return v

        counters = {k: getattr(self, k) for k in (
            "yield_n", "detour_n", "jam_clear_n", "max_queue_depth",
            "sep_violations", "reservation_double", "min_pair_m", "max_blocked_s",
            "energy_guard_n", "reservation_double")}
        counters["min_pair_m"] = (None if self.min_pair_m == float("inf")
                                  else self.min_pair_m)
        return {
            "schema_version": self.SNAPSHOT_SCHEMA,
            "kind": "game_snapshot",
            "scenario": self.sc["id"],
            "seed": self.seed,
            "tick_i": self.tick_i,
            "t_s": self.t,
            "n_ticks": self.n_ticks,
            "rng_s": self.rng.state(),
            "svc_rng_s": self.svc_rng.state(),
            "robots": {rid: r.serialize() for rid, r in self.robots.items()},
            "queue": conv(self.queue),
            "reserved": conv(self.reserved),
            "stations": conv(self.stations),
            "faults": conv(self.faults),
            "blocked_docks": sorted(self.blocked_docks),
            "next_arrival": (None if self.next_arrival == float("inf")
                             else self.next_arrival),
            "ext_i": self.ext_i,
            "order_n": self._order_n,
            "done": self.done,
            "demand": {"base_per_hour": self._base_per_hour,
                       "multiplier": self.demand_multiplier,
                       "per_hour": self.per_hour,
                       "lam": self.lam,
                       "price_overrides": dict(self.price_overrides),
                       "tariff_multiplier": self.tariff_multiplier},
            "policy": {"dispatch": self.dispatch_rule,
                       "charge": self.charge_policy,
                       "reserve_pct": self.reserve_pct,
                       "overrides": conv(self.overrides)},
            "energy": {"last_out": self._last_energy_out,
                       "last_motion": self._last_motion,
                       "last_in": self._last_energy_in},
            "log": self.log.snapshot(),
            "economy": (self.economy.snapshot_full() if getattr(self, "economy", None)
                        else None),
            "counters": counters,
            "samples": conv(self.samples),
            "inventory": self._inv_snapshot(),
            "run_dir": self._run_dir_rel(),
            "meta": {"level_id": (getattr(self, "level", {}) or {}).get("id", "")},
        }

    def _run_dir_rel(self):
        """快照要记住"我这一局的事件流在哪个目录"。

        续玩的语义是**接着同一份事件流写**（append），不是另开一局：
        另开一局就得把历史事件一起搬过去，否则 ledger 的幂等键会从 1 重号。
        """
        try:
            return self.out.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return self.out.resolve().as_posix()

    @classmethod
    def restore(cls, snap, scenario_path, sim_spec, fleet, demand, layout, grid, cfg,
                out_dir=None, external_orders=None):
        """从快照续跑。缺字段 / 版本不符 / 事件日志对不上 → 一律 raise。

        `append_log=True` 是关键：默认构造会用 `open(...,"w")` 把历史事件清空，
        那正是 R11。这里先以 append 重开，再由 `EventLog.restore` 校验磁盘 seq。
        """
        if not isinstance(snap, dict) or snap.get("kind") != "game_snapshot":
            raise ValueError("不是 game_snapshot：%r" % (snap or {}).get("kind"))
        if snap.get("schema_version") != cls.SNAPSHOT_SCHEMA:
            raise ValueError("快照版本不符：%r（本引擎支持 %r）"
                             % (snap.get("schema_version"), cls.SNAPSHOT_SCHEMA))
        required = ("tick_i", "t_s", "rng_s", "svc_rng_s", "robots", "queue", "stations",
                    "faults", "log", "counters", "policy", "demand", "inventory")
        missing = [k for k in required if k not in snap]
        if missing:
            raise ValueError("快照缺字段：%s" % ", ".join(missing))

        sim = cls(scenario_path, sim_spec, fleet, demand, layout, grid, cfg,
                  out_dir=out_dir, external_orders=external_orders, append_log=True)
        old_log = sim.log
        try:
            sim.log = EventLog.restore(sim.out / "sim_events.jsonl",
                                       sim.spec["output"]["event_ring"], snap["log"])
        finally:
            try:
                old_log.close()
            except Exception:                      # noqa: BLE001
                pass

        sim.tick_i = int(snap["tick_i"])
        sim.t = float(snap["t_s"])
        sim.n_ticks = int(snap.get("n_ticks", sim.n_ticks))
        sim.done = bool(snap.get("done", False))
        sim._started = True          # 已经在中途：别再 init 一次（会把 t 归零）
        sim.rng = Mulberry32.from_state(snap["rng_s"])
        sim.svc_rng = Mulberry32.from_state(snap["svc_rng_s"])
        sim.robots = {rid: Robot.deserialize(d, sim.model)
                      for rid, d in snap["robots"].items()}
        sim.n_robots = len(sim.robots)
        sim.queue = list(snap["queue"])
        sim.reserved = dict(snap["reserved"])
        sim.stations = dict(snap["stations"])
        sim.faults = list(snap["faults"])
        sim.blocked_docks = set(snap["blocked_docks"])
        sim.next_arrival = (float("inf") if snap.get("next_arrival") is None
                            else float(snap["next_arrival"]))
        sim.ext_i = int(snap.get("ext_i", 0))
        sim._order_n = int(snap.get("order_n", 0))

        d = snap["demand"]
        sim._base_per_hour = float(d["base_per_hour"])
        sim.demand_multiplier = float(d["multiplier"])
        sim.per_hour = float(d["per_hour"])
        sim.lam = float(d["lam"])
        sim.price_overrides = dict(d.get("price_overrides") or {})
        sim.tariff_multiplier = float(d.get("tariff_multiplier", 1.0))

        pol = snap["policy"]
        if pol["dispatch"] not in DISPATCH_RULES:
            raise ValueError("快照里的 dispatch.policy 非法：%r" % (pol["dispatch"],))
        if pol["charge"] not in CHARGE_POLICIES:
            raise ValueError("快照里的 battery.charge_policy 非法：%r" % (pol["charge"],))
        sim.dispatch_rule = pol["dispatch"]
        sim.charge_policy = pol["charge"]
        sim.reserve_pct = float(pol["reserve_pct"])
        sim.overrides = dict(pol.get("overrides") or {})

        e = snap["energy"]
        sim._last_energy_out = float(e["last_out"])
        sim._last_motion = float(e["last_motion"])
        sim._last_energy_in = float(e["last_in"])

        c = snap["counters"]
        for k in ("yield_n", "detour_n", "jam_clear_n", "max_queue_depth",
                  "sep_violations", "reservation_double", "energy_guard_n"):
            setattr(sim, k, int(c.get(k, 0)))
        sim.min_pair_m = (float("inf") if c.get("min_pair_m") is None
                          else float(c["min_pair_m"]))
        sim.max_blocked_s = float(c.get("max_blocked_s", 0.0))
        sim.samples = list(snap.get("samples") or [])

        if snap.get("economy"):
            import game_economy
            game_economy.restore_onto(sim, snap["economy"])
        sim._inv_restore(snap["inventory"])
        return sim

    def _inv_restore(self, inv):
        """库存账恢复 + **与事件流重放对账**（方案书 §5.4 的硬要求）。"""
        if not isinstance(inv, dict) or "stock" not in inv:
            raise ValueError("快照缺少 inventory.stock（有库存层的局面必须带账才能续跑）")
        self.inv_enabled = bool(inv.get("enabled", True))
        self.inv = {sku: {loc: float(q) for loc, q in rows.items()}
                    for sku, rows in (inv.get("stock") or {}).items()}
        # 批次：快照里有就必须重建；**效期开着却没有 lots 直接拒绝**，
        # 因为那种档"能跑但所有批次都消失了"，FEFO 会在续玩后静默退化成先到先出。
        self.lots, self.lot_heap = {}, []
        self.lot_seq = int(inv.get("lot_seq", 0))
        self.lot_policy = str(inv.get("lot_policy", self.lot_policy))
        lc = inv.get("lot_counters") or {}
        self.lot_opened_n = int(lc.get("opened", 0))
        self.lot_expired_n = int(lc.get("expired", 0))
        self.lot_expired_units = float(lc.get("expired_units", 0.0))
        self.lot_expired_value = float(lc.get("expired_value", 0.0))
        self.fefo_violations = int(lc.get("fefo_violations", 0))
        if self.lot_expiry_enabled:
            if "lots" not in inv:
                raise ValueError("快照缺少 inventory.lots（效期已启用，不能补空账续玩）")
            for sku, per in (inv.get("lots") or {}).items():
                for loc, arr in per.items():
                    for lot in arr:
                        self._lot_push(sku, loc, {"lot_id": lot["lot_id"],
                                                  "qty": float(lot["qty"]),
                                                  "t_in": float(lot.get("t_in", 0.0)),
                                                  "expiry_t": float(lot["expiry_t"])})
            self._inv_lot_check(fatal=True)
        self.inv_pending = {oid: dict(v)
                            for oid, v in (inv.get("pending_replenish") or {}).items()}
        self.inv_holding = float(inv.get("holding_accrued", 0.0))
        self.inv_hold_t = float(inv.get("hold_t", 0.0))
        c = inv.get("counters") or {}
        self.inv_stockout_n = int(c.get("stockout", 0))
        self.inv_reorder_n = int(c.get("reorder", 0))
        self.inv_refill_n = int(c.get("refill", 0))
        self.inv_units_out = float(c.get("units_out", 0.0))
        self.inv_units_req = float(c.get("units_req", 0.0))
        self.reorder_factor = float(inv.get("reorder_factor", 1.0))
        self.reorder_factor_by_sku = dict(inv.get("reorder_factor_by_sku") or {})
        if self.inv_enabled:
            self._inv_reconcile()

    def _inv_reconcile(self, tol=1e-6):
        """内存库存账 vs 从事件流重放 —— 不一致就 raise。

        为什么不只信快照：快照可能"看起来完整"但数值错位（漏 apply 一条增量、
        恢复顺序不对）。事件流是事实来源，重放对不上的存档不能续玩。
        """
        path = self.out / "sim_events.jsonl"
        if not path.exists():
            raise ValueError("没有事件流可对账：%s" % path)
        replay = {sku: {loc: float(q) for loc, q in rows.items()}
                  for sku, rows in (jload(DATA / "inventory_initial.json")["stock"]).items()}
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                e = json.loads(line)
                typ = e.get("type")
                sku, loc, q = e.get("sku"), e.get("location"), float(e.get("qty") or 0)
                if typ in ("STOCK_DECREMENT", "STOCK_EXPIRED"):
                    replay.setdefault(sku, {})[loc] = replay.get(sku, {}).get(loc, 0.0) - q
                elif typ in ("STOCK_RECEIPT", "STOCK_REPLENISH_ARRIVED"):
                    replay.setdefault(sku, {})[loc] = replay.get(sku, {}).get(loc, 0.0) + q
        bad = []
        for sku in set(replay) | set(self.inv):
            for loc in set(replay.get(sku, {})) | set(self.inv.get(sku, {})):
                a = replay.get(sku, {}).get(loc, 0.0)
                b = self.inv.get(sku, {}).get(loc, 0.0)
                if abs(a - b) > tol:
                    bad.append((sku, loc, a, b))
        if bad:
            raise ValueError("快照库存与事件流重放不一致（%d 处），前 3 处：%s"
                             % (len(bad), bad[:3]))


def load_external_orders(path, layout=None):
    """把 CSV/WMS 适配器产出的事件流映射成引擎的订单结构。

    返回 None 表示"没有外部数据，按需求 spec 合成"——调用方不必分支。
    引用完整性由适配器负责（缺 from/to 会在那一步 FAIL），所以这里只丢弃
    解析不了的行，并在 stderr 报数量，不静默吞掉。
    """
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise SystemExit("EVENTS_NOT_FOUND %s" % p)
    lay = layout or jload(DATA / "layout.json")
    loc = {}
    for wh in lay.get("warehouses", []):
        for item in wh.get("locations", []):
            loc[item["id"]] = item
    orders, dropped = [], 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        src, dst = loc.get(r.get("from")), loc.get(r.get("to"))
        if not src or not dst:
            dropped += 1
            continue
        orders.append({
            "order_id": r.get("order_id"),
            "type": r.get("type", "OUTBOUND"),
            "warehouse": r.get("warehouse_id") or src["id"].split("-")[0],
            "pick_loc": src["id"], "drop_loc": dst["id"],
            "pick_xy": tuple(src["access_point"]), "drop_xy": tuple(dst["access_point"]),
            "t_arrive": float(r.get("t_request", 0.0)),
            "priority": r.get("priority", "P2"),
            "deadline": float(r.get("deadline", r.get("t_request", 0.0))),
            "reassignments": 0,
        })
    if dropped:
        print("EVENTS_WARN 丢弃 %d 行（from/to 不在 layout 里）" % dropped, file=sys.stderr)
    orders.sort(key=lambda o: (o["t_arrive"], str(o["order_id"])))
    print("EVENTS_OK %d 单，来源 %s" % (len(orders), p.name), file=sys.stderr)
    return orders


def run_one(scenario_path, args, sim_spec, fleet, demand, layout, grid, cfg, out_dir=None):
    sim = Simulation(scenario_path, sim_spec, fleet, demand, layout, grid, cfg,
                     out_dir=out_dir,
                     external_orders=load_external_orders(getattr(args, "events", None)))
    if args.horizon:
        sim.horizon = args.horizon
    if args.seed is not None:
        sim.seed = args.seed
        sim.rng = Mulberry32(args.seed)
    m = sim.run()
    print("SIM_OK %s agents=%d completed=%s charge=%s lost=%s wall=%.1fs -> %s"
          % (sim.sc["id"], m["metrics"]["agents"]["value"],
             m["metrics"]["orders_completed"]["value"],
             m["metrics"]["charge_events"]["value"],
             m["metrics"]["orders_lost"]["value"],
             m["metrics"]["wall_clock_s"]["value"], sim.out.as_posix()))
    # 写相对路径：绝对路径会把 C:/Users/<name>/... 带进 out/ 与交付包
    try:
        rel = sim.out.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        rel = sim.out.as_posix()
    return {"scenario": sim.sc["id"], "run_dir": rel,
            "run_id": sim.run_id, "seed": sim.seed,
            "metrics": {k: v["value"] for k, v in m["metrics"].items()}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="tick-level warehouse simulation")
    ap.add_argument("--scenario", default="config/scenarios/baseline.json")
    ap.add_argument("--station-slots", type=int, default=None,
                    help="把工位并行位强制为该值（验证排队会真的发生）")
    ap.add_argument("--dock-slots", type=int, default=None,
                    help="把每个月的门数强制为该值（验证月台排队会真的发生）")
    ap.add_argument("--all", action="store_true", help="run all 8 frozen scenarios")
    ap.add_argument("--include-custom", action="store_true",
                    help="also run user scenarios from config/scenarios/custom/")
    ap.add_argument("--horizon", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--events", default=None,
                    help="外部订单事件 JSONL（CSV/WMS 适配器产出）；给了就不再合成需求")
    ap.add_argument("--no-index", action="store_true",
                    help="不写 out/sim_index.json（试点运行不该顶替 8 场景索引）")
    ap.add_argument("--out-dir", default=None,
                    help="write runs and index under this directory (for determinism tests)")
    args = ap.parse_args(argv)
    base = Path(args.out_dir).resolve() if args.out_dir else None
    OUT.mkdir(parents=True, exist_ok=True)
    sim_spec = jload(CONFIG / "sim_spec.json")
    fleet = jload(CONFIG / "fleet_spec.json")
    demand = jload(CONFIG / "demand_spec.json")
    layout = jload(DATA / "layout.json")
    grid = jload(DATA / "nav_grid.json")
    cfg = jload(DATA / "config.json")
    if args.station_slots:
        Simulation.station_slots_override = args.station_slots
    Simulation.dock_slots_override = getattr(args, "dock_slots", None)
    scenarios = (sorted(glob.glob(str(CONFIG / "scenarios" / "*.json")))
                 if args.all else [str(ROOT / args.scenario)])
    if args.all and args.include_custom:
        scenarios += sorted(glob.glob(str(CONFIG / "scenarios" / "custom" / "*.json")))
    runs = [run_one(p, args, sim_spec, fleet, demand, layout, grid, cfg,
                    out_dir=(base / Path(p).stem) if base else None)
            for p in scenarios]
    idx = {"schema_version": "1.0", "kind": "sim_index",
           "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "engine": "sim", "runs": runs}
    idx_dir = base if base else OUT
    if not getattr(args, "no_index", False):
        jdump(idx_dir / "sim_index.json", idx)
    if not base and not getattr(args, "no_index", False):
        jdump(OUT / "sim_latest.json", {"kind": "sim_latest", "run_dir": runs[-1]["run_dir"],
                                        "run_id": runs[-1]["run_id"],
                                        "scenario": runs[-1]["scenario"]})
    print("SIM_INDEX_OK runs=%d" % len(runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
