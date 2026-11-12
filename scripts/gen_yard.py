"""gen_yard.py — 月台挂车的到发时刻表（data/yard_trucks.json）。

为什么要单独一份数据：车不是布景。它要"开进来 → 倒进月台 → 装卸 → 开走 →
下一辆再来"，而这件事有三个消费者：
  · Blender 动画（静帧/视频里能看见车在动）
  · three.js 交互视图（拖时间轴时车跟着动）
  · 台账（`dock_served` 那些指标背后到底是谁在装卸）

三边各写一套时间逻辑必然对不上，所以时间表只生成一次，大家读同一份。

时刻取值：园区内 4 m/s，倒车 1.2 m/s，装卸 40 s（`process_spec` 里
`DOCK_IN→STG_IN` / `STG_OUT→DOCK_OUT` 的节拍），每扇门两个波次——
第一辆走掉之后第二辆才进场，这才叫"再来车"。

运行：python scripts/gen_yard.py
"""
from __future__ import annotations

import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'out')

SPEED_YARD = 6.0          # m/s，园区限速
SPEED_BACK = 1.2          # m/s，倒车进月台
DWELL_S = 34.0            # 装卸时长
GATE = (52.0, -20.0)      # 南侧货车专用大门内侧（东门走人和小车）
LANE_Z = -14.0            # 园区横向主车道
TRUCK_LEN = 10.0          # 挂车长度（assets 里的 asset_truck_box）
PARK_Z = -4.6             # 车尾顶住月台时的车身中心


def seg_len(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def path_time(path):
    return sum(seg_len(path[i], path[i + 1]) for i in range(len(path) - 1))


def main():
    layout = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
    pm = json.load(open(os.path.join(DATA, 'path_metrics.json'), encoding='utf-8'))
    duration = float(pm.get('duration_s', 120.0))

    docks = []
    for wh in layout['warehouses']:
        if wh['kind'] != 'REGIONAL':
            continue
        for dk in wh.get('docks', []):
            x0, z0, x1, z1 = dk['rect']
            docks.append({'id': dk['id'], 'x': (x0 + x1) / 2,
                          'face_z': min(z0, z1), 'kind': dk.get('kind', 'INBOUND')})

    trucks = []
    n = 0
    # 每扇门两个波次：第一辆 40 s 装卸后离开，第二辆紧接着进场
    for slot, dk in enumerate(docks):
        x = dk['x']
        lane = [(GATE[0], LANE_Z), (x + 3.0, LANE_Z), (x, -9.0), (x, PARK_Z)]
        approach = path_time([GATE] + lane) / SPEED_YARD
        t_arrive = 1.0 + slot * 2.5
        t_dock = t_arrive + approach
        for wave in range(2):
            n += 1
            base = t_dock + wave * (DWELL_S + 14.0)
            t_leave = base + DWELL_S
            if t_leave > duration + 8:
                continue
            back = (abs(PARK_Z - (-9.0))) / SPEED_BACK
            trucks.append({
                'id': 'TRK-%02d' % n,
                'dock': dk['id'],
                'kind': dk['kind'],
                'path': [[round(GATE[0], 2), round(GATE[1], 2)]] + [[round(a, 2), round(b, 2)] for a, b in lane],
                'times': [round(t_arrive, 2), round(t_dock, 2),
                          round(t_leave, 2), round(t_leave + approach, 2)],
                # 倒车段：最后 5.4 m 单独计时，车头朝向反一下
                'back_s': round(back, 2),
                'plate': '%sA-%05d' % ('鄂' if wave == 0 else '浙', 10000 + n * 137 % 89999),
            })

    doc = {
        'schema_version': '1.0',
        'kind': 'yard_trucks',
        'duration_s': duration,
        'gate': list(GATE),
        'lane_z': LANE_Z,
        'speed_yard_mps': SPEED_YARD,
        'dwell_s': DWELL_S,
        'trucks': trucks,
        'summary': {'trucks': len(trucks),
                    'docks': len({t['dock'] for t in trucks}),
                    'waves': len(trucks) / max(len(docks), 1)},
    }
    p = os.path.join(DATA, 'yard_trucks.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print('YARD_OK trucks=%d docks=%d -> %s' % (len(trucks), doc['summary']['docks'], p))


if __name__ == '__main__':
    sys.exit(main())
