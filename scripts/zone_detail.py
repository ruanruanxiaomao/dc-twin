"""zone_detail.py — 作业区里的"真设备"（v4.9）。

build_dc.py 里原来的功能家具只有托盘堆、工作台、滑道三件套，尺寸都是 2 m 级别，
在 112 m 的园区里看着像撒了一把色块。这个文件把它们换成能认出来的东西：

  · 输送线：皮带 + 侧护栏 + 支腿 + 线上一箱一箱的包裹
  · 分拣线：斜滑道（带侧板与挡头）+ 分拣台 + 显示器 + 周转箱
  · 包装线：打包台 + 显示器 + 封箱器 + 纸箱 + 作业员
  · 拣选位：料架 + 蓝色笼车
  · 作业员：低模人形（腿 / 反光背心 / 头），只为给场景一个尺度参照

坐标约定同 build_dc.py：layout (x, z) -> Blender (x, z)，高度是 Z。
"""

import math

import bmesh
import bpy

D = None          # build_dc 模块，由 build() 注入（不能直接 import，见 site_detail）

C = {
    'belt': '#3a3f47', 'belt_top': '#22262c', 'rail': '#9aa3ad',
    'chute': '#2e9be6', 'chute_dark': '#1a6fae',
    'tote': '#2f6fb5', 'carton': '#c99b62', 'hi_vis': '#f2c230',
    'leg': '#2b3038', 'skin': '#d9b48f', 'screen': '#5b6472',
    'cage': '#2f6fb5', 'steel': '#8f98a3', 'rubber': '#1b1e23',
    'belt_dark': '#2b3038', 'tray': '#8b96a3',
}


def palette():
    for k, v in C.items():
        D.PALETTE.setdefault('zd_' + k, v)


def mat(key):
    return D.material('zd_' + key)


# ---------------------------------------------------------------- 基础件

def _box(name, x, z, h, sx, sz, sy, key, coll, rot=0.0):
    o = D.box(name, (x, z, h), (sx, sz, sy), 'zd_' + key, coll, rot)
    return o


def _sphere(name, x, z, h, r, key, coll, seg=12):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=seg // 2, radius=r)
    me = bpy.data.meshes.new(name + '_mesh')
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat(key))
    o = bpy.data.objects.new(name, me)
    coll.objects.link(o)
    o.location = (x, z, h)
    return o


def worker(name, x, z, coll, rot=0.0, key='hi_vis'):
    """低模作业员：两条腿 + 反光背心 + 头。给场景一个 1.7 m 的尺度参照。"""
    _box(name + '_LEG', x, z, 0.42, 0.26, 0.26, 0.84, 'leg', coll, rot)
    _box(name + '_BODY', x, z, 1.14, 0.44, 0.3, 0.62, key, coll, rot)
    _box(name + '_ARM', x, z, 1.1, 0.56, 0.2, 0.16, key, coll, rot)
    _sphere(name + '_HEAD', x, z, 1.58, 0.13, 'skin', coll)
    return 4


def cage_trolley(name, x, z, coll, rot=0.0):
    """拣选笼车：蓝色框架 + 万向轮。"""
    x0, x1 = x - 0.6, x + 0.6
    z0, z1 = z - 0.42, z + 0.42
    for sx in (x0, x1):
        for sz in (z0, z1):
            _box('%s_POST%.1f%.1f' % (name, sx, sz), sx, sz, 0.55, 0.06, 0.06, 1.1, 'cage', coll, rot)
    for hz in (0.24, 0.62, 1.04):
        _box('%s_RAIL%.2f' % (name, hz), x, z0, hz, 1.2, 0.05, 0.05, 'cage', coll, rot)
        _box('%s_RAILB%.2f' % (name, hz), x, z1, hz, 1.2, 0.05, 0.05, 'cage', coll, rot)
    _box(name + '_DECK', x, z, 0.26, 1.2, 0.84, 0.04, 'steel', coll, rot)
    _box(name + '_LOAD', x, z, 0.5, 0.9, 0.6, 0.42, 'carton', coll, rot)
    for sx in (-0.5, 0.5):
        for sz in (-0.34, 0.34):
            _box('%s_W%.1f%.1f' % (name, sx, sz), x + sx, z + sz, 0.06, 0.14, 0.14, 0.12,
                 'rubber', coll, rot)
    return 12


# ---------------------------------------------------------------- 输送线

