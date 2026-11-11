#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把单窗口客户端打包成 Windows 应用（PyInstaller onedir）。

为什么是 onedir 而不是 onefile：
  · onefile 每次启动要把几十 MB 解包到临时目录，WebView2 还要在解包目录里找 DLL，
    首次启动 3–5 秒，且杀软更容易误报；
  · onedir 的 exe 旁边就是 `_internal/`，产物（out/、runs/、data/）也在同一层，
    用户点开 `out/` 就能看到模型和渲染图——这才像一个"项目应用"，而不是一个黑盒。

打包后目录结构（dist/DC Twin/）：

    DC Twin.exe            ← 双击即开
    _internal/             ← PyInstaller 运行时（Python / pywebview / WebView2 互操作 DLL）
    console/static/        ← 前端（three.js、10 个页签）
    data/  out/  runs/     ← 可写产物：3D 模型、KPI、路径、渲染图
    scripts/  dc_twin/     ← 重跑用的源码（需要本机 Python）
    config/  docs/         ← 配置与计划书

设计取舍：**界面打包进 exe，数据留在磁盘**。数据若打进 exe，用户既看不到 out/，
也没法重跑写盘；反过来把 Python 运行时也"外置"又失去打包的意义。

用法：
    python scripts/build_exe.py            # 打包 + 自检 + 打印体积
    python scripts/build_exe.py --no-build # 只重铺数据目录（改完 payload 快速验证）
    python scripts/build_exe.py --clean    # 先删旧 dist 再打包
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_ROOT = ROOT / "dist"
APP_NAME = "DC Twin"
APP_DIR = DIST_ROOT / APP_NAME
BUILD_DIR = ROOT / "build" / "pyinstaller"

# ---------------------------------------------------------------- 数据清单

#: 单文件（相对 ROOT）
FILES = [
    "docs/DC_MASTER_PLAN.md",
    "requirements.lock",
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
]

#: 整目录复制（相对 ROOT）
DIRS = [
    "console/static",
    "config",
    "data",
    "dc_twin",
    "scripts",
    "tests",
]

#: out/ 里要带走的文件（其余是中间产物/临时截图）
OUT_FILES = [
    "dc_preview.glb",
    "distribution_center.blend",
    "anim_dc.mp4",
    "CURRENT_STATUS.md",
    "validate_report.json",
    "artifact_report.json",
    "areas_report.json",
    "build_report.json",
    "kpi.json",
    "manifest.json",
    "cost_per_order.csv",
    "compare.md",
]

#: out/ 整目录带走（渲染图是"看得见成果"的证据）
OUT_DIRS = ["renders"]

#: 不复制的东西（缓存/备份/临时截图/帧序列）
SKIP_PATTERNS = (
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "*.pyc", "*.pyo", "*.bak", "*.blend1", ".webprofile",
    "tab_*.png", "tab2_*.png", "tab3_*.png", "tab4_*.png", "tab5_*.png",
    "webapp_*.png", "final_*.png", "pages_*.png",
)


def _skip(p: Path) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(p.name, pat) or fnmatch.fnmatch(str(p), pat)
               for pat in SKIP_PATTERNS)


def copy_tree(src: Path, dst: Path) -> int:
    n = 0
    for p in src.rglob("*"):
        if p.is_dir() or _skip(p):
            continue
        rel = p.relative_to(src)
        if any(_skip(Path(part)) for part in [rel.parent] if str(rel.parent) != "."):
            continue
        t = dst / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, t)
        n += 1
    return n


