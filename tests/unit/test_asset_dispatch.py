# -*- coding: utf-8 -*-
"""R8.2：货架派发与几何分型。

守四件事：

1. **9 类就是 9 个东西**：几何指纹互不相同（算出来的，不是看图看出来的）。
2. **派发不许静默**：声明了 rack_type 就必须能反查到同一机理；查不到抛错，
   而不是"画成横梁式但没人知道"。
3. **没有 rack_type 就是 legacy**：默认布局一个都没声明，所以走老路、字节不变。
4. **负对照不空转**：强行改回 selective 必须塌成 1 类并拒绝 8 个。
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import rack_assets as ra           # noqa: E402
import _subproc                    # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec():
    return jload(ROOT / "config" / "asset_spec.json")


def test_nine_mechanisms_look_different(spec):
    fps = {t: ra.fingerprint(t) for t in spec["rack_assets"]}
    assert len(set(fps.values())) == len(fps) == 9
    # 双深与横梁：盒子数不同、且支撑轨方向不同（尺寸集合不同）
    assert ra.signature("double_deep")["boxes"] != ra.signature("selective_pallet")["boxes"]
    assert ra.signature("drive_in")["dims"] != ra.signature("drive_through")["dims"]


def test_resolve_legacy_without_rack_type(spec):
    assert ra.resolve({}, spec)["mode"] == "legacy"
    assert ra.resolve({"id": "RACK-WA101"}, spec)["mode"] == "legacy"


def test_resolve_maps_type_to_same_mechanism(spec):
    for t in spec["rack_assets"]:
        d = ra.resolve({"rack_type": t}, spec)
        assert d["mode"] == "procedural"
        assert d["mechanism"] == t


def test_resolve_refuses_unknown_and_mismatch(spec):
    with pytest.raises(ra.DispatchError):
        ra.resolve({"rack_type": "vlm"}, spec)
    bad = copy.deepcopy(spec)
    bad["rack_assets"]["double_deep"]["source"] = "procedural:rack_gravity"
    with pytest.raises(ra.DispatchError):
        ra.resolve({"rack_type": "double_deep"}, bad)


def test_force_selective_collapses_and_refuses(spec):
    forced = ra.force_selective(spec)
    reached, refused = set(), 0
    for t in forced["rack_assets"]:
        try:
            reached.add(ra.resolve({"rack_type": t}, forced)["mechanism"])
        except ra.DispatchError:
            refused += 1
    assert reached == {"selective_pallet"} and refused == 8


def test_default_layout_has_no_rack_type():
    """默认数据一旦混进 rack_type，3D 产出就变了——必须先重冻结，不能悄悄变。"""
    lay = jload(ROOT / "data" / "layout.json")
    typed = [rk["id"] for wh in lay["warehouses"] for rk in wh["racks"] if "rack_type" in rk]
    assert typed == []


def test_dispatch_gate_passes():
    r = _subproc.run(["scripts/check_asset_dispatch.py", "--quiet"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "ASSET_DISPATCH_OK" in (r.stdout or "")


def test_dispatch_gate_self_test():
    r = _subproc.run(["scripts/check_asset_dispatch.py", "--self-test"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "ASSET_DISPATCH_SELF_TEST PASS" in (r.stdout or "")
    assert "MISSED" not in (r.stdout or "")


def test_variant_gate_reads_probe():
    """探针产物存在时，分型门必须通过；没有产物就跳过（洁净室不跑 Blender）。"""
    probe = ROOT / "out" / "variant_probe.json"
    if not probe.exists():
        pytest.skip("没有 out/variant_probe.json")
    r = _subproc.run(["scripts/check_asset_dispatch.py", "--quiet"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