#: 输送带台面分型（§16.7）。默认仍只有带式，其余为程序化变体。
CONVEYOR_STYLES = ('belt', 'roller', 'gravity', 'accumulation')


def _roller_deck(name, i, cx, cz, rot, seg, w, belt_h, style, coll):
    """辊道台面：密排辊子；重力式沿段给坡度（入口高、出口低）；积放式加挡停块。"""
    pitch = 0.12
    n = max(2, int(seg / pitch))
    slope = 0.045 if style == 'gravity' else 0.0          # ≈2.6°
    for k in range(n):
        t = (k + 0.5) / n
        px = cx + math.cos(rot) * (t - 0.5) * seg
        pz = cz + math.sin(rot) * (t - 0.5) * seg
        h = belt_h + slope * seg * (0.5 - t)
        _box('%s_ROLL%d_%d' % (name, i, k), px, pz, h, 0.09, w * 0.94, 0.05,
             'steel', coll, rot)
    # 辊道两侧的边梁（辊子不能悬空）
    for s_ in (-1, 1):
        ox = -math.sin(rot) * s_ * (w / 2)
        oz = math.cos(rot) * s_ * (w / 2)
        _box('%s_CARRIER%d_%d' % (name, i, s_), cx + ox, cz + oz, belt_h - 0.05,
             seg, 0.05, 0.12, 'belt_dark', coll, rot)
    if style == 'accumulation':
        for k in range(1, max(1, int(seg / 1.2))):
            t = k / max(1, int(seg / 1.2))
            px = cx + math.cos(rot) * (t - 0.5) * seg
            pz = cz + math.sin(rot) * (t - 0.5) * seg
            for s_ in (-1, 1):
                ox = -math.sin(rot) * s_ * (w * 0.42)
                oz = math.cos(rot) * s_ * (w * 0.42)
                _box('%s_STOP%d_%d_%d' % (name, i, k, s_), px + ox, pz + oz,
                     belt_h + 0.09, 0.06, 0.06, 0.12, 'rail', coll, rot)


def conveyor(name, path, width, coll, belt_h=0.78, style='belt'):
    """沿折线画输送线：台面（带式/辊道/重力/积放）+ 护栏 + 支腿 + 线上包裹。

    `style` 缺省 `belt`，与 R8.5 之前**逐字节一致**；其余分型只在数据显式声明时启用。
    包裹位置仍按里程定，不引入随机数（否则金标准立刻失效）。
    """
    if style not in CONVEYOR_STYLES:
        raise ValueError('未知输送带分型 %r（可选 %s）' % (style, CONVEYOR_STYLES))
    w = max(0.6, width)
    total = 0.0
    parcels = 0
    for i in range(len(path) - 1):
        (x0, z0), (x1, z1) = path[i], path[i + 1]
        dx, dz = x1 - x0, z1 - z0
        seg = math.hypot(dx, dz)
        if seg < 0.05:
            continue
        rot = math.atan2(dz, dx)
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        if style == 'belt':
            # 皮带本体 + 深色带面：两条叠在一起才有"带子在转"的层次
            _box('%s_BELT%d' % (name, i), cx, cz, belt_h, seg, w, 0.16, 'belt', coll, rot)
            _box('%s_TOP%d' % (name, i), cx, cz, belt_h + 0.09, seg, w * 0.86, 0.03,
                 'belt_top', coll, rot)
        else:
            _roller_deck(name, i, cx, cz, rot, seg, w, belt_h, style, coll)
        # 侧护栏
        for s in (-1, 1):
            ox = -math.sin(rot) * s * (w / 2 + 0.04)
            oz = math.cos(rot) * s * (w / 2 + 0.04)
            _box('%s_RAIL%d_%d' % (name, i, s), cx + ox, cz + oz, belt_h + 0.2,
                 seg, 0.06, 0.14, 'rail', coll, rot)
        # 支腿每 2.5 m 一对
        n = max(1, int(seg / 2.5))
        for k in range(1, n + 1):
            t = k / (n + 1)
            px, pz = x0 + dx * t, z0 + dz * t
            for s in (-1, 1):
                ox = -math.sin(rot) * s * (w / 2 - 0.08)
                oz = math.cos(rot) * s * (w / 2 - 0.08)
                _box('%s_LEG%d_%d_%d' % (name, i, k, s), px + ox, pz + oz,
                     belt_h / 2, 0.08, 0.08, belt_h, 'steel', coll, rot)
            _box('%s_BRACE%d_%d' % (name, i, k), px, pz, 0.22, w - 0.1, 0.05, 0.05,
                 'steel', coll, rot)
        # 线上包裹：每 3.2 m 一箱，交替大小
        m = int(seg / 3.2)
        for k in range(m):
            t = (k + 0.5) / m
            px, pz = x0 + dx * t, z0 + dz * t
            pw, pd, ph = (0.52, 0.42, 0.34) if k % 2 == 0 else (0.42, 0.36, 0.28)
            _box('%s_PARCEL%d_%d' % (name, i, k), px, pz, belt_h + 0.1 + ph / 2,
                 pw, pd, ph, 'carton', coll, rot + (0.06 if k % 3 else -0.05))
            parcels += 1
        total += seg
    return parcels


