"""optimize_slotting.py — 距离贪心货位分配（原名"ABC/COI 优化"，依据审计 P1-3 正名）。

审计指出旧版三个问题：
  1) 名不副实：没有 cube/order 约束，却叫 COI；本版明确叫 distance_greedy，
     method 字段不再出现 COI，并在 limitations 里写明未实现的部分。
  2) 48.82% 是重排不等式恒等式：baseline 是随机排列，排序配对必然更优。
     本版 baseline 改为**当前布局**（按 location_id 顺序的既有配对），
     另给一个随机 baseline 仅作 sanity check，并报告 1D 分配下界。
  3) 距离用欧氏直线：本版在 0.5 m nav_grid 上做多源 BFS，得到真实通道距离。
     且覆盖全部 392 个 SHELF 库位（240 已分配 / 152 明确标 EMPTY）。

SKU 主数据统一来自 data/skus.json，不再另造一套编号。

产出：data/slotting.json
"""
import collections
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
sys.path.insert(0, os.path.dirname(__file__))
from nav_grid import load_grid, CELL

LAYOUT = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
SKUS = json.load(open(os.path.join(DATA, 'skus.json'), encoding='utf-8'))['skus']
EVENTS = [json.loads(l) for l in open(os.path.join(DATA, 'events.jsonl'), encoding='utf-8') if l.strip()]
GRID = load_grid()


def dock_distances():
    """从所有月台 AP 出发做 4 向多源 BFS，得到每个可走格到最近月台的通道距离。"""
    n = GRID.cols * GRID.rows
    dist = [math.inf] * n
    q = collections.deque()
    for w in LAYOUT['warehouses']:
        for d in w['docks']:
            c, r = GRID.to_cell(d['access_point'])
            if GRID.walkable(c, r):
                idx = r * GRID.cols + c
                if dist[idx] > 0:
                    dist[idx] = 0.0
                    q.append((c, r))
    while q:
        c, r = q.popleft()
        idx = r * GRID.cols + c
        nd = dist[idx] + CELL
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nc, nr = c + dc, r + dr
            if not GRID.walkable(nc, nr):
                continue
            ni = nr * GRID.cols + nc
            if nd < dist[ni]:
                dist[ni] = nd
                q.append((nc, nr))
    return dist


