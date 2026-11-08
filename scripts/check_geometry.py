"""check_geometry.py — 线段级路径几何校验（独立，不信任生成器自报）。

依据 AUDIT_REPORT.md §3.4 / §3.6：
  · 碰撞半径改用 geometry.AGV_RADIUS（0.73 m，来自 AMR 实测外接圆），不再用 0.45；
  · 障碍集由 geometry.obstacle_rects 统一给出，包含月台 / 柱 / 园区叉车卡车；
  · 最小间隙用解析法 seg_rect_dist，不再只采 5 个点；
  · FAIL 时 sys.exit(1)。

运行：python scripts/check_geometry.py
产出：out/geometry_report.json
"""
import collections
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from geometry import AGV_RADIUS, obstacle_rects, inflate, seg_hits_rect, seg_rect_dist

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'out')

LAYOUT = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
CFG = json.load(open(os.path.join(DATA, 'config.json'), encoding='utf-8'))
RECTS = obstacle_rects(LAYOUT, CFG)
RAW = [r for _, _, r in RECTS]
PADDED = [inflate(r, AGV_RADIUS) for r in RAW]


def rect_gap(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dz = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dz)


def main():
    by = collections.defaultdict(list)
    for line in open(os.path.join(DATA, 'paths.jsonl'), encoding='utf-8'):
        if line.strip():
            p = json.loads(line)
            by[p['agent_id']].append(p)
    by = {k: sorted(v, key=lambda p: p['t']) for k, v in by.items()}

    segments = []
    for aid, ps in by.items():
        for a, b in zip(ps, ps[1:]):
            segments.append(((a['x'], a['y']), (b['x'], b['y']), aid, a['t']))

    hits_raw, hits_padded = [], []
    max_jump = 0.0
    min_clearance = 1e9
    for p, q, aid, t in segments:
        L = math.hypot(q[0] - p[0], q[1] - p[1])
        max_jump = max(max_jump, L)
        segbox = [min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])]
        for i, (kind, rid, r) in enumerate(RECTS):
            if seg_hits_rect(p, q, r):
                hits_raw.append((aid, t, rid))
            if seg_hits_rect(p, q, PADDED[i]):
                hits_padded.append((aid, t, rid))
            if rect_gap(segbox, RAW[i]) < min_clearance:
                min_clearance = min(min_clearance, seg_rect_dist(p, q, RAW[i]))

    kinds = collections.Counter(k for k, _, _ in RECTS)
    report = {
        'agv_radius_m': AGV_RADIUS,
        'agv_length_width_m': [CFG['agv']['length_m'], CFG['agv']['width_m']],
        'obstacle_rects': len(RECTS),
        'obstacles_by_kind': dict(kinds),
        'agents': len(by),
        'segments_checked': len(segments),
        'max_segment_jump_m': round(max_jump, 4),
        'min_path_to_obstacle_m': round(min_clearance, 4) if min_clearance < 1e8 else None,
        'segment_crossings_raw': len(hits_raw),
        'segment_crossings_with_agv_radius': len(hits_padded),
        'sample_crossings': hits_raw[:12],
        'verdict': 'PASS' if not hits_raw and not hits_padded else 'FAIL',
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'geometry_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('GEOM', report['verdict'], 'segments', len(segments),
          'crossings', len(hits_raw), '/', len(hits_padded),
          'min_clearance', report['min_path_to_obstacle_m'],
          'max_jump', report['max_segment_jump_m'], 'radius', AGV_RADIUS)
    sys.exit(0 if report['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
