"""traffic_check.py — 独立按时间采样检查 AGV 两两间距、速度、瞬移、朝向、闭环。

第六阶段：交通语义改为**严格口径 / 全程在场**。
旧版把「未派出的车」和「完成送达的车」排除在场景外（animate.py 同步隐藏），
用 459 个严格冲突换一个 PASS。现在每台车从 t=0 到 t_end 都在场：
未派出时停在 HOME，完成回程后也停在 HOME，任何时刻任意两车中心距
都必须 >= 2×AGV_RADIUS。不再有条件通过的借口。

只读取 paths.jsonl 与 layout.json，不信任生成器自报结论。
运行：python scripts/traffic_check.py   退出码 0=通过 / 1=失败
产出：out/traffic_report.json
"""
import bisect
import collections
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'out')
sys.path.insert(0, os.path.dirname(__file__))
from geometry import MIN_SEP, AGV_RADIUS   # = 2 × AGV_RADIUS，见第二轮审计 §3.2

DT = 0.05               # s
SPEED_MIN = 0.8
SPEED_MAX = 1.5
MAX_HEADING_JUMP = math.radians(10)
TELEPORT_LIMIT = SPEED_MAX * 1.5
SPAWN_TOL = 0.01
END_SEP = 1.5

PATHS = [json.loads(l) for l in
         open(os.path.join(DATA, 'paths.jsonl'), encoding='utf-8') if l.strip()]
BY = collections.defaultdict(list)
for p in PATHS:
    BY[p['agent_id']].append(p)
for k in BY:
    BY[k].sort(key=lambda p: p['t'])
TIMES = {k: [p['t'] for p in v] for k, v in BY.items()}

LAYOUT = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
SPAWN = {rb['id']: (rb['position'][0], rb['position'][2])
         for w in LAYOUT['warehouses'] for rb in w['spawn']['robots']}
ISSUES = []
AVG_SPEEDS = {}


def pos_at(pts, ts, t):
    """半无限延拓的线性插值；两端都停在 HOME，因此任意时刻车辆都在场。"""
    if t <= ts[0]:
        return pts[0]['x'], pts[0]['y']
    if t >= ts[-1]:
        return pts[-1]['x'], pts[-1]['y']
    i = bisect.bisect_right(ts, t) - 1
    a, b = pts[i], pts[i + 1]
    u = 0.0 if b['t'] == a['t'] else (t - a['t']) / (b['t'] - a['t'])
    return (a['x'] + (b['x'] - a['x']) * u, a['y'] + (b['y'] - a['y']) * u)


