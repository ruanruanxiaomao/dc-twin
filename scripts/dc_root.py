#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/dc_root.py —— 项目根目录的**唯一**解析口径。

为什么需要它：开发时 `Path(__file__).parent.parent` 是对的，打包后就不是了。
PyInstaller 把 .py 打进归档，运行时 `__file__` 落在 `_MEIPASS`（临时解包目录），
而 `config/ data/ out/ runs/ saves/` 必须留在 **exe 同级**——用户要能看到、也要能写。

历史三 bug（EXE_OK 那三条）里的"`_internal/` 静态路径"就是同一个坑。之前的做法是
在 `console/server.py` 里单独写一遍，现在游戏层（`console/game.py`、`scripts/game_*.py`）
和引擎（`scripts/sim_engine.py`）也用同一套，避免出现"页面能开、点开局说没有这个场景"。

用法：
    from dc_root import project_root
    ROOT = project_root(__file__)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root(fallback_file: str) -> Path:
    """返回项目根目录。

    - 冻结（exe）：`DC_ROOT` 环境变量 → 否则 exe 所在目录
    - 源码：`fallback_file` 的上两级（scripts/xxx.py 或 console/xxx.py → 项目根）
    """
    if getattr(sys, "frozen", False):
        env = os.environ.get("DC_ROOT")
        if env:
            return Path(env).resolve()
        return Path(sys.executable).resolve().parent
    return Path(fallback_file).resolve().parent.parent
