"""build_dc.py — 从 data/layout.json 生成配送中心 Blender 场景（纯 bpy，headless 可跑）。

几何方法参照 cargoTwin (MIT) 的 lib_geo.py：按材质累积网格、共享 mesh + 实例化对象。
资产母版来自 dc_project/assets/cargotwin_assets.glb（cargoTwin，MIT，仅提取 5 个资产）。

坐标（唯一定义，勿改）：
  layout 平面 [x, z] -> Blender (X, Y) = (x, z)
  layout 3D [x, h, z] -> Blender (X, Y, Z) = (x, z, h)
  glTF 导出时 export_yup=True 会做 (x, y, z) -> (x, z, -y)

运行： blender -b --factory-startup -P scripts/build_dc.py
产出： out/distribution_center.blend · out/build_report.json
"""
import bmesh
import bpy
import json
import math
import os
import random
import sys
from mathutils import Vector

# Blender 的 -P 不会把脚本目录放进 sys.path（cwd 是项目根），
# 所以同目录的 scene_config 必须显式加进来。踩过一次，别删。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'out')
ASSET_GLB = os.path.join(ROOT, 'assets', 'cargotwin_assets.glb')
BLEND = os.path.join(OUT, 'distribution_center.blend')

LAYOUT = json.load(open(os.path.join(DATA, 'layout.json'), encoding='utf-8'))
import scene_config
CFG = scene_config.load()
RNG = random.Random(LAYOUT['seed'])
COUNTS = {}

# 复刻 cargoTwin lib_geo.py 的 14 色 PALETTE（MIT），另加场景专用色
PALETTE = {
    'concrete': '#d8d8d2', 'ground': '#b9c3ae', 'wall': '#f0efe9',
    'roof_beam': '#9aa0a6', 'rack_frame': '#e2601f', 'rack_deck': '#b9bcc0',
    'glass': '#a9c6d8', 'line_yellow': '#f2b21b', 'line_cyan': '#25c9dd',
    'road': '#5b6068', 'dock': '#8b9099', 'conveyor': '#4a5058',
    'zone_a': '#3b82f6', 'zone_b': '#a855f7', 'zone_c': '#22c55e',
    'zone_d': '#f59e0b', 'zone_f': '#14b8a6',
    'corridor': '#8aa9bd',
    'crate': '#c99b62', 'light': '#fff6e0',
    'rack_cold': '#3b82f6', 'rack_cool': '#22c55e', 'rack_mid': '#eab308',
    'rack_warm': '#f97316', 'rack_hot': '#ef4444',
}

SLOTTING = json.load(open(os.path.join(DATA, 'slotting.json'), encoding='utf-8'))
HEAT = SLOTTING['heat_by_rack']
SCHED = json.load(open(os.path.join(DATA, 'agent_schedules.json'),
                       encoding='utf-8'))['schedules']
TASKS = json.load(open(os.path.join(DATA, 'tasks.json'), encoding='utf-8'))
CARGO = json.load(open(os.path.join(DATA, 'cargo_state.json'), encoding='utf-8'))
LOCMAP = {l['id']: l for w in LAYOUT['warehouses'] for l in w['locations']}


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_linear(h):
    h = h.lstrip('#')
    return tuple(srgb_to_linear(int(h[i:i + 2], 16) / 255.0) for i in (0, 2, 4))


