"""spec_areas.py — 功能区（area）展开与校验。

定位：`spec_layout.py` 只管**能挡住车的东西**（货架、月台、工位、柱子），功能区是
**地面上的作业分区**，它不该改变导航栅格，也不该改变库位数。所以本脚本单独把
`layout_spec.json → warehouses[].areas[]` 展开成 `data/areas.json`，**不动 layout.json**。

这点很关键：W 仓 441 个库位、196 货架、40 项验收都已经被 layout.json 钉死，为了画
几个地标去重排货架是亏本买卖。功能区走"加法"，物理布局零改动。

两类功能区（这是本脚本唯一的建模约定）：
  · **物理区（physical）**：给 `rect`，真的占地面。必须通过几何校验——
    在仓内、不压货架/月台/工位、不与别的区重叠、AP 在可走栅格上。
  · **逻辑区（logical）**：不给 `rect`，只给 `anchor`（挂靠已有实体：分区/货架/月台门/
    工位/充电桩）。适用于"贴着月台或货架作业、无需独立占地"的环节，如收货暂存、
    补货、空托盘区、电池间。

规则（计划书 §12.6 表 B，接在 L14–L22 之后）：
  L23 物理区几何合法（仓内、够大、不压实体、AP 可走）
  L24 容量必须为正（至少一项 >= 1，且没有负值）
  L25 id 唯一、命名规范、kind 在允许集合内
  L26 绑定完整：无 rect 必须有可解析的 anchor；process_spec 引用的 area 必须存在

用法：
  python scripts/spec_areas.py
  python scripts/spec_areas.py --self-test
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG = os.path.join(ROOT, 'config')
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'out')

# Windows 管道控制台默认非 UTF-8（cp1252/GBK），中文报告会 UnicodeEncodeError。
# 强制 UTF-8 + replace，保证 CI 与本地 GBK 控制台都能跑完。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


KINDS = {'RECEIVING', 'QA', 'STAGING_IN', 'STORAGE', 'PICK_FACE', 'REPLENISH',
         'SORTING', 'PACKING', 'VAS', 'STAGING_OUT', 'RETURNS',
         'EMPTY_CONTAINER', 'BATTERY', 'MAINTENANCE', 'OFFICE'}
ID_RE = re.compile(r'^[A-Z0-9]+(-[A-Z0-9]+)+$')
MIN_SIDE_M = 2.0
EPS = 1e-9


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def issue(rule, level, where, msg):
    return {'rule': rule, 'level': level, 'where': where, 'message': msg}


def derive_capacity(area):
    """理论产能 = 并行位数 × 3600 / 平均服务时间。

    只认**显式声明**的 `process.parallel_slots`。不拿 `sku_positions` / `pallet_positions`
    去凑并行位——那是缓冲容量，不是服务台数；300 个 SKU 位不等于 300 个拣货员。
    没有声明就不估算（返回 None），宁缺不算。

    这是上界：满负荷、零故障、不休息、来料不断。用途是回答"这个区够不够"，
    不是承诺"实际能发这么多"——实际值要等 M1-02 的排队模型。
    """
    proc = area.get('process') or {}
    svc = proc.get('service_time_s') or {}
    mean = svc.get('mean')
    slots = proc.get('parallel_slots')
    if not isinstance(slots, int) or slots < 1 or not mean or mean <= 0:
        return None
    per_h = slots * 3600.0 / float(mean)
    return {'slots': slots, 'service_mean_s': mean,
            'throughput_per_h': round(per_h, 2),
            'per_8h': int(round(per_h * 8)),
            'basis': '上界：满负荷+零故障+不休息'}


def overlap(a, b):
    """矩形相交面积；只接触边不算相交。"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    d = min(a[3], b[3]) - max(a[1], b[1])
    if w > EPS and d > EPS:
        return w * d
    return 0.0


