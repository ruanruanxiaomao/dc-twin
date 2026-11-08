"""geometry.py — 全项目唯一的几何常量与障碍集合定义（审计 §3.4 / §6.1 要求）。

所有脚本（plan_paths / check_geometry / build_dc / traffic_check）必须从这里取常量，
禁止各写各的 0.45。AMR 尺寸来自 cargoTwin 资产实测包围盒 1.12 × 0.77 × 0.435 m，
保守碰撞半径取外接圆半径 + 安全余量（转向/斜行时也成立）。
"""
import json
import math
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')
CFG = json.load(open(os.path.join(DATA, 'config.json'), encoding='utf-8'))
AGV = CFG['agv']

AMR_LENGTH = AGV['length_m']
AMR_WIDTH = AGV['width_m']
AMR_HEIGHT = AGV['height_m']
AMR_RADIUS = math.hypot(AMR_LENGTH / 2, AMR_WIDTH / 2)   # 0.6797
AGV_RADIUS = round(AMR_RADIUS + AGV['safety_margin_m'], 4)  # 0.7297 ≈ 0.73
# 第二轮审计 §3.2：中心距至少要 >= 两个保守外接圆半径之和，否则两台车
# 在任意朝向下都可能重叠却仍被判 PASS。旧值 1.2 m 小于直径 1.4592 m。
MIN_SEP = round(2 * AGV_RADIUS, 4)                          # 1.4592
SPEED = AGV['speed_mps']


def obstacle_rects(layout, cfg=None):
    """完整障碍集：货架 / 月台 / 输送线 / 工作站 / 充电桩 / 柱 / 园区车辆。

    审计 §3.6 指出旧版漏了月台、墙柱、叉车卡车。这里：
      · 柱子已在 layout.obstacles（PILLAR）；
      · 屋顶梁 / 后墙在空中或贴建筑边界，不构成地面通行障碍，单列说明；
      · 叉车 / 卡车从 config.scene 读取地面 footprint 并纳入。
    返回 [(kind, id, rect)]，rect = [x0,z0,x1,z1]。
    """
    cfg = cfg or CFG
    out = []
    for w in layout['warehouses']:
        for rk in w['racks']:
            x, _, z = rk['position']
            rl, _, rd = rk['size']
            out.append(('RACK', rk['id'], [x, z, x + rl, z + rd]))
        for d in w['docks']:
            out.append(('DOCK', d['id'], list(d['rect'])))
        for cv in w['conveyors']:
            pts = cv['path']
            for a, b in zip(pts, pts[1:]):
                out.append(('CONVEYOR', cv['id'],
                            [min(a[0], b[0]) - 0.5, min(a[1], b[1]) - 0.5,
                             max(a[0], b[0]) + 0.5, max(a[1], b[1]) + 0.5]))
        for st in w['stations']:
            out.append(('STATION', st['id'], list(st['rect'])))
        for ch in w['charging_stations']:
            x, _, z = ch['position']
            out.append(('CHARGER', ch['id'], [x - 0.8, z - 0.4, x + 0.8, z + 0.4]))
        for ob in w['obstacles']:
            out.append(('PILLAR', ob['id'], list(ob['rect'])))
    sc = cfg['scene']
    fw, fd = sc['vehicle_footprint_m']['forklift']
    tw, td = sc['vehicle_footprint_m']['truck']
    for i, (x, z) in enumerate(sc['forklifts'], 1):
        out.append(('FORKLIFT', 'FORKLIFT_%02d' % i,
                    [x - fw / 2, z - fd / 2, x + fw / 2, z + fd / 2]))
    for i, (x, z) in enumerate(sc['trucks'], 1):
        out.append(('TRUCK', 'TRUCK_%02d' % i,
                    [x - tw / 2, z - td / 2, x + tw / 2, z + td / 2]))
    return out


def inflate(r, d):
    return [r[0] - d, r[1] - d, r[2] + d, r[3] + d]


def seg_hits_rect(p, q, r):
    """线段 p→q 与轴对齐矩形 r 是否相交（Liang-Barsky slab 裁剪）。"""
    x0, z0, x1, z1 = r
    dx, dz = q[0] - p[0], q[1] - p[1]
    tmin, tmax = 0.0, 1.0
    for lo, hi, o, d in ((x0, x1, p[0], dx), (z0, z1, p[1], dz)):
        if abs(d) < 1e-12:
            if o < lo or o > hi:
                return False
        else:
            a, b = (lo - o) / d, (hi - o) / d
            if a > b:
                a, b = b, a
            tmin, tmax = max(tmin, a), min(tmax, b)
            if tmin > tmax:
                return False
    return True


def point_rect_dist(p, r):
    dx = max(r[0] - p[0], 0.0, p[0] - r[2])
    dz = max(r[1] - p[1], 0.0, p[1] - r[3])
    return math.hypot(dx, dz)


def point_seg_dist(p, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    L2 = vx * vx + vy * vy
    if L2 < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    u = max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L2))
    return math.hypot(p[0] - (a[0] + u * vx), p[1] - (a[1] + u * vy))


def seg_seg_dist(a, b, c, d):
    if seg_hits_rect(a, b, [min(c[0], d[0]), min(c[1], d[1]),
                            max(c[0], d[0]), max(c[1], d[1])]) and \
       seg_hits_rect(c, d, [min(a[0], b[0]), min(a[1], b[1]),
                            max(a[0], b[0]), max(a[1], b[1])]):
        return 0.0
    return min(point_seg_dist(a, c, d), point_seg_dist(b, c, d),
               point_seg_dist(c, a, b), point_seg_dist(d, a, b))


def seg_rect_dist(p, q, r):
    """线段到矩形的最小距离（解析法，替代旧的 5 点采样）。"""
    if seg_hits_rect(p, q, r):
        return 0.0
    x0, z0, x1, z1 = r
    corners = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    edges = list(zip(corners, corners[1:] + corners[:1]))
    return min([point_rect_dist(p, r), point_rect_dist(q, r)] +
               [seg_seg_dist(p, q, a, b) for a, b in edges])