def material(key):
    name = 'DC_' + key
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get('Principled BSDF')
    r, g, b = hex_linear(PALETTE[key])
    bsdf.inputs['Base Color'].default_value = (r, g, b, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.85
    if key == 'line_cyan':
        try:
            bsdf.inputs['Emission Color'].default_value = (r, g, b, 1.0)
            bsdf.inputs['Emission Strength'].default_value = 2.5
        except KeyError:
            pass
    m.diffuse_color = (r, g, b, 1.0)
    return m


def collection(name):
    c = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(c)
    return c


def B3(p):
    """layout 3D [x,h,z] -> Blender (x,z,h)"""
    return (p[0], p[2], p[1])


def B2(p, h=0.0):
    """layout 2D [x,z] -> Blender (x,z,h)"""
    return (p[0], p[1], h)


def unit_box_mesh(key):
    m = bpy.data.meshes.new('UNIT_' + key)
    v = [(-.5, -.5, -.5), (-.5, -.5, .5), (-.5, .5, -.5), (-.5, .5, .5),
         (.5, -.5, -.5), (.5, -.5, .5), (.5, .5, -.5), (.5, .5, .5)]
    f = [(0, 4, 6, 2), (1, 3, 7, 5), (0, 1, 5, 4),
         (2, 6, 7, 3), (0, 2, 3, 1), (4, 5, 7, 6)]
    m.from_pydata(v, [], f)
    m.update()
    m.materials.append(material(key))
    return m


UNIT = {}


def box(name, center, dims, key, coll, rot_z=0.0):
    if key not in UNIT:
        UNIT[key] = unit_box_mesh(key)
    o = bpy.data.objects.new(name, UNIT[key])
    coll.objects.link(o)
    o.location = center
    o.scale = dims
    o.rotation_euler[2] = rot_z
    return o


def plane(name, x0, y0, x1, y1, z, key, coll):
    m = bpy.data.meshes.new(name + '_mesh')
    m.from_pydata([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)],
                  [], [(0, 1, 2, 3)])
    m.update()
    m.materials.append(material(key))
    o = bpy.data.objects.new(name, m)
    coll.objects.link(o)
    return o


def add_bm_box(bm, lo, hi, mat_index):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    vs = [bm.verts.new(p) for p in (
        (x0, y0, z0), (x0, y0, z1), (x0, y1, z0), (x0, y1, z1),
        (x1, y0, z0), (x1, y0, z1), (x1, y1, z0), (x1, y1, z1))]
    for idx in ((0, 4, 6, 2), (1, 3, 7, 5), (0, 1, 5, 4),
                (2, 6, 7, 3), (0, 2, 3, 1), (4, 5, 7, 6)):
        face = bm.faces.new([vs[i] for i in idx])
        face.material_index = mat_index


def unit_rack_mesh(levels=4, frame_key='rack_frame'):
    """[0,1]^3 标准货架：底面 z=0，4 根立柱 + levels 层板，两个材质槽。"""
    bm = bmesh.new()
    for sx in (0.0, 0.94):
        for sy in (0.0, 0.94):
            add_bm_box(bm, (sx, sy, 0.0), (sx + 0.06, sy + 0.06, 1.0), 0)
    for i in range(levels):
        z = (i + 1) / levels
        add_bm_box(bm, (0.0, 0.0, max(z - 0.018, 0.0)), (1.0, 1.0, z), 1)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    m = bpy.data.meshes.new('UNIT_RACK_%d' % levels)
    bm.to_mesh(m)
    bm.free()
    m.materials.append(material(frame_key))
    m.materials.append(material('rack_deck'))
    return m


RACK_MESHES = {}
HEAT_KEYS = ('rack_cold', 'rack_cool', 'rack_mid', 'rack_warm', 'rack_hot')


def rack_mesh_for_heat(heat, levels=4):
    """审计 P1-1：F 仓 levels=3，不能所有货架都用 4 层 mesh。"""
    idx = min(4, int(max(0.0, min(heat, 1.0)) * 5))
    key = HEAT_KEYS[idx]
    k = (key, levels)
    if k not in RACK_MESHES:
        RACK_MESHES[k] = unit_rack_mesh(levels, key)
    return RACK_MESHES[k]


def extract_asset(root, name):
    """把 cargoTwin 的 asset_<name> Empty 下所有子网格烘成一个 root 局部空间的 mesh。"""
    inv = root.matrix_world.inverted()
    bm = bmesh.new()
    slots = []
    for ch in root.children:
        if ch.type != 'MESH':
            continue
        rel = inv @ ch.matrix_world
        src = ch.data
        for poly in src.polygons:
            sm = src.materials[poly.material_index] if poly.material_index < len(src.materials) else None
            mn = sm.name if sm else 'DC_crate'
            if mn not in slots:
                slots.append(mn)
            mi = slots.index(mn)
            vs = [bm.verts.new(rel @ src.vertices[vi].co) for vi in poly.vertices]
            bm.faces.new(vs).material_index = mi
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    m = bpy.data.meshes.new('ASSET_' + name)
    bm.to_mesh(m)
    bm.free()
    for mn in slots:
        m.materials.append(bpy.data.materials.get(mn) or material('crate'))
    m.use_fake_user = True
    return m