def latest_runs(dst: Path) -> tuple[int, int]:
    """每个场景只留最近一次快照。

    全量 runs/ 有 258 MB，而界面只读"最近一次"（KPI、PNL、账本、状态）；
    历史快照对本项目有意义，但对"发给别人看的应用"没有。
    """
    src = ROOT / "runs"
    n = 0
    kept = 0
    for scen in sorted(p for p in src.iterdir() if p.is_dir()):
        if scen.name in ("logs", "cache"):
            continue
        snaps = sorted((p for p in scen.iterdir() if p.is_dir()),
                       key=lambda p: p.stat().st_mtime)
        if not snaps:
            continue
        # 每次模拟产出两套东西：旧的 `kpi.json`（速算）和新的 `sim_kpi.json`
        # （tick 层 39 项）。只按 mtime 取最新会拿到没有 tick 指标的那套，
        # 界面"指标"页就会空白——所以两类各保一套。
        # 三种快照各自代表一层数据，缺一层界面上就少一块：
        #   latest  → 状态/账本读它
        #   sim_kpi → tick 层 39 项指标（sim_engine 产出）
        #   kpi     → 解析层 25 项（scenario_run 产出）
        # 只按 mtime 取最新，会拿到"只有 tick 没有解析层"的那套，指标页显示 0。
        pick = [snaps[-1]]
        for name in ("sim_kpi.json", "kpi.json"):
            has = [p for p in snaps if (p / name).exists()]
            if has and has[-1] not in pick:
                pick.append(has[-1])
        for s in pick:
            kept += 1
            n += copy_tree(s, dst / scen.name / s.name)
    return kept, n


def stage_payload(app_dir: Path) -> dict:
    """把界面/数据/源码铺到 exe 同级。"""
    files = 0
    for rel in FILES:
        src = ROOT / rel
        if not src.exists():
            continue
        (app_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, app_dir / rel)
        files += 1
    for rel in DIRS:
        src = ROOT / rel
        if src.exists():
            files += copy_tree(src, app_dir / rel)

    # tests/ 只要负测试那一个脚本（其余是 pytest 用例，界面跑不到）
    keep = "tests/test_validator_can_fail.py"
    tests = app_dir / "tests"
    if tests.exists():
        for p in list(tests.rglob("*")):
            if p.is_file() and p.relative_to(app_dir).as_posix() != keep:
                p.unlink()
        for d in sorted((p for p in tests.rglob("*") if p.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()

    out = app_dir / "out"
    out.mkdir(parents=True, exist_ok=True)
    for rel in OUT_FILES:
        src = ROOT / "out" / rel
        if src.exists():
            shutil.copy2(src, out / rel)
            files += 1
    for rel in OUT_DIRS:
        src = ROOT / "out" / rel
        if src.exists():
            files += copy_tree(src, out / rel)

    scen, run_files = latest_runs(app_dir / "runs")
    files += run_files

    # 空目录清理：打包目录里留一堆空壳只会让人困惑
    for d in sorted((p for p in app_dir.rglob("*") if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    return {"files": files, "scenarios": scen, "run_files": run_files}


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def run_pyinstaller() -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", APP_NAME,
        "--distpath", str(DIST_ROOT),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(BUILD_DIR),
        "--add-data", f"{ROOT / 'console' / 'static'}{os.pathsep}console/static",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "scenario_tools",
        "--hidden-import", "md2html",
        "--exclude-module", "matplotlib",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "PySide6",
        "--exclude-module", "IPython",
        str(ROOT / "dc_app.pyw"),
    ]
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打包 DC Twin 为 Windows 应用")
    ap.add_argument("--no-build", action="store_true", help="跳过 PyInstaller，只重铺数据")
    ap.add_argument("--clean", action="store_true", help="先删除旧 dist")
    ap.add_argument("--skip-check", action="store_true", help="不打运行时自检")
    a = ap.parse_args(argv)

    if a.clean and APP_DIR.exists():
        shutil.rmtree(APP_DIR)
        print("已删除旧 %s" % APP_DIR)

    t0 = time.time()
    if not a.no_build:
        run_pyinstaller()
    if not APP_DIR.exists():
        print("没有找到 %s，PyInstaller 似乎失败了" % APP_DIR, file=sys.stderr)
        return 1

    info = stage_payload(APP_DIR)
    exe = APP_DIR / (APP_NAME + ".exe")
    total = dir_size(APP_DIR)
    print("\n打包完成：%s" % exe)
    print("  数据文件 %d 个 · 场景快照 %d 套 · 总计 %.0f MB · 用时 %.1f s"
          % (info["files"], info["scenarios"], total / 1048576, time.time() - t0))

    if not a.skip_check:
        print("\n运行自检（起服务 → 逐个接口 → 退出）")
        rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "check_exe.py"),
                              "--app", str(APP_DIR)])
        if rc != 0:
            print("自检未通过，先别发出去", file=sys.stderr)
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
