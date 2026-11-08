"""gen_data.py — 生成布局 / SKU 主数据 / 业务事件（纯标准库，不依赖 Blender）。

第五阶段整改（依据 AUDIT_REPORT.md §3.1 / §3.5 / §3.7）：
  · 布局全部改为**显式坐标**，不再用 `x = ox+3+t*(W-6)` 这类一维公式，
    消除月台压货架、PACK 压充电桩、出生点落在实体内等 114 处冲突。
  · F 仓改 2 排货架（30×20 m 放不下 3 排 + 服务设施），W 仓服务带重排。
  · 新增 data/skus.json 作为唯一 SKU 主数据；events 的 from/to/kind 严格匹配且同仓。
  · stations 的 access_point 也进 locations[]，保证 events 引用完整。
  · 不再生成 paths.jsonl（旧版规则环线会被 plan_paths 覆盖，属无效计算）。
  · layout.summary 只记布局统计；路径采样数改由 path_metrics.json 记录，
    消除审计 §3.7 的「777 vs 2681」从零复现失败。

第六阶段整改（用户质疑「车走到货架最里面就没了，不应该是个循环吗」）：
  · 每台 AGV 增加显式 HOME 待命位（locations[] 中 kind=HOME）；
  · 事件改为**闭环作业单**：HOME → 取货 → 交付 → HOME，return_to 显式引用 HOME；
  · 21 台车 = 21 张订单，全部执行（PENDING=0），覆盖 5 种业务类型；
  · F 仓待命位移到东侧专用待命带（局部 x=29），避开 1.5 m 窄巷，
    否则「车停在巷子里」会让其它车永远无法通过（严格交通语义）。

产出：data/layout.json · data/skus.json · data/events.jsonl
"""
import json
import math
import os
import random

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
CFG = json.load(open(os.path.join(DATA, 'config.json'), encoding='utf-8'))
RNG = random.Random(CFG['seed'])

RL, RD = 3.0, 1.2          # 货架长 / 深（m）
N_SKUS = 240
# 一台车一张作业单，全部执行；不再造 15 条永远 PENDING 的装饰事件
N_EVENTS = 21


def new_warehouse(wid, kind, origin, size, zones_def):
    ox, oz = origin
    return {
        'id': wid, 'kind': kind, 'origin': list(origin),
        'size': dict(size),
        'grid': {'cell_size': 0.5, 'cols': int(size['width'] / 0.5),
                 'rows': int(size['depth'] / 0.5)},
        'zones': [{'id': zid, 'name': 'Zone ' + zid, 'color': color, 'floor': 1,
                   'polygon': [[ox + x0, oz + z0], [ox + x1, oz + z0],
                               [ox + x1, oz + z1], [ox + x0, oz + z1]]}
                  for zid, (x0, z0, x1, z1, color) in zones_def.items()],
        'docks': [], 'racks': [], 'locations': [], 'conveyors': [],
        'stations': [], 'charging_stations': [], 'obstacles': [],
        'spawn': {'robots': []},
    }


def add_rack_grid(wh, zone_rows):
    """zone_rows: {zone: ([rack_z...], [bay_x...], levels, height)}，仓库局部坐标。"""
    ox, oz = wh['origin']
    n = 0
    for zid, (zs, xs, levels, rh) in zone_rows.items():
        for ri, z in enumerate(zs):
            for bi, x in enumerate(xs):
                rid = 'RACK-%s%s%d%02d' % (wh['id'], zid, ri + 1, bi + 1)
                wh['racks'].append({
                    'id': rid, 'zone': zid,
                    'position': [round(ox + x, 2), 0, round(oz + z, 2)],
                    'size': [RL, rh, RD], 'rotation': 0, 'levels': levels,
                    'model': 'rack_double', 'blocks_grid': True, 'floor': 1,
                })
                # AP 距货架 1.5 m：大于 AGV_RADIUS(0.73) + 0.5 m 格宽，保证可走格存在
                for dz in (-1.5, RD + 1.5):
                    n += 1
                    # 从 AP 指向货架中心的角度：AGV 到达后要"面朝货架"停靠取货
                    face = 90.0 if (RD / 2 - dz) > 0 else -90.0
                    wh['locations'].append({
                        'id': 'SHELF-%s%04d' % (wh['id'], n), 'kind': 'SHELF',
                        'zone': zid, 'floor': 1, 'rack_id': rid,
                        'level_range': [1, levels],
                        'access_point': [round(ox + x + RL / 2, 2), round(oz + z + dz, 2)],
                        'face_deg': face,
                    })


