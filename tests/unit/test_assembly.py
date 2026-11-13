# -*- coding: utf-8 -*-
"""D10a/D12：草稿 → 校验 → 显式提交。

这一层的价值全在"拒绝"上：能存不算本事，**该拒的时候真拒了、而且盘上没东西**
才算。所以测试只盯四件事，每件都同时断言返回值与磁盘状态。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import assembly       # noqa: E402
import build_variant as bv   # noqa: E402


def _enabled():
    sel = bv.jload(bv.SELECTION) if Path(bv.SELECTION).exists() else {}
    return sel.get("enabled") or []


@pytest.fixture(scope="module")
def enabled():
    e = _enabled()
    if not e:
        pytest.skip("selection 里没有 enabled")
    return e


@pytest.fixture()
def target(tmp_path):
    return tmp_path / "committed.json"


def test_no_token_is_refused(enabled, target):
    with pytest.raises(assembly.CommitRejected) as ei:
        assembly.commit(enabled, {}, None, out_spec=target)
    assert ei.value.code == "NEED_VALIDATE"
    assert not target.exists()


def test_stale_token_is_refused(enabled, target):
    tok = assembly.validate(enabled, {})["token"]
    with pytest.raises(assembly.CommitRejected) as ei:
        assembly.commit(enabled, {"RCV-01": [1.5, 0.0]}, tok, out_spec=target)
    assert ei.value.code == "STALE_PLAN"
    assert not target.exists()


def test_invalid_geometry_is_refused(enabled, target):
    """把收货区拖进货架带：L3/L23 必须报出来，且不许落盘。"""
    moves = {"RCV-01": [0.0, -52.0]}
    v = assembly.validate(enabled, moves)
    assert not v["ok"], "越界草稿居然通过校验"
    with pytest.raises(assembly.CommitRejected) as ei:
        assembly.commit(enabled, moves, v["token"], out_spec=target)
    assert ei.value.code == "GEOMETRY_INVALID"
    assert not target.exists()


def test_valid_commit_writes(enabled, target):
    tok = assembly.validate(enabled, {})["token"]
    r = assembly.commit(enabled, {}, tok, out_spec=target)
    assert r["ok"] and r["issues"] == 0
    assert target.exists()
    import json
    spec = json.loads(target.read_text(encoding="utf-8"))
    wh = next(w for w in spec["warehouses"] if w["id"] == "W")
    assert wh["areas"], "写出的 spec 里 W 仓没有功能区"


def test_move_limit(enabled):
    with pytest.raises(assembly.CommitRejected) as ei:
        assembly.plan(enabled, {"RCV-01": [999.0, 0.0]})
    assert ei.value.code == "MOVE_TOO_FAR"
