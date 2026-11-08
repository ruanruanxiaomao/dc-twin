"""test_spec_layout.py — 证明 spec_layout.py 的校验器**会失败**。

DESIGN_PLAN.md §9 Step 4 / §15.2 最小负测试清单（前 5 条 + L10 WARN）。
每个用例：复制 baseline spec 到临时文件 → 破坏 → 跑 spec_layout.py
（隔离 --out/--report，绝不写 data/layout.json）→ 断言退出码 / 警告 → 删除临时文件。

运行：python tests/test_spec_layout.py    期望 6/6 PASS
"""
import copy
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SPEC = os.path.join(ROOT, 'config', 'layout_spec.json')
TMP = os.path.join(ROOT, 'tests', '_tmp_spec')
PY = sys.executable
CASES = []


def run(spec_path, tag):
    out = os.path.join(TMP, tag + '_layout.json')
    rep = os.path.join(TMP, tag + '_report.json')
    p = subprocess.run([PY, os.path.join(ROOT, 'scripts', 'spec_layout.py'),
                        '--spec', spec_path, '--out', out, '--report', rep, '--quiet'],
                       cwd=ROOT, capture_output=True, text=True)
    report = json.load(open(rep, encoding='utf-8')) if os.path.exists(rep) else {}
    return p.returncode, report, p.stdout


def case(name, mutate, expect, expect_rule=None):
    os.makedirs(TMP, exist_ok=True)
    base = json.load(open(SPEC, encoding='utf-8'))
    spec = mutate(copy.deepcopy(base))
    path = os.path.join(TMP, 'broken_%d.json' % (len(CASES) + 1))
    json.dump(spec, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    rc, rep, stdout = run(path, 'c%d' % (len(CASES) + 1))
    if expect == 'FAIL':
        ok = rc != 0 and rep.get('verdict') == 'FAIL'
    else:  # WARN：退出 0，但指定规则必须出现在 warnings 里
        rules = [w['rule'] for w in rep.get('warnings', [])]
        ok = rc == 0 and (expect_rule in rules if expect_rule else True)
    detail = '%s, rc=%s, rules=%s' % (
        rep.get('verdict', 'n/a'), rc,
        sorted({i['rule'] for i in rep.get('issues', [])} |
               {w['rule'] for w in rep.get('warnings', [])}))
    CASES.append({'name': name, 'expect': expect, 'ok': ok,
                  'exit_code': rc, 'detail': detail})
    os.remove(path)
    return ok


def narrow_aisle(s):
    s['warehouses'][0]['racks'][0]['aisle_width'] = 1.0
    return s


def station_on_rack(s):
    s['warehouses'][0]['stations'][0]['rect'] = [6, 13, 18, 17]
    return s


def spawn_in_rack(s):
    s['warehouses'][0]['spawns']['points'][0] = [7.5, 13.6]
    return s


def bad_levels(s):
    s['warehouses'][0]['racks'][0]['rack']['levels'] = 10
    return s


def narrow_door(s):
    s['warehouses'][0]['docks']['door_width'] = 1.0
    return s


def bad_schema(s):
    s['schema_version'] = '9.9'
    return s


def main():
    case('1 aisle_width=1.0 -> L8 FAIL', narrow_aisle, 'FAIL')
    case('2 station.rect 压货架 -> L3 FAIL', station_on_rack, 'FAIL')
    case('3 spawn 在货架里 -> L7 FAIL', spawn_in_rack, 'FAIL')
    case('4 levels=10 / top_height=6 -> L9 FAIL', bad_levels, 'FAIL')
    case('5 door_width=1.0 -> L10 WARN', narrow_door, 'WARN', 'L10')
    case('6 schema_version=9.9 -> L1 FAIL', bad_schema, 'FAIL')

    # 测试结束后用干净 spec 重建主报告，避免测试残留
    subprocess.run([PY, os.path.join(ROOT, 'scripts', 'spec_layout.py'),
                    '--spec', SPEC, '--quiet'], cwd=ROOT, capture_output=True)
    passed = sum(1 for c in CASES if c['ok'])
    out = {'total': len(CASES), 'passed': passed, 'cases': CASES,
           'verdict': 'PASS' if passed == len(CASES) else 'FAIL'}
    os.makedirs(os.path.join(ROOT, 'out'), exist_ok=True)
    json.dump(out, open(os.path.join(ROOT, 'out', 'spec_negative_tests.json'),
                        'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    for c in CASES:
        print('[%s] %s (exit=%s, %s)' %
              ('OK' if c['ok'] else 'BROKEN', c['name'], c['exit_code'], c['detail']))
    print('SPEC_NEGATIVE_TESTS', passed, '/', len(CASES))
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == '__main__':
    main()