def add_docks(wh, specs):
    """specs: [(x0,z0,x1,z1,kind)]；AP 放在月台外侧 1.5 m 的通道里。"""
    ox, oz = wh['origin']
    for i, (x0, z0, x1, z1, kind) in enumerate(specs, 1):
        did = '%s-%s-%02d' % (wh['id'], 'IN' if kind == 'INBOUND' else 'OU', i)
        mid = round((x0 + x1) / 2, 2)
        ap = [round(ox + mid, 2), round(oz + z1 + 1.5, 2)]
        wh['docks'].append({
            'id': did, 'kind': kind, 'zone': wh['zones'][(i - 1) % len(wh['zones'])]['id'],
            'rect': [round(ox + x0, 2), round(oz + z0, 2),
                     round(ox + x1, 2), round(oz + z1, 2)],
            'door': [round(ox + mid, 2), round(oz + z0, 2)], 'access_point': ap,
        })
        wh['locations'].append({
            'id': did, 'kind': kind, 'zone': wh['zones'][(i - 1) % len(wh['zones'])]['id'],
            'floor': 1, 'rack_id': None, 'level_range': None, 'access_point': ap,
            'face_deg': -90.0,     # AP 在月台外侧，车要面朝月台门停靠
        })


def add_stations(wh, specs):
    """specs: [(id,kind,x0,z0,x1,z1,ap_x,ap_z)]；AP 一并登记进 locations。"""
    ox, oz = wh['origin']
    for sid, kind, x0, z0, x1, z1, apx, apz in specs:
        full = '%s-%s' % (wh['id'], sid)
        ap = [round(ox + apx, 2), round(oz + apz, 2)]
        wh['stations'].append({
            'id': full, 'kind': kind, 'zone': wh['zones'][-1]['id'],
            'rect': [round(ox + x0, 2), round(oz + z0, 2),
                     round(ox + x1, 2), round(oz + z1, 2)], 'access_point': ap,
        })
        face = round(math.degrees(math.atan2((z0 + z1) / 2 - apz,
                                             (x0 + x1) / 2 - apx)), 1)
        wh['locations'].append({
            'id': full, 'kind': kind, 'zone': wh['zones'][-1]['id'],
            'floor': 1, 'rack_id': None, 'level_range': None, 'access_point': ap,
            'face_deg': face,
        })


def add_chargers(wh, points, ap_dz):
    ox, oz = wh['origin']
    for i, (x, z) in enumerate(points, 1):
        cid = '%s-CHG-%02d' % (wh['id'], i)
        ap = [round(ox + x, 2), round(oz + z + ap_dz, 2)]
        wh['charging_stations'].append({
            'id': cid, 'zone': wh['zones'][-1]['id'],
            'position': [round(ox + x, 2), 0, round(oz + z, 2)],
            'heading': math.pi, 'power_kw': 3.0, 'access_point': ap,
        })
        wh['locations'].append({
            'id': cid, 'kind': 'CHARGING', 'zone': wh['zones'][-1]['id'],
            'floor': 1, 'rack_id': None, 'level_range': None, 'access_point': ap,
        })


