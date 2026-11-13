# -*- coding: utf-8 -*-
"""v1.0 冻结：三锚 + 计数 + 链步数 + backlog 记账。

冻结不是声明，是**会红的门**：默认产物变了、计数变了、链步数变了、
backlog 被清空，任意一条都让这里红。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import check_milestone as cm   # noqa: E402
import _subproc               # noqa: E402


def _freeze():
    return json.loads((ROOT / "config" / "milestone_v1.json").read_text(encoding="utf-8"))


def test_milestone_gate_passes():
    r = _subproc.run(["scripts/check_milestone.py", "--quiet"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "MILESTONE_V1_OK" in (r.stdout or "")


def test_milestone_self_test():
    r = _subproc.run(["scripts/check_milestone.py", "--self-test"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "MILESTONE_SELF_TEST PASS" in (r.stdout or "")


def test_anchor_drift_is_caught():
    """把锚改成错的，必须红——否则冻结件就是橡皮章。"""
    import copy
    bad = copy.deepcopy(_freeze())
    bad["anchors"]["data/layout.json"]["sha256_prefix"] = "00000000"
    ok, _detail, problems = cm.check_anchors(bad)
    assert not ok and problems


def test_count_drift_is_caught():
    import copy
    bad = copy.deepcopy(_freeze())
    bad["counts"]["locations"] = 999
    ok, _detail, problems = cm.check_counts(bad)
    assert not ok and problems


def test_backlog_must_be_recorded():
    """v1.1 的欠账必须记账：空 backlog 直接红（不许口头承诺）。"""
    import copy
    bad = copy.deepcopy(_freeze())
    bad["backlog_v1_1"] = []
    ok, _detail, problems = cm.check_scope(bad)
    assert not ok and problems


def test_chain_step_count_is_frozen():
    """refactor 链步数与冻结件一致（加门必须同步改冻结件）。"""
    import copy
    bad = copy.deepcopy(_freeze())
    bad["refactor_chain_steps"] = 5
    ok, _detail, problems = cm.check_scope(bad)
    assert not ok and problems