def import_assets():
    wanted = ('asset_amr', 'asset_pallet_boxes', 'asset_forklift',
              'asset_truck_box', 'asset_dock_leveler')
    bpy.ops.import_scene.gltf(filepath=ASSET_GLB)
    out = {}
    for n in wanted:
        root = bpy.data.objects.get(n)
        if root:
            out[n] = extract_asset(root, n)
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    return out


def build_ground_and_buildings(assets, coll_ground, coll_build):
    S = LAYOUT['size']
    plane('GROUND_EXTERIOR', 0, 0, S['width'], S['depth'], 0.0, 'ground', coll_ground)
    # 连接 W 与前置仓的园区道路（参数来自 config.scene.roads）
    for i, (x0, z0, x1, z1) in enumerate(CFG['scene']['roads'], 1):
        plane('ROAD_%02d' % i, x0, z0, x1, z1, 0.015, 'road', coll_ground)

    for wh in LAYOUT['warehouses']:
        ox, oz = wh['origin']
        W, D, H = wh['size']['width'], wh['size']['depth'], wh['size']['height']
        c = collection('BLD_' + wh['id'])
        plane('FLOOR_' + wh['id'], ox, oz, ox + W, oz + D, 0.02, 'concrete', c)
        # 分区标线（贴地彩色条）
        for z in wh['zones']:
            pts = z['polygon']
            key = 'zone_' + z['id'].lower() if z['id'] in 'ABCD' else 'zone_f'
            if key not in PALETTE:
                key = 'zone_f'
            plane('ZONE_' + wh['id'] + '_' + z['id'], pts[0][0], pts[0][1],
                  pts[2][0], pts[2][1], 0.025, key, c)
        # 开放厂房骨架：角柱 + 边柱 + 顶梁 + 后墙（其余三面开放，便于俯视仓内）
        inset = 0.25
        for px in (ox + inset, ox + W - inset):
            for pz in (oz + inset, oz + D - inset):
                box('COL_%s_%.0f_%.0f' % (wh['id'], px, pz),
                    (px, pz, H / 2), (0.5, 0.5, H), 'roof_beam', c)
        for i in range(1, int(W / 10)):
            px = ox + i * 10
            for pz in (oz + 0.2, oz + D - 0.2):
                box('COL_%s_E%.0f_%.0f' % (wh['id'], px, pz),
                    (px, pz, H / 2), (0.35, 0.35, H), 'roof_beam', c)
        box('BEAM_%s_S' % wh['id'], (ox + W / 2, oz + 0.15, H - 0.15), (W, 0.3, 0.3), 'roof_beam', c)
        box('BEAM_%s_N' % wh['id'], (ox + W / 2, oz + D - 0.15, H - 0.15), (W, 0.3, 0.3), 'roof_beam', c)
        box('BEAM_%s_W' % wh['id'], (ox + 0.15, oz + D / 2, H - 0.15), (0.3, D, 0.3), 'roof_beam', c)
        box('BEAM_%s_E' % wh['id'], (ox + W - 0.15, oz + D / 2, H - 0.15), (0.3, D, 0.3), 'roof_beam', c)
        box('WALL_%s_BACK' % wh['id'], (ox + W / 2, oz + D - 0.1, H / 2),
            (W, 0.2, H), 'wall', c)
        # 地面 5S 标线
        for i, (x0, z0, x1, z1) in enumerate((
                (2, 2, W - 2, 2.16), (2, D - 2.16, W - 2, D - 2))):
            plane('LINE_%s_%d' % (wh['id'], i), ox + x0, oz + z0,
                  ox + x1, oz + z1, 0.03, 'line_yellow', c)


