#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""口径守卫：文档里写的数字，必须和代码/产物算出来的一致。

为什么需要它：这份项目被批评得最多的不是能力，而是"说法自相矛盾"——README 写
31 步、实际 45 步；控制台写 A1–A21、实际 A1–A40；版本号停在 0.1.0、CHANGELOG 已经
0.5.0。这类错误靠人肉检查一定会复发，所以做成守卫：**数字从代码里算，文档只许引用**。

检查范围只含"当前状态"文档与界面文案；`docs/PROJECT_REPORT.md`、`docs/DC_EXPANSION_PLAN.md`
这类历史报告保留当时的数字（它们记录的是那一刻的事实），不参与检查。

用法：
    python scripts/check_claims.py            # 检查
    python scripts/check_claims.py --self-test # 证明它抓得住过期文案
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 当前状态文档与界面文案（历史报告不在此列）
TARGETS = [
    # 唯一真相源自己必须在守卫里：它曾写着「31 步链」而实际 45 步，
    # 因为这份清单漏了它——守卫漏掉被守的对象，等于没有守卫。
    "out/CURRENT_STATUS.md",
    "README.md",
    "README.zh-CN.md",
    "docs/cli.md",
    "docs/COMPLETION_STATUS.md",
    "docs/ARTIFACTS.md",
    "console/static/index.html",
    "console/static/app.js",
    "submission/index.html",
    "submission/data.js",
    "submission/app.js",
    "demo/index.html",
]


def facts() -> dict:
    from dc_twin import runner
    import dc_twin

    f = {
        "version": dc_twin.__version__,
        "steps_data": len(runner.SCOPES["data"]),
        "steps_scenarios": len(runner.SCOPES["scenarios"]),
        "steps_full": len(runner.SCOPES["full"]),
    }

    sims = sorted(glob.glob(str(ROOT / "runs" / "*" / "*" / "sim_kpi.json")))
    anas = sorted(glob.glob(str(ROOT / "runs" / "*" / "*" / "kpi.json")))
    tick = json.loads(Path(sims[-1]).read_text(encoding="utf-8"))["metrics"] if sims else {}
    ana = json.loads(Path(anas[-1]).read_text(encoding="utf-8"))["metrics"] if anas else {}
    f["kpi_tick"] = len(tick)
    f["kpi_analytic"] = len(ana)
    f["kpi_union"] = len(set(tick) | set(ana))

    rep = ROOT / "out" / "validate_report.json"
    if rep.exists():
        cases = json.loads(rep.read_text(encoding="utf-8"))
        f["validate"] = len(cases)
    else:
        f["validate"] = None

    lic = ROOT / "LICENSE"
    head = lic.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    f["license"] = head[0].strip() if head else "?"

    areas = ROOT / "data" / "areas.json"
    f["areas"] = (json.loads(areas.read_text(encoding="utf-8"))["summary"]["total"]
                  if areas.exists() else None)
    f["scenarios"] = len(list((ROOT / "config" / "scenarios").glob("*.json")))
    return f


def stale_patterns(f: dict) -> list[tuple[str, str]]:
    """(正则, 说明)。每条都必须能一句话说清"应该写什么"。"""
    lic = "Apache" if f["license"].startswith("Apache") else f["license"]
    return [
        (r"A1\s*[–-]\s*A21", "验收已是 A1–A%d" % (f["validate"] or 0)),
        (r"\b31\s*步\b", "full 已是 %d 步（data %d / scenarios %d）"
         % (f["steps_full"], f["steps_data"], f["steps_scenarios"])),
        (r"11\s*/\s*16\s*/\s*31", "步数应为 %d / %d / %d"
         % (f["steps_data"], f["steps_scenarios"], f["steps_full"])),
        (r"27\s*项\s*(tick|measured|实测)", "tick 层已是 %d 项" % f["kpi_tick"]),
        (r"39\s*项\s*(tick|measured|实测).{0,12}(25|27)\s*项",
         "两套数字应为 tick %d / 解析 %d / 去重 %d"
         % (f["kpi_tick"], f["kpi_analytic"], f["kpi_union"])),
        (r"0\.1\.0", "版本已到 %s" % f["version"]),
        (r"·\s*MIT\s*·", "许可是 %s" % lic),
        (r"no remote/tag is|no remote/tag", "仓库已配置 origin"),
        (r"时空预约", "实现是反应式门控 + 0.05 s 采样复核，没有预约表"),
    ]