def add_pillars_and_spawns(wh, spawns):
    ox, oz = wh['origin']
    W, D = wh['size']['width'], wh['size']['depth']
    for i, (px, pz) in enumerate(((0.5, 0.5), (W - 0.5, 0.5),
                                  (0.5, D - 0.5), (W - 0.5, D - 0.5)), 1):
        wh['obstacles'].append({
            'id': '%s-PILLAR-%02d' % (wh['id'], i), 'kind': 'PILLAR',
            'rect': [round(ox + px - 0.3, 2), round(oz + pz - 0.3, 2),
                     round(ox + px + 0.3, 2), round(oz + pz + 0.3, 2)],
        })
    for i, (x, z) in enumerate(spawns, 1):
        rid = '%s-R%02d' % (wh['id'], i)
        hid = 'HOME-%s-R%02d' % (wh['id'], i)
        ap = [round(ox + x, 2), round(oz + z, 2)]
        wh['spawn']['robots'].append({
            'id': rid, 'home_id': hid,
            'position': [ap[0], 0, ap[1]],
            'heading': -math.pi / 2,
            'battery': 60 + int(RNG.random() * 40), 'floor': 1,
        })
        # HOME 是真实库位：闭环的起终点，且被 layout_audit / nav_grid 一并校验
        wh['locations'].append({
            'id': hid, 'kind': 'HOME', 'zone': wh['zones'][-1]['id'],
            'floor': 1, 'rack_id': None, 'level_range': None,
            'access_point': ap, 'robot_id': rid,
        })


def build_regional():
    """W：4 分区 × 4 排 × 10 bay，服务带 z=58..70 与货架区完全分离。"""
    zones = {'A': (4, 10, 46, 32, '#3b82f6'), 'B': (54, 10, 96, 32, '#a855f7'),
             'C': (4, 38, 46, 62, '#22c55e'), 'D': (54, 38, 96, 62, '#f59e0b')}
    wh = new_warehouse('W', 'REGIONAL', (0, 0),
                       {'width': 100, 'depth': 70, 'height': 12}, zones)
    grid = {}
    for zid, (x0, z0, _, _, _) in zones.items():
        zs = [round(z0 + 3 + r * (RD + 4.0), 2) for r in range(4)]
        xs = [round(x0 + 2 + b * (RL + 0.6), 2) for b in range(10)]
        grid[zid] = (zs, xs, 4, 6.0)
    add_rack_grid(wh, grid)
    add_docks(wh, [(2, 0, 23, 8, 'INBOUND'), (27, 0, 48, 8, 'OUTBOUND'),
                   (52, 0, 73, 8, 'INBOUND'), (77, 0, 98, 8, 'OUTBOUND')])
    wh['conveyors'] = [
        {'id': 'CV-W01', 'zone': 'A', 'path': [[10, 35], [46, 35]],
         'width': 1.0, 'speed_mps': 0.6, 'direction': 'FORWARD', 'blocks_grid': True},
        {'id': 'CV-W02', 'zone': 'B', 'path': [[54, 35], [90, 35]],
         'width': 1.0, 'speed_mps': 0.6, 'direction': 'FORWARD', 'blocks_grid': True},
    ]
    # 服务带 z=58..70：最后排货架到 57.8，站点从 62 起，留出 4.2 m 净通道
    add_stations(wh, [
        ('PACK-02', 'PACKING', 4, 62, 18, 67, 11, 60.5),
        ('PACK-01', 'PACKING', 82, 62, 96, 67, 89, 60.5),
        ('SORT-01', 'SORTING', 60, 62, 74, 67, 67, 60.5),
    ])
    # 充电桩放在站点之间的中段空区（x=30..42.5），AP 与站点 x 不重叠
    add_chargers(wh, [(30 + 2.5 * i, 68.5) for i in range(6)], -2.0)
    # 12 台车分散到 A/B/C/D 四个分区的侧通道，每区 3 台；
    # 避免全挤在东西墙导致任务路径横穿整仓（审计 §3.3 的 193 s 单车时长）
    # 贴东西墙的专用待命位：主通道在 x≈3.5 / 96.5，待命位与通道净距 >2 m，
    # 这样「停着的车」不会被经过的车擦到（严格交通语义需要）。
    # 待命位对齐 0.5 m 栅格中心，闭环起终点与 A* 折点严格同点（无 0.35 m 偏移）
    spawns = [(1.75, z) for z in (12.25, 20.25, 28.25, 40.25, 48.25, 56.25)] + \
             [(98.25, z) for z in (12.25, 20.25, 28.25, 40.25, 48.25, 56.25)]
    add_pillars_and_spawns(wh, spawns)
    return wh


