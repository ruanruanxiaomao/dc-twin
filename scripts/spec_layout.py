"""spec_layout.py — `config/layout_spec.json` → `data/layout.json`（唯一布局真相源）。

依据 DESIGN_PLAN.md §5（layout_spec 规范 / 展开算法 / L1–L12 校验 / AP 互斥区）：
  · 支持 grid / explicit 两种货架模式、strip 月台 + 多扇门元数据、
    line/explicit 充电桩与待命位、四角柱、输送线；
  · 12 项校验：FAIL → 退出 1；WARN → 退出 0 但必须列出；
  · L11 输出 `mutex_groups`（AP 距离 < 2*AGV_RADIUS 的互斥对），供调度加锁；
  · 默认只写 out/spec_layout.json；`--write data/layout.json` 才覆盖主数据，
    且覆盖前自动备份为 .bak（DESIGN_PLAN §0.2 规则 5）。

运行：
  python scripts/spec_layout.py --spec config/layout_spec.json
  python scripts/spec_layout.py --spec config/layout_spec.json --write data/layout.json --quiet
产出：
  out/spec_layout.json · out/spec_layout_report.json
"""
import argparse
import copy
import hashlib
import json
import math
import os
import random
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
CONFIG = os.path.join(ROOT, 'config')
OUT = os.path.join(ROOT, 'out')
EPS = 1e-6

# config.json 仅提供坐标约定 / 项目名 / 模式 / 渲染参数，不是布局参数。
CFG = json.load(open(os.path.join(DATA, 'config.json'), encoding='utf-8'))


# ---------------------------------------------------------------- 工具
def r2(v):
    return round(float(v), 2)


def overlap(a, b):
    return (a[0] < b[2] - EPS and b[0] < a[2] - EPS and
            a[1] < b[3] - EPS and b[1] < a[3] - EPS)


def inside(p, r):
    return (r[0] - EPS <= p[0] <= r[2] + EPS and
            r[1] - EPS <= p[1] <= r[3] + EPS)


def point_rect_dist(p, r):
    dx = max(r[0] - p[0], 0.0, p[0] - r[2])
    dz = max(r[1] - p[1], 0.0, p[1] - r[3])
    return math.hypot(dx, dz)


def rect_of_rack(rk):
    x, _, z = rk['position']
    rl, _, rd = rk['size']
    return [x, z, x + rl, z + rd]


def agv_radius(spec):
    a = spec['defaults']['agv']
    return round(math.hypot(a['length'] / 2, a['width'] / 2) + a['safety_margin'], 4)


# ---------------------------------------------------------------- L1 schema
def check_schema(spec):
    """纯标准库必填/类型校验（可选 jsonschema 增强，未安装也不影响）。"""
    issues = []
    if spec.get('schema_version') != '1.0':
        issues.append(('L1', 'schema_version must be "1.0"'))
    for key in ('site', 'defaults', 'warehouses'):
        if key not in spec:
            issues.append(('L1', 'missing required key: %s' % key))
    if issues:
        return issues
    site = spec['site']
    for key in ('id', 'size'):
        if key not in site:
            issues.append(('L1', 'site missing %s' % key))
    if not isinstance(site.get('size'), list) or len(site.get('size', [])) != 3:
        issues.append(('L1', 'site.size must be [w,d,h]'))
    d = spec['defaults']
    for key in ('agv', 'rack', 'ap', 'grid'):
        if key not in d:
            issues.append(('L1', 'defaults missing %s' % key))
    whs = spec['warehouses']
    if not isinstance(whs, list) or not whs:
        issues.append(('L1', 'warehouses must be a non-empty array'))
    for i, w in enumerate(whs if isinstance(whs, list) else []):
        for key in ('id', 'origin', 'size', 'racks'):
            if key not in w:
                issues.append(('L1', 'warehouses[%d] missing %s' % (i, key)))
        if not isinstance(w.get('origin'), list) or len(w.get('origin', [])) != 2:
            issues.append(('L1', 'warehouses[%d].origin must be [x,z]' % i))
    # 可选：jsonschema（若已安装）做更严格的结构检查
    try:
        import jsonschema
        schema = json.load(open(os.path.join(ROOT, 'config', 'schemas',
                                            'layout_spec.schema.json'), encoding='utf-8'))
        for err in jsonschema.Draft202012Validator(schema).iter_errors(spec):
            issues.append(('L1', 'schema: %s' % err.message[:120]))
    except ImportError:
        pass
    return issues


