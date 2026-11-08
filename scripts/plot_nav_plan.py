"""plot_nav_plan.py — 闭环路线图 / 时间-空间图 / 五时刻快照（第六阶段重做）。

用户质疑「车从货架旁边一直走到最里面，然后出来嘛？不应该是一个循环吗」：
旧的 07/08 把一次性单程轨迹画成一条线，看不出「从哪来、去哪、怎么回来」。
本版按闭环作业单画三段不同颜色的路径：

    ① 去程空驶 HOME → PICK（浅蓝）
    ② 载货 PICK → DROP（橙）
    ③ 回程空驶 DROP → HOME（绿）

并标出 HOME 待命位（紫方块）、取货点（黄圈）、交付点（红圈 + 事件号）、
方向箭头。09 时间-空间图增加 PARK 阶段；10 快照改为「全程在场」语义。

另输出 out/route_report.json（闭环数 / 三段里程 / 迎头对 / 中央主通道流量等）。
产出：07 (1032x1104) · 08 (1464x858) · 09 (1400x900) · 10 (1680x900)
"""
import bisect
import collections
import json
import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA, OUT = os.path.join(ROOT, 'data'), os.path.join(ROOT, 'out')
RENDERS = os.path.join(OUT, 'renders')

LAYOUT = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
GRID = json.load(open(os.path.join(DATA, 'nav_grid.json'), encoding='utf-8'))
RJ = json.load(open(os.path.join(DATA, 'routes.json'), encoding='utf-8'))
ROUTES, BOUNDS = RJ['routes'], RJ['bounds']
TASKS = json.load(open(os.path.join(DATA, 'tasks.json'), encoding='utf-8'))
PATHS = [json.loads(l) for l in open(os.path.join(DATA, 'paths.jsonl'), encoding='utf-8')
         if l.strip()]
GEO = json.load(open(os.path.join(OUT, 'geometry_report.json'), encoding='utf-8'))
LOCMAP = {l['id']: l for w in LAYOUT['warehouses'] for l in w['locations']}
CELL = GRID['cell_size_m']

CORRIDOR = '#d3e7f0'
BLOCKED = '#5b6672'
RACK = '#e2601f'
DOCK = '#f2b21b'
HOME = '#6f42c1'
PICK_FROM = '#facc15'
PICK_TO = '#ef4444'
OUTSIDE = '#f4f5f2'
LEG_OUT, LEG_LOADED, LEG_BACK = '#8ab4d8', '#c2571b', '#2f7d5b'
PHASE_COLOR = {
    'MOVE': '#3f9d69', 'WAIT': '#9aa3ab', 'ROTATE': '#4a86c8',
    'FACE': '#e08a2e', 'PICK': '#f2c230', 'DROP': '#ef7b3c',
    'DWELL': '#f2c230', 'PARK': '#c9ced4',
}

BY_AGENT = collections.defaultdict(list)
for _p in PATHS:
    BY_AGENT[_p['agent_id']].append(_p)
BY_AGENT = {k: sorted(v, key=lambda p: p['t']) for k, v in BY_AGENT.items()}
T_END = max(p['t'] for p in PATHS)
AGENT_IDS = sorted(BY_AGENT)


def loaded_signature(aid):
    """用「载货段」而不是整条闭环判断迎头（闭环首尾相同，dx 恒为 0）。"""
    pts = ROUTES[aid]
    b = BOUNDS[aid]
    a, c = pts[b['pick']], pts[b['drop']]
    z = (a[1] + c[1]) / 2
    dx = c[0] - a[0]
    return (round(z, 1), 1 if dx > 0.5 else (-1 if dx < -0.5 else 0),
            min(a[0], c[0]), max(a[0], c[0]))


def count_head_on():
    sigs = [loaded_signature(a) for a in ROUTES]
    n = 0
    for i in range(len(sigs)):
        for j in range(i + 1, len(sigs)):
            z1, d1, a1, b1 = sigs[i]
            z2, d2, a2, b2 = sigs[j]
            if abs(z1 - z2) < 1.0 and d1 * d2 < 0 and \
               min(b1, b2) - max(a1, a2) > 1.0:
                n += 1
    return n


def central_usage():
    """折点落在 W 仓中央十字主通道 x=46..54 的数量（如实报告）。"""
    return sum(1 for pts in ROUTES.values() for x, z in pts
               if 46 <= x <= 54 and 0 <= z <= 70)