def build_racks_and_cargo(assets):
    """审计 P1-1/P1-2/P0-5：层数按 rk['levels']；热力标签 0.9×0.9；托盘来自 slotting。"""
    from collections import defaultdict
    pallet_mesh = assets.get('asset_pallet_boxes')
    assigned = [a for a in SLOTTING['assignments'] if a['status'] == 'ASSIGNED']
    by_rack = defaultdict(list)
    for a in assigned:
        by_rack[a['rack_id']].append(a)
    COUNTS['racks_built'] = 0
    COUNTS['pallets_built'] = 0
    COUNTS['slotting_pallets'] = len(assigned)
    COUNTS['heat_buckets'] = dict.fromkeys(HEAT_KEYS, 0)
    label = tuple(CFG['scene']['heat_label_size_m'])
    for wh in LAYOUT['warehouses']:
        c = collection('RACKS_' + wh['id'])
        cc = collection('CARGO_' + wh['id'])
        for rk in wh['racks']:
            x, h, z = rk['position']
            rl, rh, rd = rk['size']
            levels = rk['levels']
            heat = HEAT.get(rk['id'], 0.0)
            mesh = rack_mesh_for_heat(heat, levels)
            heat_key = HEAT_KEYS[min(4, int(max(0.0, min(heat, 1.0)) * 5))]
            COUNTS['heat_buckets'][heat_key] += 1
            o = bpy.data.objects.new(rk['id'], mesh)
            c.objects.link(o)
            o.location = (x, z, h + 0.03)
            o.scale = (rl, rd, rh)
            box('HEAT_' + rk['id'], (x + rl / 2, z + rd / 2, h + rh + 0.06),
                label, heat_key, c)
            COUNTS['racks_built'] += 1
            if pallet_mesh:
                lh = rh / levels
                for j, a in enumerate(by_rack.get(rk['id'], [])):
                    lv = j % levels
                    deck_z = h + 0.03 + (lv + 1) * lh   # 第 lv 层层板顶面
                    p = bpy.data.objects.new('PALLET_%s' % a['sku'], pallet_mesh)
                    cc.objects.link(p)
                    p.location = (x + rl / 2, z + rd / 2, deck_z + 0.02)
                    p.scale = (0.92, 0.92, min(0.92, lh / 1.15))
                    COUNTS['pallets_built'] += 1


def build_docks_conveyors(assets):
    COUNTS['docks_built'] = 0
    COUNTS['conveyors_built'] = 0
    for wh in LAYOUT['warehouses']:
        c = collection('DOCKS_' + wh['id'])
        for d in wh['docks']:
            x0, z0, x1, z1 = d['rect']
            box('DOCK_' + d['id'], ((x0 + x1) / 2, (z0 + z1) / 2, 0.6),
                (x1 - x0, z1 - z0, 1.2), 'dock', c)
            box('DOOR_' + d['id'], (d['door'][0], d['door'][1] + 0.3, 2.2),
                (min(x1 - x0, 6.0), 0.15, 4.4), 'glass', c)
            plane('APRON_' + d['id'], x0, z1, x1, z1 + 3, 0.035, 'line_yellow', c)
            COUNTS['docks_built'] += 1
        for cv in wh['conveyors']:
            pts = cv['path']
            for i in range(len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                box('CV_%s_%d' % (cv['id'], i),
                    ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0.55),
                    (abs(b[0] - a[0]) or cv['width'], abs(b[1] - a[1]) or cv['width'], 1.1),
                    'conveyor', c)
                COUNTS['conveyors_built'] += 1
        for st in wh['stations']:
            x0, z0, x1, z1 = st['rect']
            box('STATION_' + st['id'], ((x0 + x1) / 2, (z0 + z1) / 2, 0.5),
                (x1 - x0, z1 - z0, 1.0), 'rack_deck', c)
        for ch in wh['charging_stations']:
            p = B3(ch['position'])
            box('CHG_' + ch['id'], (p[0], p[1], 0.35), (1.6, 0.8, 0.7), 'line_cyan', c)


