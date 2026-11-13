"""check_doc_drift.py — 文档防漂移守卫（R02 的可执行缓解）。

背景：`out/CURRENT_STATUS.md` 曾长期与代码矛盾，原因有三类，且都真实发生过：
  1) 生成脚本里**硬编码**过时表述（改了引擎不改变量）；
  2) 统计口径错位：把 `runs/` 的目录数当场景数（`custom/`、`logs/` 被算进去）；
  3) KPI 有两套（解析层 25 项 / tick 层 27 项）却只报一个数字。

本脚本做三件事，任何一条不满足都以非零码退出：
  A. 读产物现算「应有数字」，与 `out/CURRENT_STATUS.md` 中的数字逐条比对；
  B. 禁止出现已废弃的表述（黑名单）；
  C. 校验仓库副本 `docs/DC_MASTER_PLAN.md` 与桌面母本一致（母本缺失时降级为 WARN）。

用法：
  python scripts/check_doc_drift.py            # 校验
  python scripts/check_doc_drift.py --self-test # 自检：证明它真的会失败
"""
from __future__ import annotations

import glob
import re
import hashlib
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'out')
CONFIG = os.path.join(ROOT, 'config')
STATUS = os.path.join(OUT, 'CURRENT_STATUS.md')
MASTER = os.path.join(ROOT, 'docs', 'DC_MASTER_PLAN.md')
# 母本可能有多个落地位置（工作区根 / 桌面根 / 项目父目录），按顺序取第一个存在的。
PLAN_NAME = '配送中心数字孪生_完整计划书.md'
PLAN_CANDIDATES = [
    # 显式指定优先（CI / 别人机器上的布局不同，不该由代码猜）
    os.environ.get('DC_PLAN_COPY', ''),
    os.path.join(os.path.dirname(ROOT), PLAN_NAME),          # <parent>/<repo>/../计划书.md
    os.path.join(os.path.dirname(os.path.dirname(ROOT)), PLAN_NAME),
]

# 已废弃表述：出现即 FAIL。新增一条，就等于永久钉死一类旧说法。
FORBIDDEN = (
    '离线回放',
    '解析 what-if',
    '未实现 tick',
    '4 份 schema',
    'L1–L12',
    '25/27',
)


def _banned_pattern(frag):
    """废弃表述按**独立片段**匹配，不做裸子串。

    踩过的坑：`'4 份 schema' in text` 会被 `'14 份 schema'` 命中——新增一份 schema
    （asset_spec）就把这条门判红，红得莫名其妙。左右各加一个「不紧邻数字/字母」的
    边界：`14 份 schema` 里的 `4` 前面是 `1`，不算；真正写着 `4 份 schema` 的旧文照旧抓。
    """
    return re.compile(r'(?<![0-9A-Za-z])' + re.escape(frag) + r'(?![0-9A-Za-z])')


_BANNED = tuple((frag, _banned_pattern(frag)) for frag in FORBIDDEN)


def facts():
    """从产物现算「唯一真相」数字，不读任何二手文档。"""
    scen = sorted(glob.glob(os.path.join(CONFIG, 'scenarios', '*.json')))
    runs = 0
    runs_root = os.path.join(ROOT, 'runs')
    # runs/ 是 .gitignore 的生成物，清理工作区后可能整个不存在。把它当"永远在"
    # 会让口径门在干净环境里直接崩（实测 WinError 3），而不是给出可读的判定。
    top = sorted(os.listdir(runs_root)) if os.path.isdir(runs_root) else []
    for d in top:
        sub = os.path.join(runs_root, d)
        if os.path.isdir(sub) and d != 'logs':
            runs += sum(1 for p in os.listdir(sub) if os.path.isdir(os.path.join(sub, p)))
    schemas = sorted(glob.glob(os.path.join(CONFIG, 'schemas', '*.json')))

    def keys(kind):
        base = os.path.join(runs_root, 'baseline')
        if not os.path.isdir(base):
            return set()
        dirs = sorted(d for d in os.listdir(base)
                      if os.path.isfile(os.path.join(base, d, kind)))
        if not dirs:
            return set()
        return set(json.load(open(os.path.join(base, dirs[-1], kind), encoding='utf-8'))
                   .get('metrics', {}))

    analytic, tick = keys('kpi.json'), keys('sim_kpi.json')
    layout = json.load(open(os.path.join(ROOT, 'data', 'layout.json'), encoding='utf-8'))
    return {
        'scenarios': len(scen),
        'runs': runs,
        'schemas': len(schemas),
        'analytic': len(analytic),
        'tick': len(tick),
        'union': len(analytic | tick),
        'warehouses': layout['summary']['warehouses'],
        'racks': layout['summary']['racks'],
        'locations': layout['summary']['locations'],
        'robots': layout['summary']['robots'],
    }