def main():
    ids = sorted(BY)
    t_end = max(ps[-1]['t'] for ps in BY.values())
    min_sep = (1e9, None)
    steps = int(t_end / DT) + 1

    # 1) 全程在场：任意采样时刻、任意两车（含停在 HOME 的车）
    strict_violations = 0
    for s in range(steps):
        t = s * DT
        P = {i: pos_at(BY[i], TIMES[i], t) for i in ids}
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                ia, ib = ids[a], ids[b]
                d = math.hypot(P[ia][0] - P[ib][0], P[ia][1] - P[ib][1])
                if d < min_sep[0]:
                    min_sep = (d, (round(t, 2), ia, ib))
                if d < MIN_SEP:
                    strict_violations += 1
                    ISSUES.append({'kind': 'TOO_CLOSE', 't': round(t, 2),
                                   'a': ia, 'b': ib, 'd': round(d, 3)})

    # 2) 闭环与全程在场：首帧/末帧都等于 HOME，且时间轴覆盖 [0, t_end]
    ends = {}
    for aid, ps in BY.items():
        sp = SPAWN.get(aid)
        if sp:
            for tag, p in (('START', ps[0]), ('END', ps[-1])):
                d = math.hypot(p['x'] - sp[0], p['y'] - sp[1])
                if d > SPAWN_TOL:
                    ISSUES.append({'kind': 'HOME_MISMATCH', 'agent': aid,
                                   'tag': tag, 'd': round(d, 3)})
        if abs(ps[0]['t']) > 1e-6:
            ISSUES.append({'kind': 'NOT_PRESENT_AT_T0', 'agent': aid,
                           't': ps[0]['t']})
        if abs(ps[-1]['t'] - t_end) > 1e-6:
            ISSUES.append({'kind': 'NOT_PRESENT_AT_T_END', 'agent': aid,
                           't': ps[-1]['t']})
        phases = {p.get('phase') for p in ps}
        if not {'PICK', 'DROP'} <= phases:
            ISSUES.append({'kind': 'MISSING_PICK_OR_DROP', 'agent': aid})
        ends[aid] = (ps[-1]['x'], ps[-1]['y'], ps[-1]['t'])
        L = sum(math.hypot(b['x'] - a['x'], b['y'] - a['y'])
                for a, b in zip(ps, ps[1:]))
        dur = ps[-1]['t'] - ps[0]['t']
        AVG_SPEEDS[aid] = round(L / dur if dur > 0 else 0, 3)
        for a, b in zip(ps, ps[1:]):
            d = math.hypot(b['x'] - a['x'], b['y'] - a['y'])
            dt = b['t'] - a['t']
            dth = abs(b['theta'] - a['theta'])
            if dth > MAX_HEADING_JUMP + 1e-3:
                ISSUES.append({'kind': 'HEADING_JUMP', 'agent': aid,
                               't': a['t'], 'dth': round(dth, 3)})
            if d < 1e-9:       # 停靠 / 原地旋转 / 待命：位置不变
                continue
            v = d / dt if dt > 0 else math.inf
            if not (SPEED_MIN - 1e-3 <= v <= SPEED_MAX + 1e-3):
                ISSUES.append({'kind': 'SPEED', 'agent': aid, 'v': round(v, 3),
                               't': a['t']})
            if v > TELEPORT_LIMIT:
                ISSUES.append({'kind': 'TELEPORT', 'agent': aid,
                               't': a['t'], 'd': round(d, 3)})

    # 全部车在 t_end 停在各自己的 HOME，彼此必须分开
    ek = sorted(ends)
    for i in range(len(ek)):
        for j in range(i + 1, len(ek)):
            d = math.hypot(ends[ek[i]][0] - ends[ek[j]][0],
                           ends[ek[i]][1] - ends[ek[j]][1])
            if d < END_SEP:
                ISSUES.append({'kind': 'END_TOO_CLOSE', 'a': ek[i], 'b': ek[j],
                               'd': round(d, 3)})

    kinds = {}
    for it in ISSUES:
        kinds[it['kind']] = kinds.get(it['kind'], 0) + 1
    out = {
        'agents': len(ids),
        't_end_s': round(t_end, 3),
        'sample_dt_s': DT,
        'min_pair_distance_m': round(min_sep[0], 4),
        'min_pair_at': min_sep[1],
        'min_sep_threshold_m': MIN_SEP,
        'speed_range_mps': [SPEED_MIN, SPEED_MAX],
        'avg_speed_including_dwell_mps': AVG_SPEEDS,
        'semantics': 'STRICT / all agents present at all times; none leave scene',
        'completed_agents_excluded_after_t_end': False,
        'strict_violations': strict_violations,
        'strict_violations_if_completed_stay_in_scene': strict_violations,
        'closed_loop': True,
        'issue_kinds': kinds,
        'issues': ISSUES[:200],
        'count': len(ISSUES),
        'verdict': 'PASS' if not ISSUES else 'FAIL',
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'traffic_report.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('TRAFFIC', out['verdict'], 'min_pair', out['min_pair_distance_m'],
          'm at', out['min_pair_at'], 'strict_violations', strict_violations,
          'issues', out['count'], 'kinds', json.dumps(kinds, ensure_ascii=False))
    sys.exit(1 if ISSUES else 0)


if __name__ == '__main__':
    main()