def build_vehicles(assets):
    COUNTS['agv_built'] = 0
    amr = assets.get('asset_amr')
    for wh in LAYOUT['warehouses']:
        c = collection('AGV_' + wh['id'])
        for rb in wh['spawn']['robots']:
            p = B3(rb['position'])
            ag = CFG['agv']
            if amr:
                o = bpy.data.objects.new(rb['id'], amr)
            else:
                o = box(rb['id'], (p[0], p[1], ag['height_m'] / 2),
                        (ag['length_m'], ag['width_m'], ag['height_m']),
                        'line_cyan', c)
            if amr:
                c.objects.link(o)
                o.location = (p[0], p[1], 0.08)
            o.rotation_euler[2] = rb['heading'] + math.pi / 2
            # 审计 P0-5：视频里能看到"哪台车执行哪个事件"的角标
            ev = SCHED.get(rb['id'], {}).get('event_id', '')
            if ev:
                cu = bpy.data.curves.new('LABEL_' + rb['id'], type='FONT')
                cu.body, cu.size, cu.align_x = ev, 0.9, 'CENTER'
                lo = bpy.data.objects.new('LABEL_' + rb['id'], cu)
                c.objects.link(lo)
                lo.parent = o
                lo.location = (0, 0, 1.4)
                COUNTS['event_labels'] = COUNTS.get('event_labels', 0) + 1
            COUNTS['agv_built'] += 1
    # 月台设备与干线货车：坐标来自 config.scene
    sc = CFG['scene']
    c = collection('VEHICLES_YARD')
    forklift, truck = assets.get('asset_forklift'), assets.get('asset_truck_box')
    for i, (x, z) in enumerate(sc['forklifts'], 1):
        if forklift:
            o = bpy.data.objects.new('FORKLIFT_%02d' % i, forklift)
            c.objects.link(o)
            o.location = (x, z, 0.05)
            o.rotation_euler[2] = math.pi
    for i, (x, z) in enumerate(sc['trucks'], 1):
        if truck:
            o = bpy.data.objects.new('TRUCK_%02d' % i, truck)
            c.objects.link(o)
            o.location = (x, z, 0.05)
            o.rotation_euler[2] = math.pi / 2


def build_route_lines():
    """把 paths.jsonl 的轨迹画成贴地带状网格——路径信息完全来自数据，不硬编码。"""
    by = {}
    for line in open(os.path.join(DATA, 'paths.jsonl'), encoding='utf-8'):
        if not line.strip():
            continue
        p = json.loads(line)
        by.setdefault(p['agent_id'], []).append((p['x'], p['y']))
    bm = bmesh.new()
    half = 0.18
    segs = 0
    for pts in by.values():
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy)
            if L < 1e-6:
                continue
            nx, ny = -dy / L * half, dx / L * half
            vs = [bm.verts.new(v) for v in (
                (a[0] + nx, a[1] + ny, 0.055), (a[0] - nx, a[1] - ny, 0.055),
                (b[0] - nx, b[1] - ny, 0.055), (b[0] + nx, b[1] + ny, 0.055))]
            bm.faces.new(vs)
            segs += 1
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    m = bpy.data.meshes.new('AGV_ROUTE_LINES')
    bm.to_mesh(m)
    bm.free()
    m.materials.append(material('line_cyan'))
    o = bpy.data.objects.new('AGV_ROUTE_LINES', m)
    collection('ROUTES').objects.link(o)
    COUNTS['route_segments'] = segs


def build_nav_corridors():
    """把 nav_grid.json 的可通行格铺成通道底纹（只画各仓内部），直观回答"通道在哪"。

    障碍格（货架 + AGV 安全膨胀）保持原地面色，因此浅蓝灰连成的就是完整通道网。
    默认 hide_render=True，render.py 只在路径图/通道校验图/动画中打开，不影响总览图。
    """
    p = os.path.join(DATA, 'nav_grid.json')
    if not os.path.exists(p):
        COUNTS['corridor_cells'] = 0
        return
    grid = json.load(open(p, encoding='utf-8'))
    cell = grid['cell_size_m']
    inside = [(w['origin'][0], w['origin'][1],
               w['origin'][0] + w['size']['width'],
               w['origin'][1] + w['size']['depth'])
              for w in LAYOUT['warehouses']]
    rows = grid['walkable_rows']
    bm = bmesh.new()
    n = 0
    # 0.5 m 栅格按 2x2 合并成 1 m 面片，避免生成 3 万个 quad
    for r in range(0, grid['rows_count'] - 1, 2):
        for c in range(0, grid['cols'] - 1, 2):
            if not all(rows[r + dr][c + dc] == '1'
                       for dr in (0, 1) for dc in (0, 1)):
                continue
            x, z = c * cell, r * cell
            cx, cz = x + cell, z + cell
            if not any(x0 <= cx < x1 and z0 <= cz < z1
                       for x0, z0, x1, z1 in inside):
                continue
            e = 2 * cell
            vs = [bm.verts.new(v) for v in (
                (x, z, 0.028), (x + e, z, 0.028),
                (x + e, z + e, 0.028), (x, z + e, 0.028))]
            bm.faces.new(vs)
            n += 1
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    m = bpy.data.meshes.new('NAV_CORRIDORS')
    bm.to_mesh(m)
    bm.free()
    m.materials.append(material('corridor'))
    o = bpy.data.objects.new('NAV_CORRIDORS', m)
    collection('ROUTES').objects.link(o)
    o.hide_render = True
    COUNTS['corridor_cells'] = n