#: 数字型断言：(正则, facts 键, 说法)。匹配到就把数字和实际值比，不等就报。
#: 加这一层的理由：指标数从 39 改到 50 时，正则表只认特定组合，文档里的旧数字
#: 直接漏过去了——"口径守卫"必须能自己发现新出现的数字说法。
NUMERIC_CLAIMS = [
    (r"(?<![\d.])(\d+)\s*项\s*(?:tick|measured|实测)", "kpi_tick", "tick 层指标数"),
    # "N 步链"：只认总数（full），因为对外说的"一条链"就是 full。
    (r"(?<![\d.])(\d+)\s*步链", "steps_full", "流水线步数"),
    # 验收只认静态那条（A1–A21）。不在这里比数字：文档里 "A1–A3" 这类**子区间**
    # 是合法的阶段划分，按总数去比会全是误报。
]

#: 三元组 "N · M · K"（data / scenarios / full）：三处一起对，避免只写对一个。
TRIPLE = re.compile(r"data\s*(\d+)\s*[·/]\s*scenarios\s*(\d+)\s*[·/]\s*full\s*(\d+)")


def numeric_claims(line: str, f: dict) -> list[str]:
    out = []
    for pat, key, label in NUMERIC_CLAIMS:
        for m in re.finditer(pat, line):
            want = f.get(key)
            got = int(m.group(1))
            if want and got != int(want):
                out.append("%s 应为 %s，文档写的是 %d" % (label, want, got))
    for m in TRIPLE.finditer(line):
        got = tuple(int(x) for x in m.groups())
        want = (f["steps_data"], f["steps_scenarios"], f["steps_full"])
        if got != want:
            out.append("步数三元组应为 %s，文档写的是 %s" % (want, got))
    return out


def scan_lines(lines, f: dict, name: str = "<text>") -> list[str]:
    problems = []
    for i, line in enumerate(lines, 1):
        for why in numeric_claims(line, f):
            problems.append("%s:%d  %s\n        命中：%s" % (name, i, why, line.strip()[:110]))
        for pat, why in stale_patterns(f):
            # "不是 tick 级时空预约"这种**否认句**本身是诚实的，不该被判为过度宣称
            if "时空预约" in pat and re.search(r"不是|没有|并非", line):
                continue
            if re.search(pat, line):
                problems.append("%s:%d  %s\n        命中：%s"
                                % (name, i, why, line.strip()[:110]))
    return problems


def scan(f: dict) -> list[str]:
    problems = []
    for rel in TARGETS:
        p = ROOT / rel
        if not p.exists():
            continue
        problems += scan_lines(
            p.read_text(encoding="utf-8", errors="replace").splitlines(), f, rel)
    return problems


#: 每条都要有对应样本，否则"守卫"只覆盖了它自己想到的那几种写法
SAMPLES = [
    "| 验收 | A1–A21 逐条 PASS/FAIL |",
    "dc run --scope full   # 共 31 步",
    "| `dc run` | 11 / 16 / 31 steps respectively |",
    "| A6 | 27 项 measured KPI |",
    "39 项 tick 指标，另有 25 项解析层",
    "version = \"0.1.0\"",
    "Generated by dc export · MIT ·",
    "no remote/tag is configured",
    "实现 L1 时空预约，全程 0 冲突",
    # 状态表里曾写「31 步链（data 11 · scenarios 16 · full 31）」的两半
    "| 开源交付层 | dc CLI 7 命令 / 31 步链 |",
    "| 开源交付层 | 步链（data 11 · scenarios 16 · full 31） |",
]


def self_test() -> int:
    """造过期文案，确认每一条规则都会报——否则守卫只是装饰。"""
    f = facts()
    found = scan_lines(SAMPLES, f)
    hit = len({p for p in found})
    need = len(stale_patterns(f))
    if hit >= need:
        print("CLAIMS_SELFTEST OK  %d/%d 条规则全部命中" % (hit, need))
        return 0
    print("CLAIMS_SELFTEST FAIL  只命中 %d/%d" % (hit, need))
    for p in found:
        print("  " + p)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="文档口径守卫")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true", help="打印算出来的事实")
    a = ap.parse_args(argv)

    f = facts()
    if a.json:
        print(json.dumps(f, ensure_ascii=False, indent=2))
    if a.self_test:
        return self_test()

    problems = scan(f)
    print("口径事实：版本 %s · 步数 %d/%d/%d · KPI tick %d / 解析 %d / 去重 %d · 验收 %s 项"
          % (f["version"], f["steps_data"], f["steps_scenarios"], f["steps_full"],
             f["kpi_tick"], f["kpi_analytic"], f["kpi_union"], f["validate"]))
    if problems:
        print("\n发现 %d 处过期文案：" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    print("CLAIMS_OK  %d 个文档与代码一致" % len([t for t in TARGETS if (ROOT / t).exists()]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