def anchor_index(wh):
    """一个仓里所有可挂靠的实体 id → (类型, 说明)。"""
    idx = {}
    for z in wh.get('zones', []):
        idx[z['id']] = ('ZONE', z.get('name', ''))
    for r in wh.get('racks', []):
        idx[r['id']] = ('RACK', r.get('zone', ''))
    for d in wh.get('docks', []):
        idx[d['id']] = ('DOCK', d.get('kind', ''))
        for door in d.get('doors', []):
            idx[door['id']] = ('DOOR', d['id'])
    for s in wh.get('stations', []):
        idx[s['id']] = ('STATION', s.get('kind', ''))
    for c in wh.get('charging_stations', []):
        idx[c['id']] = ('CHARGER', '')
    return idx


def solid_rects(wh):
    """会挡车/占地的实体（不含逻辑分区）。"""
    out = []
    for r in wh.get('racks', []):
        x, _y, z = r['position']
        out.append((r['id'], [x, z, x + r['size'][0], z + r['size'][2]]))
    for s in wh.get('stations', []):
        out.append((s['id'], list(s['rect'])))
    for d in wh.get('docks', []):
        out.append((d['id'], list(d['rect'])))
    return out


def walkable(nav, x, z):
    if not nav:
        return None
    cs = nav['cell_size_m']
    col, row = int(x / cs), int(z / cs)
    rows = nav['walkable_rows']
    if row < 0 or row >= len(rows):
        return False
    line = rows[row]
    if col < 0 or col >= len(line):
        return False
    return line[col] == '1'


def check_area(area, wh, wh_rect, solids, nav, seen_ids):
    """单个功能区的 L23–L26（除 process_spec 引用外）。"""
    out = []
    aid = area.get('id', '?')
    kind = area.get('kind')

    # ---- L25 命名 / 类型
    if not isinstance(aid, str) or not ID_RE.match(aid):
        out.append(issue('L25', 'FAIL', aid, 'id 不符合 `<仓>-<用途>-<序号>` 形式'))
    if aid in seen_ids:
        out.append(issue('L25', 'FAIL', aid, 'id 重复'))
    if kind not in KINDS:
        out.append(issue('L25', 'FAIL', aid, 'kind=%r 不在允许集合' % (kind,)))

    # ---- L24 容量
    cap = area.get('capacity') or {}
    if not isinstance(cap, dict) or not cap:
        out.append(issue('L24', 'FAIL', aid, '缺少 capacity（至少给一项容量）'))
    else:
        vals = [v for v in cap.values() if isinstance(v, (int, float))]
        if any(v < 0 for v in vals):
            out.append(issue('L24', 'FAIL', aid, 'capacity 出现负值'))
        if not any(v >= 1 for v in vals):
            out.append(issue('L24', 'FAIL', aid, 'capacity 全部 < 1，区域等于不存在'))

    # ---- L26 绑定
    rect = area.get('rect')
    anchor = area.get('anchor')
    if rect is None and not anchor:
        out.append(issue('L26', 'FAIL', aid, '既无 rect 也无 anchor：区域挂在哪里？'))
    if rect is not None and anchor:
        out.append(issue('L26', 'WARN', aid, 'rect 与 anchor 同时给出，按物理区处理，anchor 仅作文档'))

    # ---- L23 几何
    if rect is not None:
        if not (isinstance(rect, list) and len(rect) == 4):
            out.append(issue('L23', 'FAIL', aid, 'rect 必须是 [x0,z0,x1,z1]'))
        else:
            x0, z0, x1, z1 = rect
            if x1 <= x0 or z1 <= z0:
                out.append(issue('L23', 'FAIL', aid, 'rect 退化（x1<=x0 或 z1<=z0）'))
            elif x1 - x0 < MIN_SIDE_M or z1 - z0 < MIN_SIDE_M:
                out.append(issue('L23', 'FAIL', aid, '过窄：%.1f×%.1f m（最小 %.1f m/边）'
                                 % (x1 - x0, z1 - z0, MIN_SIDE_M)))
            if wh_rect:
                wx0, wz0, wx1, wz1 = wh_rect
                if x0 < wx0 - EPS or z0 < wz0 - EPS or x1 > wx1 + EPS or z1 > wz1 + EPS:
                    out.append(issue('L23', 'FAIL', aid, '超出仓库 footprint %s' % (wh_rect,)))
            for sid, sr in solids:
                a = overlap(rect, sr)
                if a > 0:
                    out.append(issue('L23', 'FAIL', aid,
                                     '与实体 %s 重叠 %.1f m²' % (sid, a)))
            ap = area.get('access_point')
            if ap:
                w = walkable(nav, ap[0], ap[1])
                if w is False:
                    out.append(issue('L23', 'FAIL', aid,
                                     'access_point %s 落在不可通行栅格上' % (ap,)))
            elif kind not in ('BATTERY', 'OFFICE'):
                out.append(issue('L23', 'WARN', aid, '物理区缺 access_point，调度无法停靠'))
    if anchor:
        idx = anchor_index(wh)
        if anchor not in idx:
            out.append(issue('L26', 'FAIL', aid, 'anchor `%s` 在该仓找不到' % anchor))
    return out


