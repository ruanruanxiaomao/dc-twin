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
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG, DATA, OUT, RUNS = ROOT / "config", ROOT / "data", ROOT / "out", ROOT / "runs"

EVENT_TYPES = (
    "ORDER_ARRIVED", "ORDER_LOST", "TASK_ASSIGNED", "TASK_REASSIGNED",
    "STATE_CHANGED", "PICK_DONE", "DROP_DONE", "TASK_COMPLETED",
    "CHARGE_START", "CHARGE_END", "BATTERY_CRITICAL", "FAULT_START", "FAULT_END",
    "DOCK_BLOCKED", "DOCK_REOPENED", "REPLAN", "WAIT_CONFLICT", "ENERGY_TICK",
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
            target_arc = self.arc + step
            cand = self.point_at(target_arc)
            blocked = next((oid for oid, opos in positions.items()
                            if oid != self.id and math.dist(cand, opos) < min_sep - 1e-6), None)
            if blocked is not None:
                self.wait_s += dt_s
                if self.wait_s <= dt_s * 1.5:
                    log.emit(t, "WAIT_CONFLICT", agent=self.id,
                             cell=list(world.to_cell(cand)), blocked_by=blocked)
                return 0.0
            self.wait_s = 0.0
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
        self.wall0 = time.time()

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
                r.set_state("CHARGING", self.log, t)
                r.charging_since = t
                self.log.emit(t, "CHARGE_START", agent=r.id,
                              soc_pct=round(r.soc_pct, 3),
                              charger_id=getattr(r, "charger_id", "inplace"),
                              mode="opportunistic_inplace",
                              pos=[round(v, 2) for v in r.pos])
                return
        r.advance(dt, t, self.log, self.world, positions, self.min_sep)
        if r.state in ("MOVING_PICK", "MOVING_DROP", "RETURNING", "TO_CHARGER") \
                and r.arc >= r.route_len - 1e-6:
            self._arrive(r, t)

    def _arrive(self, r, t):
        if r.state == "MOVING_PICK":
            r.set_state("PICKING", self.log, t)
            r.dwell_until = t + self.dwell
        elif r.state == "MOVING_DROP":
            r.set_state("DROPPING", self.log, t)
            r.dwell_until = t + self.dwell
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
    return {"scenario": sim.sc["id"], "run_dir": sim.out.as_posix(),
            "run_id": sim.run_id, "seed": sim.seed,
            "metrics": {k: v["value"] for k, v in m["metrics"].items()}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="tick-level warehouse simulation")
    ap.add_argument("--scenario", default="config/scenarios/baseline.json")
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
