"""build_variant.py — 用户自选功能区 → 生成"扩仓变体"布局。

解决的问题：主链 `config/layout_spec.json` 的 W 仓 100×70 已经被货架、月台、工位、
充电桩铺满（见计划书 §23.2 的实测），12 个功能区里只有 3 个能落地。方案 A 的做法是
**把 W 仓向北扩 18 m**，用新增的作业带容纳用户选择的功能区。

用法（三步）：
  1) 看菜单：      python scripts/build_variant.py --list
  2) 选功能区：    python scripts/build_variant.py --preset ecommerce
                   python scripts/build_variant.py --enable VAS-01 MNT-01 --disable REP-01
     （结果写回 config/areas_selection.json，可手改）
  3) 生成并校验：  python scripts/build_variant.py --build
     → config/layout_spec_areas.json（变体 spec）+ 校验报告
  4) 用变体跑系统：python run.py --scope scenarios --spec config/layout_spec_areas.json

设计约束：
  · **绝不修改** `config/layout_spec.json` 与 `data/layout.json`。变体是并排的第二份 spec。
  · 货架、月台、工位、充电桩、待命位原样保留 → 库位数不变（441），A1–A40 仍按老口径成立。
  · 功能区全部为**物理区**（有 rect + access_point），按 priority 降序货架式排布。
  · 深度不够时自动加深并 WARN，不静默裁掉用户选的功能区。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG = os.path.join(ROOT, 'config')
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'out')

CATALOG = os.path.join(CONFIG, 'areas_catalog.json')
SELECTION = os.path.join(CONFIG, 'areas_selection.json')
BASE_SPEC = os.path.join(CONFIG, 'layout_spec.json')
VARIANT_SPEC = os.path.join(CONFIG, 'layout_spec_areas.json')
SCENE_VARIANT = os.path.join(DATA, 'scene_variant.json')
FLEET = os.path.join(CONFIG, 'fleet_spec.json')

PRESETS = {
    'minimal': ['RCV-01', 'STGIN-01', 'PACK-03', 'STGOUT-01'],
    'ecommerce': ['RCV-01', 'QA-01', 'STGIN-01', 'FWD-01', 'REP-01',
                  'SORT-EXT', 'PACK-03', 'PACK-04', 'VAS-01', 'STGOUT-01',
                  'RET-01', 'EMP-01', 'BATT-01'],
    'full': None,      # None = 目录里全部
}


def jload(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def jdump(p, obj):
    with open(p, 'w', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def load_catalog():
    c = jload(CATALOG)
    return {t['template']: t for t in c['templates']}, c


def resolve_selection(args, catalog):
    """命令行 > 现有 selection 文件。返回 (enabled 列表, 配置字典)。"""
    sel = jload(SELECTION) if os.path.exists(SELECTION) else {}
    enabled = list(sel.get('enabled') or [])
    if args.preset:
        if args.preset == 'full':
            enabled = sorted(catalog, key=lambda k: -catalog[k]['priority'])
        else:
            enabled = list(PRESETS[args.preset])
    for e in args.enable or []:
        if e not in enabled:
            enabled.append(e)
    for d in args.disable or []:
        if d in enabled:
            enabled.remove(d)
    unknown = [e for e in enabled if e not in catalog]
    if unknown:
        raise SystemExit('未知功能区模板：%s（用 --list 查看菜单）' % ', '.join(unknown))
    return enabled, sel


def pack(items, band, x_margin, gap, row_gap=2.0):
    """货架式排布：按 priority 降序，从左到右，排满换行。

    band = (x0, z0, x1)  —— 北侧作业带的左界、起始 z、右界。
    """
    x0, z0, x1 = band
    placed, cursor_x, cursor_z, row_h = [], x0, z0, 0.0
    for it in items:
        w, d = it['size']
        if cursor_x + w > x1 + 1e-9:
            cursor_z += row_h + row_gap
            cursor_x, row_h = x0, 0.0
        rect = [round(cursor_x, 2), round(cursor_z, 2),
                round(cursor_x + w, 2), round(cursor_z + d, 2)]
        placed.append(dict(it, rect=rect))
        cursor_x += w + gap
        row_h = max(row_h, d)
    depth_used = (cursor_z - z0) + row_h if placed else 0.0
    return placed, depth_used


def build(args):
    catalog, raw = load_catalog()
    enabled, sel = resolve_selection(args, catalog)
    base = jload(BASE_SPEC)
    wh = next((w for w in base['warehouses'] if w['id'] == 'W'), None)
    if wh is None:
        raise SystemExit('基础 spec 里没有 W 仓')

    base_depth = float(wh['size']['depth'])
    exp = dict(sel.get('expand') or {})
    place = dict(sel.get('placement') or {})
    # 目标深度只认命令行；selection 文件里存的是上次算出来的结果，不能反过来当成约束
    target = float(args.target_depth) if args.target_depth else base_depth + 18
    north_aisle = float(exp.get('north_aisle_m', 2.0))
    x_margin = float(place.get('x_margin_m', 4.0))
    gap = float(place.get('gap_m', 2.0))
    offset = float(place.get('band_start_offset_m', 2.0))

    items = sorted((catalog[k] for k in enabled), key=lambda t: -t['priority'])
    band_z0 = base_depth + offset
    placed, depth_used = pack(items, (x_margin, band_z0, wh['size']['width'] - x_margin),
                              x_margin, gap)

    need = base_depth + offset + depth_used + north_aisle
    grew = False
    if need > target:
        grew = True
        target = need
    target = max(target, base_depth + offset + depth_used + north_aisle)

    # 与北侧前置仓的最小净距：W 向北长，不能贴到 F1/F2/F3 的南墙
    clearance = 2.0
    others = [float(w['origin'][1]) for w in base['warehouses'] if w['id'] != 'W']
    if others:
        limit = min(others) - clearance
        if target > limit + 1e-9:
            print('VARIANT_FAIL 需要的深度 %.1f m 超过允许上限 %.1f m'
                  '（北侧前置仓南墙 %.1f m − 净距 %.1f m）'
                  % (target, limit, min(others), clearance))
            print('           办法：--disable 一两个功能区，或缩短 --target-depth，'
                  '或先把前置仓整体北移')
            return None

    # 变体：只改 W 的 depth 与 areas，其余原样
    variant = copy.deepcopy(base)
    vwh = next(w for w in variant['warehouses'] if w['id'] == 'W')
    vwh['size']['depth'] = round(target, 2)
    vwh['areas'] = []
    for p in placed:
        area = {
            'id': 'W-%s' % p['template'],
            'kind': p['kind'],
            'name': p['name'],
            'group': p.get('group', ''),
            'rect': p['rect'],
            'capacity': p['capacity'],
            'process': p['process'],
            'constraints': p['constraints'],
            'access_point': [round((p['rect'][0] + p['rect'][2]) / 2, 2),
                             round(p['rect'][1] - 1.5, 2)],
        }
        vwh['areas'].append(area)

    jdump(VARIANT_SPEC, variant)

    # 扩仓会吞掉北侧园区道路与停车位（叉车 z=74、卡车 z=78、道路 z=68..92）。
    # 不处理的话，这些车会"停在新厂房里"，导航栅格把整条作业带判成阻塞。
    # 做法：园区整体挪到东侧空地（x 100..112），并写一套变体场景配置。
    base_cfg = jload(os.path.join(DATA, 'config.json'))
    sc = copy.deepcopy(base_cfg)
    sc['scene']['forklifts'] = [[104, 20], [104, 45], [104, 70]]
    sc['scene']['trucks'] = [[106, 8], [106, 62]]
    sc['scene']['roads'] = [[100, 4, 112, 86]]
    sc.setdefault('meta', {})['variant_note'] = (
        '扩仓变体：北侧园区道路与停车位已被厂房占用，车辆改停东侧空地（x 100..112）。')
    jdump(SCENE_VARIANT, sc)
    sel_out = dict(sel)
    sel_out.update({
        'schema_version': '1.0',
        'warehouse': 'W',
        'enabled': enabled,
        'expand': {'mode': 'north', 'target_depth_m': round(target, 2),
                   'north_aisle_m': north_aisle},
    })
    jdump(SELECTION, sel_out)

    print('>> 已生成变体 %s' % os.path.relpath(VARIANT_SPEC, ROOT))
    print('>> 场景配置 %s（北侧车辆/道路已挪到东侧空场）'
          % os.path.relpath(SCENE_VARIANT, ROOT))
    print('>> W 仓深度：%.1f → %.1f m（%s）'
          % (base_depth, target, '自动加深' if grew else '按选择值'))
    if grew:
        print('   WARN 选择的功能区放不下 %.1f m，已自动加深到 %.1f m' % (base_depth + 18, target))
    print('   功能区 %d 个：' % len(placed))
    for p in sorted(placed, key=lambda x: (x['rect'][1], x['rect'][0])):
        print('     %-12s %-14s rect=%-26s %.1f×%.1f m'
              % (p['template'], p['kind'], str(p['rect']), p['size'][0], p['size'][1]))
    rest = sorted(set(catalog) - set(enabled))
    if rest:
        print('   未选（用户可随时 --enable）：%s' % ', '.join(rest))
    return variant, placed


def validate():
    """用 spec_layout 校验变体，产物写到 out/，绝不覆盖 data/layout.json。"""
    # 变体产物单独放 out/variant/，不与主链产物混在一起
    out_dir = os.path.join(OUT, 'variant')
    os.makedirs(out_dir, exist_ok=True)
    out_layout = os.path.join(out_dir, 'variant_layout.json')
    out_report = os.path.join(out_dir, 'variant_layout_report.json')
    cmd = [sys.executable, 'scripts/spec_layout.py',
           '--spec', os.path.relpath(VARIANT_SPEC, ROOT),
           '--out', os.path.relpath(out_layout, ROOT),
           '--report', os.path.relpath(out_report, ROOT),
           '--fleet-spec', 'config/fleet_spec.json', '--quiet']
    p = subprocess.run(cmd, cwd=ROOT)
    if p.returncode != 0:
        print('VARIANT_FAIL 布局校验未通过（报告 %s）'
              % os.path.relpath(out_report, ROOT))
        return 1
    # 功能区 L23–L26：变体还没有自己的 nav_grid，AP 可走性留到真正跑流水线时再核
    areas_report = os.path.join(out_dir, 'variant_areas_report.json')
    cmd2 = [sys.executable, 'scripts/spec_areas.py',
            '--spec', os.path.relpath(VARIANT_SPEC, ROOT),
            '--layout', os.path.relpath(out_layout, ROOT),
            '--nav', os.path.join(OUT, 'no_such_nav.json'),
            '--out', 'data/areas_variant.json',
            '--report', os.path.relpath(areas_report, ROOT), '--quiet']
    p2 = subprocess.run(cmd2, cwd=ROOT)
    rep = jload(out_report)
    s = rep['summary']
    print('VARIANT_%s racks=%s locations=%s docks=%s doors=%s issues=%d warnings=%d'
          % (rep['verdict'], s['racks'], s['locations'], s['docks'], s['doors'],
             len(rep['issues']), len(rep['warnings'])))
    if p2.returncode != 0:
        print('  功能区校验 FAIL，见 %s' % os.path.relpath(areas_report, ROOT))
        return 1
    print('  功能区 L23–L26：PASS')
    return 0


def list_menu():
    catalog, raw = load_catalog()
    sel = set((jload(SELECTION).get('enabled') if os.path.exists(SELECTION) else []) or [])
    print('%-10s %-16s %-10s %-8s %-10s %s' % ('模板', '类型', '分组', '占地', '并行位', '状态'))
    print('-' * 74)
    for k in sorted(catalog, key=lambda x: -catalog[x]['priority']):
        t = catalog[k]
        print('%-10s %-16s %-10s %-8s %-10s %s'
              % (k, t['kind'], t.get('group', ''), '%dx%d' % tuple(t['size']),
                 t['process'].get('parallel_slots', '-'),
                 '已选' if k in sel else '未选'))
    print('\n预设：minimal(%d) / ecommerce(%d) / full(%d)'
          % (len(PRESETS['minimal']), len(PRESETS['ecommerce']), len(catalog)))
    print('用法：--preset ecommerce | --enable VAS-01 MNT-01 | --disable REP-01 | --build')


def main(argv=None):
    ap = argparse.ArgumentParser(description='用户自选功能区 → 扩仓变体')
    ap.add_argument('--list', action='store_true', help='打印功能区菜单')
    ap.add_argument('--preset', choices=sorted(PRESETS), help='整体套用预设')
    ap.add_argument('--enable', nargs='*', metavar='ID', help='追加功能区模板')
    ap.add_argument('--disable', nargs='*', metavar='ID', help='去掉功能区模板')
    ap.add_argument('--target-depth', type=float, default=None,
                    help='W 仓目标深度（米），默认 88')
    ap.add_argument('--build', action='store_true', help='生成并校验变体 spec')
    ap.add_argument('--no-validate', action='store_true')
    a = ap.parse_args(argv)

    if a.list:
        list_menu()
        return 0
    if not (a.build or a.preset or a.enable or a.disable):
        ap.print_help()
        return 0

    res = build(a)
    if res is None:
        return 2
    if a.no_validate:
        return 0
    return validate()


if __name__ == '__main__':
    sys.exit(main())
