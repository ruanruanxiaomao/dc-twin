"""site_detail.py — 园区与建筑外壳（v4.9）。

为什么单独一个文件：build_dc.py 已经 1000 行，而"场地/围墙/雨棚/招牌"属于
**表现层**，改它不该碰到货架、路径、功能区那些跟数据较真的部分。

这里只加"看得出来是什么"的东西，不动任何布局坐标：
  · 场地：整体沥青 + 四周绿化带 + 路缘石 + 车道边线/中线 + 停车位
  · 围墙：立柱 + 三道横杆，东侧开口做大门（门卫房 + 道闸）
  · 建筑：三面 2.2 m 女儿墙（俯视仍能看进仓内）、门洞封边、月台雨棚、
          檐口挑檐、外立面招牌
  · 景观：行道树、园区灯杆

坐标约定与 build_dc.py 完全一致：layout (x, z) -> Blender (x, z)，高度是 Z。
"""

import math

import bpy

#: build_dc 模块（由 build() 注入）。不能在这里 `import build_dc`：
#: Blender 用 `-P scripts/build_dc.py` 跑时，模块名是 __main__，
#: 再 import 一次会把整个脚本重新执行一遍，PALETTE/COUNTS 全变成另一份。
B = None


# ---------------------------------------------------------------- 配色

def _palette():
    B.PALETTE.setdefault('asphalt', '#4f5560')
    B.PALETTE.setdefault('asphalt_light', '#5c626d')
    B.PALETTE.setdefault('grass', '#7f9c6b')
    B.PALETTE.setdefault('kerb', '#e6e4dc')
    B.PALETTE.setdefault('fence', '#9aa3ad')
    B.PALETTE.setdefault('canopy', '#c9ced6')
    B.PALETTE.setdefault('wall_cap', '#e9e7df')
    B.PALETTE.setdefault('wall_low', '#eceae2')
    B.PALETTE.setdefault('sign_text', '#0f1723')
    B.PALETTE.setdefault('trunk', '#6b5136')
    B.PALETTE.setdefault('leaf', '#4f7d4a')
    B.PALETTE.setdefault('guard', '#f0eee6')
    B.PALETTE.setdefault('line_white', '#f4f6f8')
    B.PALETTE.setdefault('eave_w', '#2f6fd0')
    B.PALETTE.setdefault('eave_f', '#0f9c8a')


# 南侧要多留 22 m：10 m 的挂车倒进月台，车尾还得留出通行宽度。
SITE = (-12.0, -22.0)                      # 园区左下角
FENCE_H = 1.9
WALL_H = 2.2                               # 女儿墙：够看出是"房子"，又不挡俯视


def _site_extent():
    s = B.LAYOUT['size']
    return SITE[0], SITE[1], s['width'] + 12.0, s['depth'] + 12.0


def ground(coll):
    x0, z0, x1, z1 = _site_extent()
    # 先铺一块草，再把沥青盖上去：边缘留绿化带，比整片灰地好看得多
    B.plane('SITE_GRASS', x0, z0, x1, z1, -0.03, 'grass', coll)
    gx0, gz0, gx1, gz1 = x0 + 3.5, z0 + 3.5, x1 - 3.5, z1 - 3.5
    B.plane('SITE_ASPHALT', gx0, gz0, gx1, gz1, -0.012, 'asphalt', coll)

    # 建筑之间的作业带（W 与前置仓之间那条 20 m 宽的主通道）
    B.plane('YARD_BAND', 2, 68, 112, 92, -0.008, 'asphalt_light', coll)
    # 东侧进出场道路，接到大门
    B.plane('YARD_EAST', 104, 90, 112, 116, -0.008, 'asphalt_light', coll)

    # 每栋楼四周的混凝土散水/装卸场
    for wh in B.LAYOUT['warehouses']:
        ox, oz = wh['origin']
        w, d = wh['size']['width'], wh['size']['depth']
        B.plane('APRON_' + wh['id'], ox - 3, oz - 3, ox + w + 3, oz + d + 3,
                0.004, 'concrete', coll)

    # 月台装卸场：横向主车道 + 每扇门一个倒车位（挂车倒进来要看得见边界）
    B.plane('TRUCK_LANE', 2, -15.4, 106, -13.0, 0.012, 'asphalt_light', coll)
    for i in range(24):
        x = 4 + i * 4.5
        B.plane('TL_DASH_%d' % i, x, -14.25, x + 2.2, -14.15, 0.014, 'line_white', coll)
    for wh in B.LAYOUT['warehouses']:
        if wh['kind'] != 'REGIONAL':
            continue
        for dk in wh.get('docks', []):
            dx0, dz0, dx1, dz1 = dk['rect']
            if min(dz0, dz1) > 8:
                continue
            cx = (dx0 + dx1) / 2
            for s2 in (-1, 1):
                bx = cx + s2 * 1.85
                B.plane('BAYL_%s_%d' % (dk['id'], s2), bx - 0.06, -10.0, bx + 0.06, -1.2,
                        0.015, 'line_yellow', coll)
            B.plane('BAYB_%s' % dk['id'], cx - 1.85, -10.0, cx + 1.85, -9.85,
                    0.015, 'line_yellow', coll)

    # 车道：边线 + 中线虚线（道路坐标来自 config.scene.roads，仍是数据驱动）
    for i, (rx0, rz0, rx1, rz1) in enumerate(B.CFG['scene']['roads'], 1):
        cx = (rx0 + rx1) / 2
        B.plane('RLINE_L_%d' % i, rx0 + 0.4, rz0, rx0 + 0.55, rz1, 0.012, 'line_white', coll)
        B.plane('RLINE_R_%d' % i, rx1 - 0.55, rz0, rx1 - 0.4, rz1, 0.012, 'line_white', coll)
        z = rz0 + 1.5
        while z < rz1 - 1.5:
            B.plane('RDASH_%d_%.0f' % (i, z), cx - 0.09, z, cx + 0.09, z + 2.2,
                    0.013, 'line_yellow', coll)
            z += 4.5