def build_pick_markers():
    """把每个已派任务的取货点(from)/交付点(to)标在地面上，视频里可见。"""
    c = collection('PICK_MARKERS')
    n = 0
    for t in TASKS['tasks']:
        if t['status'] != 'ASSIGNED':
            continue
        for key, mat in (('from', 'line_yellow'), ('to', 'rack_hot')):
            lid = t['legs'][0][key]
            if lid not in LOCMAP:
                continue
            x, z = LOCMAP[lid]['access_point']
            box('PICK_%s_%s' % (t['event_id'], key.upper()),
                (x, z, 0.12), (0.55, 0.55, 0.24), mat, c)
            n += 1
    COUNTS['pick_markers'] = n


def build_dynamic_cargo():
    """订单货物实体：初始摆在交付点（静态 GLB 可见），动画阶段由 animate.py
    写入「取货前隐藏 → 随 AGV 移动 → 交付后落地」关键帧。"""
    c = collection('CARGO_DYNAMIC')
    n = 0
    for cg in CARGO['cargos']:
        x, z = cg['drop_ap']
        box(cg['cargo_id'], (x, z, 0.55), (0.9, 0.7, 0.45), 'crate', c)
        n += 1
    COUNTS['cargo_objects'] = n
    # HOME 待命位标记：让「车从哪来、回哪去」在俯视图里一眼可见
    c2 = collection('HOME_BAYS')
    m = 0
    for w in LAYOUT['warehouses']:
        for l in w['locations']:
            if l['kind'] != 'HOME':
                continue
            x, z = l['access_point']
            box('HOME_BAY_' + l['id'], (x, z, 0.06),
                (1.3, 1.3, 0.12), 'line_yellow', c2)
            m += 1
    COUNTS['home_bays'] = m


# ---------------------------------------------------------------- 功能区（v3.8）
# data/areas.json 是功能区唯一真相（spec_areas.py 展开 + L23–L26 校验）。
# 3D 只做"看得见"的那一层：物理区画地面色块 + 边界 + 名称牌；逻辑区没有独立地面，
# 只在挂靠点放一块小标牌——不画假的矩形，免得评审以为逻辑区也占了地。
AREA_COLORS = {
    'RECEIVING': '#2f80ed', 'QA': '#9b51e0', 'STAGING_IN': '#56ccf2',
    'STORAGE': '#4f9d69', 'PICK_FACE': '#27ae60', 'REPLENISH': '#f2994a',
    'SORTING': '#eb5757', 'PACKING': '#f2c94c', 'VAS': '#bb6bd9',
    'STAGING_OUT': '#6fcf97', 'RETURNS': '#e07a5f', 'EMPTY_CONTAINER': '#828282',
    'BATTERY': '#c0392b', 'MAINTENANCE': '#4f4f4f', 'OFFICE': '#bdbdbd',
}
for _k, _v in AREA_COLORS.items():
    PALETTE.setdefault('area_' + _k.lower(), _v)
PALETTE.setdefault('area_edge', '#1f2937')
PALETTE.setdefault('area_text', '#0b1220')

AREAS_PATH = os.path.join(DATA, 'areas.json')
AREAS = json.load(open(AREAS_PATH, encoding='utf-8')) if os.path.exists(AREAS_PATH) else {'areas': []}

# Blender 自带字体没有中日韩字形，中文标签会变成空框；能加载系统中文字体就用中文。
AREA_FONTS = (
    'C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/msyhl.ttc',
    'C:/Windows/Fonts/simhei.ttf', 'C:/Windows/Fonts/simsun.ttc',
    '/System/Library/Fonts/PingFang.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
)
_FONT = {'tried': False, 'font': None}


def area_font():
    if _FONT['tried']:
        return _FONT['font']
    _FONT['tried'] = True
    for fp in AREA_FONTS:
        if not os.path.exists(fp):
            continue
        try:
            _FONT['font'] = bpy.data.fonts.load(fp, check_existing=True)
            break
        except Exception:
            continue
    return _FONT['font']