def build_forward(wid, origin):
    """F：2 排货架 + 6 m 月台 + 贴后墙 PACK，所有实体互不重叠。"""
    zones = {'Z1': (2, 2, 28, 18, '#14b8a6')}
    wh = new_warehouse(wid, 'FORWARD', origin,
                       {'width': 30, 'depth': 20, 'height': 8}, zones)
    add_rack_grid(wh, {'Z1': ([9.0, 13.4],
                              [round(3 + b * (RL + 0.6), 2) for b in range(6)],
                              3, 4.5)})
    add_docks(wh, [(2, 0, 14, 6, 'INBOUND'), (16, 0, 28, 6, 'OUTBOUND')])
    # 第二排货架到 14.6，PACK 从 18 起，留出 3.4 m 净通道
    add_stations(wh, [('PACK-01', 'PACKING', 22, 18, 28, 20, 25, 16.5)])
    add_chargers(wh, [(4, 18), (6.5, 18)], -1.9)
    # 东侧专用待命带（局部 x=29，z 分别落在前巷 / 排间 / 后巷）。
    # 旧版把 3 台车停在 1.54 m 宽的正面前巷里，任何一台车出动都会擦到另两台，
    # 是「完成即离场」语义掩盖掉的硬冲突；改到侧向后巷后所有车可全程在场。
    add_pillars_and_spawns(wh, [(29.25, 7.25), (29.25, 11.25), (29.25, 15.25)])
    return wh


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


# (取货点 kind, 允许的交付点 kind 集合)。PICK 在本布局中允许打包台 / 分拣台 /
# 出库集货口三类真实作业点：否则前区车的「拣货」必须横跨全仓去后区打包台，
# 单车路径会膨胀到 170+ m，把整个演示拖成数千帧。
EVENT_RULES = {
    'INBOUND': ('INBOUND', {'SHELF'}),
    'REPLENISH': ('INBOUND', {'SHELF'}),
    'OUTBOUND': ('SHELF', {'OUTBOUND'}),
    'PICK': ('SHELF', {'PACKING', 'SORTING', 'OUTBOUND'}),
    'TRANSFER': ('SHELF', {'SHELF'}),
}


EVENT_TYPES = ('OUTBOUND', 'PICK', 'TRANSFER', 'INBOUND', 'REPLENISH')


