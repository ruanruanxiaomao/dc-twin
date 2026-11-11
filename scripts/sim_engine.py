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

ROOT = Path(__file__).resolve().parent.parent
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
    "DOCK_BLOCKED", "DOCK_REOPENED", "REPLAN", "WAIT_CONFLICT", "ENERGY_TICK",
    "STATION_ENTER", "STATION_QUEUED", "STATION_EXIT", "DEADLOCK_YIELD",
    "JAM_CLEARED",
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
    def __init__(self, path, ring=5000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "w", encoding="utf-8")
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

    def __init__(self, scenario_path, sim_spec, fleet, demand, layout, grid, cfg, out_dir=None):
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
        self.per_hour = float(self.overrides.get("demand.orders.per_hour",
                                                 self.demand["orders"]["per_hour"]))
        self.n_robots = self._fleet_size()
        self.run_id = "%s/%s" % (self.sc["id"], dt.datetime.now(dt.timezone.utc)
                                 .strftime("%Y%m%dT%H%M%SZ"))
        self.out = Path(out_dir) if out_dir else RUNS / self.sc["id"] / Path(self.run_id).name
        self.out.mkdir(parents=True, exist_ok=True)
        self.log = EventLog(self.out / "sim_events.jsonl", self.spec["output"]["event_ring"])
        self.robots = self._make_robots()
        self.queue = []
        self.reserved = {}
        self.blocked_docks = set()
        self.stations = self._load_stations()
        if Simulation.station_slots_override:
            for v in self.stations.values():
                v["slots"] = int(Simulation.station_slots_override)
        self.faults = self._disruptions()
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
                        "mean": float(mean),
                        "cv": float(svc.get("cv", 0.0) or 0.0),
                        "slots": max(1, int(proc.get("parallel_slots") or 1)),
                        "buffer": int((proc.get("buffer") or {}).get("capacity") or 0),
                        "busy": 0, "queue": [], "served": 0, "busy_s": 0.0,
                        "wait_sum": 0.0, "wait_n": 0, "qmax": 0,
                    }
            except Exception:
                pass
        return out

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
            cand = [r for r in idle if r.wid == order["warehouse"]]
            if not cand:
                continue
            r = min(cand, key=lambda x: math.dist(x.pos, order["pick_xy"]))
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
    def run(self):
        self._order_n = 0
        t = 0.0
        tick = 0
        n_ticks = int(self.horizon / self.tick)
        lam = self.per_hour / 3600.0
        self.next_arrival = self.rng.expovariate(lam) if lam > 0 else float("inf")
        while tick < n_ticks:
            t = tick * self.tick
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
            future = [x for x in scheduled if x > t + 1e-9]
            next_t = min(future) if future else float("inf")
            if not busy and not self.queue and not due:
                if next_t == float("inf"):
                    tick = n_ticks          # no future event: jump to horizon end
                    continue
                if next_t > t + self.tick:
                    jump = int((next_t - t) / self.tick)
                    tick = min(n_ticks, tick + jump)
                    continue

            # 1) demand
            while self.next_arrival <= t:
                self._order_n += 1
                o = self.new_order(self.next_arrival)
                if o:
                    self.queue.append(o)
                    self.log.emit(self.next_arrival, "ORDER_ARRIVED",
                                  order_id=o["order_id"], order_type=o["type"],
                                  warehouse=o["warehouse"], priority=o["priority"])
                self.next_arrival += self.rng.expovariate(lam) if lam > 0 else float("inf")

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
            if tick % 10 == 0:
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
            if tick % self.spec["output"]["kpi_every_ticks"] == 0:
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
            tick += 1

        # Flush the partial tail window so SUM(ENERGY_TICK) closes exactly with
        # sim_state energy_out/energy_in (S2 requires <1% error).
        every = self.spec["output"]["kpi_every_ticks"]
        self._emit_energy_window(self.horizon, (n_ticks % every or every) * self.tick,
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
            if r.soc_pct < self.critical_pct:
                self.log.emit(t, "BATTERY_CRITICAL", agent=r.id,
                              soc_pct=round(r.soc_pct, 3))
                self._start_charge(r, t)
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
            self.log.emit(t, "TASK_COMPLETED", agent=r.id, order_id=o["order_id"],
                          order_type=o["type"], warehouse=o["warehouse"],
                          legs_m={"empty_out": round(r.leg_m[0], 3),
                                  "loaded": round(r.leg_m[1], 3),
                                  "empty_back": round(r.leg_m[2], 3)},
                          energy_kwh=round(r.leg_m[0] * r.cons + r.leg_m[1] * r.cons
                                           + r.leg_m[2] * r.cons, 6),
                          tardiness_s=round(max(0.0, t - o["deadline"]), 3),
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
        st_total = len(self.stations)
        st_served = sum(v["served"] for v in self.stations.values())
        st_busy_s = sum(v["busy_s"] for v in self.stations.values())
        st_wait_n = sum(v["wait_n"] for v in self.stations.values())
        st_wait_s = sum(v["wait_sum"] for v in self.stations.values())
        st_qmax = max([v["qmax"] for v in self.stations.values()] + [0])
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
        m["wall_clock_s"] = M(round(wall_s, 3), "s", "Wall clock", "time.time() delta")
        return {"schema_version": "1.0", "kind": "sim_kpi", "engine": "sim",
                "scenario": self.sc["id"], "seed": self.seed,
                "run_id": self.run_id, "model": "measured",
                "metrics": m, "samples": self.samples[-200:],
                "note": "measured from sim_events.jsonl; analytic M/M/c KPIs are separate"}

    def _state(self):
        return {"schema_version": "1.0", "kind": "sim_state", "scenario": self.sc["id"],
                "robots": {r.id: {"state": r.state, "pos": [round(v, 3) for v in r.pos],
                                  "soc_initial_kwh": round(r.soc_initial, 6),
                                  "soc_final_kwh": round(r.soc, 6),
                                  "soc_pct": round(r.soc_pct, 3), "completed": r.completed,
                                  "distance_m": round(r.distance_m, 3),
                                  "energy_out_kwh": round(r.energy_out, 6),
                                  "energy_in_kwh": round(r.energy_in, 6),
                                  "move_events": r.move_events}
                           for r in self.robots.values()},
                "queue_depth": len(self.queue), "blocked_docks": sorted(self.blocked_docks)}


def run_one(scenario_path, args, sim_spec, fleet, demand, layout, grid, cfg, out_dir=None):
    sim = Simulation(scenario_path, sim_spec, fleet, demand, layout, grid, cfg,
                     out_dir=out_dir)
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
    ap.add_argument("--all", action="store_true", help="run all 8 frozen scenarios")
    ap.add_argument("--include-custom", action="store_true",
                    help="also run user scenarios from config/scenarios/custom/")
    ap.add_argument("--horizon", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
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
    jdump(idx_dir / "sim_index.json", idx)
    if not base:
        jdump(OUT / "sim_latest.json", {"kind": "sim_latest", "run_dir": runs[-1]["run_dir"],
                                        "run_id": runs[-1]["run_id"],
                                        "scenario": runs[-1]["scenario"]})
    print("SIM_INDEX_OK runs=%d" % len(runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
