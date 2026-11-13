# -*- coding: utf-8 -*-
"""R8：3D 资产目录（asset_spec）与一致性门。

守四件事：

1. **一个键管两边**：rack_assets / equipment_assets 与 rack_spec / equipment_spec 的
   类型键**双向相等**——多一个（凭空造类型）少一个（漏资产）都判红。
2. **不臆造母版**：每个 ``glb:`` 源都能在 GLB 的 JSON chunk 里查到；查到的是 38 个
   asset_ 根，不是记忆里的数字。
3. **footprint 是算出来的**：货架 footprint 由 rack_spec.geometry 复算（3 货格宽 ×
   深位数），凑数会红；料箱货架比托盘货架小一号正是靠这条抓出来的。
4. **门能失败**：``--self-test`` 注入的 7 个反例必须全被抓住；``--strict`` 在还有
   pending 资产时必须红（R8.2 落地后它才该转绿）。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import check_asset_consistency as cac      # noqa: E402
import _subproc                            # noqa: E402

CONFIG = ROOT / "config"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec():
    return jload(CONFIG / "asset_spec.json")


@pytest.fixture(scope="module")
def ctx(spec):
    glb = cac.glb_inventory(ROOT / spec["meta"]["provenance"]["glb"])
    return spec, jload(CONFIG / "rack_spec.json"), jload(CONFIG / "equipment_spec.json"), glb


def test_spec_schema_and_bijection(ctx):
    spec, rack, equip, _glb = ctx
    ok, detail, problems = cac.check_consistency(spec, rack, equip)
    assert ok, problems
    assert detail["rack_types"] == detail["rack_assets"]
    assert detail["equipment_types"] == detail["equipment_assets"]


def test_every_glb_source_exists_in_the_binary(ctx):
    """38 个母版是实测出来的，不是抄的：这里现算一遍再比对。"""
    spec, _rack, _equip, glb = ctx
    assert glb["magic"] == "glTF" and glb["version"] == 2
    assert len(glb["roots"]) == spec["meta"]["provenance"]["measured"]["asset_roots"]
    assert glb["bytes"] == spec["meta"]["provenance"]["measured"]["bytes"]
    ok, detail, problems = cac.check_sources(spec, glb)
    assert ok, problems
    assert detail["glb_roots"] == 38


def test_claims_are_true(ctx):
    spec = ctx[0]
    ok, detail, problems = cac.check_truthful(spec)
    assert ok, problems
    assert detail["lines_checked"] >= 5


def test_footprint_follows_geometry(ctx):
    spec, rack, _equip, _glb = ctx
    ok, detail, problems = cac.check_footprint(spec, rack)
    assert ok, problems
    # 托盘货架 3 货格宽 = 2.91 m，料箱货架小一号 = 1.95 m
    assert spec["rack_assets"]["selective_pallet"]["footprint_m"] == [2.91, 1.35]
    assert spec["rack_assets"]["carton_flow"]["footprint_m"] == [1.95, 0.54]
    assert detail["bays"] == 3


def test_gaps_are_named(ctx):
    spec = ctx[0]
    ok, detail, problems = cac.check_gaps(spec)
    assert ok, problems
    assert detail["gap_entries"] >= 1


def test_gate_self_test_catches_all_injections():
    r = _subproc.run(["scripts/check_asset_consistency.py", "--self-test"],
                     cwd=str(ROOT))
    out = r.stdout or ""
    assert r.returncode == 0, out + (r.stderr or "")
    assert "ASSET_SELF_TEST PASS" in out
    assert "MISSED" not in out


def test_strict_mode_still_red_while_pending():
    """R8.2 之前，28 项资产仍是 pending —— 这条把"没做完"钉成事实，不许悄悄翻绿。"""
    r = _subproc.run(["scripts/check_asset_consistency.py", "--strict"],
                     cwd=str(ROOT))
    assert r.returncode == 1
    assert "NO_PENDING_OK" in (r.stdout or "")
    rep = json.loads((ROOT / "out" / "asset_consistency_report.json")
                     .read_text(encoding="utf-8"))
    assert rep["strict_ok"] is False and rep["pending_assets"]