def fence_and_gate(coll):
    x0, z0, x1, z1 = _site_extent()
    x0, z0, x1, z1 = x0 + 1.6, z0 + 1.6, x1 - 1.6, z1 - 1.6
    gate0, gate1 = 96.0, 108.0                      # 东侧大门（人/小车）
    sgate0, sgate1 = 46.0, 58.0                     # 南侧货车专用门

    # 横杆：南/北墙沿 X（南墙留货车门），西/东墙沿 Y（东墙留人车门）
    for h in (0.55, 1.15, 1.75):
        B.box('FENCE_N_%.2f' % h, ((x0 + x1) / 2, z1, h), (x1 - x0, 0.06, 0.06), 'fence', coll)
        for seg, (a, b) in enumerate(((x0, sgate0), (sgate1, x1))):
            B.box('FENCE_S%d_%.2f' % (seg, h), ((a + b) / 2, z0, h),
                  (b - a, 0.06, 0.06), 'fence', coll)
    for h in (0.55, 1.15, 1.75):
        B.box('FENCE_W_%.2f' % h, (x0, (z0 + z1) / 2, h), (0.06, z1 - z0, 0.06), 'fence', coll)
        for seg, (a, b) in enumerate(((z0, gate0), (gate1, z1))):
            B.box('FENCE_E%d_%.2f' % (seg, h), (x1, (a + b) / 2, h),
                  (0.06, b - a, 0.06), 'fence', coll)

    # 立柱：每 8 m 一根
    n = 0

    def post(x, z):
        nonlocal n
        n += 1
        B.box('FPOST_%03d' % n, (x, z, FENCE_H / 2 + 0.1),
              (0.14, 0.14, FENCE_H + 0.2), 'fence', coll)

    v = z0
    while v <= z1 + 0.01:
        post(x0, v)
        if not (gate0 - 1 < v < gate1 + 1):
            post(x1, v)
        v += 8.0
    u = x0 + 8
    while u < x1 - 1:
        if not (sgate0 - 1 < u < sgate1 + 1):
            post(u, z0)
        post(u, z1)
        u += 8.0

    # 门卫房 + 道闸
    B.box('GUARD_HOUSE', (x1 - 3.2, (gate0 + gate1) / 2, 1.5), (3.4, 5.0, 3.0), 'guard', coll)
    B.box('GUARD_ROOF', (x1 - 3.2, (gate0 + gate1) / 2, 3.12), (3.9, 5.5, 0.24), 'eave_w', coll)
    B.box('GUARD_WIN', (x1 - 1.55, (gate0 + gate1) / 2, 1.75), (0.08, 3.0, 1.1), 'glass', coll)
    B.box('BARRIER_POST', (x1 - 0.4, gate0 + 1.0, 0.6), (0.3, 0.3, 1.2), 'steel', coll)
    B.box('BARRIER_ARM', (x1 - 0.4, gate0 + 4.0, 1.12), (0.14, 6.0, 0.14), 'line_yellow', coll)

    # 南门：道闸 + 货车待检线
    B.box('SGUARD_POST', (sgate0 + 0.8, z0 - 0.4, 0.7), (0.3, 0.3, 1.4), 'steel', coll)
    B.box('SGUARD_ARM', (sgate0 + 4.0, z0 - 0.4, 1.3), (6.0, 0.14, 0.14), 'line_yellow', coll)
    for i in range(2):
        B.plane('SGATE_LINE_%d' % i, sgate0 + 0.6, z0 + 1.5 + i * 6.0,
                sgate1 - 0.6, z0 + 1.65 + i * 6.0, 0.016, 'line_white', coll)

    # 标线：门口斑马线 + 东侧停车位
    for i in range(6):
        B.plane('ZEBRA_%d' % i, 98 + i * 1.7, 92.0, 99.1 + i * 1.7, 115.0, 0.016, 'line_white', coll)
    for i in range(4):
        zz = 96 + i * 3.2
        B.plane('BAY_%d' % i, 90, zz, 104, zz + 0.14, 0.016, 'line_white', coll)


