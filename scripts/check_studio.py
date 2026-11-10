"""check_studio.py — 驾驶舱与生成器的三方一致性守卫。

为什么需要它：功能区勾选页、`build_variant.py`、`layout_spec_areas.json`
这是**三个地方在描述同一件事**。页面显示 13 个区、生成器写 13 个区、产物里
13 个区——任何一方单独改动，用户就会看到"界面上有、跑出来没有"。

本脚本做三件事：
  1) 结构：PAGES 里的每页都能 import，key 唯一，页面数 == 导出类数。
  2) 排布：用 build_variant.pack() 复算勾选结果，逐区比对 rect / 总深度，
     与 layout_spec_areas.json 必须完全一致。
  3) 可失败：--self-test 篡改一个 rect 与一个深度，必须报 FAIL。

Tkinter 缺失时（常见于 Linux CI）跳过第 1 步并如实打印 SKIP，不伪装成 PASS。
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG = os.path.join(ROOT, 'config')
TOL = 1e-6

CHECKS = []


def check(level, name, message):
    CHECKS.append({'level': level, 'name': name, 'message': message})
    print('[%s] %s — %s' % (level, name, message))


def jload(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def load_engine():
    import importlib.util
    path = os.path.join(ROOT, 'scripts', 'build_variant.py')
    spec = importlib.util.spec_from_file_location('dc_build_variant', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 1 结构
def check_pages():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        check('SKIP', '页面结构', '当前解释器没有 tkinter（CI 常见），跳过导入检查')
        return
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    try:
        app_mod = importlib.import_module('dc_studio.app')
        pages_mod = importlib.import_module('dc_studio.pages')
    except Exception as exc:
        check('FAIL', '页面结构', '导入驾驶舱失败：%s' % exc)
        return

    keys = [k for k, *_ in app_mod.PAGES]
    if len(keys) != len(set(keys)):
        dup = sorted({k for k in keys if keys.count(k) > 1})
        check('FAIL', '页面结构', '页面 key 重复：%s' % ', '.join(dup))
        return
    exported = set(getattr(pages_mod, '__all__', []))
    missing = []
    for key, _g, _l, _s, cls in app_mod.PAGES:
        if cls.__name__ not in exported:
            missing.append('%s(%s)' % (cls.__name__, key))
    if missing:
        check('FAIL', '页面结构', 'PAGES 引用的页面未在 pages.__all__ 导出：%s'
              % ', '.join(missing))
        return
    check('PASS', '页面结构', '%d 个页面，key 唯一，全部可导入（%s）'
          % (len(keys), ' / '.join(keys)))


# ------------------------------------------------------------------ 2 排布
def repack(selection, base, catalog, engine):
    """完全复刻 build_variant.build() 的排布，返回 (placed, need)。"""
    wh = next(w for w in base['warehouses'] if w['id'] == 'W')
    base_depth = float(wh['size']['depth'])
    width = float(wh['size']['width'])
    place = selection.get('placement') or {}
    exp = selection.get('expand') or {}
    x_margin = float(place.get('x_margin_m', 4.0))
    gap = float(place.get('gap_m', 2.0))
    offset = float(place.get('band_start_offset_m', 2.0))
    north = float(exp.get('north_aisle_m', 2.0))

    items = sorted((catalog[k] for k in selection['enabled']), key=lambda t: -t['priority'])
    placed, depth_used = engine.pack(
        items, (x_margin, base_depth + offset, width - x_margin), x_margin, gap)
    need = base_depth + offset + depth_used + north
    return placed, need


def check_layout(expected_override=None):
    catalog_file = os.path.join(CONFIG, 'areas_catalog.json')
    sel_file = os.path.join(CONFIG, 'areas_selection.json')
    spec_file = os.path.join(CONFIG, 'layout_spec_areas.json')
    base_file = os.path.join(CONFIG, 'layout_spec.json')

    if not os.path.exists(spec_file):
        check('SKIP', '功能区排布', '还没有变体 spec（先跑 build_variant.py --build）')
        return

    catalog = {t['template']: t for t in jload(catalog_file)['templates']}
    selection = jload(sel_file)
    base = jload(base_file)
    variant = expected_override or jload(spec_file)

    unknown = [k for k in selection.get('enabled', []) if k not in catalog]
    if unknown:
        check('FAIL', '功能区排布', '勾选里有菜单中不存在的模板：%s' % ', '.join(unknown))
        return

    engine = load_engine()
    placed, need = repack(selection, base, catalog, engine)

    vwh = next(w for w in variant['warehouses'] if w['id'] == 'W')
    v_areas = {a['id']: a for a in (vwh.get('areas') or [])}
    want_ids = {'W-%s' % p['template'] for p in placed}

    if set(v_areas) != want_ids:
        check('FAIL', '功能区排布', '变体里的区与勾选不一致：多 %s / 少 %s'
              % (sorted(set(v_areas) - want_ids), sorted(want_ids - set(v_areas))))
        return

    bad = []
    for p in placed:
        want = p['rect']
        got = v_areas['W-%s' % p['template']]['rect']
        if any(abs(a - b) > TOL for a, b in zip(want, got)):
            bad.append('%s 期望 %s 实际 %s' % (p['template'], want, got))
    if bad:
        check('FAIL', '功能区排布', 'rect 不一致：' + '；'.join(bad[:3]))
        return

    depth = float(vwh['size']['depth'])
    if depth + TOL < need:
        check('FAIL', '功能区排布', 'W 深度 %.1f m 小于排布所需 %.1f m' % (depth, need))
        return

    check('PASS', '功能区排布',
          '%d 个区，rect 与勾选一致，W 深度 %.1f m ≥ 需求 %.1f m'
          % (len(placed), depth, need))


# ------------------------------------------------------------------ 3 自检
def self_test():
    catalog = {t['template']: t for t in jload(os.path.join(CONFIG, 'areas_catalog.json'))['templates']}
    selection = jload(os.path.join(CONFIG, 'areas_selection.json'))
    base = jload(os.path.join(CONFIG, 'layout_spec.json'))
    spec_file = os.path.join(CONFIG, 'layout_spec_areas.json')
    if not os.path.exists(spec_file):
        print('SELF_TEST_SKIP 没有变体 spec，无法构造反例')
        return 0
    engine = load_engine()

    cases = []

    # 反例 1：把一个区的 rect 挪 1 m
    v = copy.deepcopy(jload(spec_file))
    vwh = next(w for w in v['warehouses'] if w['id'] == 'W')
    if vwh.get('areas'):
        a = vwh['areas'][0]
        a['rect'] = [a['rect'][0] + 1.0] + a['rect'][1:]
    cases.append(('篡改 rect +1 m', v))

    # 反例 2：深度缩到 70 m（排不下）
    v2 = copy.deepcopy(jload(spec_file))
    w2 = next(w for w in v2['warehouses'] if w['id'] == 'W')
    w2['size']['depth'] = 70.0
    cases.append(('深度缩回 70 m', v2))

    # 反例 3：删掉一个区
    v3 = copy.deepcopy(jload(spec_file))
    w3 = next(w for w in v3['warehouses'] if w['id'] == 'W')
    if w3.get('areas'):
        w3['areas'] = w3['areas'][1:]
    cases.append(('少生成一个区', v3))

    misses = 0
    for name, variant in cases:
        CHECKS.clear()
        check_layout(expected_override=variant)
        levels = [c['level'] for c in CHECKS]
        if 'FAIL' in levels:
            print('   ✓ 反例「%s」按预期失败：%s'
                  % (name, next(c['message'] for c in CHECKS if c['level'] == 'FAIL')))
        else:
            misses += 1
            print('   ✗ 反例「%s」没有被抓住' % name)

    # 反向检查：干净配置必须通过，防止"全都 FAIL 的假安全感"
    CHECKS.clear()
    check_layout()
    if CHECKS and CHECKS[0]['level'] == 'PASS':
        print('   ✓ 干净配置通过（%s）' % CHECKS[0]['message'])
    else:
        misses += 1
        print('   ✗ 干净配置没有通过，守卫本身有问题')

    if misses:
        print('STUDIO_SELFTEST_FAIL 有 %d 个反例未生效' % misses)
        return 1
    print('STUDIO_SELFTEST_OK cases=%d bad=0' % len(cases))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='驾驶舱 ↔ 生成器 ↔ 变体 spec 一致性守卫')
    ap.add_argument('--self-test', action='store_true', help='用坏配置证明守卫会失败')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()

    check_pages()
    check_layout()

    fail = [c for c in CHECKS if c['level'] == 'FAIL']
    if fail:
        print('STUDIO_FAIL %d 项不一致' % len(fail))
        return 1
    skipped = sum(1 for c in CHECKS if c['level'] == 'SKIP')
    if not a.quiet:
        print('STUDIO_OK checks=%d skipped=%d' % (len(CHECKS), skipped))
    return 0


if __name__ == '__main__':
    sys.exit(main())