# ---------------------------------------------------------------- 分拣线

def _plate(name, cx, cz, rot, length, width, h_near, h_far, thick, key, coll,
           off=0.0, lift=0.0):
    """斜板：显式建网格（顶面两端不同高 + 底面 + 四周），不用欧拉角硬凑。

    之前用 `rotation_euler[1] = -ang` 去歪一个立方体，遇到 rot=90° 时
    旋转次序不对，滑道会横躺在地上——渲染出来只是一条蓝线。
    """
    dx, dz = math.cos(rot), math.sin(rot)
    px, pz = -math.sin(rot), math.cos(rot)
    L2, W2 = length / 2, width / 2

    def P(u, v, h):
        return (cx + dx * u + px * (v + off), cz + dz * u + pz * (v + off), h + lift)

    top = [P(-L2, -W2, h_near), P(L2, -W2, h_far), P(L2, W2, h_far), P(-L2, W2, h_near)]
    bot = [(v[0], v[1], v[2] - thick) for v in top]
    verts = top + bot
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    me = bpy.data.meshes.new(name + '_mesh')
    me.from_pydata(verts, [], faces)
    me.update()
    me.materials.append(mat(key))
    o = bpy.data.objects.new(name, me)
    coll.objects.link(o)
    return o


def chute(name, x, z, coll, rot=0.0, length=3.0, width=0.9, drop=0.55):
    """斜滑道：sloped 台面 + 两侧挡板 + 末端挡头 + 末端周转箱。"""
    h_near, h_far = 0.95, 0.95 - drop
    _plate(name + '_BED', x, z, rot, length, width, h_near, h_far, 0.07, 'chute', coll)
    for s in (-1, 1):
        _plate('%s_SIDE%d' % (name, s), x, z, rot, length, 0.045,
               h_near + 0.28, h_far + 0.28, 0.30, 'chute_dark', coll,
               off=s * (width / 2 + 0.02))
    ex, ez = math.cos(rot) * (length / 2), math.sin(rot) * (length / 2)
    _plate(name + '_STOP', x + ex, z + ez, rot, 0.12, width + 0.1,
           h_far + 0.34, h_far, 0.34, 'chute_dark', coll)
    # 滑到底部的周转箱
    _box(name + '_TOTE', x + ex * 0.86, z + ez * 0.86, h_far + 0.18,
         width * 0.62, width * 0.62, 0.34, 'tote', coll, rot)
    # 近端支撑腿
    nx, nz = -math.cos(rot) * (length / 2 - 0.3), -math.sin(rot) * (length / 2 - 0.3)
    _box(name + '_FOOT', x + nx, z + nz, (h_near - 0.1) / 2,
         0.09, 0.09, h_near - 0.1, 'steel', coll, rot)
    return 5


#: 分拣分型（§16.7）。GLB 里除斜滑道外都没有母版，全部程序化。
SORTER_KINDS = ('chute', 'crossbelt', 'slider', 'swing_wheel',
                'narrow_belt', 'tilt_tray', 'put_wall')


