"""layout_audit.py — 独立检查 layout.json 的实体冲突 / 访问点 / 出生点 / 边界。

依据 AUDIT_REPORT.md §3.1 / §6.1，扩展了 AP 净距、dock AP 到货架、
出生点两两间距、实体 footprint 等判据。不调用任何项目生成脚本，
只读 layout.json，因此对「生成器自报」保持独立。

运行：python scripts/layout_audit.py
退出码：0 = 无问题，1 = 有问题
产出：out/layout_audit.json
"""
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'out')
LAYOUT = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
EPS = 1e-6
AP_CLEARANCE = 0.5      # 访问点到最近实体最小净距（m）
DOCK_AP_TO_RACK = 1.0   # 月台 AP 到最近货架（m）
SPAWN_SEP = 1.5         # 出生点两两最小间距（m）
ISSUES = []
WARNINGS = []


def rect_of_rack(rk):
    x, _, z = rk['position']
    rl, _, rd = rk['size']
    return (x, z, x + rl, z + rd)


def overlap(a, b):
    return (a[0] < b[2] - EPS and b[0] < a[2] - EPS and
            a[1] < b[3] - EPS and b[1] < a[3] - EPS)


def inside(p, r):
    return (r[0] - EPS <= p[0] <= r[2] + EPS and
            r[1] - EPS <= p[1] <= r[3] + EPS)


def dist_point_rect(p, r):
    dx = max(r[0] - p[0], 0.0, p[0] - r[2])
    dz = max(r[1] - p[1], 0.0, p[1] - r[3])
    return math.hypot(dx, dz)


def add(kind, a, b, detail):
    ISSUES.append({'kind': kind, 'a': a, 'b': b, 'detail': detail})


def audit_warehouse(w):
    ox, oz = w['origin']
    W, D = w['size']['width'], w['size']['depth']
    solids = []
    for rk in w['racks']:
        solids.append(('RACK', rk['id'], rect_of_rack(rk)))
    for d in w['docks']:
        solids.append(('DOCK', d['id'], tuple(d['rect'])))
    for s in w['stations']:
        solids.append(('STATION', s['id'], tuple(s['rect'])))
    for ch in w['charging_stations']:
        x, _, z = ch['position']
        solids.append(('CHARGER', ch['id'], (x - 0.8, z - 0.4, x + 0.8, z + 0.4)))
    for ob in w['obstacles']:
        solids.append(('PILLAR', ob['id'], tuple(ob['rect'])))
    racks = [(sid, r) for kind, sid, r in solids if kind == 'RACK']

    # 1) 实体两两重叠
    for i in range(len(solids)):
        for j in range(i + 1, len(solids)):
            ka, ia, ra = solids[i]
            kb, ib, rb = solids[j]
            if overlap(ra, rb):
                add('OVERLAP', ia, ib, '%s %s vs %s %s' % (ka, ia, kb, ib))

    # 2) 实体必须落在所属仓库 footprint 内
    for kind, sid, r in solids:
        if (r[0] < ox - EPS or r[1] < oz - EPS or
                r[2] > ox + W + EPS or r[3] > oz + D + EPS):
            add('OUT_OF_FOOTPRINT', sid, w['id'],
                '%s %s rect=%s outside %s footprint' % (kind, sid, list(r), w['id']))

    def check_ap(owner, ap, tag):
        for kind, sid, r in solids:
            if inside(ap, r):
                add('AP_INSIDE', owner, sid,
                    '%s AP %s inside %s %s' % (tag, ap, kind, sid))
                break
        if not (0 <= ap[0] <= LAYOUT['size']['width'] and
                0 <= ap[1] <= LAYOUT['size']['depth']):
            add('AP_OUTSIDE', owner, None, '%s AP %s outside site' % (tag, ap))
        d = min((dist_point_rect(ap, r) for _, _, r in solids), default=99.0)
        if d < AP_CLEARANCE:
            WARNINGS.append({'kind': 'AP_NEAR_SOLID', 'a': owner, 'd': round(d, 3)})
        return d

    # 3) locations[].access_point
    for loc in w['locations']:
        check_ap(loc['id'], loc['access_point'], 'LOC')

    # 4) docks / stations / charging_stations 自带的 access_point
    for group in ('docks', 'stations', 'charging_stations'):
        for item in w[group]:
            ap = item.get('access_point')
            if ap:
                check_ap(item['id'], tuple(ap), group.upper())

    # 5) 月台 AP 到最近货架 >= 1.0 m
    for d in w['docks']:
        for loc in w['locations']:
            if loc['id'] != d['id']:
                continue
            ap = loc['access_point']
            dr = min((dist_point_rect(ap, r) for _, r in racks), default=99.0)
            if dr < DOCK_AP_TO_RACK:
                add('DOCK_AP_TOO_CLOSE_TO_RACK', d['id'], None,
                    'dock AP %s only %.3f m to nearest rack' % (ap, dr))

    # 6) 出生点：不在实体内、不出界、两两间距
    spawns = []
    for rb in w['spawn']['robots']:
        p = (rb['position'][0], rb['position'][2])
        spawns.append((rb['id'], p))
        for kind, sid, r in solids:
            if inside(p, r):
                add('SPAWN_INSIDE', rb['id'], sid,
                    'spawn %s inside %s %s' % (p, kind, sid))
                break
        if not (0 <= p[0] <= LAYOUT['size']['width'] and
                0 <= p[1] <= LAYOUT['size']['depth']):
            add('SPAWN_OUTSIDE', rb['id'], None, 'spawn %s outside site' % (p,))
    for i in range(len(spawns)):
        for j in range(i + 1, len(spawns)):
            d = math.hypot(spawns[i][1][0] - spawns[j][1][0],
                           spawns[i][1][1] - spawns[j][1][1])
            if d < SPAWN_SEP:
                add('SPAWN_TOO_CLOSE', spawns[i][0], spawns[j][0],
                    '%.3f m < %.2f m' % (d, SPAWN_SEP))


def main():
    for w in LAYOUT['warehouses']:
        audit_warehouse(w)
    kinds = {}
    for it in ISSUES:
        kinds[it['kind']] = kinds.get(it['kind'], 0) + 1
    out = {
        'layout': 'data/layout.json',
        'warehouses': len(LAYOUT['warehouses']),
        'thresholds': {'ap_clearance_m': AP_CLEARANCE,
                       'dock_ap_to_rack_m': DOCK_AP_TO_RACK,
                       'spawn_sep_m': SPAWN_SEP},
        'issue_kinds': kinds,
        'issues': ISSUES,
        'count': len(ISSUES),
        'warnings': len(WARNINGS),
        'warning_sample': WARNINGS[:10],
        'verdict': 'PASS' if not ISSUES else 'FAIL',
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'layout_audit.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('LAYOUT_AUDIT', out['verdict'], 'issues', out['count'],
          'warnings', out['warnings'], 'kinds', json.dumps(kinds, ensure_ascii=False))
    for it in ISSUES[:20]:
        print(' ', it['kind'], it['a'], '->', it['b'], it['detail'])
    sys.exit(1 if ISSUES else 0)


if __name__ == '__main__':
    main()