def anchor_centers():
    """挂靠点 id -> 平面中心（布局坐标）。逻辑区靠它定位标牌。"""
    out = {}
    for wh in LAYOUT['warehouses']:
        for z in wh.get('zones', []):
            pts = z.get('polygon') or []
            if len(pts) >= 4:
                out[z['id']] = (sum(p[0] for p in pts) / len(pts),
                                sum(p[1] for p in pts) / len(pts))
        for r in wh.get('racks', []):
            x, _y, zz = r['position']
            out[r['id']] = (x + r['size'][0] / 2, zz + r['size'][2] / 2)
        for d in wh.get('docks', []):
            x0, z0, x1, z1 = d['rect']
            out[d['id']] = ((x0 + x1) / 2, (z0 + z1) / 2)
        for st in wh.get('stations', []):
            x0, z0, x1, z1 = st['rect']
            out[st['id']] = ((x0 + x1) / 2, (z0 + z1) / 2)
        for ch in wh.get('charging_stations', []) or []:
            ap = ch.get('access_point') or ch.get('position')
            if ap:
                out[ch['id']] = (ap[0], ap[1])
    return out


def text_label(name, body, cx, cz, size, coll, z=0.06):
    cu = bpy.data.curves.new(name, type='FONT')
    cu.body = body
    cu.size = size
    cu.align_x = 'CENTER'
    cu.align_y = 'CENTER'
    cu.extrude = 0.0
    f = area_font()
    if f:
        cu.font = f
    cu.materials.append(material('area_text'))
    o = bpy.data.objects.new(name, cu)
    coll.objects.link(o)
    o.location = (cx, cz, z)
    return o


def build_functional_areas():
    """把 21 个功能区画进 3D：物理区真实占位，逻辑区只放挂靠标牌。"""
    centers = anchor_centers()
    c = collection('AREAS')
    n_phys = n_logi = n_label = 0
    has_cjk = area_font() is not None
    for a in AREAS.get('areas', []):
        kind = a.get('kind', 'STORAGE')
        key = 'area_' + kind.lower()
        if key not in PALETTE:
            key = 'area_storage'
        aid = a['id']
        if a.get('rect'):
            x0, z0, x1, z1 = a['rect']
            # 边界（深色底 + 内缩 0.12 m 的色块），避免和地面 5S 黄线糊在一起
            plane('AREA_EDGE_' + aid, x0, z0, x1, z1, 0.034, 'area_edge', c)
            plane('AREA_' + aid, x0 + 0.12, z0 + 0.12, x1 - 0.12, z1 - 0.12,
                  0.038, key, c)
            w, d = x1 - x0, z1 - z0
            size = max(0.7, min(1.8, min(w, d) * 0.16))
            body = '%s\n%s' % (aid, a.get('name', kind)) if has_cjk else aid
            text_label('AREA_TXT_' + aid, body, (x0 + x1) / 2, (z0 + z1) / 2,
                       size, c, z=0.05)
            n_phys += 1
            n_label += 1
            continue

        # 逻辑区：只放一块 1.4×0.8 m 的标牌，位置取挂靠点中心
        ctr = centers.get(a.get('anchor'))
        if not ctr:
            continue
        cx, cz = ctr
        plane('AREA_TAG_' + aid, cx - 0.7, cz - 0.4, cx + 0.7, cz + 0.4,
              0.041, key, c)
        plane('AREA_TAGEDGE_' + aid, cx - 0.76, cz - 0.46, cx + 0.76, cz + 0.46,
              0.039, 'area_edge', c)
        text_label('AREA_TAG_TXT_' + aid, aid, cx, cz, 0.30, c, z=0.055)
        n_logi += 1
        n_label += 1

    COUNTS['areas_total'] = len(AREAS.get('areas', []))
    COUNTS['areas_physical_drawn'] = n_phys
    COUNTS['areas_logical_tags'] = n_logi
    COUNTS['area_labels'] = n_label
    COUNTS['area_cjk_font'] = bool(has_cjk)
    return n_phys, n_logi


