#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STEP 基准：把批跑产物冻成**逐字节**快照，用来证明 `run()` 拆 `step()` 没改变任何东西。

为什么不能复用 `kpi_baseline.py`：
  - KPI 基准只比 32 项聚合指标、浮点还留 1e-6 容差。step 化最容易犯的错是
    **丢一次日志、错一个 seq、少跳一个 tick**，这些在聚合指标里可能被平均掉，
    但在事件流里是硬伤。所以这里比字节。

两处必须归一化（否则每次跑都不等，门会变成永远红）：
  - `run_id`：等于本次 `runs/<scenario>/<stamp>` 目录名，天生每次都变。
  - `metrics.wall_clock_s.value`：`time.time()` 差值，跟机器快慢有关。
  归一化只替换这两个 token，其余字节原样保留 —— 仍是"一个字节都不能动"。

三份产物：
  - `sim_events.jsonl`：事件溯源的全部事实（seq 连续、类型计数、时序）
  - `sim_kpi.json`：56 项 tick 层指标（除墙钟外全部冻结）
  - `sim_state.json`：终态摘要（机器人 11 态终值、队列深度、封门集合）

用法：
    python scripts/step_baseline.py --freeze    # 重构**前**必须先冻一次
    python scripts/step_baseline.py --check     # 重构**后**比对，任一不符即 FAIL
    python scripts/step_baseline.py --self-test # 证明该门真的会失败
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
GOLDEN = ROOT / "tests" / "golden" / "step_baseline.json"          # v1（永久保留）
GOLDEN_V2 = ROOT / "tests" / "golden" / "step_baseline_v2.json"     # P6a 之后的默认基准


def default_golden():
    """默认比 v2；v2 还没冻时退回 v1（P6a-5 之前的行为保持不变）。"""
    return GOLDEN_V2 if GOLDEN_V2.exists() else GOLDEN

ARTIFACTS = ("sim_events.jsonl", "sim_kpi.json", "sim_state.json")

#: 归一化：把每次都会变的两处换成固定串，其余字节保持原样
NORMALISERS = [
    (re.compile(r'"run_id":\s*"[^"]*"'), '"run_id": "<normalized>"'),
    (re.compile(r'("wall_clock_s":\s*\{\s*"value":\s*)([-0-9.eE+]+)'), r"\g<1>0.0"),
]

#: 这些目录不是场景（pilot 有独立基准，logs 是运行日志）
SKIP = {"custom", "logs", "pilot_csv", "game"}


def latest_runs() -> dict[str, Path]:
    """每个场景取最新一次跑（按 sim_kpi.json 的 mtime）。"""
    best: dict[str, tuple[float, Path]] = {}
    for f in RUNS.glob("*/*/sim_kpi.json"):
        scen = f.parts[-3]
        if scen in SKIP:
            continue
        m = f.stat().st_mtime
        if scen not in best or m > best[scen][0]:
            best[scen] = (m, f.parent)
    return {k: v[1] for k, v in best.items()}


def normalise(text: str) -> str:
    for rx, repl in NORMALISERS:
        text = rx.sub(repl, text)
    return text


def digest(path: Path) -> dict:
    """一文件的归一化字节摘要 + 体量（体量能区分"等价但漏了一行"以外的粗错）。"""
    raw = path.read_bytes()
    text = raw.decode("utf-8").replace("\r\n", "\n")
    norm = normalise(text).encode("utf-8")
    return {
        "sha256": hashlib.sha256(norm).hexdigest(),
        "bytes": len(norm),
        "lines": norm.count(b"\n"),
    }


