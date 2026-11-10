"""gen_data.py — 派生数据入口：布局来自 spec，SKU / 订单来自本脚本。

DESIGN_PLAN.md Step 3 整改：
  · **删除全部硬编码布局函数**（new_warehouse / add_rack_grid / add_docks /
    add_stations / add_chargers / add_pillars_and_spawns / build_regional /
    build_forward）。布局的唯一输入现在是 `config/layout_spec.json`，
    由 `scripts/spec_layout.py` 展开并校验后写入 `data/layout.json`。
  · 改仓库尺寸、排数、bay 数、通道宽、月台门数、待命位，只需改 JSON，
    不再改 Python（§1.1 参数化目标）。
  · 保留 SKU 主数据（Zipf 合成）与闭环订单生成逻辑；订单数不再写死，
    等于布局里的 AGV / HOME 数量。
  · 为保持基线随机序列可复现：spec_layout 用同一 seed 独立生成 AGV battery，
    本脚本在生成 SKU 前跳过同等次数的 RNG 调用（历史布局阶段消耗 21 次）。

产出：data/layout.json（由 spec_layout 写）· skus.json · events.jsonl
"""
import json
import math
import os
import random
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
CFG = json.load(open(os.path.join(DATA, 'config.json'), encoding='utf-8'))
RNG = random.Random(CFG['seed'])
N_SKUS = 240
# 布局来源默认是主 spec；用变体跑（build_variant.py 生成的扩仓版）时由 DC_LAYOUT_SPEC 覆盖。
# 这是"用户自选功能区 → 跑出自己仓"的唯一开关，其余步骤都读派生出的 data/layout.json。
SPEC = os.environ.get('DC_LAYOUT_SPEC') or os.path.join(ROOT, 'config', 'layout_spec.json')
FLEET_SPEC = os.path.join(ROOT, 'config', 'fleet_spec.json')
SPEC_LAYOUT = os.path.join(os.path.dirname(__file__), 'spec_layout.py')


def build_skus():
    raw = [1.0 / ((i + 1) ** 0.85) for i in range(N_SKUS)]
    total = sum(raw)
    out = []
    for i, v in enumerate(raw):
        q = (i + 1) / N_SKUS
        abc = 'A' if q <= 0.4 else ('B' if q <= 0.75 else 'C')
        out.append({
            'sku': 'SKU-%04d' % (i + 1), 'name': 'ITEM-%04d' % (i + 1),
            'velocity': round(v / total, 6), 'abc': abc,
            'cube_m3': round(0.04 + RNG.random() * 0.12, 3),
            'weight_kg': round(4 + RNG.random() * 24, 2),
        })
    return {'units': 'm3/kg', 'velocity_source': 'zipf s=0.85, normalized (synthetic)',
            'count': len(out), 'skus': out}


# (取货点 kind, 允许的交付点 kind 集合)
EVENT_RULES = {
    'INBOUND': ('INBOUND', {'SHELF'}),
    'REPLENISH': ('INBOUND', {'SHELF'}),
    'OUTBOUND': ('SHELF', {'OUTBOUND'}),
    'PICK': ('SHELF', {'PACKING', 'SORTING', 'OUTBOUND'}),
    'TRANSFER': ('SHELF', {'SHELF'}),
}
EVENT_TYPES = ('OUTBOUND', 'PICK', 'TRANSFER', 'INBOUND', 'REPLENISH')