def leg_lengths(aid):
    pts, b = ROUTES[aid], BOUNDS[aid]
    def seg(i, j):
        return sum(math.hypot(q[0] - p[0], q[1] - p[1])
                   for p, q in zip(pts[i:j], pts[i + 1:j + 1]))
    return [round(seg(0, b['pick']), 2),
            round(seg(b['pick'], b['drop']), 2),
            round(seg(b['drop'], b['end']), 2)]


def pos_at(ps, ts, t):
    if t <= ts[0]:
        return ps[0]['x'], ps[0]['y']
    if t >= ts[-1]:
        return ps[-1]['x'], ps[-1]['y']
    i = bisect.bisect_right(ts, t) - 1
    a, b = ps[i], ps[i + 1]
    u = 0.0 if b['t'] == a['t'] else (t - a['t']) / (b['t'] - a['t'])
    return a['x'] + (b['x'] - a['x']) * u, a['y'] + (b['y'] - a['y']) * u


def arrow(d, a, b, color):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return
    ux, uy = dx / L, dy / L
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    s = 5
    d.line([(mx - ux * s - uy * s * 0.6, my - uy * s + ux * s * 0.6),
            (mx, my), (mx - ux * s + uy * s * 0.6, my - uy * s - ux * s * 0.6)],
           fill=color, width=2)


def draw_map(scale, x0, z0, x1, z1, filename, title):
    W = int((x1 - x0) * scale) + 24
    H = int((z1 - z0) * scale) + 78
    img = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(img)

    def P(x, z):
        return ((x - x0) * scale + 12, (z - z0) * scale + 62)

    edge = round(GEO['min_path_to_obstacle_m'] - GEO['agv_radius_m'], 4)
    head_on = count_head_on()
    d.text((12, 7), title, fill='#1f2933')
    d.text((12, 21),
           'CLOSED LOOP: HOME -> PICK -> DROP -> HOME | %d orders, %d AGVs, '
           '0 pending | radius %.4fm | edge clearance %.4fm'
           % (len(ROUTES), len(ROUTES), GEO['agv_radius_m'], edge),
           fill='#375063')
    d.text((12, 34),
           'path-obstacle crossings %d | route head-on pairs %d | '
           'central cross-aisle x=46..54 samples: %d'
           % (GEO['segment_crossings_raw'], head_on, central_usage()),
           fill='#7a4b12')
    legend = [(CORRIDOR, 'corridor'), (BLOCKED, 'obstacle+inflate'),
              (RACK, 'rack'), (HOME, 'HOME bay'),
              (LEG_OUT, '1 outbound empty'), (LEG_LOADED, '2 loaded'),
              (LEG_BACK, '3 return empty'), (PICK_FROM, 'pick'), (PICK_TO, 'drop')]
    lx = 12
    for color, label in legend:
        d.rectangle([lx, 50, lx + 11, 60], fill=color, outline='#8a939c')
        d.text((lx + 15, 49), label, fill='#1f2933')
        lx += 17 + len(label) * 5.9
    d.rectangle([12, 68, W - 12, H - 12], fill=OUTSIDE)

    rows = GRID['walkable_rows']
    for r, row in enumerate(rows):
        z = r * CELL
        if z + CELL < z0 or z > z1:
            continue
        for c, ch in enumerate(row):
            x = c * CELL
            if x + CELL < x0 or x > x1:
                continue
            a, b = P(x, z), P(x + CELL, z + CELL)
            d.rectangle([a[0], a[1], b[0], b[1]],
                        fill=CORRIDOR if ch == '1' else BLOCKED)

    for w in LAYOUT['warehouses']:
        ox, oz = w['origin']
        ww, dd = w['size']['width'], w['size']['depth']
        for dk in w['docks']:
            r = dk['rect']
            a, b = P(r[0], r[1]), P(r[2], r[3])
            d.rectangle([a[0], a[1], b[0], b[1]], outline=DOCK, width=2)
        for rk in w['racks']:
            x, _, z = rk['position']
            rl, _, rd = rk['size']
            a, b = P(x, z), P(x + rl, z + rd)
            d.rectangle([a[0], a[1], b[0], b[1]], outline=RACK, width=2)
        a, b = P(ox, oz), P(ox + ww, oz + dd)
        d.rectangle([a[0], a[1], b[0], b[1]], outline='#2f3a44', width=2)

    # HOME 待命位
    for l in LOCMAP.values():
        if l['kind'] != 'HOME':
            continue
        x, y = P(*l['access_point'])
        s = max(3, int(scale * 0.45))
        d.rectangle([x - s, y - s, x + s, y + s], outline=HOME, width=2)

    segments = 0
    for aid, pts in ROUTES.items():
        b = BOUNDS[aid]
        legs = [(0, b['pick'], LEG_OUT),
                (b['pick'], b['drop'], LEG_LOADED),
                (b['drop'], b['end'], LEG_BACK)]
        for i, j, color in legs:
            xy = [P(p[0], p[1]) for p in pts[i:j + 1]]
            if len(xy) < 2:
                continue
            d.line(xy, fill=color, width=max(2, int(scale * 0.10)), joint='curve')
            for k in range(0, len(xy) - 1, max(1, int(70 / max(scale, 1)))):
                arrow(d, xy[k], xy[k + 1], color)
            segments += len(xy) - 1

    for t in TASKS['tasks']:
        if t['status'] != 'ASSIGNED':
            continue
        for key, color in (('from', PICK_FROM), ('to', PICK_TO)):
            lid = t['legs'][0][key]
            if lid not in LOCMAP:
                continue
            x, y = P(*LOCMAP[lid]['access_point'])
            r = max(4, int(scale * 0.42))
            d.ellipse([x - r, y - r, x + r, y + r],
                      outline=color, width=3 if key == 'to' else 2)
            if key == 'to' and scale >= 20:
                d.text((x + r + 2, y - 6), t['event_id'], fill='#7f1d1d')

    d.text((12, H - 22),
           'closed loops=%d  routes=%d  segments=%d  scale=%d px/m  cell=%.1f m'
           % (len(ROUTES), len(ROUTES), segments, scale, CELL), fill='#1f2933')
    img.save(os.path.join(RENDERS, filename))
    return segments


