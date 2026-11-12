"""test_cleanroom.py — 全新空目录复现数据层（DESIGN_PLAN A40 / §9 Step 10）。

在临时目录只放 `scripts/*.py` + `data/config.json` + `config/**`，
从零跑完数据链；关键 JSON 与主工程逐字节（忽略 CRLF）一致才算 PASS。
不依赖 .venv，不写主工程任何文件。

运行：python tests/test_cleanroom.py   产出：out/cleanroom_report.json
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PY = sys.executable
STEPS = ['gen_data', 'layout_audit', 'nav_grid', 'optimize_slotting', 'gen_inventory',
         'gen_tasks', 'plan_paths', 'gen_cargo', 'traffic_check', 'check_geometry']
COMPARE = ['data/layout.json', 'data/skus.json', 'data/events.jsonl',
           'data/tasks.json', 'data/paths.jsonl', 'data/cargo_state.json',
           'data/sku_master.json', 'data/inventory_slotting.json',
           'data/inventory_initial.json',
           'out/traffic_report.json', 'out/geometry_report.json']


def norm(b):
    return b.replace(b'\r\n', b'\n')


def main():
    tmp = tempfile.mkdtemp(prefix='dc_cleanroom_')
    try:
        shutil.copytree(os.path.join(ROOT, 'scripts'), os.path.join(tmp, 'scripts'))
        shutil.copytree(os.path.join(ROOT, 'config'), os.path.join(tmp, 'config'))
        os.makedirs(os.path.join(tmp, 'data'))
        os.makedirs(os.path.join(tmp, 'out'))
        shutil.copy2(os.path.join(ROOT, 'data', 'config.json'),
                     os.path.join(tmp, 'data', 'config.json'))
        steps = []
        for s in STEPS:
            r = subprocess.run([PY, os.path.join('scripts', s + '.py')],
                               cwd=tmp, capture_output=True, text=True)
            steps.append({'step': s, 'exit_code': r.returncode,
                          'stdout_tail': r.stdout.strip().splitlines()[-1][:160]
                          if r.stdout.strip() else ''})
            if r.returncode != 0:
                break
        results = {}
        for f in COMPARE:
            a = os.path.join(ROOT, f)
            b = os.path.join(tmp, f)
            if not (os.path.exists(a) and os.path.exists(b)):
                results[f] = {'identical': False, 'reason': 'missing file'}
                continue
            na, nb = norm(open(a, 'rb').read()), norm(open(b, 'rb').read())
            results[f] = {'identical': na == nb,
                          'sha256': hashlib.sha256(na).hexdigest()[:16],
                          'bytes': len(na)}
        ok_steps = all(s['exit_code'] == 0 for s in steps)
        ok_files = all(v['identical'] for v in results.values())
        report = {'verdict': 'PASS' if ok_steps and ok_files else 'FAIL',
                  'steps': steps, 'files': results,
                  'steps_total': len(STEPS), 'steps_run': len(steps),
                  'note': 'pure-python data layer only; Blender rendering is '
                          'verified by run_all.ps1 on the main workspace'}
        json.dump(report, open(os.path.join(ROOT, 'out', 'cleanroom_report.json'),
                               'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print('CLEANROOM', report['verdict'], 'steps', len(steps), '/', len(STEPS),
              'identical', sum(1 for v in results.values() if v['identical']),
              '/', len(results))
        sys.exit(0 if report['verdict'] == 'PASS' else 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