def build_lights_cameras():
    c = collection('LIGHTS_CAMERAS')
    world = bpy.context.scene.world or bpy.data.worlds.new('World')
    bpy.context.scene.world = world
    world.use_nodes = True
    world.node_tree.nodes.get('Background').inputs['Color'].default_value = (0.62, 0.66, 0.70, 1)
    world.node_tree.nodes.get('Background').inputs['Strength'].default_value = 0.8

    # 审计 P1-6：相机/灯/车辆/道路参数全部来自 config.scene，不在源码写死
    sc = CFG['scene']
    sun_data = bpy.data.lights.new('SUN', type='SUN')
    sun_data.energy = sc['sun']['energy']
    sun = bpy.data.objects.new('SUN', sun_data)
    c.objects.link(sun)
    sun.rotation_euler = tuple(math.radians(v) for v in sc['sun']['rotation_deg'])
    for i, al in enumerate(sc['area_lights']):
        ld = bpy.data.lights.new('AREA_%d' % i, type='AREA')
        ld.energy, ld.shape, ld.size = al['energy'], 'DISK', al['size']
        lo = bpy.data.objects.new('AREA_%d' % i, ld)
        c.objects.link(lo)
        lo.location = tuple(al['loc'])

    specs = [(cam['name'], tuple(cam['loc']), tuple(cam['target']),
              cam['scale'], cam['type']) for cam in sc['cameras']]
    for name, loc, target, scale, kind in specs:
        cd = bpy.data.cameras.new(name)
        cd.type = kind
        if kind == 'ORTHO':
            cd.ortho_scale = scale
        else:
            cd.lens = scale
        o = bpy.data.objects.new(name, cd)
        c.objects.link(o)
        o.location = loc
        direction = Vector(target) - o.location
        o.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.camera = bpy.data.objects['CAM_overview']


def world_bbox():
    # 纯脚本改 location/scale 后必须刷新依赖图，否则 matrix_world 仍是旧值
    bpy.context.view_layer.update()
    mn = [1e9] * 3
    mx = [-1e9] * 3
    for o in bpy.data.objects:
        if o.type != 'MESH':
            continue
        for corner in o.bound_box:
            v = o.matrix_world @ Vector(corner)
            for i in range(3):
                mn[i] = min(mn[i], v[i])
                mx[i] = max(mx[i], v[i])
    return mn, mx


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    assets = import_assets()
    coll_ground = collection('GROUND')
    build_ground_and_buildings(assets, coll_ground, None)
    build_racks_and_cargo(assets)
    build_docks_conveyors(assets)
    build_functional_areas()
    build_vehicles(assets)
    build_route_lines()
    build_nav_corridors()
    build_pick_markers()
    build_dynamic_cargo()
    build_lights_cameras()

    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1.0
    for eng in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'CYCLES'):
        try:
            scene.render.engine = eng
            break
        except TypeError:
            continue
    scene.render.resolution_x = CFG['render']['width']
    scene.render.resolution_y = CFG['render']['height']
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.fps = CFG['render']['fps']
    scene.frame_start = 1
    PM = json.load(open(os.path.join(DATA, 'path_metrics.json'), encoding='utf-8'))
    # 审计 P0-3：帧范围由真实时间线决定，不再用固定 18 s
    scene.frame_end = int(round(PM['duration_s'] * scene.render.fps)) + 1
    scene.view_settings.view_transform = 'AgX'

    mn, mx = world_bbox()
    # 审计 P2-1：只统计场景实际使用的数据块，避免导入 GLB 留下的 0 用户残留
    used_meshes = {o.data for o in bpy.data.objects if o.type == 'MESH'}
    used_mats = {m for mesh in used_meshes for m in mesh.materials if m}
    report = {
        'blender': bpy.app.version_string,
        'engine': scene.render.engine,
        'asset_source': 'cargotwin_assets.glb (cargoTwin, MIT)',
        'asset_meshes': sorted(k for k in assets),
        'slotting': SLOTTING['summary'],
        'path_planning': json.load(open(os.path.join(DATA, 'path_metrics.json'), encoding='utf-8')),
        'layout_summary': LAYOUT['summary'],
        'layout_size': LAYOUT['size'],
        'counts': COUNTS,
        'objects': len(bpy.data.objects),
        'meshes': len(used_meshes),
        'materials': len(used_mats),
        'cameras': [o.name for o in bpy.data.objects if o.type == 'CAMERA'],
        'bbox_min': [round(v, 4) for v in mn],
        'bbox_max': [round(v, 4) for v in mx],
        'bbox_size': [round(mx[i] - mn[i], 4) for i in range(3)],
    }
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    with open(os.path.join(OUT, 'build_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('BUILD_OK', json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
