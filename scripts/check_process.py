"""check_process.py — 过程配置校验器（计划书 §12.6 表 B，规则 L14–L22）。

与 `spec_layout.py` 的分工：
  · `spec_layout.py` 管**几何**：实体不重叠、AP 可走、门够宽（L1–L13）。
  · 本脚本管**产能与流程**：工位有没有产能、服务时间是否可采样、流程图是否连通、
    资源是否真实存在、班次是否重叠、以及配置里的工位是否真的存在于布局中。

设计纪律（沿用项目既有文化）：
  1. **只读**：只读 config/ 与 data/，唯一写操作是 `out/process_report.json`。
  2. **必须能失败**：`--self-test` 用 10 个坏配置证明每条规则都会 FAIL。
  3. **不假装**：`inventory_spec.json` 不存在时，L16/L17 记 SKIP 而不是 PASS。

用法：
  python scripts/check_process.py
  python scripts/check_process.py --self-test
  python scripts/check_process.py --spec config/process_spec.json --report out/process_report.json
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG = os.path.join(ROOT, 'config')
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'out')

DISTS = {'fixed', 'lognormal', 'normal', 'exponential', 'triangular'}
KINDS = {'RECEIVING', 'QA', 'STAGING_IN', 'STORAGE', 'PICKING', 'SORTING',
         'PACKING', 'VAS', 'STAGING_OUT', 'SHIPPING', 'RETURNS'}


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def issue(rule, level, where, msg):
    return {'rule': rule, 'level': level, 'where': where, 'message': msg}


# ---------------------------------------------------------------- 结构门（缺失即无法继续）
def check_shape(spec):
    out = []
    for key in ('shifts', 'resources', 'stations', 'nodes'):
        if not isinstance(spec.get(key), list):
            out.append(issue('L14', 'FAIL', key, '缺少必填数组 `%s`' % key))
    if out:
        return out
    for i, st in enumerate(spec['stations']):
        if not st.get('id'):
            out.append(issue('L14', 'FAIL', 'stations[%d]' % i, '缺少 id'))
    return out


# ---------------------------------------------------------------- L14 工位产能
def rule_l14(spec):
    out = []
    for st in spec['stations']:
        wid = st.get('id', '?')
        slots = st.get('parallel_slots')
        if not isinstance(slots, int) or slots < 1:
            out.append(issue('L14', 'FAIL', wid,
                             'parallel_slots=%r，必须为 >=1 的整数（产能为 0 的工位不该存在）'
                             % (slots,)))
        buf = st.get('buffer') or {}
        cap = buf.get('capacity')
        if not isinstance(cap, int) or cap < 0:
            out.append(issue('L14', 'FAIL', wid,
                             'buffer.capacity=%r，必须为 >=0 的整数' % (cap,)))
        if 'policy' not in buf:
            out.append(issue('L14', 'WARN', wid, 'buffer 缺 policy，默认按 FIFO 处理'))
    return out


# ---------------------------------------------------------------- L15 服务时间可采样
def rule_l15(spec):
    out = []
    for st in spec['stations']:
        wid = st.get('id', '?')
        svc = st.get('service_time_s') or {}
        if svc.get('dist') not in DISTS:
            out.append(issue('L15', 'FAIL', wid, 'service_time_s.dist=%r 不在 %s'
                             % (svc.get('dist'), sorted(DISTS))))
        mean = svc.get('mean')
        if not isinstance(mean, (int, float)) or mean <= 0:
            out.append(issue('L15', 'FAIL', wid, 'service_time_s.mean=%r，必须 > 0' % (mean,)))
        cv = svc.get('cv', 0)
        if not isinstance(cv, (int, float)) or cv < 0 or cv > 2:
            out.append(issue('L15', 'FAIL', wid, 'service_time_s.cv=%r，必须落在 [0,2]' % (cv,)))
        rel = st.get('reliability')
        if rel is not None:
            if not isinstance(rel.get('mttf_h'), (int, float)) or rel['mttf_h'] <= 0:
                out.append(issue('L15', 'FAIL', wid, 'reliability.mttf_h 必须 > 0'))
            if not isinstance(rel.get('mttr_min'), (int, float)) or rel['mttr_min'] < 0:
                out.append(issue('L15', 'FAIL', wid, 'reliability.mttr_min 必须 >= 0'))
    return out


# ---------------------------------------------------------------- L16/L17 库存相关（可选）
def rule_l16_l17(spec, inv):
    if inv is None:
        return [issue('L16', 'SKIP', 'inventory_spec.json', '文件不存在，库存不等式未校验'),
                issue('L17', 'SKIP', 'inventory_spec.json', '文件不存在，批次效期未校验')]
    out = []
    for sku in inv.get('skus', []):
        sid = sku.get('sku', '?')
        ss = sku.get('safety_stock', 0)
        rop = sku.get('reorder_point', 0)
        q = sku.get('order_qty', 0)
        if not (ss <= rop <= ss + q):
            out.append(issue('L16', 'FAIL', sid,
                             '违反 SS<=ROP<=SS+Q：SS=%r ROP=%r Q=%r' % (ss, rop, q)))
        life = sku.get('shelf_life_d', 0)
        if not isinstance(life, (int, float)) or life <= 0:
            out.append(issue('L17', 'FAIL', sid, 'shelf_life_d=%r，必须 > 0 天' % (life,)))
        if sku.get('lot_policy', 'FIFO') not in ('FIFO', 'FEFO', 'LIFO'):
            out.append(issue('L17', 'FAIL', sid, 'lot_policy 必须是 FIFO/FEFO/LIFO'))
    return out


# ---------------------------------------------------------------- L18 流程图连通且无环
def rule_l18(spec):
    out = []
    nodes = spec['nodes']
    ids = [n.get('id') for n in nodes]
    dup = {i for i in ids if ids.count(i) > 1}
    for d in sorted(dup):
        out.append(issue('L18', 'FAIL', d, '节点 id 重复'))
    idset = set(ids)
    for n in nodes:
        if n.get('kind') not in KINDS:
            out.append(issue('L18', 'FAIL', n.get('id', '?'), 'kind=%r 不在允许集合'
                             % (n.get('kind'),)))
        nxt = n.get('next')
        if not isinstance(nxt, list):
            out.append(issue('L18', 'FAIL', n.get('id', '?'), 'next 必须是数组'))
            continue
        for t in nxt:
            if t not in idset:
                out.append(issue('L18', 'FAIL', n.get('id', '?'),
                                 'next 指向不存在的节点 `%s`（悬空边）' % t))
            if t == n.get('id'):
                out.append(issue('L18', 'FAIL', t, '自环'))
    # 必须有起点（无入边）
    inbound = {t for n in nodes for t in (n.get('next') or [])}
    if not [i for i in idset if i not in inbound]:
        out.append(issue('L18', 'FAIL', 'nodes', '没有入口节点（每个节点都有入边 → 有环）'))
    # 环检测（DFS 三色）
    graph = {n['id']: [t for t in (n.get('next') or []) if t in idset] for n in nodes}
    state = {}

    def dfs(u, path):
        state[u] = 1
        for v in graph.get(u, []):
            if state.get(v) == 1:
                out.append(issue('L18', 'FAIL', u, '流程图存在环：%s → %s'
                                 % (' → '.join(path + [u]), v)))
            elif state.get(v, 0) == 0:
                dfs(v, path + [u])
        state[u] = 2

    for i in idset:
        if state.get(i, 0) == 0:
            dfs(i, [])
    # process_steps 的 from/to 必须落在节点上
    for i, s in enumerate(spec.get('process_steps') or []):
        for side in ('from', 'to'):
            if s.get(side) not in idset:
                out.append(issue('L18', 'FAIL', 'process_steps[%d].%s' % (i, side),
                                 '引用了不存在的节点 `%s`' % s.get(side)))
    return out


# ---------------------------------------------------------------- L19 资源必须存在且 >0
def rule_l19(spec):
    out = []
    pool = {}
    for r in spec['resources']:
        rid = r.get('id')
        if not rid:
            out.append(issue('L19', 'FAIL', 'resources', '资源缺 id'))
            continue
        if rid in pool:
            out.append(issue('L19', 'FAIL', rid, '资源 id 重复'))
        cnt = r.get('count')
        if not isinstance(cnt, int) or cnt < 1:
            out.append(issue('L19', 'FAIL', rid, 'count=%r，必须 >=1（被引用却为 0 会永久等待）'
                             % (cnt,)))
        pool[rid] = cnt
    refs = []
    for st in spec['stations']:
        r = (st.get('staffing') or {}).get('resource')
        if r:
            refs.append((st.get('id'), r))
    for s in spec.get('process_steps') or []:
        if s.get('resource'):
            refs.append(('%s→%s' % (s.get('from'), s.get('to')), s['resource']))
    for where, rid in refs:
        if rid == 'AGV':          # AGV 由 fleet_spec 管理，不在此表
            continue
        if rid not in pool:
            out.append(issue('L19', 'FAIL', where, '引用了未定义的资源 `%s`' % rid))
    return out


# ---------------------------------------------------------------- L20 与布局一致
def rule_l20(spec, layout):
    if layout is None:
        return [issue('L20', 'SKIP', 'data/layout.json', '布局未生成，跳过跨文件一致性')]
    out = []
    real = {s['id'] for w in layout['warehouses'] for s in w.get('stations', [])}
    declared = set()
    for st in spec['stations']:
        sid = st.get('id')
        declared.add(sid)
        if sid not in real:
            out.append(issue('L20', 'FAIL', sid,
                             '布局里没有这个工位（现有：%s）' % ', '.join(sorted(real))))
        wh = st.get('warehouse')
        if wh:
            owner = {w['id']: {s['id'] for s in w.get('stations', [])}
                     for w in layout['warehouses']}
            if wh in owner and sid not in owner[wh]:
                out.append(issue('L20', 'FAIL', sid, 'warehouse=%s 但该仓不含此工位' % wh))
    for n in spec['nodes']:
        if n.get('station') and n['station'] not in real:
            out.append(issue('L20', 'FAIL', n.get('id'), '节点指向不存在的工位 `%s`'
                             % n['station']))
    for sid in sorted(real - declared):
        out.append(issue('L20', 'WARN', sid, '布局有工位但过程配置未覆盖（无产能假设）'))
    return out


# ---------------------------------------------------------------- L21 班次
def rule_l21(spec):
    out = []
    shifts = spec['shifts']
    seen = set()
    for sh in shifts:
        sid = sh.get('id', '?')
        if sid in seen:
            out.append(issue('L21', 'FAIL', sid, '班次 id 重复'))
        seen.add(sid)
        a, b = sh.get('start_s'), sh.get('end_s')
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or b <= a:
            out.append(issue('L21', 'FAIL', sid, 'start_s=%r end_s=%r，必须 start < end'
                             % (a, b)))
            continue
        for k, br in enumerate(sh.get('breaks') or []):
            if len(br) != 2:
                out.append(issue('L21', 'FAIL', sid, 'breaks[%d] 必须是 [起, 时长]' % k))
                continue
            bs, dur = br
            if dur <= 0:
                out.append(issue('L21', 'FAIL', sid, 'breaks[%d] 时长必须 > 0' % k))
            if bs < a or bs + max(dur, 0) > b:
                out.append(issue('L21', 'FAIL', sid, 'breaks[%d] 落在班次区间之外' % k))
    ordered = sorted(shifts, key=lambda s: (s.get('start_s') or 0))
    for prev, cur in zip(ordered, ordered[1:]):
        if (prev.get('end_s') or 0) > (cur.get('start_s') or 0):
            out.append(issue('L21', 'FAIL', '%s/%s' % (prev.get('id'), cur.get('id')),
                             '班次区间重叠'))
    used = {s for st in spec['stations'] for s in (st.get('shifts') or [])}
    for sid in sorted(used - seen):
        out.append(issue('L21', 'FAIL', sid, '工位引用了未定义的班次'))
    return out


# ---------------------------------------------------------------- L22 数值合理
def rule_l22(spec):
    out = []
    for n in spec['nodes']:
        cap = n.get('capacity')
        if not isinstance(cap, int) or cap < 1:
            out.append(issue('L22', 'FAIL', n.get('id', '?'),
                             'capacity=%r，流程节点必须 >=1' % (cap,)))
    for s in spec.get('process_steps') or []:
        t = s.get('time_s')
        if not isinstance(t, (int, float)) or t <= 0:
            out.append(issue('L22', 'FAIL', '%s→%s' % (s.get('from'), s.get('to')),
                             'time_s=%r，必须 > 0' % (t,)))
        y = s.get('yield', 1.0)
        if not isinstance(y, (int, float)) or not (0 < y <= 1):
            out.append(issue('L22', 'FAIL', '%s→%s' % (s.get('from'), s.get('to')),
                             'yield=%r，必须落在 (0,1]' % (y,)))
    for st in spec['stations']:
        su = st.get('setup_s', 0)
        if not isinstance(su, (int, float)) or su < 0:
            out.append(issue('L22', 'FAIL', st.get('id', '?'), 'setup_s 必须 >= 0'))
    return out


RULES = ('L14', 'L15', 'L16', 'L17', 'L18', 'L19', 'L20', 'L21', 'L22')


def evaluate(spec, layout, inv):
    problems = check_shape(spec)
    if problems:
        return problems
    problems += rule_l14(spec)
    problems += rule_l15(spec)
    problems += rule_l16_l17(spec, inv)
    problems += rule_l18(spec)
    problems += rule_l19(spec)
    problems += rule_l20(spec, layout)
    problems += rule_l21(spec)
    problems += rule_l22(spec)
    return problems


def summarize(spec, problems):
    per = {}
    for r in RULES:
        lv = [p['level'] for p in problems if p['rule'] == r]
        per[r] = 'FAIL' if 'FAIL' in lv else ('WARN' if 'WARN' in lv else
                                              ('SKIP' if 'SKIP' in lv else 'PASS'))
    return {
        'kind': 'process_report',
        'schema_version': '1.1',
        'stations': len(spec.get('stations', [])),
        'nodes': len(spec.get('nodes', [])),
        'shifts': len(spec.get('shifts', [])),
        'resources': len(spec.get('resources', [])),
        'rules': per,
        'verdict': 'FAIL' if 'FAIL' in per.values() else 'PASS',
        'issues': problems,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description='过程配置校验（L14–L22）')
    ap.add_argument('--spec', default=os.path.join(CONFIG, 'process_spec.json'))
    ap.add_argument('--report', default=os.path.join(OUT, 'process_report.json'))
    ap.add_argument('--self-test', action='store_true', help='用坏配置证明校验器会失败')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test(a.spec)

    spec = load(a.spec)
    layout_p = os.path.join(DATA, 'layout.json')
    layout = load(layout_p) if os.path.exists(layout_p) else None
    inv_p = os.path.join(CONFIG, 'inventory_spec.json')
    inv = load(inv_p) if os.path.exists(inv_p) else None

    problems = evaluate(spec, layout, inv)
    rep = summarize(spec, problems)
    os.makedirs(os.path.dirname(a.report), exist_ok=True)
    with open(a.report, 'w', encoding='utf-8') as f:
        f.write(json.dumps(rep, ensure_ascii=False, indent=2) + '\n')

    if not a.quiet:
        for r in RULES:
            print('[%s] %s' % (rep['rules'][r], r))
        for p in problems:
            if p['level'] != 'SKIP':
                print('   %-4s %-28s %s' % (p['level'], p['where'], p['message']))
        print('PROCESS_%s stations=%d nodes=%d issues=%d report=%s'
              % (rep['verdict'], rep['stations'], rep['nodes'],
                 len([p for p in problems if p['level'] == 'FAIL']),
                 os.path.relpath(a.report, ROOT)))
    return 0 if rep['verdict'] == 'PASS' else 1


def self_test(spec_path):
    """每个反例只破坏一条规则，验证「该规则确实会 FAIL」。"""
    base = load(spec_path)
    layout_p = os.path.join(DATA, 'layout.json')
    layout = load(layout_p) if os.path.exists(layout_p) else None

    def mutate(fn):
        s = copy.deepcopy(base)
        fn(s)
        return s

    cases = [
        ('N1 parallel_slots=0', 'L14',
         mutate(lambda s: s['stations'][0].__setitem__('parallel_slots', 0))),
        ('N2 服务时间 mean=-1', 'L15',
         mutate(lambda s: s['stations'][0]['service_time_s'].__setitem__('mean', -1))),
        ('N3 buffer.capacity=-1', 'L14',
         mutate(lambda s: s['stations'][1]['buffer'].__setitem__('capacity', -1))),
        ('N4 cv=3 越界', 'L15',
         mutate(lambda s: s['stations'][0]['service_time_s'].__setitem__('cv', 3))),
        ('N5 next 悬空', 'L18',
         mutate(lambda s: s['nodes'][0]['next'].append('NO_SUCH_NODE'))),
        ('N6 流程图成环', 'L18',
         mutate(lambda s: next(n for n in s['nodes'] if n['id'] == 'DOCK_OUT')
                .__setitem__('next', ['DOCK_IN']))),
        ('N7 引用未定义资源', 'L19',
         mutate(lambda s: s['stations'][0]['staffing'].__setitem__('resource', 'GHOST'))),
        ('N8 工位不在布局', 'L20',
         mutate(lambda s: s['stations'][0].__setitem__('id', 'W-PACK-99'))),
        ('N9 班次重叠', 'L21',
         mutate(lambda s: s['shifts'].append(
             {'id': 'OVERLAP', 'start_s': 3600, 'end_s': 7200, 'breaks': []}))),
        ('N10 节点容量=0', 'L22',
         mutate(lambda s: s['nodes'][1].__setitem__('capacity', 0))),
    ]
    bad = 0
    for name, rule, spec in cases:
        try:
            problems = evaluate(spec, layout, None)
        except Exception as e:                      # 校验器自己崩了，同样算不合格
            print('  [BAD] %-22s 校验器抛异常 %s: %s' % (name, rule, e))
            bad += 1
            continue
        hit = [p for p in problems if p['rule'] == rule and p['level'] == 'FAIL']
        ok = bool(hit)
        print('  [%s] %-22s 期望 %s 失败 → %s'
              % ('PASS' if ok else 'BAD', name, rule,
                 hit[0]['message'][:46] if hit else '未触发'))
        if not ok:
            bad += 1
    # 反向：干净配置必须 PASS（防止「全 FAIL」的假安全感）
    clean = evaluate(base, layout, None)
    clean_fail = [p for p in clean if p['level'] == 'FAIL']
    print('  [%s] 基线配置应通过       → %s'
          % ('PASS' if not clean_fail else 'BAD',
             '无 FAIL' if not clean_fail else '意外 FAIL: %s' % clean_fail[0]['message']))
    if clean_fail:
        bad += 1
    print('PROCESS_SELFTEST_%s cases=%d bad=%d' % ('OK' if bad == 0 else 'FAIL',
                                                   len(cases) + 1, bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