def build_events(layout, skus):
    """每台 AGV 一张闭环作业单：HOME → PICK → DROP → HOME，覆盖 5 种类型。"""
    from collections import defaultdict
    ids = [s['sku'] for s in skus['skus']]
    events = []
    n = 0
    for w in layout['warehouses']:
        lanes = defaultdict(list)
        for l in w['locations']:
            if l['kind'] == 'SHELF':
                lanes[round(l['access_point'][1], 1)].append(l)
        w_center = w['origin'][0] + w['size']['width'] / 2
        docks = w['docks']
        stations = list(w['stations']) + [d for d in docks if d['kind'] == 'OUTBOUND']
        used = set()
        zone_count = {'F': 0, 'B': 0}
        FRONT_TYPES = ('OUTBOUND', 'INBOUND', 'REPLENISH', 'PICK', 'TRANSFER')
        BACK_TYPES = ('PICK', 'TRANSFER')

        for rb in w['spawn']['robots']:
            sp = (rb['position'][0], rb['position'][2])
            side = 'W' if sp[0] < w_center else 'E'
            zone = 'F' if sp[1] < w['origin'][1] + w['size']['depth'] / 2 else 'B'
            cand = sorted((abs(z - sp[1]), z) for z in lanes if z not in used)
            z_key = cand[0][1]
            used.add(z_key)
            row = sorted(lanes[z_key], key=lambda l: l['access_point'][0])
            split = len(row) // 2
            half = (row[:split] if side == 'W' else row[split:]) if len(row) >= 12 else row
            other = (row[split:] if side == 'W' else row[:split]) if len(row) >= 12 else []
            deep = half[-1] if side == 'W' else half[0]
            near = half[0] if side == 'W' else half[-1]

            def dist(a, b):
                return math.hypot(a[0] - b[0], a[1] - b[1])

            def best(pool, anchor):
                return min(pool, key=lambda d: dist(anchor, d['access_point'])
                           + dist(d['access_point'], sp))['id']

            n += 1
            pool_types = FRONT_TYPES if zone == 'F' else BACK_TYPES
            typ = pool_types[zone_count[zone] % len(pool_types)]
            zone_count[zone] += 1
            deep_ap = deep['access_point']
            if typ in ('INBOUND', 'REPLENISH'):
                pool = [d for d in docks if d['kind'] == 'INBOUND'] or docks
                src = min(pool, key=lambda d: dist(sp, d['access_point'])
                          + dist(d['access_point'], deep_ap))['id']
                dst = deep['id']
            elif typ == 'OUTBOUND':
                pool = [d for d in docks if d['kind'] == 'OUTBOUND'] or docks
                src, dst = deep['id'], best(pool, deep_ap)
            elif typ == 'PICK':
                src, dst = deep['id'], best(stations, deep_ap)
            else:
                src = deep['id']
                cand = other or [x for x in row if x['id'] != src]
                dst = min(cand, key=lambda x: dist(deep_ap, x['access_point'])
                          + dist(x['access_point'], sp))['id']
            if src == dst:
                src, dst = near['id'], deep['id']

            t = round(n * 0.5 + RNG.uniform(0, 0.2), 2)
            events.append({
                'event_id': 'E%04d' % n, 'type': typ, 'warehouse_id': w['id'],
                'sku': RNG.choice(ids), 'qty': RNG.randint(1, 5),
                'from': src, 'to': dst, 'return_to': rb['home_id'],
                'preferred_agent': rb['id'], 'lane_z': z_key,
                'route_kind': 'CLOSED_LOOP',
                'phases': ['DEADHEAD_OUT', 'PICK', 'LOADED', 'DROP',
                           'DEADHEAD_BACK', 'PARK'],
                't_request': t, 'deadline': round(t + 240.0, 2),
                'priority': 1 if typ in ('OUTBOUND', 'PICK') else 2,
            })
    expected = sum(len(w['spawn']['robots']) for w in layout['warehouses'])
    assert len(events) == expected, (len(events), expected)
    return events


def main():
    # Step 3：布局唯一来源 = config/layout_spec.json（校验 PASS 才会 --write）
    subprocess.run([sys.executable, SPEC_LAYOUT, '--spec', SPEC,
                    '--fleet-spec', FLEET_SPEC,
                    '--write', os.path.join(DATA, 'layout.json'), '--quiet'],
                   check=True)
    layout = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))

    # spec_layout 用同一 seed 独立生成 battery（历史布局阶段消耗 21 次 RNG）。
    # 这里跳过同等次数，保证 skus.json / events.jsonl 与冻结基线逐字节一致。
    for _ in range(sum(len(w['spawn']['robots']) for w in layout['warehouses'])):
        RNG.random()

    skus = build_skus()
    with open(os.path.join(DATA, 'skus.json'), 'w', encoding='utf-8') as f:
        json.dump(skus, f, ensure_ascii=False, indent=2)
    events = build_events(layout, skus)
    locmap = {l['id']: l for w in layout['warehouses'] for l in w['locations']}
    for e in events:
        for k in ('from', 'to', 'return_to'):
            if e[k] not in locmap:
                raise SystemExit('EVENT_REF_FAIL %s %s=%s' % (e['event_id'], k, e[k]))
        if locmap[e['return_to']]['kind'] != 'HOME':
            raise SystemExit('RETURN_TO_NOT_HOME %s' % e['event_id'])
        got = (locmap[e['from']]['kind'], locmap[e['to']]['kind'])
        want_from, want_to = EVENT_RULES[e['type']]
        if got[0] != want_from or got[1] not in want_to:
            raise SystemExit('EVENT_KIND_FAIL %s %s expected=%s'
                             % (e['event_id'], got, EVENT_RULES[e['type']]))
    with open(os.path.join(DATA, 'events.jsonl'), 'w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    types = sorted({e['type'] for e in events})
    print('GEN_DATA_OK', json.dumps(layout['summary'], ensure_ascii=False),
          'skus', skus['count'], 'events', len(events),
          'event_types', types, 'layout_source', 'config/layout_spec.json')


if __name__ == '__main__':
    main()