def expected_fragments(f):
    return [
        '%d 个冻结场景' % f['scenarios'],
        # 运行次数是**快照**：CI 从空 runs/ 起跑，提交里的数字不可能对上。
        # 所以只校验格式存在，不校验数值——数值断言会把"可复现"变成"必须同一台机器"。

        '%d 份 schema' % f['schemas'],
        '解析层 %d 项' % f['analytic'],
        'tick 层 %d 项' % f['tick'],
        '%d 仓' % f['warehouses'],
        '%d 货架' % f['racks'],
    ]


def check_text(text, f):
    """返回 (problems, notes)。文本层面的所有判定都在这里，便于 self-test 复用。"""
    problems, notes = [], []
    for bad, pat in _BANNED:
        if pat.search(text):
            problems.append('废弃表述仍在：%r' % bad)
    for frag in expected_fragments(f):
        if frag not in text:
            problems.append('缺少应有数字：%r（产物已变，状态文档未重生成？）' % frag)
    if not re.search(r'\d+ 次运行', text):
        problems.append('缺少「N 次运行」的计数表述（运营规模这一段被删了？）')
    if '## 口径纪律' not in text:
        problems.append('缺少「口径纪律」小节')
    if 'runs' in f:
        notes.append('运行次数为快照值（当前产物 %d 次），不做数值断言' % f['runs'])
    return problems, notes


def check_plan_copy():
    src = next((p for p in PLAN_CANDIDATES if os.path.exists(p)), None)
    if src is None:
        return ['WARN', '未找到桌面母本（试过 %d 个路径），跳过副本一致性检查'
                % len(PLAN_CANDIDATES)]
    if not os.path.exists(MASTER):
        return ['FAIL', '仓库副本 docs/DC_MASTER_PLAN.md 缺失']
    def h(p):
        return hashlib.sha256(open(p, 'rb').read()).hexdigest()
    if h(src) != h(MASTER):
        return ['FAIL', '仓库副本与母本不一致（母本 %s）：先同步，勿单边编辑' % src]
    return ['OK', '仓库副本与母本一致（%s）' % os.path.relpath(src, ROOT)]


def main():
    f = facts()
    if not os.path.exists(STATUS):
        print('FAIL 缺少 out/CURRENT_STATUS.md，先跑 scripts/report_status.py')
        return 1
    text = open(STATUS, encoding='utf-8').read()
    problems, _ = check_text(text, f)
    level, msg = check_plan_copy()
    if level == 'FAIL':
        problems.append(msg)

    print('产物事实：场景 %d / 运行 %d / schema %d / KPI 解析 %d + tick %d（去重 %d）'
          % (f['scenarios'], f['runs'], f['schemas'], f['analytic'], f['tick'], f['union']))
    print('副本一致性：%s %s' % (level, msg))
    if problems:
        for p in problems:
            print('  [FAIL] %s' % p)
        print('DRIFT_FAIL %d 项' % len(problems))
        return 1
    print('DRIFT_OK 文档与产物一致')
    return 0


def self_test():
    """证明这个守卫真的会失败——沿用项目「验证器必须能失败」的纪律。"""
    f = facts()
    real = open(STATUS, encoding='utf-8').read()
    cases = [
        ('原样文本', real, False),
        ('篡改场景数', real.replace('%d 个冻结场景' % f['scenarios'], '10 个冻结场景', 1), True),
        ('塞回旧表述', real + '\n- 运营层是离线回放\n', True),
        ('塞回 4 份 schema', real + '\n- 当时只有 4 份 schema\n', True),
        ('删除口径纪律', real.replace('## 口径纪律', '## 其他'), True),
    ]
    bad = 0
    # 边界用例：14 份 schema 不该被当成旧的「4 份 schema」
    if _banned_pattern('4 份 schema').search('现在是 14 份 schema'):
        print('  [BAD] 子串误伤：14 份 schema 被判成 4 份 schema')
        bad += 1
    else:
        print('  [PASS] 子串不误伤（14 份 schema）')
    for name, text, should_fail in cases:
        problems, _ = check_text(text, f)
        failed = bool(problems)
        ok = failed == should_fail
        print('  [%s] %s（%s）' % ('PASS' if ok else 'BAD', name,
                                  '按预期失败' if failed else '按预期通过'))
        if not ok:
            bad += 1
    print('SELF_TEST %s（%d 个反例未生效）' % ('OK' if bad == 0 else 'FAIL', bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(self_test() if '--self-test' in sys.argv else main())