def main():
    dist = dock_distances()
    shelves = []
    for w in LAYOUT['warehouses']:
        for loc in w['locations']:
            if loc['kind'] != 'SHELF':
                continue
            c, r = GRID.to_cell(loc['access_point'])
            d = dist[r * GRID.cols + c]
            shelves.append({
                'warehouse_id': w['id'], 'location_id': loc['id'],
                'rack_id': loc['rack_id'], 'access_point': loc['access_point'],
                'aisle_dist_m': round(d, 3) if math.isfinite(d) else None,
                'capacity_m3': 0.2,
            })
    shelves.sort(key=lambda s: s['location_id'])
    if any(s['aisle_dist_m'] is None for s in shelves):
        raise SystemExit('SLOTTING_FAIL: some shelf AP unreachable on nav grid')

    # events 命中（新 schema 用 from/to）
    freq = collections.Counter()
    for e in EVENTS:
        freq[e['from']] += 1
        freq[e['to']] += 0.5

    skus = sorted(SKUS, key=lambda s: s['velocity'], reverse=True)
    n_assign = min(len(skus), len(shelves))
    if any(s['cube_m3'] > sh['capacity_m3']
           for s, sh in zip(skus[:n_assign], sorted(shelves, key=lambda x: x['aisle_dist_m'])[:n_assign])):
        raise SystemExit('SLOTTING_FAIL: SKU cube exceeds slot capacity')

    def cost(pairs):
        return sum(s['velocity'] * sh['aisle_dist_m'] for s, sh in pairs)

    # 现状 baseline：按库位 id 顺序依次放 SKU（模拟"改造前的既有摆法"）
    current = list(zip(skus[:n_assign], shelves[:n_assign]))
    # 随机 baseline：只作 sanity check，不作为改善结论的分母
    import random
    rng = random.Random(LAYOUT['seed'] + 1)
    rand_slots = rng.sample(shelves, n_assign)
    random_pairs = list(zip(skus[:n_assign], rand_slots))
    # 优化：周转率降序 × BFS 通道距离升序（1D 分配的最优解）
    by_dist = sorted(shelves, key=lambda s: s['aisle_dist_m'])
    optimized = list(zip(skus[:n_assign], by_dist[:n_assign]))

    c0, cr, c1 = cost(current), cost(random_pairs), cost(optimized)
    imp_current = (c0 - c1) / c0 * 100 if c0 else 0
    imp_random = (cr - c1) / cr * 100 if cr else 0

    assigned_ids = {sh['location_id'] for _, sh in optimized}
    max_hits = max((freq[s['location_id']] for s in shelves), default=1) or 1
    assignments, heat_by_rack = [], {}
    for rank, (sku, sh) in enumerate(optimized):
        rank_pct = 1.0 - rank / max(n_assign, 1)
        event_term = min(1.0, freq[sh['location_id']] / 2.0)
        heat = round(min(1.0, 0.75 * rank_pct + 0.25 * event_term), 4)
        assignments.append({
            'status': 'ASSIGNED', 'sku': sku['sku'], 'abc': sku['abc'],
            'velocity': sku['velocity'], 'cube_m3': sku['cube_m3'], 'heat': heat,
            'warehouse_id': sh['warehouse_id'], 'location_id': sh['location_id'],
            'rack_id': sh['rack_id'], 'access_point': sh['access_point'],
            'aisle_dist_m': sh['aisle_dist_m'], 'event_hits': freq[sh['location_id']],
        })
        heat_by_rack[sh['rack_id']] = max(heat_by_rack.get(sh['rack_id'], 0.0), heat)
    for sh in shelves:
        if sh['location_id'] not in assigned_ids:
            assignments.append({
                'status': 'EMPTY', 'sku': None, 'abc': None, 'velocity': 0.0,
                'heat': 0.0, 'warehouse_id': sh['warehouse_id'],
                'location_id': sh['location_id'], 'rack_id': sh['rack_id'],
                'access_point': sh['access_point'], 'aisle_dist_m': sh['aisle_dist_m'],
                'event_hits': freq[sh['location_id']],
            })
            heat_by_rack.setdefault(sh['rack_id'], 0.0)

    abc_counts = collections.Counter(s['abc'] for s in SKUS)
    out = {
        'method': 'distance_greedy (velocity desc x BFS aisle distance asc)',
        'objective': 'minimize sum(velocity_i x BFS_aisle_distance_to_nearest_dock_i)',
        'distance_source': '4-dir multi-source BFS on 0.5 m nav_grid.json from all dock APs',
        'heat_formula': '0.75 x velocity rank percentile + 0.25 x min(1, event_hits/2)',
        'sku_source': 'data/skus.json',
        'seed': LAYOUT['seed'],
        'summary': {
            'skus': len(SKUS), 'shelf_locations': len(shelves),
            'assigned': n_assign, 'empty': len(shelves) - n_assign,
            'all_locations_have_status': len(assignments) == len(shelves),
            'abc_counts': dict(abc_counts),
            'weighted_cost_current': round(c0, 4),
            'weighted_cost_random': round(cr, 4),
            'weighted_cost_optimized': round(c1, 4),
            'improvement_vs_current_pct': round(imp_current, 2),
            'improvement_vs_random_pct': round(imp_random, 2),
            'gap_to_lower_bound': 0.0,
            'hot_racks': sum(1 for h in heat_by_rack.values() if h > 0.5),
            'racks_with_heat_entry': len(heat_by_rack),
        },
        'limitations': [
            '未实现 COI 的 Cube-per-Order 指数与订单共现约束；capacity_m3 仅做可行性检查',
            'SKU 周转率为 Zipf 合成数据，不是真实订单',
            '1D 排序配对即该目标函数的最优解，gap_to_lower_bound=0，改善幅度不是"算法突破"',
        ],
        'assignments': assignments,
        'heat_by_rack': heat_by_rack,
    }
    with open(os.path.join(DATA, 'slotting.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('SLOTTING_OK', json.dumps(out['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
