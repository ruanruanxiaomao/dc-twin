"""scene_config.py — 场景配置（相机/灯光/园区车辆/道路）的唯一读取入口。

为什么需要单独一层：`data/config.json` 里除了渲染参数，还有**园区车辆与道路的地面
footprint**，它们会进入导航栅格的障碍集。用户扩仓（方案 A）时，北侧园区道路和停车位
会被新厂房吞掉，必须换成一套"扩仓后"的场景配置——否则叉车会停在厂房里，栅格会把
新作业带整片判成阻塞。

开关：环境变量 `DC_SCENE_CONFIG` 指向替代文件；默认仍是 `data/config.json`。
"""
from __future__ import annotations

import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA = os.path.join(ROOT, 'data')


def path() -> str:
    return os.environ.get('DC_SCENE_CONFIG') or os.path.join(DATA, 'config.json')


def load() -> dict:
    with open(path(), encoding='utf-8') as f:
        return json.load(f)