def sorter(name, x, z, coll, rot=0.0, kind='chute', length=3.0, width=0.9, drop=0.55):
    """按分型搭一个分拣末端。缺省 `chute` = R8.5 之前的行为（逐字节不变）。

    每种分型必须有**看得出的结构差异**，否则等于没做：交叉带是环线小车、滑块是
    侧推板、摆轮是三轮、窄带是窄皮带、翻板是斜托盘、播种墙是格口阵列。差异由
    `variant_probe` 在 Blender 里量（顶点数/对象数），不靠渲染图用眼睛比。
    """
    if kind not in SORTER_KINDS:
        raise ValueError('未知分拣分型 %r（可选 %s）' % (kind, SORTER_KINDS))
    if kind == 'chute':
        # drop 必须由调用方给：sorting_line 原来用 0.5，这里若默认 0.55 会悄悄改掉
        # 默认场景的滑道坡度（第一次跑就撞出 GLB 换了 436 字节）。
        return chute(name, x, z, coll, rot=rot, length=length, width=width, drop=drop)

    n = 0
    dx, dz = math.cos(rot), math.sin(rot)
    px, pz = -math.sin(rot), math.cos(rot)

    if kind in ('slider', 'swing_wheel', 'tilt_tray', 'crossbelt', 'narrow_belt'):
        # 共用一个台面：分型差异体现在台面之上的机构
        bed_w = width * (0.5 if kind == 'narrow_belt' else 1.0)
        _plate(name + '_BED', x, z, rot, length, bed_w, 0.9, 0.88, 0.07, 'chute', coll)
        n += 1
    if kind == 'slider':
        for k in range(3):
            u = -length / 2 + length * (k + 0.5) / 3
            _box('%s_PUSH%d' % (name, k), x + dx * u + px * (width * 0.45),
                 z + dz * u + pz * (width * 0.45), 1.02,
                 0.5, 0.08, 0.26, 'steel', coll, rot)
            n += 1
    elif kind == 'swing_wheel':
        for k in range(3):
            u = -length / 2 + length * (k + 0.5) / 3
            _box('%s_WHEEL%d' % (name, k), x + dx * u, z + dz * u, 0.98,
                 0.34, 0.12, 0.12, 'rubber', coll, rot)
            n += 1
    elif kind == 'tilt_tray':
        for k in range(3):
            u = -length / 2 + length * (k + 0.5) / 3
            _plate('%s_TRAY%d' % (name, k), x + dx * u, z + dz * u, rot,
                   0.8, width * 0.9, 0.96 + 0.06 * k, 0.92 + 0.06 * k, 0.05,
                   'tray', coll)
            n += 1
    elif kind == 'crossbelt':
        # 环线：两侧各一排小车，中间留出主线
        for s_ in (-1, 1):
            for k in range(4):
                u = -length / 2 + length * (k + 0.5) / 4
                _box('%s_CAR%d_%d' % (name, s_, k), x + dx * u + px * s_ * (width * 0.4),
                     z + dz * u + pz * s_ * (width * 0.4), 1.0,
                     0.5, 0.5, 0.12, 'tray', coll, rot)
                n += 1
    elif kind == 'narrow_belt':
        _box(name + '_NARROW', x, z, 0.86, length, 0.34, 0.05, 'belt_top', coll, rot)
        n += 1
    elif kind == 'put_wall':
        for i in range(3):
            for j in range(2):
                bx = x + px * (j - 0.5) * (width * 0.55)
                bz = z + pz * (j - 0.5) * (width * 0.55)
                _box('%s_CELL%d_%d' % (name, i, j), bx + dx * (i - 1) * 0.9,
                     bz + dz * (i - 1) * 0.9, 0.9,
                     0.8, width * 0.5, 0.7, 'tote', coll, rot)
                n += 1
    # 末端支撑腿（除播种墙自带格口）
    if kind != 'put_wall':
        for s_ in (-1, 1):
            _box('%s_FOOT%d' % (name, s_), x + dx * (length / 2 - 0.3) + px * s_ * (width * 0.4),
                 z + dz * (length / 2 - 0.3) + pz * s_ * (width * 0.4), 0.4,
                 0.08, 0.08, 0.8, 'steel', coll, rot)
            n += 1
    return n


def sort_station(name, x, z, coll, rot=0.0):
    """分拣台：台面 + 显示器 + 扫码枪 + 两个周转箱。"""
    _box(name + '_TOP', x, z, 0.86, 2.4, 1.0, 0.08, 'steel', coll, rot)
    for sx in (-1, 1):
        for sz in (-1, 1):
            _box('%s_LEG%d%d' % (name, sx, sz), x + sx * 1.05, z + sz * 0.4, 0.42,
                 0.07, 0.07, 0.84, 'leg', coll, rot)
    _box(name + '_MON', x - 0.4, z - 0.15, 1.3, 0.62, 0.05, 0.4, 'screen', coll, rot)
    _box(name + '_MONST', x - 0.4, z - 0.15, 1.06, 0.06, 0.06, 0.22, 'leg', coll, rot)
    _box(name + '_KEY', x - 0.4, z + 0.18, 0.93, 0.5, 0.16, 0.03, 'leg', coll, rot)
    _box(name + '_TOTE1', x + 0.7, z - 0.2, 1.06, 0.55, 0.42, 0.34, 'tote', coll, rot)
    _box(name + '_TOTE2', x + 0.7, z + 0.3, 1.06, 0.55, 0.42, 0.34, 'carton', coll, rot)
    return 9


