"""reservation_check.py — 时间扩展预约表独立复核（DESIGN_PLAN §11.2 a/b + §5.2.5.1）。

不改主链，只读 paths.jsonl / cargo_state.json / spec_layout_report.json，
在 0.05 s 采样 + 0.5 m 栅格上独立复算：
  1) 顶点冲突：同一 cell 同一时刻被两车占用；
  2) 边冲突：相邻采样时刻两车交换 cell（head-on swap）；
  3) AP_MUTEX 同时占用：同一时刻两车分别停在互斥 AP 上
     （mutex_groups 由 spec_layout.py 输出，见 DESIGN_PLAN §5.2.5.1）。

结论边界：0 冲突只说明「当前 21 车 / 21 单 / 该采样粒度下」无冲突，
不是连续时间的形式化证明，也不等于 CBS/WHCA*。
产出：out/reservation_report.json
"""
import bisect
import collections
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA, OUT = os.path.join(ROOT, 'data'), os.path.join(ROOT, 'out')
DT = 0.05
CELL = 0.5


def pos_at(pts, ts, t):
    if t <= ts[0]:
        return pts[0]['x'], pts[0]['y']
    if t >= ts[-1]:
        return pts[-1]['x'], pts[-1]['y']
    i = bisect.bisect_right(ts, t) - 1
    a, b = pts[i], pts[i + 1]
    u = 0.0 if b['t'] == a['t'] else (t - a['t']) / (b['t'] - a['t'])
    return a['x'] + (b['x'] - a['x']) * u, a['y'] + (b['y'] - a['y']) * u


def cell_of(p):
    return (int(math.floor(p[0] / CELL)), int(math.floor(p[1] / CELL)))


def main():
    paths = [json.loads(l) for l in open(os.path.join(DATA, 'paths.jsonl'),
                                         encoding='utf-8') if l.strip()]
    by = collections.defaultdict(list)
    for p in paths:
        by[p['agent_id']].append(p)
    times = {}
    for k in by:
        by[k].sort(key=lambda p: p['t'])
        times[k] = [p['t'] for p in by[k]]
    ids = sorted(by)
    t_end = max(ts[-1] for ts in times.values())
    steps = int(t_end / DT) + 1

    vertex, edge = [], []
    prev = None
    for s in range(steps):
        t = s * DT
        cur = {a: cell_of(pos_at(by[a], times[a], t)) for a in ids}
        groups = collections.defaultdict(list)
        for a, c in cur.items():
            groups[c].append(a)
        for c, members in groups.items():
            if len(members) > 1:
                vertex.append({'t': round(t, 2), 'cell': list(c), 'agents': members})
        if prev:
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    if cur[a] != cur[b] and prev[a] == cur[b] and prev[b] == cur[a]:
                        edge.append({'t': round(t, 2), 'a': a, 'b': b,
                                     'cells': [list(cur[a]), list(cur[b])]})
        prev = cur

    # AP_MUTEX 同时占用（停靠窗口 = [t_pick/t_drop, +dwell_pick_s]）
    spec_rep = json.load(open(os.path.join(OUT, 'spec_layout_report.json'),
                              encoding='utf-8'))
    mutex = {frozenset((m['a'], m['b'])) for m in spec_rep.get('mutex_groups', [])}
    cargo = json.load(open(os.path.join(DATA, 'cargo_state.json'), encoding='utf-8'))
    cfg = json.load(open(os.path.join(DATA, 'config.json'), encoding='utf-8'))
    dwell = cfg['agv']['dwell_pick_s']
    stops = []
    for c in cargo['cargos']:
        stops.append((c['t_pick_s'], c['t_pick_s'] + dwell,
                      c['pickup_location'], c['agent_id']))
        stops.append((c['t_drop_s'], c['t_drop_s'] + dwell,
                      c['drop_location'], c['agent_id']))
    mutex_hits = []
    for i in range(len(stops)):
        for j in range(i + 1, len(stops)):
            a, b = stops[i], stops[j]
            if a[3] == b[3] or min(a[1], b[1]) <= max(a[0], b[0]):
                continue
            if frozenset((a[2], b[2])) in mutex:
                mutex_hits.append({'a': a[3], 'b': b[3], 'ap_a': a[2], 'ap_b': b[2],
                                   'overlap_s': round(min(a[1], b[1]) - max(a[0], b[0]), 3)})

    report = {
        'method': 'time-expanded reservation replay (vertex + edge swap), 0.05 s / 0.5 m',
        'agents': len(ids), 'sample_dt_s': DT, 'cell_size_m': CELL,
        'samples': steps, 't_end_s': round(t_end, 3),
        'vertex_conflicts': len(vertex), 'edge_conflicts': len(edge),
        'vertex_samples': vertex[:10], 'edge_samples': edge[:10],
        'mutex_pairs_total': len(mutex),
        'mutex_simultaneous_occupations': len(mutex_hits),
        'mutex_samples': mutex_hits[:10],
        'verdict': 'PASS' if not vertex and not edge and not mutex_hits else 'FAIL',
        'note': 'AP_MUTEX is currently prevented implicitly by the global '
                'center-distance >= 2*AGV_RADIUS rule in traffic_check.py, '
                'NOT by an explicit mutex lock in plan_paths.py. '
                'A 0 result is NOT a continuous-time / CBS proof.',
    }
    with open(os.path.join(OUT, 'reservation_report.json'), 'w',
              encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('RESERVATION', report['verdict'], 'vertex', len(vertex),
          'edge', len(edge), 'mutex_simultaneous', len(mutex_hits),
          'of', len(mutex), 'mutex pairs')
    sys.exit(0 if report['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