def _wall_run(prefix, coll, axis, fixed, a0, a1, h, thick, gaps):
    """沿一条边画墙，遇到门洞就断开。axis='x' 表示墙沿 X 走。"""
    cuts = sorted(gaps)
    segs, cur = [], a0
    for g0, g1 in cuts:
        if g0 > cur + 0.05:
            segs.append((cur, max(cur, min(g0, a1))))
        cur = max(cur, g1)
    if cur < a1 - 0.05:
        segs.append((cur, a1))
    for i, (s, e) in enumerate(segs):
        if e - s < 0.3:
            continue
        mid = (s + e) / 2
        if axis == 'x':
            B.box('%s_%d' % (prefix, i), (mid, fixed, h / 2), (e - s, thick, h), 'wall_low', coll)
            B.box('%s_CAP_%d' % (prefix, i), (mid, fixed, h + 0.07),
                  (e - s + 0.1, thick + 0.16, 0.14), 'wall_cap', coll)
        else:
            B.box('%s_%d' % (prefix, i), (fixed, mid, h / 2), (thick, e - s, h), 'wall_low', coll)
            B.box('%s_CAP_%d' % (prefix, i), (fixed, mid, h + 0.07),
                  (thick + 0.16, e - s + 0.1, 0.14), 'wall_cap', coll)


def _sign(name, body, x, y, z, size, coll, rot_z=0.0):
    """立面招牌：文字立起来贴在墙上（默认平放是给俯视看的）。"""
    cu = bpy.data.curves.new(name, type='FONT')
    cu.body = body
    cu.size = size
    cu.align_x = 'CENTER'
    cu.align_y = 'CENTER'
    cu.extrude = 0.03
    f = B.area_font()
    if f:
        cu.font = f
    cu.materials.append(B.material('sign_text'))
    o = bpy.data.objects.new(name, cu)
    coll.objects.link(o)
    o.location = (x, y, z)
    o.rotation_euler = (math.radians(90), 0, rot_z)
    return o


