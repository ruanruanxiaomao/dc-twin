"""plan_paths.py — 闭环作业路径规划（第六阶段整改）。

用户质疑「车走到货架最里面就没了，不应该是个循环吗」：旧版是单程搬运，
到最深处直接结束并离场。本版每台车一个完整作业循环：

    HOME(待命) --① 空驶--> PICK(取货) --② 载货--> DROP(交付) --③ 空驶--> HOME

关键语义（与第二轮审计 P0-2 对齐）：
  · **全程在场**：所有 AGV 从 t=0 到仿真结束都在场景里；未派出的车停在 HOME，
    完成回程的车也停在 HOME，不再 hide_render「离场」；
  · 因此交通判据改为严格口径：任意时刻、任意两车中心距 >= 2×AGV_RADIUS；
  · 每台车的 HOME 是专用待命位（贴墙 / 侧带），且 A* 对**其它车的 HOME**
    加软代价，避免「停着的车被经过的车擦到」；
  · 优先级 + 出发错峰仍然是轻量近似（WareTwin 的 tick 级 cell reservation
    未移植），但现在不再用「完成即离场」来掩盖冲突。

产出：data/paths.jsonl · routes.json · agent_schedules.json · path_metrics.json
"""
import bisect
import heapq
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
sys.path.insert(0, os.path.dirname(__file__))
from nav_grid import load_grid
from geometry import AGV_RADIUS, MIN_SEP, SPEED

CFG = json.load(open(os.path.join(DATA, 'config.json'), encoding='utf-8'))
LAYOUT = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
TASKS = json.load(open(os.path.join(DATA, 'tasks.json'), encoding='utf-8'))
GRID = load_grid()

AGVC = CFG['agv']
OMEGA = AGVC['omega_rad_s']
DWELL_PICK = AGVC['dwell_pick_s']
MAX_WAIT = AGVC['max_schedule_wait_s']
TASK_BY_AGENT = {t['agent_id']: t for t in TASKS['tasks']
                 if t['status'] == 'ASSIGNED'}
FPS = CFG['render']['fps']
SAMPLE_DT = 0.1             # 排程采样（traffic_check 用 0.05 s 独立复核）
SCHEDULE_MARGIN = 0.06      # 排程留量，避免粗采样漏掉边界冲突
MAX_DTH = math.radians(9.0)
SQRT2 = math.sqrt(2.0)
STEP = GRID.cell
DIRS = ((1, 0, STEP), (-1, 0, STEP), (0, 1, STEP), (0, -1, STEP),
        (1, 1, STEP * SQRT2), (1, -1, STEP * SQRT2),
        (-1, 1, STEP * SQRT2), (-1, -1, STEP * SQRT2))

LOC, FACE, SPAWNS, HOMES = {}, {}, {}, {}
for w in LAYOUT['warehouses']:
    LOC[w['id']] = {l['id']: l['access_point'] for l in w['locations']}
    for l in w['locations']:
        if 'face_deg' in l:
            FACE[l['id']] = math.radians(l['face_deg'])
    for rb in w['spawn']['robots']:
        SPAWNS[rb['id']] = (rb['position'][0], rb['position'][2])
        HOMES[rb['id']] = tuple(LOC[w['id']][rb['home_id']])


def home_avoid_cells(aid):
    """其它车 HOME 周围的软代价格：让 A* 主动绕开停着的车。"""
    out = {}
    rad = MIN_SEP + 0.35
    for other, home in HOMES.items():
        if other == aid:
            continue
        c, r = GRID.to_cell(home)
        span = int(math.ceil(rad / STEP))
        for dc in range(-span, span + 1):
            for dr in range(-span, span + 1):
                cc, rr = c + dc, r + dr
                if not GRID.walkable(cc, rr):
                    continue
                x, z = GRID.center(cc, rr)
                if math.hypot(x - home[0], z - home[1]) <= rad:
                    out[rr * GRID.cols + cc] = 12.0
    return out