def freeze(path: Path = GOLDEN, tag: str = "v1") -> int:
    runs = latest_runs()
    if not runs:
        print("没有找到 runs/*/*/sim_kpi.json，先跑 `dc run --scope scenarios`", file=sys.stderr)
        return 2
    data = {
        "schema_version": "1.0",
        "kind": "step_baseline",
        "note": "批跑产物逐字节快照。step 化重构的回归门；仅 run_id / wall_clock_s 归一化。",
        "normalised": ["run_id", "metrics.wall_clock_s.value"],
        "artifacts": list(ARTIFACTS),
        "scenarios": {},
    }
    missing = []
    for scen in sorted(runs):
        entry = {}
        for name in ARTIFACTS:
            f = runs[scen] / name
            if not f.exists():
                missing.append("%s/%s" % (scen, name))
                continue
            entry[name] = digest(f)
        if entry:
            data["scenarios"][scen] = entry
    if missing:
        print("缺产物，拒绝冻结（半截基准比没有基准更危险）：%s" % ", ".join(missing),
              file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(len(v) for v in data["scenarios"].values())
    print("STEP_BASELINE_FROZEN[%s] %d 场景 / %d 份产物 -> %s" % (tag,
                                       len(data["scenarios"]), total,
                                       path.relative_to(ROOT).as_posix()))
    return 0


def compare(expected: dict, actual: dict) -> list[str]:
    """纯函数：expected/actual 都是 {scenario: {artifact: digest}}。"""
    problems = []
    for scen, arts in expected.items():
        if scen not in actual:
            problems.append("%s：场景缺失（这次没跑？）" % scen)
            continue
        for name, exp in arts.items():
            act = actual[scen].get(name)
            if act is None:
                problems.append("%s/%s：产物缺失" % (scen, name))
                continue
            if exp["sha256"] == act["sha256"]:
                continue
            detail = []
            if exp["lines"] != act["lines"]:
                detail.append("行数 %d→%d" % (exp["lines"], act["lines"]))
            if exp["bytes"] != act["bytes"]:
                detail.append("字节 %d→%d" % (exp["bytes"], act["bytes"]))
            problems.append("%s/%s：字节不一致 %s%s"
                            % (scen, name, (", ".join(detail) + " ") if detail else "",
                               exp["sha256"][:12] + "→" + act["sha256"][:12]))
    return problems


def check(path: Path = GOLDEN) -> int:
    if not path.exists():
        print("缺少基准 %s；重构前先用 --freeze 生成" % path, file=sys.stderr)
        return 2
    golden = json.loads(path.read_text(encoding="utf-8"))
    expected = golden["scenarios"]
    runs = latest_runs()
    actual = {}
    for scen, d in runs.items():
        actual[scen] = {name: digest(d / name) for name in ARTIFACTS if (d / name).exists()}
    problems = compare(expected, actual)
    if problems:
        print("STEP_DRIFT %d 处（批跑产物被改动了）：" % len(problems))
        for p in problems[:40]:
            print("  - " + p)
        if len(problems) > 40:
            print("  … 其余 %d 处略" % (len(problems) - 40))
        print("\n这不是「数字变好」，是「确定性被破坏」。要么修回去，要么在 ADR 里写明为什么"
              "允许变，再 --freeze。")
        return 1
    n = sum(len(v) for v in expected.values())
    print("STEP_BASELINE_OK  %d 场景 / %d 份产物逐字节一致" % (len(expected), n))
    return 0


def self_test(path: Path = None) -> int:
    """证明门会失败：原样必须过，改一位必须挂，三种产物都要能抓到。"""
    path = path or default_golden()
    golden = json.loads(path.read_text(encoding="utf-8"))
    expected = golden["scenarios"]
    if compare(expected, {k: dict(v) for k, v in expected.items()}):
        print("STEP_SELFTEST FAIL 原样比对居然不通过")
        return 1

    scen = sorted(expected)[0]
    caught = 0
    for name in ARTIFACTS:
        bad = {k: {a: dict(d) for a, d in v.items()} for k, v in expected.items()}
        bad[scen][name]["sha256"] = "0" * 64
        if compare(expected, bad):
            caught += 1
    if caught == len(ARTIFACTS):
        print("STEP_SELFTEST OK  %d 类产物任一改动都能被抓到" % caught)
        return 0
    print("STEP_SELFTEST FAIL 只抓到 %d/%d 类" % (caught, len(ARTIFACTS)))
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="批跑产物逐字节基准（step 化回归门）")
    ap.add_argument("--freeze", action="store_true", help="冻结 v1（历史，只在重建历史时用）")
    ap.add_argument("--freeze-v2", action="store_true", help="冻结 v2（P6a 之后的默认基准）")
    ap.add_argument("--check", nargs="?", const="auto", default=None,
                    choices=["auto", "v1", "v2"],
                    help="与基准比对：默认 auto（有 v2 比 v2）；v1/v2 显式指定")
    ap.add_argument("--self-test", action="store_true", help="证明比对会失败")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test(default_golden())
    if a.freeze:
        return freeze(GOLDEN)
    if a.freeze_v2:
        return freeze(GOLDEN_V2, tag="v2")
    which = a.check or "auto"
    return check(GOLDEN if which == "v1" else (GOLDEN_V2 if which == "v2" else default_golden()))


if __name__ == "__main__":
    sys.exit(main())