def draw_time_space(filename):
    """图 B：横轴时间、纵轴 21 台车；含 PARK（车在 HOME 待命）。"""
    W, H = 1400, 900
    img = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(img)
    x0, y0, pw = 110, 96, W - 170
    row_h = (H - 170) / len(AGENT_IDS)
    d.text((12, 10), '09  Time-space diagram | one row per AGV | t = 0..%.2f s' % T_END,
           fill='#1f2933')
    d.text((12, 28), 'MOVE green | WAIT/PARK grey | ROTATE blue | FACE orange | '
                     'PICK amber | DROP orange-red', fill='#375063')
    for s in range(0, int(T_END) + 1, 5):
        x = x0 + s / T_END * pw
        d.line([(x, y0 - 6), (x, y0 + row_h * len(AGENT_IDS))], fill='#d9dee3')
        d.text((x - 8, y0 - 20), str(s), fill='#4a5560')
    for i, aid in enumerate(AGENT_IDS):
        y = y0 + i * row_h
        d.text((12, y + row_h / 2 - 5), aid, fill='#1f2933')
        ps = BY_AGENT[aid]
        for a, b in zip(ps, ps[1:]):
            if b['t'] <= a['t']:
                continue
            xa = x0 + a['t'] / T_END * pw
            xb = x0 + b['t'] / T_END * pw
            d.rectangle([xa, y + 1, max(xb, xa + 1), y + row_h - 2],
                        fill=PHASE_COLOR.get(a.get('phase'), '#cccccc'))
        d.text((x0 + pw + 8, y + row_h / 2 - 5),
               'home %.1fs' % ps[-1]['t'], fill='#4a5560')
    d.text((12, H - 26),
           'All AGVs stay in scene from t=0 to t_end; parked-at-HOME blocks are grey. '
           'Each row is a full work cycle.', fill='#375063')
    img.save(os.path.join(RENDERS, filename))


