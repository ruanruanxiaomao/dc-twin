"""run_game_tests.py —— 游戏层测试的单一入口（供 `dc run --scope game` 调用）。

为什么单独一个脚本：`dc run` 的每一步都是"一个可执行脚本 + 参数"，这样任意一步
失败都能定位到具体命令。用 `pytest -k` 拼参数会随测试增删而漂移，所以把"跑哪些
文件"固定在这里（新增游戏测试时只改这一处）。

运行：python tests/run_game_tests.py
"""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
FILES = [
    'tests/unit/test_snapshot_primitives.py',
    'tests/unit/test_commands.py',
    'tests/unit/test_scenario_policy_overrides.py',
    'tests/integration/test_economy.py',
    'tests/integration/test_levels.py',
    'tests/integration/test_save_roundtrip.py',
    'tests/integration/test_game_session.py',
    'tests/integration/test_frontend_wiring.py',
]


def main():
    missing = [f for f in FILES if not os.path.exists(os.path.join(ROOT, f))]
    if missing:
        print('GAME_TESTS FAIL 缺少测试文件：%s' % ', '.join(missing))
        return 1
    cmd = [sys.executable, '-m', 'pytest', '-q', '--no-header'] + FILES
    print('GAME_TESTS >> %d 个文件' % len(FILES), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print('GAME_TESTS FAIL exit=%d' % r.returncode)
        return r.returncode
    print('GAME_TESTS PASS %d 个文件' % len(FILES))
    return 0


if __name__ == '__main__':
    sys.exit(main())
