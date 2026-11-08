"""scenario_run.py — 场景化运行（DESIGN_PLAN §7 / §9 Step 5）。

流程：读 scenario → 解析四层配置 → spec_layout 展开布局并校验（**不写主 data/**）
→ demand_source 生成订单 → kpi 计算 → 断言 → runs/<id>/<timestamp>/。

场景层是**解析 what-if 试算**（M/M/c 排队近似），不是逐车 A* 仿真；
逐车路径与碰撞仍由主链 plan_paths / traffic_check 验证，并在 KPI 里标注来源。
每个场景至少有一条可能失败的断言；断言不满足 → 退出 1。

运行：
  python scripts/scenario_run.py --scenario config/scenarios/baseline.json
  python scripts/scenario_run.py --all
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
import spec_layout
import demand_source
import kpi

CONFIG = os.path.join(ROOT, 'config')
RUNS = os.path.join(ROOT, 'runs')
OUT = os.path.join(ROOT, 'out')


def jload(p):
    return json.load(open(p, encoding='utf-8'))


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def resolve(scenario_path, key):
    p = scenario_path[key]
    return os.path.normpath(os.path.join(os.path.dirname(scenario_path['__path']), p))


def baseline_metric(expr):
    """解析 '1.5*baseline:throughput_orders_per_h' 这类跨场景断言。"""
    factor, metric = expr.split('*baseline:', 1)
    dirs = sorted(glob.glob(os.path.join(RUNS, 'baseline', '*')))
    if not dirs:
        raise SystemExit('BASELINE_RUN_NOT_FOUND: 先跑 baseline 场景')
    k = jload(os.path.join(dirs[-1], 'kpi.json'))
    return float(factor) * k['metrics'][metric]['value']


OPS = {
    '==': lambda a, b: abs(a - b) < 1e-9 if isinstance(a, (int, float)) else a == b,
    '!=': lambda a, b: a != b,
    '>=': lambda a, b: a >= b - 1e-9,
    '<=': lambda a, b: a <= b + 1e-9,
    '>': lambda a, b: a > b,
    '<': lambda a, b: a < b,
}


def run_one(scenario_file, verbose=True):
    t0 = time.time()
    sc = jload(scenario_file)
    sc['__path'] = scenario_file
    spec_path = resolve(sc, 'layout_spec')
    fleet_path = resolve(sc, 'fleet_spec')
    demand_path = resolve(sc, 'demand_spec')
    spec = jload(spec_path)
    fleet = jload(fleet_path)
    demand = jload(demand_path)

    # 覆盖（点号路径）
    for key, val in sc.get('overrides', {}).items():
        if key == 'demand.orders.per_hour':
            demand['orders']['per_hour'] = val
        elif key in ('fleet.size', 'fleet.deployment'):
            pass    # 由 kpi 直接读 overrides
    if sc.get('seed') is not None:
        spec['site']['seed'] = sc['seed']

    schema_issues = spec_layout.check_schema(spec)
    if schema_issues:
        raise SystemExit('SCENARIO_FAIL schema: %s' % schema_issues[:3])
    layout = spec_layout.expand(spec)
    issues, warnings, mutex, R = spec_layout.validate(layout, spec)

    src = demand_source.load_source(demand, layout, sc['seed'])
    events = src.generate(sc['simulation']['duration_s'],
                          demand['orders']['per_hour'])
    problems = demand_source.validate_events(events, layout)
    if problems:
        raise SystemExit('SCENARIO_FAIL event refs: %s' % problems[:3])

    wall = time.time() - t0
    kp = kpi.compute_kpi(sc, layout, events, fleet, demand,
                         spec_issues=len(issues), wall_clock_s=wall)
    metrics = {k: v['value'] for k, v in kp['metrics'].items()}

    assertions = []
    for a in sc['assertions']:
        expected = a['value']
        if isinstance(expected, str) and '*baseline:' in expected:
            expected = baseline_metric(expected)
        actual = metrics.get(a['metric'])
        ok = actual is not None and OPS[a['op']](actual, expected)
        assertions.append({'metric': a['metric'], 'op': a['op'],
                           'expected': round(expected, 6) if isinstance(expected, float) else expected,
                           'actual': actual, 'status': 'PASS' if ok else 'FAIL'})
    verdict = 'PASS' if all(x['status'] == 'PASS' for x in assertions) and not issues else 'FAIL'

    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    run_dir = os.path.join(RUNS, sc['id'], ts)
    os.makedirs(run_dir, exist_ok=True)
    manifest = {
        'scenario': sc['id'], 'seed': sc['seed'],
        'started_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'duration_s': round(time.time() - t0, 3),
        'inputs': {k: {'path': os.path.relpath(p, ROOT).replace('\\', '/'),
                       'sha256': sha(p)}
                   for k, p in (('layout_spec', spec_path), ('fleet_spec', fleet_path),
                                ('demand_spec', demand_path), ('scenario', scenario_file))},
        'versions': {'python': sys.version.split()[0],
                     'blender': jload(os.path.join(OUT, 'build_report.json')).get('blender', 'n/a')},
        'git_commit': subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                                     cwd=ROOT, capture_output=True, text=True).stdout.strip(),
        'layout_issues': len(issues), 'layout_warnings': len(warnings),
        'mutex_pairs': len(mutex), 'agv_radius_m': R,
        'verdict': verdict,
    }
    json.dump(sc, open(os.path.join(run_dir, 'scenario.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    json.dump(layout, open(os.path.join(run_dir, 'layout.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    with open(os.path.join(run_dir, 'events.jsonl'), 'w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    json.dump(kp, open(os.path.join(run_dir, 'kpi.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    json.dump({'scenario': sc['id'], 'verdict': verdict, 'assertions': assertions},
              open(os.path.join(run_dir, 'assertions.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    json.dump(manifest, open(os.path.join(run_dir, 'manifest.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    lines = [
        '# %s · 场景报告（自动生成，勿手改）' % sc['id'], '',
        '> 由 `scripts/scenario_run.py` 从本目录 JSON 生成。'
        '主链结论请看 `out/CURRENT_STATUS.md`。', '',
        '| 项 | 值 |', '|---|---|',
        '| 断言结论 | %s |' % verdict,
        '| 订单数 | %s |' % len(events),
        '| 仓库 / AGV | %s / %s |' % (len(layout['warehouses']), layout['summary']['robots']),
        '| 布局 FAIL / WARN | %s / %s |' % (len(issues), len(warnings)),
        '| AP 互斥对 | %s |' % len(mutex),
        '| 运行时长 | %s s |' % manifest['duration_s'], '',
        '## 断言', '', '| 指标 | 判据 | 期望 | 实际 | 结果 |', '|---|---|---|---|---|',
    ]
    lines += ['| %s | %s %s | %s | %s | %s |' %
              (a['metric'], a['op'], a['expected'], a['expected'], a['actual'], a['status'])
              for a in assertions]
    lines += ['', '## KPI（每个指标的定义/公式/来源见 kpi.json）', '',
              '| KPI | 值 | 单位 | 来源 |', '|---|---|---|---|']
    lines += ['| %s | %s | %s | %s |' % (k, v['value'], v['unit'], v['source'])
              for k, v in kp['metrics'].items()]
    lines += ['', '## 边界', '',
              '- 场景层是 M/M/c 解析 what-if，**不是逐车 A\\* 仿真**；'
              '碰撞/路径以主链 `out/traffic_report.json`、`out/path_metrics.json` 为准。',
              '- 需求为合成（Zipf + 泊松到达），不是企业真实订单。',
              '- AP 互斥 %s 对由主链全局中心距判据隐含约束；'
              '若要 0 互斥需加宽通道。' % len(mutex), '']
    open(os.path.join(run_dir, 'report.md'), 'w', encoding='utf-8').write('\n'.join(lines))

    if verbose:
        print('SCENARIO %s %s orders=%s throughput=%s on_time=%s '
              'max_tardiness=%s strict_collisions=%s layout_issues=%s'
              % (sc['id'], verdict, len(events), metrics.get('throughput_orders_per_h'),
                 metrics.get('on_time_rate'), metrics.get('max_tardiness_s'),
                 metrics.get('strict_collisions'), len(issues)))
        for a in assertions:
            if a['status'] != 'PASS':
                print('  ASSERT_FAIL', a)
        print('wrote', os.path.relpath(run_dir, ROOT).replace('\\', '/'))
    return verdict == 'PASS', run_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', default=os.path.join(CONFIG, 'scenarios', 'baseline.json'))
    ap.add_argument('--all', action='store_true')
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(CONFIG, 'scenarios', '*.json'))) if args.all \
        else [args.scenario]
    # baseline 先跑，供跨场景断言引用
    files.sort(key=lambda p: 0 if 'baseline' in os.path.basename(p) else 1)
    ok = True
    for f in files:
        passed, _ = run_one(f)
        ok = ok and passed
    print('SCENARIO_SUMMARY', 'PASS' if ok else 'FAIL', len(files), 'scenarios')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