def expand(spec, layout, nav):
    areas_out, problems = [], []
    wh_index = {w['id']: w for w in layout['warehouses']}
    seen = set()
    by_kind, by_wh = {}, {}
    n_phys = n_logi = n_derived = 0
    for wh in spec['warehouses']:
        wid = wh['id']
        lw = wh_index.get(wid)
        if lw is None:
            continue
        ox, oz = lw['origin']
        wh_rect = [ox, oz, ox + lw['size']['width'], oz + lw['size']['depth']]
        solids = solid_rects(lw)
        local_seen = []
        for a in wh.get('areas', []):
            problems += check_area(a, lw, wh_rect, solids + [(x[0], x[1]) for x in local_seen],
                                   nav, seen)
            seen.add(a.get('id'))
            if a.get('rect'):
                local_seen.append((a['id'], list(a['rect'])))
                n_phys += 1
            else:
                n_logi += 1
            by_kind[a.get('kind')] = by_kind.get(a.get('kind'), 0) + 1
            by_wh[wid] = by_wh.get(wid, 0) + 1
            rec = dict(a)
            rec['warehouse'] = wid
            rec['physical'] = a.get('rect') is not None
            if rec['physical']:
                r = a['rect']
                rec['area_m2'] = round((r[2] - r[0]) * (r[3] - r[1]), 2)
            if a.get('anchor'):
                idx = anchor_index(lw)
                rec['anchor_resolved'] = idx.get(a['anchor'], ('UNKNOWN', ''))[0]
            der = derive_capacity(a)
            if der:
                rec['derived'] = der
                n_derived += 1
            areas_out.append(rec)
    # 跨文件：process_spec 节点引用的 area
    ps = os.path.join(CONFIG, 'process_spec.json')
    if os.path.exists(ps):
        for n in load(ps).get('nodes', []):
            ref = n.get('area')
            if ref and ref not in seen:
                problems.append(issue('L26', 'FAIL', n.get('id', '?'),
                                      'process_spec 引用了不存在的功能区 `%s`' % ref))
    summary = {'total': len(areas_out), 'physical': n_phys, 'logical': n_logi,
               'with_throughput': n_derived,
               'by_kind': by_kind, 'by_warehouse': by_wh}
    return areas_out, summary, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description='功能区展开与校验（L23–L26）')
    # 变体运行（用户自选功能区的扩仓版）由 DC_LAYOUT_SPEC 指定，和 gen_data 保持一致
    ap.add_argument('--spec',
                    default=os.environ.get('DC_LAYOUT_SPEC')
                    or os.path.join(CONFIG, 'layout_spec.json'))
    ap.add_argument('--layout', default=os.path.join(DATA, 'layout.json'))
    ap.add_argument('--nav', default=os.path.join(DATA, 'nav_grid.json'))
    ap.add_argument('--out', default=os.path.join(DATA, 'areas.json'))
    ap.add_argument('--report', default=os.path.join(OUT, 'areas_report.json'))
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test(a.spec, a.layout, a.nav)

    spec, layout = load(a.spec), load(a.layout)
    nav = load(a.nav) if os.path.exists(a.nav) else None
    areas, summary, problems = expand(spec, layout, nav)
    per = {}
    for r in ('L23', 'L24', 'L25', 'L26'):
        lv = [p['level'] for p in problems if p['rule'] == r]
        per[r] = 'FAIL' if 'FAIL' in lv else ('WARN' if 'WARN' in lv else 'PASS')
    verdict = 'FAIL' if 'FAIL' in per.values() else 'PASS'

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'kind': 'areas', 'schema_version': '1.1',
                            'generated_from': 'config/layout_spec.json',
                            'summary': summary, 'areas': areas},
                           ensure_ascii=False, indent=2) + '\n')
    with open(a.report, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'kind': 'areas_report', 'schema_version': '1.1',
                            'rules': per, 'verdict': verdict, 'summary': summary,
                            'issues': problems}, ensure_ascii=False, indent=2) + '\n')
    if not a.quiet:
        for r in ('L23', 'L24', 'L25', 'L26'):
            print('[%s] %s' % (per[r], r))
        for p in problems:
            print('   %-4s %-14s %s' % (p['level'], p['where'], p['message']))
        rows = sorted([a for a in areas if a.get('derived')],
                      key=lambda a: -a['derived']['throughput_per_h'])
        if rows:
            print('   理论产能（上界，满负荷零故障）：')
            for r in rows[:8]:
                d = r['derived']
                print('     %-14s %-14s %2d 位 × %5.0fs → %7.1f /h  (8h≈%d)'
                      % (r['id'], r['kind'], d['slots'], d['service_mean_s'],
                         d['throughput_per_h'], d['per_8h']))
        print('AREAS_%s total=%d physical=%d logical=%d issues=%d'
              % (verdict, summary['total'], summary['physical'], summary['logical'],
                 len([p for p in problems if p['level'] == 'FAIL'])))
    return 0 if verdict == 'PASS' else 1