# ---------------------------------------------------------------- 展开
def _equipment_types():
    """读 config/equipment_spec.json 的 types；缺文件返回 {}（AST 耦合就无从算起）。"""
    path = os.path.join(CONFIG, "equipment_spec.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("types") or {}
    except Exception:                      # noqa: BLE001
        return {}


def aisle_width_for(group, defaults, ast_enabled=False, ast_mm=None, clearance=0.2):
    """算一个货架组的工作巷道宽（R3 / D5）。

    优先级（高 → 低）：

    1. **AST 驱动**（`ast_enabled` **且**该组声明了 `equipment_type`）——
       `宽 = AST/1000 + 净空`。声明了"这一区由哪种设备服务"却不用它的转弯半径，
       那这个声明就是装饰；
    2. 组内显式 `aisle_width` —— 没声明设备类型时，人手写的值就是最终值；
    3. 默认横通道宽。

    **第一版把显式宽度放在 AST 之上，结果出厂布局每组都写着 `aisle_width: 4.0`，
    AST 开了也一点不生效**——开关看着是开的，行为没变，正是最难发现的那种"功能存在
    但无效"。现在的语义是：声明了 `equipment_type` = 允许该组被设备驱动。

    AST 推出低于 `defaults.aisle.one_way_min` 时必须**夹紧到下限并留痕**
    （`clamped=True`）：窄于下限的巷道车根本过不去，静默采用等于生成一条
    仿真里能走、现实里撞墙的通道。夹紧不是错误，**不报告**才是错误。

    返回 dict：width / raw / clamped / source / min_aisle / ast_mm / equipment_type。
    """
    d_aisle = (defaults or {}).get("aisle") or {}
    min_aisle = float(d_aisle.get("one_way_min", 1.8))
    explicit = group.get("aisle_width")
    et_name = (group.get("rack") or {}).get("equipment_type") or group.get("equipment_type")

    # 没开耦合、或这一组没声明设备类型 → 维持历史行为（显式值 > 默认值）
    if not (ast_enabled and et_name):
        if explicit is not None:
            return {"width": float(explicit), "raw": float(explicit), "clamped": False,
                    "source": "explicit", "min_aisle": min_aisle,
                    "ast_mm": None, "equipment_type": et_name}
        return {"width": float(d_aisle.get("cross", 4.0)),
                "raw": float(d_aisle.get("cross", 4.0)), "clamped": False,
                "source": "default", "min_aisle": min_aisle,
                "ast_mm": None, "equipment_type": et_name}

    table = _equipment_types()
    row = table.get(et_name)
    if row is None:
        raise ValueError("AST 耦合引用了不存在的设备类型：%r" % (et_name,))
    ast = float((ast_mm or {}).get(et_name, row.get("ast_mm") or 0.0))
    if ast <= 0:
        raise ValueError("设备类型 %r 没有 ast_mm，无法驱动巷道宽" % (et_name,))
    raw = ast / 1000.0 + float(clearance)
    width = max(min_aisle, raw)
    return {"width": round(width, 4), "raw": round(raw, 4),
            "clamped": raw < min_aisle - 1e-9, "source": "ast",
            # 把被覆盖的显式值一并带出来：审阅时"为什么这条巷道是 2.0 不是 4.0"
            # 必须能一眼看到依据，否则等于静默改几何。
            "explicit_width": (float(explicit) if explicit is not None else None),
            "min_aisle": min_aisle, "ast_mm": ast, "equipment_type": et_name}


def _rack_types():
    """读 config/rack_spec.json 的 types；缺文件返回 {}（退回"只有一种货架"的现状）。"""
    path = os.path.join(CONFIG, "rack_spec.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("types") or {}
    except Exception:                      # noqa: BLE001
        return {}


def expand(spec, fleet_counts=None):
    """按 §5.4 伪代码展开；坐标一律 = warehouse.origin + 局部坐标。

    C5: when fleet_counts is given (from config/fleet_spec.json deployment),
    the standby lane is trimmed to that count. Over-capacity is recorded as an
    overflow and rejected by L13 rather than silently spawning overlapping AGVs.
    With the shipped fleet_spec (12/3/3/3 = 21) output is byte-identical to the
    frozen baseline.
    """
    #: R2：货架类型库（缺文件 → {}，退回"只有一种货架"）
    rack_spec_types = _rack_types()

    # R3：打开了 AST 耦合，却没有任何货架组声明 equipment_type → 耦合**不可能**
    # 生效。这时必须报错：否则玩家/关卡以为自己开了窄巷道，实际拿到的是出厂
    # 4.0 m 横通道，而且所有门都是绿的。单个组不声明是合法的（= 该组豁免），
    # 全局一个都没有才是配置错误。
    if (spec.get('options') or {}).get('ast_drives_aisle_width'):
        declared = [(g.get('rack') or {}).get('equipment_type') or g.get('equipment_type')
                    for w in spec.get('warehouses', []) for g in w.get('racks', [])]
        if not any(declared):
            raise ValueError("已开启 ast_drives_aisle_width，但没有任何货架组声明 "
                             "equipment_type：耦合不会生效")
    fleet_info = {'source': 'config/fleet_spec.json' if fleet_counts is not None else None,
                  'per_warehouse': {}, 'overflow': []}
    defaults = spec['defaults']
    d_rack = defaults['rack']
    d_ap = defaults['ap']
    d_dock = defaults['dock']
    rng = random.Random(spec['site'].get('seed', 0))
    warehouses = []

    for wh in spec['warehouses']:
        ox, oz = wh['origin']
        W, D, H = (wh['size']['width'], wh['size']['depth'], wh['size']['height'])
        zones = []
        for z in wh.get('zones', [{'id': 'Z1', 'name': 'Zone Z1',
                                   'rect': [0, 0, W, D], 'color': '#14b8a6'}]):
            x0, z0, x1, z1 = z['rect']
            zones.append({
                'id': z['id'], 'name': z.get('name', 'Zone ' + z['id']),
                'color': z.get('color', '#14b8a6'), 'floor': 1,
                'polygon': [[r2(ox + x0), r2(oz + z0)], [r2(ox + x1), r2(oz + z0)],
                            [r2(ox + x1), r2(oz + z1)], [r2(ox + x0), r2(oz + z1)]],
            })
        w = {
            'id': wh['id'], 'kind': wh.get('kind', 'REGIONAL'),
            'origin': [r2(ox), r2(oz)], 'size': dict(wh['size']),
            'grid': {'cell_size': defaults['grid']['cell_size'],
                     'cols': int(W / defaults['grid']['cell_size']),
                     'rows': int(D / defaults['grid']['cell_size'])},
            'zones': zones,
            'docks': [], 'racks': [], 'locations': [], 'conveyors': [],
            'stations': [], 'charging_stations': [], 'obstacles': [],
            'spawn': {'robots': []},
        }
        # ---- 货架（grid / explicit）
        shelf_n = 0
        for group in wh['racks']:
            g = dict(d_rack)
            g.update(group.get('rack', {}))
            RL, RD = g['length'], g['depth']
            levels, top = g['levels'], g['top_height']
            ap_off = group.get('ap_offset', d_ap['offset'])
            items = []
            if group.get('mode', 'grid') == 'explicit':
                for it in group['items']:
                    items.append((it['position'], it.get('size', [RL, top, RD]),
                                  it.get('levels', levels), it.get('id')))
            else:
                gx, gz = group['origin']
                mx, mz = group.get('margin', [2, 3])
                rows, bays = group['rows'], group['bays']
                # R3：巷道宽统一走 aisle_width_for（默认 AST 关闭 → 与历史完全一致）
                aw_info = aisle_width_for(
                    group, defaults,
                    ast_enabled=bool((spec.get('options') or {}).get('ast_drives_aisle_width')))
                aw = aw_info['width']
                gap = group.get('bay_gap', 0.6)
                for ri in range(rows):
                    for bi in range(bays):
                        if group.get('orientation', 'x') == 'x':
                            x = ox + gx + mx + bi * (RL + gap)
                            z = oz + gz + mz + ri * (RD + aw)
                            size = [RL, top, RD]
                        else:
                            x = ox + gx + mx + ri * (RD + aw)
                            z = oz + gz + mz + bi * (RL + gap)
                            size = [RD, top, RL]
                        rid = 'RACK-%s%s%d%02d' % (wh['id'], group['zone'], ri + 1, bi + 1)
                        items.append(([x, 0, z], size, levels, rid))
            # R2（可选）：货架存取机理。**只有 group 显式声明 rack_type 才会展开深位**，
            # 默认布局里没有这个键 → data/layout.json 与历史逐字节一致（D4）。
            rt = (group.get("rack") or {}).get("rack_type") or group.get("rack_type")
            rt_spec = (rack_spec_types.get(rt) if (rt and rack_spec_types) else None)
            n_depth = 1
            if rt_spec:
                nd = rt_spec.get("n_depth")
                n_depth = int(nd) if isinstance(nd, int) else int(group.get("lane_depth", 4))

            for pos, size, lv, rid in items:
                rk = {
                    'id': rid, 'zone': group.get('zone', zones[-1]['id']),
                    'position': [r2(pos[0]), 0, r2(pos[2])],
                    'size': [r2(size[0]), r2(size[1]), r2(size[2])],
                    'rotation': 0, 'levels': lv, 'model': g.get('model', 'rack_double'),
                    'blocks_grid': True, 'floor': 1,
                }
                w['racks'].append(rk)
                cx = rk['position'][0] + rk['size'][0] / 2
                for side, dz in enumerate((-ap_off, rk['size'][2] + ap_off)):
                    # 深位：一个"货道"按 n_depth 拆成多个库位，靠通道的 depth_index=0。
                    # 只声明了 rack_type 才拆；否则仍然「每侧一个库位」。
                    for di in range(n_depth):
                        shelf_n += 1
                        face = 90.0 if (rk['size'][2] / 2 - dz) > 0 else -90.0
                        loc = {
                            'id': 'SHELF-%s%04d' % (wh['id'], shelf_n), 'kind': 'SHELF',
                            'zone': rk['zone'], 'floor': 1, 'rack_id': rid,
                            'level_range': [1, lv],
                            'access_point': [r2(cx), r2(rk['position'][2] + dz)],
                            'face_deg': face,
                        }
                        if rt_spec:
                            loc.update({
                                'rack_type': rt,
                                'access_mechanism': rt_spec['access_mechanism'],
                                'cell_id': '%s-S%d' % (rid, side),
                                'depth_index': di,
                                'n_depth': n_depth,
                            })
                        w['locations'].append(loc)
        # ---- 月台（strip）+ 门元数据
        docks = wh.get('docks', {})
        if isinstance(docks, list):
            docks = {'mode': 'strip', 'edge': 'south',
                     'depth': d_dock['platform_depth'], 'zones': docks}
        depth = docks.get('depth', d_dock['platform_depth'])
        door_w = docks.get('door_width', d_dock['door_width'])
        door_h = docks.get('door_height', d_dock['door_height'])
        edge = docks.get('edge', 'south')
        for i, dz in enumerate(docks.get('zones', []), 1):
            x0, x1 = dz['x0'], dz['x1']
            z0 = 0 if edge == 'south' else D - depth
            z1 = z0 + depth
            did = '%s-%s-%02d' % (wh['id'], 'IN' if dz['kind'] == 'INBOUND' else 'OU', i)
            mid = r2((x0 + x1) / 2)
            ap = [r2(ox + mid), r2(oz + (z1 + d_ap['offset'] if edge == 'south' else z0 - d_ap['offset']))]
            zone_id = zones[(i - 1) % len(zones)]['id']
            doors = []
            n_doors = int(dz.get('doors', 1))
            for k in range(n_doors):
                cx = x0 + (k + 0.5) * (x1 - x0) / n_doors
                doors.append({
                    'id': '%s-D%02d' % (did, k + 1), 'kind': dz['kind'],
                    'center': [r2(ox + cx), r2(oz + (z0 if edge == 'south' else z1))],
                    'width': door_w, 'height': door_h,
                })
            w['docks'].append({
                'id': did, 'kind': dz['kind'], 'zone': zone_id,
                'rect': [r2(ox + x0), r2(oz + z0), r2(ox + x1), r2(oz + z1)],
                'door': [r2(ox + mid), r2(oz + (z0 if edge == 'south' else z1))],
                'doors': doors, 'access_point': ap,
            })
            w['locations'].append({
                'id': did, 'kind': dz['kind'], 'zone': zone_id,
                'floor': 1, 'rack_id': None, 'level_range': None,
                'access_point': ap, 'face_deg': -90.0,
            })
        # ---- 站
        for st in wh.get('stations', []):
            sid = '%s-%s' % (wh['id'], st['id'])
            x0, z0, x1, z1 = st['rect']
            apx, apz = st.get('ap', [(x0 + x1) / 2, z0 - 1.5])
            ap = [r2(ox + apx), r2(oz + apz)]
            zone_id = st.get('zone', zones[-1]['id'])
            w['stations'].append({
                'id': sid, 'kind': st['kind'], 'zone': zone_id,
                'rect': [r2(ox + x0), r2(oz + z0), r2(ox + x1), r2(oz + z1)],
                'access_point': ap,
            })
            face = round(math.degrees(math.atan2((z0 + z1) / 2 - apz,
                                                 (x0 + x1) / 2 - apx)), 1)
            w['locations'].append({
                'id': sid, 'kind': st['kind'], 'zone': zone_id,
                'floor': 1, 'rack_id': None, 'level_range': None,
                'access_point': ap, 'face_deg': face,
            })
        # ---- 充电桩
        ch = wh.get('chargers', {})
        pts = []
        if ch.get('mode', 'explicit') == 'line':
            sx, sz = ch['start']
            for k in range(int(ch.get('count', 1))):
                pts.append([sx + k * ch.get('pitch', 2.5), sz])
        else:
            pts = ch.get('points', [])
        ap_dz = ch.get('ap_dz', -2.0)
        for i, (x, z) in enumerate(pts, 1):
            cid = '%s-CHG-%02d' % (wh['id'], i)
            ap = [r2(ox + x), r2(oz + z + ap_dz)]
            zone_id = zones[-1]['id']
            w['charging_stations'].append({
                'id': cid, 'zone': zone_id,
                'position': [r2(ox + x), 0, r2(oz + z)],
                'heading': math.pi, 'power_kw': ch.get('power_kw', 3.0),
                'access_point': ap,
            })
            w['locations'].append({
                'id': cid, 'kind': 'CHARGING', 'zone': zone_id,
                'floor': 1, 'rack_id': None, 'level_range': None,
                'access_point': ap,
            })
        # ---- 柱
        pil = wh.get('pillars', {'mode': 'corners', 'inset': 0.5, 'size': [0.6, 0.6]})
        pw, pd = pil.get('size', [0.6, 0.6])
        ins = pil.get('inset', 0.5)
        if pil.get('mode', 'corners') == 'corners':
            ppts = [(ins, ins), (W - ins, ins), (ins, D - ins), (W - ins, D - ins)]
        else:
            ppts = pil.get('points', [])
        for i, (px, pz) in enumerate(ppts, 1):
            w['obstacles'].append({
                'id': '%s-PILLAR-%02d' % (wh['id'], i), 'kind': 'PILLAR',
                'rect': [r2(ox + px - pw / 2), r2(oz + pz - pd / 2),
                         r2(ox + px + pw / 2), r2(oz + pz + pd / 2)],
            })
        # ---- 待命位 / HOME
        sp = wh.get('spawns', {})
        spawns = list(sp.get('points', []))
        if sp.get('mode') == 'line':
            sx, sz = sp['start']
            dx, dz = sp.get('step', [0, 1])
            pitch = sp.get('pitch', 8.0)
            spawns = [[sx + k * dx * pitch, sz + k * dz * pitch]
                      for k in range(int(sp.get('count', 1)))]
        if fleet_counts is not None and wh['id'] in fleet_counts:
            target = int(fleet_counts[wh['id']])
            capacity = len(spawns)
            if target > capacity:
                fleet_info['overflow'].append(
                    {'warehouse': wh['id'], 'requested': target, 'capacity': capacity})
            else:
                spawns = spawns[:target]
            fleet_info['per_warehouse'][wh['id']] = {
                'requested': target, 'capacity': capacity,
                'actual': min(target, capacity)}
        for i, (x, z) in enumerate(spawns, 1):
            rid = '%s-R%02d' % (wh['id'], i)
            hid = 'HOME-%s-R%02d' % (wh['id'], i)
            ap = [r2(ox + x), r2(oz + z)]
            w['spawn']['robots'].append({
                'id': rid, 'home_id': hid,
                'position': [ap[0], 0, ap[1]],
                'heading': -math.pi / 2,
                'battery': 60 + int(rng.random() * 40), 'floor': 1,
            })
            w['locations'].append({
                'id': hid, 'kind': 'HOME', 'zone': zones[-1]['id'],
                'floor': 1, 'rack_id': None, 'level_range': None,
                'access_point': ap, 'robot_id': rid,
            })
        # ---- 输送线（path 用站点全局坐标，与现有 geometry/build_dc 口径一致）
        for cv in wh.get('conveyors', []):
            w['conveyors'].append({
                'id': cv.get('id', 'CV-%s-%02d' % (wh['id'], len(w['conveyors']) + 1)),
                'zone': cv.get('zone', zones[-1]['id']),
                'path': [[r2(p[0]), r2(p[1])] for p in cv['path']],
                'width': cv.get('width', 1.0),
                'speed_mps': cv.get('speed_mps', 0.6),
                'direction': cv.get('direction', 'FORWARD'),
                'blocks_grid': True,
            })
        warehouses.append(w)

    max_x = max(w['origin'][0] + w['size']['width'] for w in warehouses)
    max_z = max(w['origin'][1] + w['size']['depth'] for w in warehouses)
    site = spec['site']['size']
    size = {'width': round(max(site[0], max_x + 4), 2),
            'depth': round(max(site[1], max_z + 4), 2),
            'height': max(site[2], max(w['size']['height'] for w in warehouses))}
    layout = {
        'schema_version': '2.0', 'id': 'dc-network-v2',
        'name': CFG['project'], 'units': 'm', 'coord': CFG['coord'],
        'seed': spec['site'].get('seed', 0), 'mode': CFG['mode'],
        'size': size, 'warehouses': warehouses,
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
    # Do NOT add a field when deployment equals the standby lane capacity:
    # that would change layout.json byte-for-byte and break the frozen baseline
    # (A1-A40 / baseline_sha256.json). Metadata appears only on a real change.
    if fleet_counts is not None and (
            fleet_info['overflow']
            or any(v['actual'] != v['capacity']
                   for v in fleet_info['per_warehouse'].values())):
        layout['fleet_deployment'] = fleet_info
    return layout


# ---------------------------------------------------------------- L2–L12
def build_grid(layout, radius, cell=0.5):
    """独立复算 0.5 m 栅格，不依赖 data/nav_grid.json（避免自证循环）。"""
    cols = int(math.ceil(layout['size']['width'] / cell))
    rows = int(math.ceil(layout['size']['depth'] / cell))
    cells = bytearray(cols * rows)
    solids = []
    for w in layout['warehouses']:
        solids += [(rk['id'], rect_of_rack(rk)) for rk in w['racks']]
        solids += [(d['id'], list(d['rect'])) for d in w['docks']]
        solids += [(s['id'], list(s['rect'])) for s in w['stations']]
        for ch in w['charging_stations']:
            x, _, z = ch['position']
            solids.append((ch['id'], [x - 0.8, z - 0.4, x + 0.8, z + 0.4]))
        for ob in w['obstacles']:
            solids.append((ob['id'], list(ob['rect'])))
        for cv in w['conveyors']:
            for a, b in zip(cv['path'], cv['path'][1:]):
                solids.append((cv['id'], [min(a[0], b[0]) - 0.5, min(a[1], b[1]) - 0.5,
                                          max(a[0], b[0]) + 0.5, max(a[1], b[1]) + 0.5]))
    sc = CFG['scene']
    fw, fd = sc['vehicle_footprint_m']['forklift']
    tw, td = sc['vehicle_footprint_m']['truck']
    for i, (x, z) in enumerate(sc['forklifts'], 1):
        solids.append(('FORKLIFT_%02d' % i, [x - fw / 2, z - fd / 2, x + fw / 2, z + fd / 2]))
    for i, (x, z) in enumerate(sc['trucks'], 1):
        solids.append(('TRUCK_%02d' % i, [x - tw / 2, z - td / 2, x + tw / 2, z + td / 2]))
    for _, r in solids:
        x0, z0, x1, z1 = [r[0] - radius, r[1] - radius, r[2] + radius, r[3] + radius]
        c0, c1 = max(0, math.floor(x0 / cell)), min(cols, math.ceil(x1 / cell))
        r0, r1 = max(0, math.floor(z0 / cell)), min(rows, math.ceil(z1 / cell))
        for c in range(c0, c1):
            for rr in range(r0, r1):
                if (c + 1) * cell > x0 and c * cell < x1 and \
                   (rr + 1) * cell > z0 and rr * cell < z1:
                    cells[rr * cols + c] = 1
    return cols, rows, cell, cells, solids


def validate(layout, spec):
    issues, warnings, mutex = [], [], []
    R = agv_radius(spec)
    ap_clear = spec['defaults']['ap']['min_clearance']
    spawn_sep = spec['defaults']['ap']['spawn_sep']
    one_way = 2 * R + 0.3
    two_way = 4 * R + 0.2
    cols, rows, cell, cells, solids = build_grid(layout, R)

    # L13 车队部署不得超过待命带容量（C5）
    for o in layout.get('fleet_deployment', {}).get('overflow', []):
        issues.append(('L13', 'fleet_spec deployment %s requests %d robots but '
                       'standby lane capacity is %d; widen the lane first'
                       % (o['warehouse'], o['requested'], o['capacity'])))
    # L12 尺寸 / 层数
    if layout['size']['width'] <= 0 or layout['size']['depth'] <= 0:
        issues.append(('L12', 'site size must be > 0'))
    # L1 已在 check_schema 做
    for w in layout['warehouses']:
        ox, oz = w['origin']
        W, D = w['size']['width'], w['size']['depth']
        if W <= 0 or D <= 0:
            issues.append(('L12', '%s size must be > 0' % w['id']))
        rects = [(rk['id'], rect_of_rack(rk)) for rk in w['racks']]
        rects += [(d['id'], list(d['rect'])) for d in w['docks']]
        rects += [(s['id'], list(s['rect'])) for s in w['stations']]
        for ch in w['charging_stations']:
            x, _, z = ch['position']
            rects.append((ch['id'], [x - 0.8, z - 0.4, x + 0.8, z + 0.4]))
        rects += [(o['id'], list(o['rect'])) for o in w['obstacles']]
        # L2 footprint
        for sid, r in rects:
            if r[0] < ox - EPS or r[1] < oz - EPS or r[2] > ox + W + EPS or r[3] > oz + D + EPS:
                issues.append(('L2', '%s %s outside %s footprint' % (sid, r, w['id'])))
        # L3 重叠
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                if overlap(rects[i][1], rects[j][1]):
                    issues.append(('L3', '%s vs %s OVERLAP' % (rects[i][0], rects[j][0])))
        # L4/L5/L6 访问点
        for l in w['locations']:
            ap = l['access_point']
            for sid, r in rects:
                if inside(ap, r):
                    issues.append(('L4', '%s AP inside %s' % (l['id'], sid)))
                    break
            dmin = min((point_rect_dist(ap, r) for _, r in rects), default=99.0)
            if dmin < ap_clear:
                warnings.append(('L5', '%s AP near solid %.3f m' % (l['id'], dmin)))
            c, rr = int(math.floor(ap[0] / cell)), int(math.floor(ap[1] / cell))
            if not (0 <= c < cols and 0 <= rr < rows and cells[rr * cols + c] == 0):
                issues.append(('L6', '%s AP not walkable' % l['id']))
        # L7 待命位
        sp = [(rb['id'], [rb['position'][0], rb['position'][2]])
              for rb in w['spawn']['robots']]
        for rid, p in sp:
            for sid, r in rects:
                if inside(p, r):
                    issues.append(('L7', '%s spawn inside %s' % (rid, sid)))
                    break
        for i in range(len(sp)):
            for j in range(i + 1, len(sp)):
                d = math.hypot(sp[i][1][0] - sp[j][1][0], sp[i][1][1] - sp[j][1][1])
                if d < spawn_sep:
                    issues.append(('L7', '%s/%s spawn %.3f m < %.2f' %
                                   (sp[i][0], sp[j][0], d, spawn_sep)))
        # L8 通道 / L9 层高
        for group in spec['warehouses']:
            if group['id'] != w['id']:
                continue
            for g in group['racks']:
                aw = aisle_width_for(
                    g, spec['defaults'],
                    ast_enabled=bool((spec.get('options') or {}).get('ast_drives_aisle_width'))
                )['width']
                if aw < spec['defaults']['aisle']['one_way_min'] - 1e-9:
                    warnings.append(('L8', '%s 的 AST 巷道 %.2f 低于下限，已被夹紧'
                                     % (w['id'], aw)))
                if aw < one_way:
                    issues.append(('L8', '%s aisle %.2f < %.4f (one-way min)' %
                                   (w['id'], aw, one_way)))
                elif aw < two_way:
                    warnings.append(('L8', '%s aisle %.2f < %.4f (two-way rec)' %
                                     (w['id'], aw, two_way)))
                rk = dict(spec['defaults']['rack'])
                rk.update(g.get('rack', {}))
                if rk['levels'] * rk['level_height'] > rk['top_height'] + EPS:
                    issues.append(('L9', '%s levels*level_height > top_height' % w['id']))
                if rk['levels'] < 1:
                    issues.append(('L12', '%s levels < 1' % w['id']))
        # L10 门
        for d in w['docks']:
            dspec = next((x for x in spec['warehouses'] if x['id'] == w['id']), {})
            docks = dspec.get('docks', {})
            dw = docks.get('door_width', spec['defaults']['dock']['door_width'])
            dp = docks.get('door_pitch', spec['defaults']['dock']['door_pitch'])
            if dw < 2.4:
                warnings.append(('L10', '%s door width %.2f < 2.4' % (d['id'], dw)))
            if dp < dw + 0.6:
                warnings.append(('L10', '%s door pitch %.2f < width+0.6' % (d['id'], dp)))

    # L11 AP 互斥区（跨仓也算，因为站点是同一张栅格）
    locs = [l for w in layout['warehouses'] for l in w['locations']]
    threshold = 2 * R
    for i in range(len(locs)):
        for j in range(i + 1, len(locs)):
            a, b = locs[i], locs[j]
            d = math.hypot(a['access_point'][0] - b['access_point'][0],
                           a['access_point'][1] - b['access_point'][1])
            if d < threshold:
                warnings.append(('L11', '%s/%s AP_MUTEX %.3f m' % (a['id'], b['id'], d)))
                mutex.append({'a': a['id'], 'b': b['id'], 'kind_a': a['kind'],
                              'kind_b': b['kind'], 'distance_m': round(d, 4),
                              'threshold_m': round(threshold, 4)})
    return issues, warnings, mutex, R


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--spec', default=os.path.join(ROOT, 'config', 'layout_spec.json'))
    ap.add_argument('--out', default=os.path.join(OUT, 'spec_layout.json'))
    ap.add_argument('--report', default=os.path.join(OUT, 'spec_layout_report.json'),
                    help='isolated report path for negative tests')
    ap.add_argument('--write', default=None,
                    help='only with this flag will data/layout.json be overwritten')
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--fleet-spec', default=None,
                    help='C5: read config/fleet_spec.json deployment counts '
                         '(over-capacity is rejected by L13)')
    args = ap.parse_args()

    fleet_counts = None
    if args.fleet_spec:
        fp = args.fleet_spec if os.path.isabs(args.fleet_spec) else os.path.join(ROOT, args.fleet_spec)
        fl = json.load(open(fp, encoding='utf-8'))
        # C5/G4：**部署**混编目前不支持（布局不给不同车型分别预留回场位与通行宽度）。
        # 但"车型库里有多个车型"是合法的——库是价目表，部署才是现场。
        # 第一版按库的大小判，于是加一个"备选车型"就整个布局生成失败，
        # 而现场其实只跑一种车（洁净室复现立刻抓到了）。
        deployed = {d.get('model') for d in fl.get('deployment', []) if d.get('model')}
        if len(deployed) > 1:
            print('SPEC_LAYOUT refuses mixed fleet models (C5 G4: single model only)，'
                  '本次部署了 %s' % '、'.join(sorted(deployed)))
            sys.exit(1)
        fleet_counts = {d['warehouse']: d['count'] for d in fl.get('deployment', [])}
    spec = json.load(open(args.spec, encoding='utf-8'))
    schema_issues = check_schema(spec)
    if schema_issues:
        # L1 失败也必须留下可追溯的报告，不能只打印一句就退出
        os.makedirs(OUT, exist_ok=True)
        fail_report = {
            'spec': os.path.relpath(args.spec, ROOT).replace('\\', '/'),
            'verdict': 'FAIL',
            'issues': [{'rule': k, 'message': m} for k, m in schema_issues],
            'warnings': [], 'mutex_groups': [],
            'issue_kinds': {k: sum(1 for kk, _ in schema_issues if kk == k)
                            for k in sorted({k for k, _ in schema_issues})},
            'warning_kinds': {},
            'note': 'schema validation failed before expansion',
        }
        with open(args.report, 'w', encoding='utf-8') as f:
            json.dump(fail_report, f, ensure_ascii=False, indent=2)
        for _, m in schema_issues:
            print('SPEC_FAIL', m)
        print('SPEC_LAYOUT FAIL warehouses 0 racks 0 locations 0 docks 0 '
              'doors 0 robots 0 issues %s warnings 0' % len(schema_issues))
        sys.exit(1)

    layout = expand(spec, fleet_counts)
    issues, warnings, mutex, R = validate(layout, spec)
    issues = schema_issues + issues
    os.makedirs(OUT, exist_ok=True)
    payload = json.dumps(layout, ensure_ascii=False, indent=2)
    sha = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    doors = sum(len(d.get('doors', [])) for w in layout['warehouses'] for d in w['docks'])
    report = {
        'spec': os.path.relpath(args.spec, ROOT).replace('\\', '/'),
        'spec_sha256': hashlib.sha256(open(args.spec, 'rb').read()).hexdigest(),
        'fleet_spec_checked': fleet_counts is not None,
        'fleet_deployment': layout.get('fleet_deployment'),
        'fleet_robots_actual': layout['summary']['robots'],
        'layout_sha256': sha,
        'agv_radius_m': R,
        'schema_checks': 'L1-L12 + optional jsonschema',
        'summary': dict(layout['summary'], doors=doors),
        'mutex_groups': mutex,
        'mutex_pairs': len(mutex),
        'issue_kinds': {k: sum(1 for kk, _ in issues if kk == k)
                        for k in sorted({k for k, _ in issues})},
        'warning_kinds': {k: sum(1 for kk, _ in warnings if kk == k)
                          for k in sorted({k for k, _ in warnings})},
        'issues': [{'rule': k, 'message': m} for k, m in issues],
        'warnings': [{'rule': k, 'message': m} for k, m in warnings[:400]],
        'verdict': 'PASS' if not issues else 'FAIL',
        'note': 'AP_MUTEX is WARN: two APs closer than 2*AGV_RADIUS cannot be '
                'occupied simultaneously; the dispatcher must hold a mutex lock. '
                'To reach 0 mutex, widen aisle to >= 2*ap_offset + 2R + 0.2.',
    }
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(payload)
    with open(args.report, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if args.write:
        if issues:
            print('SPEC_LAYOUT refuses --write: validation FAIL')
            sys.exit(1)
        dst = args.write if os.path.isabs(args.write) else os.path.join(ROOT, args.write)
        if os.path.exists(dst):
            shutil.copy2(dst, dst + '.bak')
        with open(dst, 'w', encoding='utf-8') as f:
            f.write(payload)

    s = report['summary']
    print('SPEC_LAYOUT %s warehouses %s racks %s locations %s docks %s doors %s '
          'robots %s issues %s warnings %s'
          % (report['verdict'], s['warehouses'], s['racks'], s['locations'],
             s['docks'], doors, s['robots'], len(issues), len(warnings)))
    if not args.quiet:
        for k, m in issues[:15]:
            print('  FAIL', k, m)
        for k, m in warnings[:5]:
            print('  WARN', k, m)
    sys.exit(1 if issues else 0)


if __name__ == '__main__':
    main()