def astar(start, goal, avoid=None):
    avoid = avoid or {}
    s = GRID.nearest_walkable(*GRID.to_cell(start))
    g = GRID.nearest_walkable(*GRID.to_cell(goal))
    if s == g:
        return []
    n = GRID.cols * GRID.rows
    gs = [math.inf] * n
    came = [-1] * n
    closed = bytearray(n)
    si, gi = s[1] * GRID.cols + s[0], g[1] * GRID.cols + g[0]

    def h(c, r):
        dx, dz = abs(c - g[0]), abs(r - g[1])
        return STEP * ((dx + dz) + (SQRT2 - 2.0) * min(dx, dz))

    gs[si] = 0.0
    seq = 0
    heap = [(h(*s), seq, si)]
    while heap:
        _, _, cur = heapq.heappop(heap)
        if cur == gi:
            break
        if closed[cur]:
            continue
        closed[cur] = 1
        cc, cr = cur % GRID.cols, cur // GRID.cols
        for dx, dz, base in DIRS:
            nc, nr = cc + dx, cr + dz
            if not GRID.walkable(nc, nr):
                continue
            if dx and dz and (not GRID.walkable(cc + dx, cr) or
                              not GRID.walkable(cc, cr + dz)):
                continue  # 禁止切角
            ni = nr * GRID.cols + nc
            ng = gs[cur] + base + avoid.get(ni, 0.0)
            if ng < gs[ni]:
                gs[ni] = ng
                came[ni] = cur
                seq += 1
                heapq.heappush(heap, (ng + h(nc, nr), seq, ni))
    if came[gi] == -1:
        return None
    out, i = [], gi
    while i != si:
        out.append((i % GRID.cols, i // GRID.cols))
        i = came[i]
    out.reverse()
    return [GRID.center(c, r) for c, r in out]


def poly_length(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def unwrap(theta, prev):
    while theta - prev > math.pi:
        theta -= 2 * math.pi
    while theta - prev < -math.pi:
        theta += 2 * math.pi
    return theta


def build_route(task):
    """闭环折点：HOME -> 取货 -> 交付 -> HOME。

    返回 (clean 折点, {idx: (lid, face, phase)}, 段界 [pick_idx, drop_idx, end_idx])。
    """
    aid = task['agent_id']
    home = HOMES[aid]
    wid = task['warehouse_id']
    lg = task['legs'][0]
    stops = [(tuple(LOC[wid][lg['from']]), lg['from'], 'PICK'),
             (tuple(LOC[wid][lg['to']]), lg['to'], 'DROP')]
    waypoints = [(home, None, 'START')] + stops + [(home, None, 'PARK')]

    route = [home]
    dwell_lid = {}
    for b, lid, phase in waypoints[1:]:
        a = route[-1]
        leg = astar(a, b, home_avoid_cells(aid))
        if leg is None:
            raise RuntimeError('ASTAR_NO_PATH %s %s -> %s' % (aid, a, b))
        route.extend(leg)
        if lid:
            dwell_lid[len(route) - 1] = (lid, phase)
    clean = [route[0]]
    dwell, bounds = {}, {}
    for i, p in enumerate(route[1:], 1):
        if math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) <= 1e-9:
            continue
        clean.append(p)
        if i in dwell_lid:
            lid, phase = dwell_lid[i]
            dwell[len(clean) - 1] = (lid, FACE.get(lid), phase)
            bounds['pick' if phase == 'PICK' else 'drop'] = len(clean) - 1
    bounds['end'] = len(clean) - 1
    return clean, dwell, bounds


def _rotate(out, t, p, src, dst, aid, task, phase='ROTATE'):
    diff = dst - src
    if abs(diff) < 1e-9:
        return t, src
    n = max(1, int(math.ceil(abs(diff) / MAX_DTH)))
    step_t = (abs(diff) / n) / OMEGA
    for k in range(1, n):
        t += step_t
        out.append(_kf(t, p, src + diff * k / n, aid, task, phase))
    t += step_t
    return t, dst


def emit_keyframes(route, dwell, aid, task):
    """弧长/速度定时间；取货/交付点「转向作业面 → 停靠 → 转回」；收尾 PARK。"""
    out = []
    t = 0.0
    prev_theta = None
    for i, p in enumerate(route):
        if i > 0:
            t += math.hypot(p[0] - route[i - 1][0], p[1] - route[i - 1][1]) / SPEED
        if i + 1 < len(route):
            raw = math.atan2(route[i + 1][1] - p[1], route[i + 1][0] - p[0])
        else:
            q = route[i - 1] if i else p
            raw = math.atan2(p[1] - q[1], p[0] - q[0]) if i else 0.0
        theta = unwrap(raw, prev_theta) if prev_theta is not None else raw
        if prev_theta is not None and abs(theta - prev_theta) > MAX_DTH:
            t, theta = _rotate(out, t, p, prev_theta, theta, aid, task)
        out.append(_kf(t, p, theta, aid, task,
                       'PARK' if i == len(route) - 1 else 'MOVE'))
        if i in dwell:
            lid, face, phase = dwell[i]
            if face is not None:
                f = unwrap(face, theta)
                t, facing = _rotate(out, t, p, theta, f, aid, task, 'FACE')
                prev_theta = facing
            else:
                facing = theta
            t += DWELL_PICK
            out.append(_kf(t, p, facing, aid, task, phase))
            prev_theta = facing
        else:
            prev_theta = theta
    return out


def _kf(t, p, theta, aid, task, phase):
    return {'t': round(t, 4), 'agent_id': aid,
            'x': round(p[0], 4), 'y': round(p[1], 4), 'theta': round(theta, 5),
            'phase': phase, 'event_id': task['event_id'], 'task_id': task['task_id']}


def pos_at(pts, ts, t):
    if t <= ts[0]:
        return pts[0]['x'], pts[0]['y']
    if t >= ts[-1]:
        return pts[-1]['x'], pts[-1]['y']
    i = bisect.bisect_right(ts, t) - 1
    a, b = pts[i], pts[i + 1]
    u = 0.0 if b['t'] == a['t'] else (t - a['t']) / (b['t'] - a['t'])
    return (a['x'] + (b['x'] - a['x']) * u, a['y'] + (b['y'] - a['y']) * u)


def sample(pts, ts, s_end):
    return [pos_at(pts, ts, s * SAMPLE_DT) for s in range(s_end + 1)]


def schedule(timelines):
    """严格口径优先级排程：所有车 t=0 起就在场，完成回 HOME 后继续占位。

    仍只检查候选车与已排程车（已排程车彼此在上一次迭代已解决，且停在
    互不重叠的 HOME），因此复杂度为 O(车数 × 出发尝试 × 采样数 × 车数)。
    """
    scheduled = []          # [(pts, ts, sampled)]
    result = {}
    max_s = 0

    def order_key(aid):
        t = TASK_BY_AGENT[aid]
        return (int(t.get('priority', 2)), float(t.get('t_request', 0.0)), aid)

    for aid in sorted(timelines, key=order_key):
        task = TASK_BY_AGENT[aid]
        t_req = float(task.get('t_request', 0.0))
        deadline = float(task.get('deadline', 1e9))
        base = timelines[aid]
        home = HOMES[aid]
        meta = {'event_id': task['event_id'], 'task_id': task['task_id']}

        # 静态不可解检测：HOME 与任何其它 HOME 太近
        for other, hp in HOMES.items():
            if other != aid and math.hypot(home[0] - hp[0], home[1] - hp[1]) < MIN_SEP:
                raise RuntimeError('HOME_TOO_CLOSE %s <-> %s' % (aid, other))

        chosen, t0 = None, max(0.0, t_req)
        while t0 - t_req <= MAX_WAIT:
            shifted = [dict(p, t=round(p['t'] + t0, 4)) for p in base]
            if t0 > 0:
                shifted.insert(0, _kf(0.0, home, base[0]['theta'], aid, meta, 'PARK'))
            ts = [p['t'] for p in shifted]
            s_end = max(max_s, int(shifted[-1]['t'] / SAMPLE_DT) + 1)
            me = sample(shifted, ts, s_end)
            ok = True
            for pts, ots, opos in scheduled:
                for s in range(s_end + 1):
                    q = opos[s] if s < len(opos) else (pts[-1]['x'], pts[-1]['y'])
                    if math.hypot(me[s][0] - q[0], me[s][1] - q[1]) < \
                            MIN_SEP + SCHEDULE_MARGIN:
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                chosen = (shifted, ts, me)
                break
            t0 += 0.5
        if chosen is None:
            raise RuntimeError('SCHEDULE_FAIL %s: no conflict-free t0 within %s s'
                               % (aid, MAX_WAIT))
        shifted, ts, me = chosen
        # 旧车在新增的时间范围里停在各自的 HOME，补齐采样
        if len(me) - 1 > max_s:
            for i, (pts, ots, opos) in enumerate(scheduled):
                tail = (pts[-1]['x'], pts[-1]['y'])
                opos.extend([tail] * (len(me) - len(opos)))
                scheduled[i] = (pts, ots, opos)
            max_s = len(me) - 1
        scheduled.append((shifted, ts, me))
        t_end = shifted[-1]['t']
        result[aid] = {
            'priority': int(task.get('priority', 2)),
            't_request_s': round(t_req, 2), 'deadline_s': round(deadline, 2),
            'departure_t_s': round(t0, 2),
            'release_wait_s': round(t_req, 2),
            'conflict_wait_s': round(t0 - t_req, 2),
            't_end_s': t_end,
            'tardiness_s': round(max(0.0, t_end - deadline), 2),
            **meta,
        }
    return result, [pts for pts, _, _ in scheduled]


def main():
    assigned = [t for t in TASKS['tasks'] if t['status'] == 'ASSIGNED']
    timelines, routes, lengths, bounds = {}, {}, {}, {}
    total_m = 0.0
    stops_n = 0
    for task in assigned:
        aid = task['agent_id']
        route, dwell, b = build_route(task)
        routes[aid] = [[round(p[0], 4), round(p[1], 4)] for p in route]
        bounds[aid] = b
        timelines[aid] = emit_keyframes(route, dwell, aid, task)
        stops_n += len(dwell)
        lengths[aid] = round(poly_length(route), 3)
        total_m += lengths[aid]

    if os.environ.get('PLAN_DIAG'):
        for aid in sorted(timelines):
            print('DIAG', aid, 'path_m', lengths[aid],
                  'dur_s', round(timelines[aid][-1]['t'], 1),
                  'keyframes', len(timelines[aid]))
        print('DIAG_TOTAL_M', round(total_m, 1))
        return

    schedules, all_timelines = schedule(timelines)
    t_end = max(p['t'] for pts in all_timelines for p in pts)
    # 全程在场：每台车都在全局 t_end 补一个 HOME  PARK 关键帧
    for pts in all_timelines:
        aid = pts[0]['agent_id']
        task = TASK_BY_AGENT[aid]
        if pts[-1]['t'] < t_end - 1e-6:
            pts.append(_kf(t_end, (pts[-1]['x'], pts[-1]['y']), pts[-1]['theta'],
                           aid, task, 'PARK'))
    rows = [p for pts in all_timelines for p in pts]
    rows.sort(key=lambda p: (p['t'], p['agent_id']))

    with open(os.path.join(DATA, 'paths.jsonl'), 'w', encoding='utf-8') as f:
        for p in rows:
            f.write(json.dumps(p, ensure_ascii=False) + '\n')
    with open(os.path.join(DATA, 'routes.json'), 'w', encoding='utf-8') as f:
        json.dump({'units': 'm', 'coord': 'layout 2D [x,z]',
                   'note': '闭环折点：HOME → PICK → DROP → HOME；不含停靠/旋转关键帧',
                   'route_kind': 'CLOSED_LOOP', 'bounds': bounds,
                   'routes': routes}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA, 'agent_schedules.json'), 'w', encoding='utf-8') as f:
        json.dump({'method': 'strict-presence priority planning; all AGVs in scene '
                             'from t=0 to t_end; completed AGVs park at HOME; '
                             'min center sep %.4f m' % MIN_SEP,
                   'speed_mps': SPEED, 'schedules': schedules},
                  f, ensure_ascii=False, indent=2)

    metrics = {
        'method': 'closed-loop task-driven 8-dir A* on 0.5 m grid '
                  '(AGV_RADIUS inflated, no corner cutting, HOME-avoid soft cost) '
                  '+ arc-length timing + strict-presence staggering',
        'reference': 'WareTwin backend/app/sim/astar.py (source-reviewed)',
        'grid': {'cell_size_m': GRID.cell, 'cols': GRID.cols, 'rows': GRID.rows,
                 'agv_radius_m': AGV_RADIUS},
        'speed_mps': SPEED, 'omega_rad_s': OMEGA, 'min_sep_m': MIN_SEP,
        'agents': len(routes), 'tasks': len(assigned),
        'pending_events': TASKS['summary']['pending'],
        'route_kind': 'CLOSED_LOOP',
        'loop_legs': ['DEADHEAD_OUT', 'LOADED', 'DEADHEAD_BACK'],
        'pick_drop_stops': stops_n, 'dwell_pick_s': DWELL_PICK,
        'return_to_home': True,
        'agents_present_whole_time': True,
        'astar_failures': 0,
        'total_path_m': round(total_m, 2),
        'avg_path_m': round(total_m / max(len(routes), 1), 2),
        'duration_s': round(t_end, 2), 'fps': FPS,
        'samples': len(rows),
        'max_departure_t_s': max(s['departure_t_s'] for s in schedules.values()),
        'max_conflict_wait_s': max(s['conflict_wait_s'] for s in schedules.values()),
        'total_conflict_wait_s': round(sum(s['conflict_wait_s'] for s in schedules.values()), 2),
        'event_coverage': round(len(assigned) / max(TASKS['summary']['events'], 1), 4),
        'event_types_executed': TASKS['summary']['event_types_executed'],
        'on_time_rate': round(sum(1 for s in schedules.values() if s['tardiness_s'] <= 0)
                              / max(len(schedules), 1), 3),
        'max_tardiness_s': max(s['tardiness_s'] for s in schedules.values()),
        'avg_tardiness_s': round(sum(s['tardiness_s'] for s in schedules.values())
                                 / max(len(schedules), 1), 2),
        'speed_disclaimer': 'all moving segments use exactly %.2f m/s; '
                            'dwell/rotate/park segments are stationary' % SPEED,
    }
    euc = 0.0
    for task in assigned:
        wid, aid = task['warehouse_id'], task['agent_id']
        sp = HOMES[aid]
        pts = [sp] + [tuple(LOC[wid][lg[k]]) for lg in task['legs']
                      for k in ('from', 'to')] + [sp]
        euc += sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    metrics['path_to_euclid_ratio'] = round(total_m / euc, 3) if euc else 0

    with open(os.path.join(DATA, 'path_metrics.json'), 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print('PLAN_OK', json.dumps(metrics, ensure_ascii=False))


if __name__ == '__main__':
    main()
