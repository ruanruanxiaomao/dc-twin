"""负测试：故意破坏 7 处，断言校验器**会失败**（退出码 != 0）。

依据 AUDIT_REPORT.md §3.6(4) / §8 A13。不能失败的验证器等于没有验证器。
每个用例都在 try/finally 中备份并还原真实文件；结束后重建被测试改写的报告。

运行：python tests/test_validator_can_fail.py
产出：out/negative_tests.json
"""
import json
import os
import shutil
import subprocess
import sys

from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA, OUT = os.path.join(ROOT, 'data'), os.path.join(ROOT, 'out')
PY = sys.executable
CASES = []


def run(script):
    return subprocess.run([PY, script], cwd=ROOT,
                          capture_output=True, text=True).returncode


def rebuild_reports():
    for s in ('scripts/layout_audit.py', 'scripts/traffic_check.py',
              'scripts/parse_artifacts.py'):
        run(s)


def case(name, path, break_fn, check_script, pre=None):
    """返回 True 表示校验器确实报错（期望行为）。"""
    bak = path + '.neg_bak'
    shutil.copy2(path, bak)
    try:
        break_fn(path)
        if pre:
            run(pre)
        rc = run(check_script)
        ok = rc != 0
        CASES.append({'name': name, 'check': check_script,
                      'exit_code': rc, 'expected': 'nonzero', 'ok': ok})
        return ok
    finally:
        shutil.copy2(bak, path)
        os.remove(bak)
        rebuild_reports()


def break_layout(path):
    d = json.load(open(path, encoding='utf-8'))
    d['warehouses'][0]['racks'][0]['position'][0] += 1.6  # 挤进相邻货架
    json.dump(d, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


def break_traffic(path):
    rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    other = [p for p in rows if p['agent_id'] == 'F1-R02']
    pos = {p['t']: (p['x'], p['y']) for p in other}
    for p in rows:
        if p['agent_id'] == 'F1-R01' and p['t'] in pos:
            p['x'], p['y'] = pos[p['t']]
    with open(path, 'w', encoding='utf-8') as f:
        for p in rows:
            f.write(json.dumps(p, ensure_ascii=False) + '\n')


def break_png(path):
    Image.new('RGB', (100, 100), 'white').save(path)


def break_glb(path):
    d = open(path, 'rb').read()
    open(path, 'wb').write(d[:10000])


def hide(path):
    # case() 已备份到 .neg_bak，直接删除即可，避免残留 .hidden 文件
    os.remove(path)


def restore_hidden(path):
    hidden = path + '.hidden'
    if os.path.exists(hidden):
        if os.path.exists(path):
            os.remove(hidden)      # 清理早期版本留下的残件
        else:
            os.rename(hidden, path)


def break_metrics(path):
    d = json.load(open(path, encoding='utf-8'))
    d['samples'] = 777
    json.dump(d, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


def main():
    layout = os.path.join(DATA, 'layout.json')
    paths = os.path.join(DATA, 'paths.jsonl')
    metrics = os.path.join(DATA, 'path_metrics.json')
    png08 = os.path.join(OUT, 'renders', '08_corridor_plan_zoom.png')
    glb = os.path.join(OUT, 'dc_preview.glb')
    lic = os.path.join(ROOT, 'LICENSE')
    notice = os.path.join(ROOT, 'THIRD_PARTY_NOTICES.md')

    case('1 货架平移到相邻货架 -> layout_audit 必须失败',
         layout, break_layout, 'scripts/layout_audit.py')
    case('2 两台车轨迹重合 -> traffic_check 必须失败',
         paths, break_traffic, 'scripts/traffic_check.py')
    case('3 08 图换成小尺寸空白 PNG -> validate 必须失败',
         png08, break_png, 'scripts/validate.py', pre='scripts/parse_artifacts.py')
    case('4 删除 LICENSE -> validate 必须失败',
         lic, hide, 'scripts/validate.py')
    restore_hidden(lic)
    case('5 path_metrics.samples 改成 777 -> validate 必须失败',
         metrics, break_metrics, 'scripts/validate.py')
    case('6 GLB 截断到 10 KB -> validate 必须失败',
         glb, break_glb, 'scripts/validate.py', pre='scripts/parse_artifacts.py')
    case('7 删除 THIRD_PARTY_NOTICES.md -> validate 必须失败',
         notice, hide, 'scripts/validate.py')
    restore_hidden(notice)

    rebuild_reports()
    # 最后一个用例是「删掉 NOTICE 后跑 validate」，它会在 validate_report.json
    # 里留下 FAIL 记录。必须在全部文件恢复后再跑一次干净的 validate，
    # 否则后续 report_status.py 会把「19/21」当成当前验收结论写进状态表。
    run('scripts/validate.py')
    passed = sum(1 for c in CASES if c['ok'])
    out = {'total': len(CASES), 'passed': passed, 'cases': CASES,
           'verdict': 'PASS' if passed == len(CASES) else 'FAIL'}
    with open(os.path.join(OUT, 'negative_tests.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    for c in CASES:
        print('[%s] %s (exit=%s)' %
              ('OK' if c['ok'] else 'BROKEN', c['name'], c['exit_code']))
    print('NEGATIVE_TESTS', passed, '/', len(CASES))
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == '__main__':
    main()
