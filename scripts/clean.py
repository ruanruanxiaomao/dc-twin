#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作区清理：删可重建的中间产物，留能证明结论的东西。

原则（按顺序判断）：

1. **能一条命令重建的，删**：`out/frames/`（942 帧 PNG，已经编码成 mp4）、
   历史 `runs/` 快照、`artifacts/` 旧产物包、PyInstaller 的 `build/` 工作目录。
2. **是"结论证据"的，留**：`out/renders/`（10 张图）、`dc_preview.glb`、
   `distribution_center.blend`、`anim_dc.mp4`、各 `*_report.json`、`runs/` 里每个
   场景的**最新**快照（界面要读它）、`dist/DC Twin/`（成品）。
3. **看不清用途的，留**：`demo/`、`submission/`、`assets/` 都被 Pages 或 README 引用。

默认只打印不删除，`--apply` 才真删（不给自己留"手滑"的机会）。

    python scripts/clean.py            # 看看能清多少
    python scripts/clean.py --apply    # 真删
    python scripts/clean.py --apply --keep-artifacts 3   # 多留几套产物包
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 整目录删除（相对 ROOT）
DIRS = [
    "out/frames",          # 渲染帧序列，mp4 已编码，重渲即得
    "out/variant",         # build_variant.py 派生
    "dist/release",        # v3.7 的旧交付包，已被 dist/DC Twin 取代
    "build",               # PyInstaller 工作目录
]

#: 临时截图/中间文件（out/ 下按前缀匹配）
SHOT_PREFIXES = ("tab", "tab2", "tab3", "tab4", "tab5", "webapp_", "final_",
                 "pages_", "exe_")

#: 备份与缓存
GLOBS = [
    "out/*.blend1",
    "data/*.bak",
    "data/*.bak.json",
    "config/**/*.bak.json",
    "**/__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".webprofile",
]


def size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def human(n: int) -> str:
    return "%.1f MB" % (n / 1048576) if n >= 1048576 else "%.0f KB" % (n / 1024)


def plan_runs(keep_bundles: int) -> list[Path]:
    """每个场景保留：最新快照 + 最新含 sim_kpi.json 的快照；其余删除。"""
    out = []
    runs = ROOT / "runs"
    if not runs.exists():
        return out
    for scen in sorted(p for p in runs.iterdir() if p.is_dir()):
        if scen.name in ("logs", "cache"):
            continue
        snaps = sorted((p for p in scen.iterdir() if p.is_dir()),
                       key=lambda p: p.stat().st_mtime)
        if not snaps:
            continue
        # 三类各自保一套：最新 / 最新含 tick 指标 / 最新含解析层指标。
        # 只保"最新 + 最新含 sim_kpi"曾把带 kpi.json 的那套删掉，验收链的
        # A25/A26/A27/A32/A35 立刻红——清理脚本不该删掉验收要用的证据。
        keep = {snaps[-1]}
        for name in ("sim_kpi.json", "kpi.json"):
            has = [p for p in snaps if (p / name).exists()]
            if has:
                keep.add(has[-1])
        out.extend(p for p in snaps if p not in keep)
    return out


def plan_artifacts(keep: int) -> list[Path]:
    art = ROOT / "artifacts"
    if not art.exists():
        return []
    bundles = sorted((p for p in art.iterdir() if p.is_dir()),
                     key=lambda p: p.stat().st_mtime)
    return bundles[:-keep] if keep > 0 else bundles


def collect(keep_artifacts: int) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for rel in DIRS:
        p = ROOT / rel
        if p.exists():
            items.append((p, "目录"))
    out = ROOT / "out"
    if out.exists():
        for p in sorted(out.iterdir()):
            if p.is_file() and p.suffix == ".png" and p.name.startswith(SHOT_PREFIXES):
                items.append((p, "临时截图"))
    for pat in GLOBS:
        for p in ROOT.glob(pat):
            if p.exists() and not any(p == q for q, _ in items):
                items.append((p, "缓存/备份"))
    for p in plan_runs(keep_artifacts):
        items.append((p, "旧 run 快照"))
    for p in plan_artifacts(keep_artifacts):
        items.append((p, "旧产物包"))
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="清理工作区")
    ap.add_argument("--apply", action="store_true", help="真的删除（默认只预览）")
    ap.add_argument("--keep-artifacts", type=int, default=1,
                    help="保留最近 N 套 artifacts 产物包（0 = 全删）")
    a = ap.parse_args(argv)

    items = collect(a.keep_artifacts)
    if not items:
        print("没有可清理的东西")
        return 0

    by_kind: dict[str, list[int]] = {}
    total = 0
    for p, kind in items:
        n = size(p)
        total += n
        by_kind.setdefault(kind, []).append(n)

    print("可清理 %d 项，共 %s\n" % (len(items), human(total)))
    for kind, sizes in sorted(by_kind.items()):
        print("  %-10s %2d 项  %s" % (kind, len(sizes), human(sum(sizes))))
    print()
    for p, kind in items[:12]:
        print("  - [%s] %s  %s" % (kind, p.relative_to(ROOT), human(size(p))))
    if len(items) > 12:
        print("  … 其余 %d 项略" % (len(items) - 12))

    if not a.apply:
        print("\n只是预览。确认无误后加 --apply 执行。")
        return 0

    done = 0
    for p, _kind in items:
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            done += 1
        except Exception as e:                        # noqa: BLE001
            print("  删不掉 %s：%r" % (p, e))
    left = size(ROOT)
    print("\n已删除 %d/%d 项，释放 %s；工作区现在 %s"
          % (done, len(items), human(total), human(left)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
