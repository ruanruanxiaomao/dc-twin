#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令把环境弄好：`python scripts/bootstrap.py`。

为什么需要它：README 写的是 `python -m venv .venv && pip install -e ".[dev]"`，
换台机器照做常常卡在三个地方——用错了 python（比如 3.14 上某些依赖还没轮子）、
装完没跑 doctor、装完发现 `dc` 不在 PATH。这里把三步串起来，并且**用锁文件装**：

    requirements.lock   ← 验证过的精确版本（本机 + CI 都跑通过）

`--check` 只检查不安装，给 CI / 排查用：python 版本、依赖、doctor 是否 PASS。

用法：
    python scripts/bootstrap.py            # 建 .venv 并安装
    python scripts/bootstrap.py --check     # 只验证当前环境
    python scripts/bootstrap.py --force     # 删掉旧 .venv 重来
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
LOCK = ROOT / "requirements.lock"
MIN_PY = (3, 10)
#: 可选依赖：缺了不影响数据链/控制台，只影响"原生单窗口"这一种形态
OPTIONAL = {"pywebview", "pythonnet"}
TESTED = ((3, 11), (3, 13))      # CI 矩阵实际跑过的版本


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(cmd, **kw):
    print("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=str(ROOT), **kw).returncode


def check_python(v=None) -> tuple[bool, str]:
    v = v or sys.version_info
    ver = "%d.%d" % (v[0], v[1])
    if (v[0], v[1]) < MIN_PY:
        return False, "需要 Python >= %d.%d，当前 %s" % (MIN_PY + (ver,))
    if (v[0], v[1]) in TESTED:
        return True, "Python %s（CI 验证过的版本）" % ver
    if (v[0], v[1]) > TESTED[-1]:
        return True, "Python %s（超出 CI 矩阵 %s，能跑但没在 CI 验证过）" % (
            ver, "/".join("%d.%d" % t for t in TESTED))
    return True, "Python %s" % ver


def check_deps() -> tuple[list[str], list[str]]:
    """返回 (缺失的必需依赖, 未安装的可选依赖)。"""
    import importlib.metadata as md
    missing, optional = [], []
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split(";")[0].split("==")[0].strip()
        try:
            md.version(name)
        except Exception:
            (optional if name.lower() in OPTIONAL else missing).append(name)
    return missing, optional


def do_check() -> int:
    ok, why = check_python()
    print(("[OK]  " if ok else "[FAIL] ") + why)
    missing, optional = check_deps()
    if missing:
        print("[FAIL] 缺少依赖：%s（运行 python scripts/bootstrap.py 安装）"
              % ", ".join(missing), flush=True)
        return 1
    print("[OK]  必需依赖齐全（按 requirements.lock）", flush=True)
    if optional:
        print("[--]  可选依赖未装：%s（不影响数据链，只影响原生单窗口）"
              % ", ".join(optional), flush=True)
    rc = run([sys.executable, "-m", "dc_twin", "doctor"])
    return 0 if (ok and rc == 0) else 1


def do_install(force: bool) -> int:
    ok, why = check_python()
    print(("[OK]  " if ok else "[FAIL] ") + why)
    if not ok:
        return 1
    if force and VENV.exists():
        print("删除旧 .venv …")
        shutil.rmtree(VENV)
    if not VENV.exists():
        rc = run([sys.executable, "-m", "venv", str(VENV)])
        if rc:
            return rc
    py = str(venv_python())
    if not Path(py).exists():
        print("虚拟环境里没找到 python：%s" % py, file=sys.stderr)
        return 1
    rc = run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    if rc:
        return rc
    rc = run([py, "-m", "pip", "install", "-q", "-r", str(LOCK)])
    if rc:
        print("依赖安装失败。离线环境可先下载 wheel 再用 --no-index 安装。", file=sys.stderr)
        return rc
    # 再以可编辑方式装本项目，但不重复解析依赖（已在 lock 里锁死）
    rc = run([py, "-m", "pip", "install", "-q", "-e", ".", "--no-deps"])
    if rc:
        return rc
    rc = run([py, "-m", "dc_twin", "doctor"])
    print("\n完成。用这个解释器：")
    print("  %s -m dc_twin doctor" % py)
    print("  %s -m dc_twin run --scope scenarios" % py)
    if os.name == "nt":
        print("  %s\\Scripts\\activate" % VENV)
    else:
        print("  source %s/bin/activate" % VENV)
    return 0 if rc == 0 else rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="环境引导（建 venv + 按锁安装 + doctor）")
    ap.add_argument("--check", action="store_true", help="只检查当前环境，不安装")
    ap.add_argument("--force", action="store_true", help="删除已有 .venv 重新创建")
    a = ap.parse_args(argv)
    if not LOCK.exists():
        print("缺少 %s" % LOCK, file=sys.stderr)
        return 1
    return do_check() if a.check else do_install(a.force)


if __name__ == "__main__":
    sys.exit(main())
