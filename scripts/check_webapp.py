"""check_webapp.py — 校验静态 3D 网页版（docs/app）没有引用断链。

为什么需要它：Pages 上没有人会跑 Python，断链的表现为"3D 转半天不出来"或
"面板全空"，而 CI 完全看不出来。这个脚本把"页面引用的东西是否真的存在、
哈希是否对得上"变成可失败的检查。

检查项：
  W1 manifest.json 里每个文件的 sha256 与磁盘一致（可发现手改或漏拷）
  W2 index.html 里的本地引用（css/js/importmap）都存在
  W3 app.js 里 fetch 的 /api/* 路径，要么在快照里、要么在"轮询白名单"里
  W4 GLB 与 out/dc_preview.glb 是同一份（sha256 相同）

用法：
  python scripts/check_webapp.py
  python scripts/check_webapp.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "docs" / "app"
GLB = ROOT / "out" / "dc_preview.glb"

# 静态快照里故意不提供、由垫片就地返回的接口
RUNTIME_ONLY = {"/api/job", "/api/scenario/get", "/api/run", "/api/cancel",
                "/api/config", "/api/scenario/save", "/api/scenario/delete",
                "/api/scenario/run", "/api/scenario/dry-run", "/api/scenario/validate"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check(app: Path = APP) -> list[str]:
    problems = []
    man_p = app / "manifest.json"
    if not man_p.exists():
        return ["缺 docs/app/manifest.json —— 先跑 python scripts/build_webapp.py"]
    man = json.loads(man_p.read_text(encoding="utf-8"))

    # W1 哈希一致性
    for rel, meta in man.get("files", {}).items():
        p = app / rel
        if not p.exists():
            problems.append("W1 缺文件：%s" % rel)
        elif sha(p) != meta.get("sha256"):
            problems.append("W1 哈希不符：%s（构建后被改过？）" % rel)

    # W2 index.html 的本地引用
    idx = (app / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="(\./[^"]+)"', idx)
    refs += re.findall(r'"(\./vendor/[^"]+)"', idx)      # importmap
    for ref in sorted(set(refs)):
        if not (app / ref[2:]).exists():
            problems.append("W2 引用不存在：%s" % ref)
    if "./snapshot.js" not in idx:
        problems.append("W2 缺 snapshot.js —— 静态快照的 fetch 垫片没插进去")

    # W3 API 覆盖
    api = set()
    for js in ("app.js", "ops.js"):
        p = app / js
        if p.exists():
            api |= set(re.findall(r"['\"](/api/[a-z0-9/-]+)", p.read_text(encoding="utf-8")))
    provided = {"/api/" + str(p.relative_to(app / "api")).replace("\\", "/")[:-5]
                for p in (app / "api").rglob("*.json")}
    missing = sorted(api - provided - RUNTIME_ONLY)
    if missing:
        problems.append("W3 快照缺接口：%s" % ", ".join(missing))

    # W5 计划书 HTML（iframe 直接引用，不走 fetch，W3 覆盖不到）
    plan = app / "api" / "plan.html"
    if not plan.exists():
        problems.append("W5 缺 api/plan.html —— 计划书面板会空白")
    elif plan.stat().st_size < 50_000:
        problems.append("W5 api/plan.html 只有 %d 字节，像是转换失败的残片"
                        % plan.stat().st_size)

    # W4 GLB 与主产物同源
    g = app / "out" / "dc_preview.glb"
    if not g.exists():
        problems.append("W4 快照里没有 out/dc_preview.glb")
    elif GLB.exists() and sha(g) != sha(GLB):
        problems.append("W4 GLB 与 out/dc_preview.glb 不是同一份（模型更新后忘了重建？）")
    return problems


def self_test() -> int:
    """证明它真的会失败：拷一份快照，删一个 api 文件、再改一个字节。"""
    if not APP.exists():
        print("SELF_TEST SKIP（没有 docs/app）")
        return 0
    root = Path(tempfile.mkdtemp(prefix="dc_webapp_"))
    cases = []

    def fresh(tag):
        dst = root / tag
        shutil.copytree(APP, dst)
        return dst

    cases.append(("原样", check(fresh("ok")), False))

    t1 = fresh("no_api")
    (t1 / "api" / "bootstrap.json").unlink()
    cases.append(("删 api/bootstrap.json", check(t1), True))

    t2 = fresh("extra_api")
    js = t2 / "app.js"
    js.write_text(js.read_text(encoding="utf-8") + "\nfetch('/api/not-in-snapshot');\n",
                  encoding="utf-8")
    # 同步 manifest 哈希，让 W1 通过、只剩 W3 该报（否则用例是靠 W1 蒙对的）
    man_p = t2 / "manifest.json"
    man = json.loads(man_p.read_text(encoding="utf-8"))
    man["files"]["app.js"]["sha256"] = sha(js)
    man["files"]["app.js"]["bytes"] = js.stat().st_size
    man_p.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cases.append(("加未冻结接口", check(t2), True))
    bad = 0
    for name, probs, should_fail in cases:
        failed = bool(probs)
        ok = failed == should_fail
        print("  [%s] %s（%s）" % ("PASS" if ok else "BAD", name,
                                  probs[0] if probs else "无问题"))
        if not ok:
            bad += 1
    print("WEBAPP_SELFTEST %s（%d 个反例未生效）" % ("OK" if bad == 0 else "FAIL", bad))
    return 0 if bad == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="静态 3D 网页版断链检查")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()

    probs = check()
    if probs:
        print("WEBAPP_FAIL %d 项" % len(probs))
        for p in probs:
            print("  - " + p)
        return 1
    man = json.loads((APP / "manifest.json").read_text(encoding="utf-8"))
    if not a.quiet:
        print("WEBAPP_OK files=%d bytes=%.1fMB api=%d glb=%.0fKB"
              % (man["counts"]["files"], man["counts"]["bytes"] / 1048576,
                 len(list((APP / "api").rglob("*.json"))),
                 (APP / "out" / "dc_preview.glb").stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