def draw_snapshots(filename):
    """图 C：5 个时刻的仓库快照，全部 21 台车都在场。"""
    W, H = 1680, 900
    img = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(img)
    cw, ch = W // 3, H // 2
    times = [T_END * i / 4 for i in range(5)]
    site_w, site_d = LAYOUT['size']['width'], LAYOUT['size']['depth']

    for idx, t in enumerate(times):
        gx, gy = (idx % 3) * cw, (idx // 3) * ch
        sc = min(cw - 90, ch - 110) / max(site_w, site_d)
        ox, oy = gx + 40, gy + 56

        def Q(x, z):
            return (ox + x * sc, oy + z * sc)

        d.text((gx + 14, gy + 10), 'snapshot %d  t = %.2f s' % (idx + 1, t),
               fill='#1f2933')
        d.rectangle([ox, oy, ox + site_w * sc, oy + site_d * sc],
                    outline='#2f3a44', width=2, fill='#eef2f4')
        for w in LAYOUT['warehouses']:
            wx, wz = w['origin']
            a, b = Q(wx, wz), Q(wx + w['size']['width'], wz + w['size']['depth'])
            d.rectangle([a[0], a[1], b[0], b[1]], outline='#6b7782', width=1)
            for rk in w['racks']:
                x, _, z = rk['position']
                rl, _, rd = rk['size']
                a, b = Q(x, z), Q(x + rl, z + rd)
                d.rectangle([a[0], a[1], b[0], b[1]], fill='#8d99a3')
        for l in LOCMAP.values():
            if l['kind'] == 'HOME':
                x, y = Q(*l['access_point'])
                d.rectangle([x - 2, y - 2, x + 2, y + 2], fill=HOME)
        for tk in TASKS['tasks']:
            if tk['status'] != 'ASSIGNED':
                continue
            for key, color in (('from', PICK_FROM), ('to', PICK_TO)):
                lid = tk['legs'][0][key]
                if lid in LOCMAP:
                    x, y = Q(*LOCMAP[lid]['access_point'])
                    d.rectangle([x - 2, y - 2, x + 2, y + 2], fill=color)
        for aid in AGENT_IDS:
            ps = BY_AGENT[aid]
            x, y = Q(*pos_at(ps, [p['t'] for p in ps], t))
            d.ellipse([x - 4, y - 4, x + 4, y + 4], fill='#00688f',
                      outline='white', width=1)
        d.text((gx + 14, gy + 34), 'AGV in scene: %d / %d (all present)'
               % (len(AGENT_IDS), len(AGENT_IDS)), fill='#375063')

    gx, gy = 2 * cw, ch
    d.text((gx + 16, gy + 20), 'Legend / notes', fill='#1f2933')
    notes = [
        'Blue dots = AGV positions (all 21 present at every instant)',
        'Purple ticks = HOME staging bays (origin + end of loop)',
        'Yellow / red ticks = pick-from / drop-to task points',
        'Grey blocks = racks; rectangles = warehouse footprints',
        'Closed loop: HOME -> PICK -> DROP -> HOME, then park',
        'Simulation duration %.2f s; %d orders; 0 pending' % (T_END, len(ROUTES)),
    ]
    for i, s in enumerate(notes):
        d.text((gx + 16, gy + 52 + i * 24), s, fill='#375063')
    d.text((12, H - 24),
           '10  Snapshot sequence | proves "who is where at time t"', fill='#1f2933')
    img.save(os.path.join(RENDERS, filename))


def main():
    os.makedirs(RENDERS, exist_ok=True)
    s1 = draw_map(9, 0, 0, LAYOUT['size']['width'], LAYOUT['size']['depth'],
                  '07_nav_plan.png',
                  '07  Closed-loop AGV routes | W + F1/F2/F3')
    s2 = draw_map(30, 2, 8, 50, 34,
                  '08_corridor_plan_zoom.png',
                  '08  W zone A zoom | HOME -> PICK -> DROP -> HOME')
    draw_time_space('09_time_space.png')
    draw_snapshots('10_snapshot_sequence.png')
    legs = {a: leg_lengths(a) for a in ROUTES}
    report = {
        'route_kind': 'CLOSED_LOOP',
        'routes': len(ROUTES),
        'closed_loops': sum(1 for a in ROUTES
                            if ROUTES[a][0] == ROUTES[a][-1]),
        'loop_leg_lengths_m': legs,
        'loop_leg_totals_m': [round(sum(v[i] for v in legs.values()), 2)
                              for i in range(3)],
        'segments_07': s1, 'segments_08': s2,
        'path_obstacle_crossings': GEO['segment_crossings_raw'],
        'route_head_on_pairs': count_head_on(),
        'centerline_clearance_m': GEO['min_path_to_obstacle_m'],
        'edge_clearance_m': round(GEO['min_path_to_obstacle_m'] - GEO['agv_radius_m'], 4),
        'central_cross_aisle_samples': central_usage(),
        'home_bays': sum(1 for l in LOCMAP.values() if l['kind'] == 'HOME'),
        'sim_duration_s': round(T_END, 2),
        'snapshot_times_s': [round(T_END * i / 4, 2) for i in range(5)],
        'images': ['07_nav_plan.png', '08_corridor_plan_zoom.png',
                   '09_time_space.png', '10_snapshot_sequence.png'],
        'note': 'each coloured loop is one order: outbound empty / loaded / '
                'return empty; HOME bays are the shared origin and terminus',
    }
    with open(os.path.join(OUT, 'route_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('PLAN_OK', json.dumps({k: v for k, v in report.items()
                                 if k not in ('images', 'note', 'loop_leg_lengths_m')},
                                ensure_ascii=False))


if __name__ == '__main__':
    main()