def build_events(layout, skus):
    """21 张**闭环作业单**：HOME → 取货 → 交付 → HOME，全部执行、覆盖 5 种类型。

    用户质疑「车从货架旁边一直走到最里面，然后出来嘛？不应该是一个循环吗」：
    旧版一台车只做 SHELF→SHELF 单程搬运，一头扎进货架深处就结束并离场消失，
    既没有交付点也没有回待命位。本版每台车一个完整作业循环：

      ① HOME 待命位出发（空驶 outbound）
      ② 到取货点 PICK：面朝作业点停靠 2 s
      ③ 载货到交付点 DROP：卸货
      ④ 空驶回 HOME，停在那里直到仿真结束（充电 / 待下一单）

    取货点固定在该车专属排间通道的**深端**，交付点固定在**对面半区**的
    月台 / 打包站 / 另一半货架，因此路径必须横穿整排货架与中央主通道；
    再叠加回程段，形成闭合回路而不是「进死胡同就停」。
    """
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
        # 前区车派「月台类」订单（月台在 z 前端），后区车派「打包/移库」订单
        # （打包台在 z 后端）。否则后排车为了去前排月台要横穿整仓，
        # 单车 170+ m，21 台车会被错峰串行化到数百秒。
        FRONT_TYPES = ('OUTBOUND', 'INBOUND', 'REPLENISH', 'PICK', 'TRANSFER')
        BACK_TYPES = ('PICK', 'TRANSFER')

        for rb in w['spawn']['robots']:
            sp = (rb['position'][0], rb['position'][2])
            side = 'W' if sp[0] < w_center else 'E'
            zone = 'F' if sp[1] < w['origin'][1] + w['size']['depth'] / 2 else 'B'
            # 第二轮审计 P0-1：lane 唯一键必须是 z，同一通道只派一台车
            cand = sorted((abs(z - sp[1]), z) for z in lanes if z not in used)
            z_key = cand[0][1]
            used.add(z_key)
            row = sorted(lanes[z_key], key=lambda l: l['access_point'][0])
            split = len(row) // 2
            half = (row[:split] if side == 'W' else row[split:]) if len(row) >= 12 else row
            other = (row[split:] if side == 'W' else row[:split]) if len(row) >= 12 else []
            deep = half[-1] if side == 'W' else half[0]      # 深入货架区的一端
            near = half[0] if side == 'W' else half[-1]       # 靠 HOME 的一端

            def dist(a, b):
                return math.hypot(a[0] - b[0], a[1] - b[1])

            def best(pool, anchor):
                """挑让「深端 -> 交付点 -> HOME」总里程最短的真实作业点。

                取最远端虽然能横穿全仓，但单车会长到 200+ m、把 21 台车
                串行化到上千帧。这里按闭环里程选点：仍是真实月台/打包站，
                且回路更紧凑；TRANSFER 单另走对面半区以保留中央主通道流量。
                """
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
            else:   # TRANSFER：同一通道的另一半区，强制跨中央主通道
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
    assert len(events) == N_EVENTS, (len(events), N_EVENTS)
    return events


def main():
    warehouses = [build_regional()] + [
        build_forward(wid, tuple(o))
        for wid, o in zip(('F1', 'F2', 'F3'), ((6, 90), (42, 90), (78, 90)))]
    max_x = max(w['origin'][0] + w['size']['width'] for w in warehouses)
    max_z = max(w['origin'][1] + w['size']['depth'] for w in warehouses)
    layout = {
        'schema_version': '2.0', 'id': 'dc-network-v2',
        'name': CFG['project'], 'units': 'm', 'coord': CFG['coord'],
        'seed': CFG['seed'], 'mode': CFG['mode'],
        'size': {'width': round(max_x + 4, 2), 'depth': round(max_z + 4, 2),
                 'height': max(w['size']['height'] for w in warehouses)},
        'warehouses': warehouses,
        'summary': {
            'warehouses': len(warehouses),
            'racks': sum(len(w['racks']) for w in warehouses),
            'locations': sum(len(w['locations']) for w in warehouses),
            'shelf_locations': sum(1 for w in warehouses for l in w['locations']
                                   if l['kind'] == 'SHELF'),
            'docks': sum(len(w['docks']) for w in warehouses),
            'conveyors': sum(len(w['conveyors']) for w in warehouses),
            'stations': sum(len(w['stations']) for w in warehouses),
            'robots': sum(len(w['spawn']['robots']) for w in warehouses),
        },
    }
    with open(os.path.join(DATA, 'layout.json'), 'w', encoding='utf-8') as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)
    skus = build_skus()
    with open(os.path.join(DATA, 'skus.json'), 'w', encoding='utf-8') as f:
        json.dump(skus, f, ensure_ascii=False, indent=2)
    events = build_events(layout, skus)
    locmap = {l['id']: l for w in warehouses for l in w['locations']}
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
          'event_types', types, 'size', json.dumps(layout['size'], ensure_ascii=False))


if __name__ == '__main__':
    main()
