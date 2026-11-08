"""run_all_negative.py — 顺序执行 4 个负测试脚本（DESIGN_PLAN A37）。

与 validate.py 同构的单一入口，避免 run_all.ps1 里用 `python -c` 传超长参数
在 PowerShell 5.1 下出现漏步。任一脚本非零退出即整体失败。

运行：python tests/run_all_negative.py
"""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPTS = [
    'test_spec_layout.py',       # 6 个 spec 负测试
    'test_scenario.py',          # 4 个场景/适配器负测试
    'test_cleanroom.py',         # 全新空目录复现
    'test_validator_can_fail.py',  # 7 个主链负测试
]


def main():
    for name in SCRIPTS:
        path = os.path.join(ROOT, 'tests', name)
        print('NEGATIVE_SUITE >>', name, flush=True)
        r = subprocess.run([sys.executable, path], cwd=ROOT)
        if r.returncode != 0:
            print('NEGATIVE_SUITE FAIL', name, 'exit', r.returncode)
            sys.exit(1)
    print('NEGATIVE_SUITE PASS', len(SCRIPTS), 'scripts')
    sys.exit(0)


if __name__ == '__main__':
    main()
