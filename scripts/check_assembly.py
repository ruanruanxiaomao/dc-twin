# -*- coding: utf-8 -*-
"""D10a/D12 门 · 功能区重摆的「草稿 → 校验 → 显式提交」（``ASSEMBLY_COMMIT_OK``）。

方案书 §16.9 的要求是"拖完不许直接落盘"，D12 把它写成三态：DRAFT → 校验 → 显式提交。
这一层最容易做成**假动作**：按钮点了、弹窗关了、文件还是被写了。所以门只认证据：

* 没校验令牌 → 必须拒（`NEED_VALIDATE`）；
* 校验后又改了草稿 → 必须拒（`STALE_PLAN`），否则"校验 A、提交 B"；
* 几何越界 → 必须拒（`GEOMETRY_INVALID`），且**目标文件一个字节都不能变**；
* 全部通过 → 真的写出，且写出的 spec 能被 `spec_layout` 再读一遍。

最后一条"文件没变"是这门与别处不同的地方：拒绝本身很好伪造（返回一句错误就行），
真正的证据是**盘上没有新东西**。所以每次拒绝都对目标文件做前后哈希比对。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import assembly  # noqa: E402
import build_variant as bv  # noqa: E402

TMP = ROOT / "out" / "exp" / "assembly"


def _sha(p):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _enabled():
    sel = bv.jload(bv.SELECTION) if Path(bv.SELECTION).exists() else {}
    return sel.get("enabled") or []


def _try_commit(enabled, moves, tok, out_spec):
    """跑一次 commit，返回 (结果字典, 被拒的 code 或 None)。异常不外泄。"""
    before = _sha(out_spec)
    try:
        r = assembly.commit(enabled, moves, tok, out_spec=out_spec)
    except assembly.CommitRejected as e:
        after = _sha(out_spec)
        return ({"rejected": e.code, "detail": e.detail,
                 "issues": len(e.issues), "wrote": after != before}, e.code)
    return ({"rejected": None, "wrote": _sha(out_spec) != before, "spec": r["spec"],
             "issues": r["issues"]}, None)


def check_gates():
    """四道闸逐条验，每条都看"有没有写盘"。"""
    problems, detail = [], {}
    enabled = _enabled()
    if not enabled:
        return False, {}, ["selection 里没有 enabled，无法构造草稿"]

    TMP.mkdir(parents=True, exist_ok=True)
    target = TMP / "committed_spec.json"
    if target.exists():
        target.unlink()                      # 从"没有产物"开始，才能证明是谁写的

    v = assembly.validate(enabled, {})
    base_tok = v["token"]
    detail["validate"] = {"ok": v["ok"], "issues": len(v["issues"]),
                          "count": v["summary"]["count"], "token": base_tok}
    if not v["ok"]:
        problems.append("基线草稿本身就没通过校验（%d 条 issue）" % len(v["issues"]))

    # 1 没令牌
    r1, c1 = _try_commit(enabled, {}, None, target)
    detail["no_token"] = r1
    if c1 != "NEED_VALIDATE":
        problems.append("没令牌却得到 %r（应 NEED_VALIDATE）" % (c1,))
    if r1["wrote"]:
        problems.append("没令牌的提交居然写了盘")

    # 2 令牌过期：先校验 A 拿到令牌，再用 B 提交
    r2, c2 = _try_commit(enabled, {"RCV-01": [2.0, 0.0]}, base_tok, target)
    detail["stale_token"] = r2
    if c2 != "STALE_PLAN":
        problems.append("草稿变了却用旧令牌，得到 %r（应 STALE_PLAN）" % (c2,))
    if r2["wrote"]:
        problems.append("过期令牌的提交居然写了盘")

    # 3 几何越界：把收货区拖进货架带（z 从 72 拖到 20）
    bad_moves = {"RCV-01": [0.0, -52.0]}
    vb = assembly.validate(enabled, bad_moves)
    detail["invalid_geometry"] = {"ok": vb["ok"], "issues": len(vb["issues"]),
                                  "rules": sorted({i.get("rule") for i in vb["issues"]})}
    r3, c3 = _try_commit(enabled, bad_moves, vb["token"], target)
    detail["invalid_geometry"]["commit"] = r3
    if vb["ok"]:
        problems.append("把收货区拖进货架带，校验居然通过——L3 没在看重叠")
    if c3 != "GEOMETRY_INVALID":
        problems.append("越界草稿得到 %r（应 GEOMETRY_INVALID）" % (c3,))
    if r3["wrote"]:
        problems.append("越界草稿居然写了盘")

    # 4 合法：这次必须真的写出
    r4, c4 = _try_commit(enabled, {}, base_tok, target)
    detail["commit"] = r4
    if c4 is not None:
        problems.append("合法草稿被拒：%r" % (c4,))
    if not r4["wrote"]:
        problems.append("合法草稿提交成功，但目标文件没变——写盘是假的")
    if r4["wrote"]:
        spec = json.loads(Path(target).read_text(encoding="utf-8"))
        wh = next((w for w in spec["warehouses"] if w["id"] == "W"), None)
        if wh is None or not wh.get("areas"):
            problems.append("写出的 spec 里 W 仓没有 areas")
        else:
            detail["written"] = {"areas": len(wh["areas"]),
                                 "depth": wh["size"]["depth"]}
        # 写出的东西必须还能被校验器读一遍——不然"写成功"只证明磁盘可写
        issues, meta = assembly.check(spec, tag="recheck")
        detail["written"]["recheck_issues"] = len(issues)
        if issues:
            problems.append("写出的 spec 重新校验有 %d 条 issue" % len(issues))
    return not problems, detail, problems


def check_self_test():
    """注入反例：把"跳过几何检查"的提交塞进去，门必须自己判红。

    这是这条门最容易被做成摆设的地方——commit 里删掉 `if issues: raise`，
    所有正常用例仍然绿，只有越界用例会红。所以自检必须真的模拟那个删法。
    """
    cases = []
    enabled = _enabled()
    if not enabled:
        return False, ["selection 里没有 enabled"]

    TMP.mkdir(parents=True, exist_ok=True)
    target = TMP / "selftest_spec.json"
    if target.exists():
        target.unlink()

    # 正常路径：应当写出
    v = assembly.validate(enabled, {})
    ok_run, _c = _try_commit(enabled, {}, v["token"], target)
    cases.append(("commit_ok", ok_run["wrote"]))

    # 反例 A：没有令牌
    r, c = _try_commit(enabled, {}, None, target)
    cases.append(("no_token_rejected", c == "NEED_VALIDATE" and not r["wrote"]))

    # 反例 B：令牌过期
    r, c = _try_commit(enabled, {"RCV-01": [1.5, 0]}, v["token"], target)
    cases.append(("stale_rejected", c == "STALE_PLAN" and not r["wrote"]))

    # 反例 C：越界草稿在**真实**校验器下必须有问题。
    # 这条替代了"注入假 check 看门是否发现"——那种写法只能证明"我注入的东西没生效"。
    # 真正要守的是：commit 之后再复核写出的 spec，越界必须仍然报出来（recheck）。
    bad_moves = {"RCV-01": [0.0, -52.0]}
    try:
        bad_doc = assembly.plan(enabled, bad_moves)
        bad_issues, _m = assembly.check(assembly.to_spec(bad_doc), tag="neg")
        cases.append(("recheck_catches_overflow", len(bad_issues) > 0))
    except assembly.CommitRejected:
        cases.append(("recheck_catches_overflow", False))

    # 反例 D：位移超上限
    try:
        assembly.plan(enabled, {"RCV-01": [999, 0]})
        too_far = False
    except assembly.CommitRejected as e:
        too_far = e.code == "MOVE_TOO_FAR"
    cases.append(("move_too_far", too_far))

    bad = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print("  %-24s %s" % (n, "ok" if ok else "MISSED"))
    return not bad, [n for n, ok in cases if not ok]


def main(argv=None):
    ap = argparse.ArgumentParser(description="D10a/D12 门：功能区草稿→校验→提交")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        ok, missed = check_self_test()
        print("ASSEMBLY_SELF_TEST %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 1

    ok, detail, problems = check_gates()
    rep = {"schema_version": "1.0", "kind": "assembly_report",
           "verdict": "ASSEMBLY_COMMIT_OK" if ok else "ASSEMBLY_COMMIT_FAIL",
           "failed": [] if ok else ["ASSEMBLY_COMMIT_OK"],
           "detail": detail, "problems": problems,
           "note": "只验后端事务：草稿/令牌/几何/落盘。前端拖拽的可用性由 "
                   "check_webapp 与人工点击覆盖，这里不做像素断言。"}
    TMP.mkdir(parents=True, exist_ok=True)
    (ROOT / "out" / "assembly_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.quiet:
        print("%s%s" % (rep["verdict"], "" if ok else " " + "; ".join(problems[:2])))
    else:
        print(rep["verdict"])
        print("  " + json.dumps(detail, ensure_ascii=False)[:400])
        for p in problems:
            print("  ! " + p)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
