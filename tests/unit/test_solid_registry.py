# -*- coding: utf-8 -*-
"""R8.11：实体登记册（纯函数层 + 门）。

要证的不是"几何算得对"，而是**没有任何实体隐身**：

1. 布局里每个实体键都登记了（`elevators` 这种新键必须被判红）；
2. 每种实体至少有一个消费者（登记了没人管的，等于隐身）；
3. 登记册几何与 `spec_layout` 的历史口径逐架一致（两份实现不许漂移）；
4. 功能区（不在 layout 里，R8.9 的洞）也必须进册；
5. 消费者集合是显式的：放宽 L2/L3 会改金标准，门必须红。
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import solid_registry as reg       # noqa: E402
import check_solid_registry as csr  # noqa: E402
import _subproc                    # noqa: E402


def _layout():
    return json.loads((ROOT / "data" / "layout.json").read_text(encoding="utf-8"))


def test_registry_gate_passes():
    r = _subproc.run(["scripts/check_solid_registry.py", "--quiet"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "REGISTRY_COVERAGE_OK" in (r.stdout or "")


def test_registry_self_test():
    r = _subproc.run(["scripts/check_solid_registry.py", "--self-test"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "REGISTRY_SELF_TEST PASS" in (r.stdout or "")


def test_unknown_entity_key_is_caught():
    """新加一类实体却没登记 → 覆盖门必须红（这就是 R8.9/R8.10 的失败模式）。"""
    whs = copy.deepcopy(_layout()["warehouses"])
    whs[0]["elevators"] = [{"id": "E1", "rect": [0, 0, 2, 2]}]
    ok, _detail, problems = csr.check_coverage(whs)
    assert not ok and any("elevators" in p for p in problems)


def test_orphan_kind_is_caught(monkeypatch):
    """登记了但没人消费 → 孤儿，必须红。"""
    monkeypatch.setitem(reg.KINDS, "sprayer",
                        {"space": "rect", "guards": (), "note": "test"})
    ok, detail, _problems = csr.check_coverage(_layout()["warehouses"])
    assert not ok and "sprayer" in detail["orphans"]


def test_guard_typo_is_caught(monkeypatch):
    """guards 写了个不存在的消费者（改名没同步）→ 必须红。"""
    monkeypatch.setitem(reg.KINDS, "sprayer",
                        {"space": "rect", "guards": ("nobody.checks",), "note": "test"})
    ok, _detail, _problems = csr.check_coverage(_layout()["warehouses"])
    assert not ok


def test_rect_parity_with_spec_layout():
    """登记册的几何必须与 spec_layout 逐架一致（顺序也要一致）。"""
    ok, detail, problems = csr.check_rect_parity(_layout()["warehouses"])
    assert ok, problems
    assert detail["racks"] == 196


def test_scope_must_not_widen():
    """把输送线塞进 L2/L3 会改变默认校验结果 → 门必须红。"""
    ok, _detail, problems = csr.check_scope(l2l3=list(reg.L2L3_KINDS) + ["conveyor"])
    assert not ok and problems


def test_conveyor_is_not_in_l2l3():
    """输送线只走导航（已知窄口），这条差异必须是显式的，不许哪天被悄悄补上。"""
    assert "conveyor" in reg.NAV_KINDS
    assert "conveyor" not in reg.L2L3_KINDS
    assert "nav.bake" in reg.KINDS["conveyor"]["guards"]


def test_physical_area_without_rect_is_caught():
    ok, _detail, _problems = csr.check_areas(
        {"areas": [{"id": "X", "physical": True, "rect": None}]})
    assert not ok


def test_spawn_points_are_registry_backed():
    """宽度轴的 blockers 必须来自登记册（否则又是"每加一条轴手写一遍"）。"""
    spec = json.loads((ROOT / "config" / "layout_spec.json").read_text(encoding="utf-8"))
    pts = reg.spawn_points(spec["warehouses"][0])
    assert len(pts) == 12
    assert pts[0]["id"] == "spawn_01"
    assert max(p["x"] for p in pts) == pytest.approx(98.25)