def building_shells(coll):
    for wh in B.LAYOUT['warehouses']:
        ox, oz = wh['origin']
        w, d, h = wh['size']['width'], wh['size']['depth'], wh['size']['height']
        is_regional = wh['kind'] == 'REGIONAL'
        eave = 'eave_w' if is_regional else 'eave_f'

        # dock rect 是绝对坐标，origin 只用来判断"靠哪面墙"
        south_gaps, north_gaps = [], []
        for dk in wh.get('docks', []):
            dx0, dz0, dx1, dz1 = dk['rect']
            (south_gaps if dz1 <= oz + d / 2 else north_gaps).append((dx0, dx1))

        # 三面女儿墙（北面已有整面背墙，不重复）
        _wall_run('LWALL_%s_S' % wh['id'], coll, 'x', oz + 0.2, ox, ox + w, WALL_H, 0.3, south_gaps)
        _wall_run('LWALL_%s_E' % wh['id'], coll, 'y', ox + w - 0.2, oz, oz + d, WALL_H, 0.3, [])
        _wall_run('LWALL_%s_W' % wh['id'], coll, 'y', ox + 0.2, oz, oz + d, WALL_H, 0.3, [])

        # 檐口挑檐：只在墙外挑 2.5 m，不遮内部（遮了热力图就废了）。
        # 主体用浅灰，只在最外沿压一条窄色带——整圈刷成饱和蓝的话，
        # 远看像一圈围栏，反而看不出是屋檐。
        oh = 2.5
        B.box('EAVE_%s_S' % wh['id'], (ox + w / 2, oz - oh / 2, h - 0.32),
              (w + 2 * oh, oh, 0.34), 'wall_cap', coll)
        B.box('EAVE_%s_N' % wh['id'], (ox + w / 2, oz + d + oh / 2, h - 0.32),
              (w + 2 * oh, oh, 0.34), 'wall_cap', coll)
        B.box('EAVE_%s_W' % wh['id'], (ox - oh / 2, oz + d / 2, h - 0.32),
              (oh, d, 0.34), 'wall_cap', coll)
        B.box('EAVE_%s_E' % wh['id'], (ox + w + oh / 2, oz + d / 2, h - 0.32),
              (oh, d, 0.34), 'wall_cap', coll)
        B.box('EAVE_EDGE_%s' % wh['id'], (ox + w / 2, oz - oh + 0.12, h - 0.36),
              (w + 2 * oh, 0.24, 0.42), eave, coll)

        # 月台：门洞封边 + 雨棚 + 门封
        for dk in wh.get('docks', []):
            dx0, dz0, dx1, dz1 = dk['rect']
            south = dz1 <= oz + d / 2
            zw = dz0 if south else dz1
            outward = -1 if south else 1
            dw = dx1 - dx0
            B.box('POR_FRAME_L_' + dk['id'], (dx0 + 0.18, zw, 2.6), (0.36, 0.4, 5.2), 'steel', coll)
            B.box('POR_FRAME_R_' + dk['id'], (dx1 - 0.18, zw, 2.6), (0.36, 0.4, 5.2), 'steel', coll)
            B.box('POR_FRAME_T_' + dk['id'], ((dx0 + dx1) / 2, zw, 5.1),
                  (dw, 0.4, 0.45), 'steel', coll)
            # 雨棚：向外伸出 4 m，两根柱子落地
            cy = zw + outward * 2.2
            B.box('CANOPY_' + dk['id'], ((dx0 + dx1) / 2, cy, 5.6),
                  (dw + 1.6, 4.4, 0.22), 'canopy', coll)
            for px in (dx0 + 0.5, dx1 - 0.5):
                B.box('CANPOST_%s_%.1f' % (dk['id'], px), (px, zw + outward * 4.0, 2.75),
                      (0.18, 0.18, 5.5), 'steel', coll)

        # 招牌：南立面一块，写清楚这是哪栋
        label = '区域仓 W' if is_regional else ('前置仓 ' + wh['id'])
        sub = '%d × %d m · 檐高 %d m' % (w, d, h)
        _sign('SIGN_%s' % wh['id'], label, ox + w / 2, oz - 0.24, h - 1.7,
              2.4 if is_regional else 1.5, coll)
        _sign('SIGN_SUB_%s' % wh['id'], sub, ox + w / 2, oz - 0.24, h - 3.3,
              1.0 if is_regional else 0.7, coll)


def _cone(name, x, z, r, h, base_z, key, coll, seg=8):
    """低模锥体树冠。用立方体会像一块绿色纸板，锥体才像树。"""
    verts = [(x + r * math.cos(2 * math.pi * i / seg),
              z + r * math.sin(2 * math.pi * i / seg), base_z) for i in range(seg)]
    verts.append((x, z, base_z + h))
    faces = [(i, (i + 1) % seg, seg) for i in range(seg)]
    faces.append(tuple(range(seg - 1, -1, -1)))
    me = bpy.data.meshes.new(name + '_mesh')
    me.from_pydata(verts, [], faces)
    me.update()
    me.materials.append(B.material(key))
    o = bpy.data.objects.new(name, me)
    coll.objects.link(o)
    return o


def greenery(coll):
    """行道树 + 园区灯杆：把"灰色方块"变成"有人管的园区"。"""
    x0, z0, x1, z1 = _site_extent()
    spots = []
    x = x0 + 8
    while x < x1 - 6:
        spots += [(x, z0 + 1.6), (x, z1 - 1.6)]
        x += 14
    z = z0 + 12
    while z < z1 - 10:
        spots.append((x0 + 1.6, z))
        z += 16
    for i, (tx, tz) in enumerate(spots):
        if 94 < tz < 110 and tx > 100:
            continue
        B.box('TREE_T%d' % i, (tx, tz, 1.5), (0.26, 0.26, 3.0), 'trunk', coll)
        _cone('TREE_C%d' % i, tx, tz, 1.7, 3.6, 2.6, 'leaf', coll)
        _cone('TREE_C2%d' % i, tx, tz, 1.15, 2.6, 4.6, 'leaf', coll)
    for i, (lx, lz) in enumerate(((6, 66), (52, 66), (98, 66), (6, 94), (52, 94), (98, 94))):
        B.box('MAST_%d' % i, (lx, lz, 5.0), (0.22, 0.22, 10.0), 'steel', coll)
        B.box('MAST_HEAD_%d' % i, (lx, lz, 10.05), (1.1, 0.4, 0.18), 'light', coll)
        B.box('MAST_BASE_%d' % i, (lx, lz, 0.25), (0.7, 0.7, 0.5), 'concrete', coll)


def build(dc_module, assets=None):
    global B
    B = dc_module
    _palette()
    coll = B.collection('SITE')
    ground(coll)
    fence_and_gate(coll)
    building_shells(coll)
    greenery(coll)
    B.COUNTS['site_objects'] = len(coll.objects)
    return coll
