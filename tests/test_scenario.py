"""test_scenario.py — 证明场景断言与数据适配器**会失败**（DESIGN_PLAN §15.2 / A37）。

4 个用例，每个都期望非零退出或结果变化；结束后重建干净产物。
运行：python tests/test_scenario.py   产出：out/scenario_negative_tests.json
"""
import copy
import csv
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TMP = os.path.join(ROOT, 'tests', '_tmp_scenario')
PY = sys.executable
CASES = []


def run(args):
    return subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True)


def record(name, ok, rc, detail=''):
    CASES.append({'name': name, 'ok': ok, 'exit_code': rc, 'detail': detail})


def main():
    os.makedirs(TMP, exist_ok=True)
    # 1) 断言改成不可能满足的 1.01 → 场景必须 FAIL
    sc = json.load(open(os.path.join(ROOT, 'config', 'scenarios', 'baseline.json'),
                        encoding='utf-8'))
    bad = copy.deepcopy(sc)
    bad['id'] = 'baseline_neg'
    # 临时场景文件放在 tests/_tmp_scenario/，原相对路径会解析到错误目录；
    # 显式改成绝对路径，确保这个用例真正是因为「断言不满足」而 FAIL。
    for key, name in (('layout_spec', 'layout_spec.json'),
                      ('fleet_spec', 'fleet_spec.json'),
                      ('demand_spec', 'demand_spec.json')):
        bad[key] = os.path.join(ROOT, 'config', name)
    bad['assertions'] = [{'metric': 'on_time_rate', 'op': '>=', 'value': 1.01}]
    p1 = os.path.join(TMP, 'baseline_neg.json')
    json.dump(bad, open(p1, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    r = run(['scripts/scenario_run.py', '--scenario', p1])
    record('1 on_time_rate 断言 1.01 -> 场景 FAIL', r.returncode != 0, r.returncode,
           'SCENARIO baseline_neg FAIL')

    # 2) CSV 缺列 → 适配器必须 FAIL
    badcsv = os.path.join(TMP, 'missing_column.csv')
    with open(badcsv, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['order_id', 'sku'])
        w.writeheader()
        w.writerow({'order_id': 'X1', 'sku': 'SKU-0001'})
    r = run(['scripts/adapters/csv_orders.py', '--csv', badcsv,
             '--out', os.path.join(TMP, 'bad_events.jsonl')])
    record('2 CSV 缺列 -> 适配器 FAIL', r.returncode != 0, r.returncode,
           'CSV_MISSING_COLUMNS')

    # 3) 改 seed → 生成的订单必须变化
    sys.path.insert(0, os.path.join(ROOT, 'scripts'))
    import demand_source
    layout = json.load(open(os.path.join(ROOT, 'data', 'layout.json'), encoding='utf-8'))
    demand = json.load(open(os.path.join(ROOT, 'config', 'demand_spec.json'), encoding='utf-8'))
    a = demand_source.SyntheticSource(demand, layout, 20261106).generate(3600)
    b = demand_source.SyntheticSource(demand, layout, 20261107).generate(3600)
    ha = json.dumps(a, ensure_ascii=False, sort_keys=True)
    hb = json.dumps(b, ensure_ascii=False, sort_keys=True)
    record('3 改 seed -> 订单不同', ha != hb, 0, 'same_seed_hash_differs=%s' % (ha != hb))

    # 4) 场景引用坏 spec → scenario_run 必须 FAIL
    badspec = copy.deepcopy(json.load(open(os.path.join(ROOT, 'config', 'layout_spec.json'),
                                          encoding='utf-8')))
    badspec['warehouses'] = []
    p4 = os.path.join(TMP, 'bad_layout_spec.json')
    json.dump(badspec, open(p4, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    sc4 = copy.deepcopy(sc)
    sc4['id'] = 'bad_spec_neg'
    for key, name in (('fleet_spec', 'fleet_spec.json'),
                      ('demand_spec', 'demand_spec.json')):
        sc4[key] = os.path.join(ROOT, 'config', name)
    sc4['layout_spec'] = p4   # 绝对路径：故意指向空 warehouses 的坏 spec
    p4s = os.path.join(TMP, 'bad_spec_scenario.json')
    json.dump(sc4, open(p4s, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    r = run(['scripts/scenario_run.py', '--scenario', p4s])
    record('4 空 warehouses spec -> 场景 FAIL', r.returncode != 0, r.returncode,
           'SCENARIO_FAIL schema')

    shutil.rmtree(TMP, ignore_errors=True)
    # 清理负测试留下的 runs/baseline_neg 目录，避免污染 baseline 引用
    shutil.rmtree(os.path.join(ROOT, 'runs', 'baseline_neg'), ignore_errors=True)
    passed = sum(1 for c in CASES if c['ok'])
    out = {'total': len(CASES), 'passed': passed, 'cases': CASES,
           'verdict': 'PASS' if passed == len(CASES) else 'FAIL'}
    json.dump(out, open(os.path.join(ROOT, 'out', 'scenario_negative_tests.json'),
                        'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    for c in CASES:
        print('[%s] %s (exit=%s)' % ('OK' if c['ok'] else 'BROKEN', c['name'], c['exit_code']))
    print('SCENARIO_NEGATIVE_TESTS', passed, '/', len(CASES))
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == '__main__':
    main()
