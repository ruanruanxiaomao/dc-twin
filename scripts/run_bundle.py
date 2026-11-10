"""run_bundle.py — 一次运行 = 一个自包含产物包。

为什么需要它：项目原本把产物写在 `out/` 与 `runs/` 两处，跑几次之后就无法回答
"这批数字是哪一次跑出来的"。本脚本把「运行 + 一致性检查 + 产物拷贝 + 指纹」收进
`artifacts/DC_<时间戳>_<标签>/`，每个包自带 manifest，可单独拷给别人看。

目录结构：
  artifacts/DC_20261110T133000Z_m1_process/
    ├─ manifest.json        # 包指纹：时间、scope、种子、计数、每个文件的 sha256
    ├─ out/                 # 报告类产物（json/jsonl/md/csv）
    ├─ runs/                # 本次新增的 run 目录（不含历史）
    ├─ logs/                # 各步骤 stdout/stderr
    └─ README.txt           # 包里有什么、怎么复现

用法：
  python scripts/run_bundle.py --scope scenarios --label m1_process
  python scripts/run_bundle.py --scope data --label quick --no-post
  python scripts/run_bundle.py --scope scenarios --with-media   # 连 blend/glb/mp4 一起收
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'out')
RUNS = os.path.join(ROOT, 'runs')
CONFIG = os.path.join(ROOT, 'config')
ARTIFACTS = os.path.join(ROOT, 'artifacts')

REPORT_EXT = ('.json', '.jsonl', '.md', '.csv', '.txt')
MEDIA_EXT = ('.blend', '.glb', '.mp4', '.png', '.jpg', '.ico')
MAX_RUN_FILE_MB = 64


def sha256(path, blocks=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(blocks), b''):
            h.update(chunk)
    return h.hexdigest()


def run_dirs():
    """runs/<scenario>/<run_id> → 相对路径集合。"""
    found = set()
    if not os.path.isdir(RUNS):
        return found
    for sc in os.listdir(RUNS):
        sub = os.path.join(RUNS, sc)
        if not os.path.isdir(sub) or sc == 'logs':
            continue
        for rid in os.listdir(sub):
            if os.path.isdir(os.path.join(sub, rid)):
                found.add(os.path.join(sc, rid))
    return found


def step(cmd, logdir, name):
    os.makedirs(logdir, exist_ok=True)
    log = os.path.join(logdir, name + '.log')
    t0 = dt.datetime.now(dt.timezone.utc)
    with open(log, 'w', encoding='utf-8') as f:
        p = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, text=True)
    dt_s = (dt.datetime.now(dt.timezone.utc) - t0).total_seconds()
    tail = ''
    try:
        with open(log, encoding='utf-8', errors='replace') as f:
            tail = ' / '.join(f.read().strip().splitlines()[-2:])[:300]
    except OSError:
        pass
    print('   %-28s exit=%d %.1fs  %s' % (name, p.returncode, dt_s, tail))
    return p.returncode, dt_s


def copy_reports(dest, with_media):
    exts = REPORT_EXT + (MEDIA_EXT if with_media else ())
    n = 0
    os.makedirs(dest, exist_ok=True)
    for name in sorted(os.listdir(OUT)):
        src = os.path.join(OUT, name)
        if not os.path.isfile(src):
            continue
        if not name.lower().endswith(exts):
            continue
        shutil.copy2(src, os.path.join(dest, name))
        n += 1
    return n


def copy_new_runs(dest, since):
    new = sorted(run_dirs() - since)
    os.makedirs(dest, exist_ok=True)
    copied, skipped = 0, []
    for rel in new:
        src = os.path.join(RUNS, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            sp = os.path.join(src, name)
            if not os.path.isfile(sp):
                continue
            mb = os.path.getsize(sp) / (1 << 20)
            if mb > MAX_RUN_FILE_MB:
                skipped.append('%s (%.0f MB)' % (rel + '/' + name, mb))
                continue
            shutil.copy2(sp, os.path.join(dst, name))
            copied += 1
    return new, copied, skipped


def facts():
    scen = sorted(glob.glob(os.path.join(CONFIG, 'scenarios', '*.json')))
    schemas = sorted(glob.glob(os.path.join(CONFIG, 'schemas', '*.json')))

    def n_keys(kind):
        base = os.path.join(RUNS, 'baseline')
        if not os.path.isdir(base):
            return 0
        cands = sorted(d for d in os.listdir(base)
                       if os.path.isfile(os.path.join(base, d, kind)))
        if not cands:
            return 0
        with open(os.path.join(base, cands[-1], kind), encoding='utf-8') as f:
            return len(json.load(f).get('metrics', {}))

    seeds = {}
    for rel, key in (('layout_spec.json', ('site', 'seed')), ('sim_spec.json', ('seed',))):
        p = os.path.join(CONFIG, rel)
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                node = json.load(f)
            for k in key:
                node = node.get(k, {}) if isinstance(node, dict) else {}
            seeds[rel] = node
    return {
        'scenarios': len(scen),
        'runs_total': len(run_dirs()),
        'schemas': len(schemas),
        'kpi_analytic': n_keys('kpi.json'),
        'kpi_tick': n_keys('sim_kpi.json'),
        'seeds': seeds,
    }


def git_head():
    try:
        p = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT,
                           capture_output=True, text=True)
        return p.stdout.strip() or 'unknown'
    except OSError:
        return 'no-git'


def main(argv=None):
    ap = argparse.ArgumentParser(description='一次运行 → 一个产物包')
    ap.add_argument('--scope', default='scenarios', choices=['data', 'scenarios', 'full'])
    ap.add_argument('--label', default='run', help='包名标签，如 m1_process')
    ap.add_argument('--no-post', action='store_true', help='跳过后置一致性检查')
    ap.add_argument('--with-media', action='store_true', help='连 blend/glb/mp4/png 一起收')
    ap.add_argument('--spec', default=None,
                    help='变体布局 spec（用户自选功能区后由 build_variant.py 生成）')
    ap.add_argument('--out-root', default=ARTIFACTS)
    a = ap.parse_args(argv)

    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    bundle = os.path.join(a.out_root, 'DC_%s_%s' % (stamp, a.label))
    os.makedirs(bundle, exist_ok=True)
    logdir = os.path.join(bundle, 'logs')
    print('>> bundle %s' % bundle)

    before = run_dirs()
    t_start = dt.datetime.now(dt.timezone.utc)

    print('>> pipeline scope=%s' % a.scope)
    cmd = [sys.executable, 'run.py', '--scope', a.scope, '--quiet']
    if a.spec:
        cmd += ['--spec', a.spec]
    rc, dt_pipe = step(cmd, logdir, 'pipeline')
    if rc != 0:
        print('BUNDLE_FAIL pipeline exit=%d（日志见 %s）' % (rc, logdir))
        return rc

    post = []
    if not a.no_post:
        print('>> 一致性检查')
        for name, cmd in (
            ('check_process', [sys.executable, 'scripts/check_process.py']),
            ('spec_areas', [sys.executable, 'scripts/spec_areas.py', '--quiet']),
            ('check_station', [sys.executable, 'scripts/check_station.py', '--quiet']),
            ('check_deadlock', [sys.executable, 'scripts/check_deadlock.py', '--quiet']),
            ('check_studio', [sys.executable, 'scripts/check_studio.py', '--quiet']),
            ('report_status', [sys.executable, 'scripts/report_status.py']),
            ('check_doc_drift', [sys.executable, 'scripts/check_doc_drift.py']),
            ('validate', [sys.executable, 'scripts/validate.py']),
        ):
            r, d = step(cmd, logdir, name)
            post.append({'step': name, 'exit': r, 'duration_s': round(d, 2)})

    print('>> 收集产物')
    n_rep = copy_reports(os.path.join(bundle, 'out'), a.with_media)
    new_runs, n_run_files, skipped = copy_new_runs(os.path.join(bundle, 'runs'), before)

    files = []
    for base, _dirs, names in os.walk(bundle):
        for nm in sorted(names):
            p = os.path.join(base, nm)
            rel = os.path.relpath(p, bundle).replace('\\', '/')
            if rel == 'manifest.json':
                continue
            files.append({'path': rel, 'bytes': os.path.getsize(p), 'sha256': sha256(p)})

    manifest = {
        'kind': 'dc_run_bundle',
        'schema_version': '1.0',
        'bundle_id': os.path.basename(bundle),
        'created_utc': t_start.isoformat(timespec='seconds'),
        'git_head': git_head(),
        'scope': a.scope,
        'layout_spec': a.spec or 'config/layout_spec.json',
        'label': a.label,
        'python': sys.version.split()[0],
        'pipeline': {'exit': rc, 'duration_s': round(dt_pipe, 2)},
        'post_checks': post,
        'facts': facts(),
        'counts': {'out_files': n_rep, 'new_runs': len(new_runs),
                   'run_files': n_run_files, 'skipped_large': skipped},
        'new_runs': new_runs,
        'files': files,
    }
    with open(os.path.join(bundle, 'manifest.json'), 'w', encoding='utf-8') as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')

    with open(os.path.join(bundle, 'README.txt'), 'w', encoding='utf-8') as f:
        f.write(
            'DC 产物包 %s\n\n' % manifest['bundle_id']
            + 'scope=%s  git=%s  python=%s\n' % (a.scope, manifest['git_head'], manifest['python'])
            + '产物：out/ 报告 %d 个；本次新增运行 %d 个（%d 个文件）\n'
              % (n_rep, len(new_runs), n_run_files)
            + '检查：' + '；'.join('%s=%s' % (p['step'], 'OK' if p['exit'] == 0 else 'FAIL')
                                   for p in post) + '\n\n'
            + '复现：python run.py --scope %s\n' % a.scope
            + '校验：python scripts/check_doc_drift.py && python scripts/check_process.py\n'
        )

    bad = [p for p in post if p['exit'] != 0]
    print('BUNDLE_%s %s  out=%d runs=%d files=%d %s'
          % ('WARN' if bad else 'OK', os.path.basename(bundle), n_rep, len(new_runs),
             len(files), ('失败步骤: ' + ','.join(p['step'] for p in bad)) if bad else ''))
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