def sorting_line(coll, rect, name='SORT', sorter_kind='chute', conveyor_style='belt'):
    """在工位 rect 里搭一条分拣线：输送带 + 滑道 + 两端分拣台。

    只在 rect 内部排版——越界就会压到巷道和货架，那是数据画的边界，不能为了
    好看去改。rect 一般 14×5 m，所以：主线贴南边、滑道朝北、分拣台放两端。
    """
    x0, z0, x1, z1 = rect
    w, d = x1 - x0, z1 - z0
    cz_line = z0 + 1.0                       # 主线贴南侧
    conveyor(name + '_LINE', [(x0 + 1.2, cz_line), (x1 - 1.2, cz_line)], 0.9, coll,
             style=conveyor_style)

    # 4 条滑道：从主线朝北滑出，长度受 rect 深度约束
    n = 4
    run = max(1.6, min(2.6, d - 2.0))
    for i in range(n):
        bx = x0 + 3.2 + (w - 6.4) * i / (n - 1)
        sorter('%s_CH%d' % (name, i), bx, cz_line + run / 2 + 0.5, coll,
               rot=math.radians(90), kind=sorter_kind, length=run, width=1.05, drop=0.5)

    # 两端分拣台 + 作业员（贴着滑道外侧，不压主线）
    for i, sx in enumerate((x0 + 1.5, x1 - 1.5)):
        sort_station('%s_ST%d' % (name, i), sx, z0 + d * 0.62, coll,
                     rot=math.radians(90))
        worker('%s_OP%d' % (name, i), sx + (0.9 if i == 0 else -0.9), z0 + d * 0.62, coll)
    return 2 + n * 6 + 2 * 9 + 2 * 4


def pack_line(coll, rect, name='PACK'):
    """包装线：两排打包台 + 显示器 + 纸箱 + 作业员。"""
    x0, z0, x1, z1 = rect
    w = x1 - x0
    cz = (z0 + z1) / 2
    for i, t in enumerate((0.3, 0.7)):
        px = x0 + w * t
        sort_station('%s_T%d' % (name, i), px, cz, coll)
        worker('%s_OP%d' % (name, i), px - 0.9, cz, coll)
        for k in range(3):
            _box('%s_BOX%d_%d' % (name, i, k), px + 1.0, cz - 0.9 + k * 0.35, 0.25,
                 0.5, 0.42, 0.34, 'carton', coll, rot=0.1 * k)
    return 6


def pick_face(coll, rect, name='PICK'):
    """拣选位：料架 + 蓝色笼车。"""
    x0, z0, x1, z1 = rect
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    for i, t in enumerate((0.3, 0.7)):
        cage_trolley('%s_C%d' % (name, i), cx - 1.5 + 3.0 * i, cz, coll)


def build(dc_module):
    global D
    D = dc_module
    palette()
    coll = D.collection('ZONE_DETAIL')
    n = 0
    parcels = 0
    for wh in D.LAYOUT['warehouses']:
        for cv in wh.get('conveyors', []) or []:
            # 分型来自数据：没有 style 字段就是 belt / chute，与 R8.5 之前一致
            parcels += conveyor(cv['id'].replace('-', ''), cv['path'],
                                float(cv.get('width', 1.0)), coll,
                                style=cv.get('style', 'belt'))
        for st in wh.get('stations', []) or []:
            kind = st.get('kind')
            if kind == 'SORTING':
                n += sorting_line(coll, st['rect'], st['id'].replace('-', ''),
                                  sorter_kind=st.get('sorter', 'chute'),
                                  conveyor_style=st.get('conveyor_style', 'belt'))
            elif kind == 'PACKING':
                n += pack_line(coll, st['rect'], st['id'].replace('-', ''))
    D.COUNTS['zone_detail_parts'] = n
    D.COUNTS['conveyor_parcels'] = parcels
    return n