def self_test(spec_path, layout_path, nav_path):
    base = load(spec_path)
    layout = load(layout_path)
    nav = load(nav_path) if os.path.exists(nav_path) else None

    def first_physical(s):
        for wh in s['warehouses']:
            for a in wh.get('areas', []):
                if a.get('rect'):
                    return wh, a
        raise AssertionError('没有物理功能区可做反例')

    def mutate(fn):
        s = copy.deepcopy(base)
        wh, area = first_physical(s)
        fn(wh, area)
        return s

    cases = [
        ('A1 rect 越界', 'L23', mutate(lambda w, a: a.__setitem__('rect', [999, 999, 1010, 1005]))),
        ('A2 压住货架', 'L23', mutate(lambda w, a: a.__setitem__(
            'rect', [6.0, 13.0, 20.0, 18.0]))),
        ('A3 AP 不可走', 'L23', mutate(lambda w, a: a.__setitem__('access_point', [7.5, 13.6]))),
        ('A4 容量全 0', 'L24', mutate(lambda w, a: a.__setitem__(
            'capacity', {'pallet_positions': 0}))),
        ('A5 id 重复', 'L25', mutate(lambda w, a: a.__setitem__(
            'id', w['areas'][1]['id']))),
        ('A6 无 rect 无 anchor', 'L26', mutate(lambda w, a: (
            a.pop('rect'), a.pop('access_point', None)))),
        ('A7 anchor 不存在', 'L26', mutate(lambda w, a: (
            a.pop('rect'), a.pop('access_point', None), a.__setitem__('anchor', 'NO-SUCH'))))
    ]
    bad = 0
    for name, rule, spec in cases:
        try:
            _areas, _sum, problems = expand(spec, layout, nav)
        except Exception as e:
            print('  [BAD] %-20s 校验器抛异常: %s' % (name, e))
            bad += 1
            continue
        hit = [p for p in problems if p['rule'] == rule and p['level'] == 'FAIL']
        print('  [%s] %-20s 期望 %s 失败 → %s'
              % ('PASS' if hit else 'BAD', name, rule,
                 hit[0]['message'][:44] if hit else '未触发'))
        bad += 0 if hit else 1

    _a, _s, clean = expand(base, layout, nav)
    clean_fail = [p for p in clean if p['level'] == 'FAIL']
    print('  [%s] 基线配置应通过      → %s'
          % ('PASS' if not clean_fail else 'BAD',
             '无 FAIL' if not clean_fail else '意外 FAIL: %s' % clean_fail[0]['message']))
    bad += 0 if not clean_fail else 1
    print('AREAS_SELFTEST_%s cases=%d bad=%d' % ('OK' if bad == 0 else 'FAIL', len(cases) + 1, bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
