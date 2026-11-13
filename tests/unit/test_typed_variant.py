# -*- coding: utf-8 -*-
"""D11-A：分型进关卡场景，出厂冻结。

A 方案的全部承诺就四条：

1. 关卡 spec 是**脚本生成**的（手改 = 第二份真相）；
2. 关卡场景真的用上 9 类货架 / 4 型输送，且校验 0 issue；
3. 出厂布局**一个字节不动**，且没有任何分型键；
4. 9 类几何指纹两两不同（复用 R8.2 探针，不新造视觉门）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import check_typed_variant as ctv   # noqa: E402
import _subproc                    # noqa: E402


def _jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def test_typed_gate_passes():
    r = _subproc.run(["scripts/check_typed_variant.py", "--quiet"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "TYPED_VARIANT_OK" in (r.stdout or "")


def test_typed_gate_self_test():
    r = _subproc.run(["scripts/check_typed_variant.py", "--self-test"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "TYPED_SELF_TEST PASS" in (r.stdout or "")


def test_typed_spec_is_generated_not_edited():
    r = _subproc.run(["scripts/build_typed_spec.py", "--check"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "TYPED_SPEC_OK" in (r.stdout or "")


def test_typed_layout_has_nine_mechanisms():
    """9 类机理必须真的声明在关卡 spec 里。

    不读 `out/exp/typed/` 的产物：那是门的中间文件，全量里会被其它清理型测试
    抹掉（实测顺序一变就 FileNotFound）。"展开后 1033 库位 / 4 型输送 / 0 issue"
    由门自己的 `TYPED_LAYOUT_OK` 断言，而 `test_typed_gate_passes` 保证门返回 0。
    """
    spec = _jload(ROOT / "config" / "layout_spec_typed.json")
    types = {g.get("rack", {}).get("rack_type")
             for w in spec["warehouses"] for g in w.get("racks") or []}
    types.discard(None)
    assert len(types) == 9
    styles = {cv.get("style") for w in spec["warehouses"] for cv in w.get("conveyors") or []}
    styles.discard(None)
    assert len(styles) == 4


def test_factory_layout_has_no_typed_keys():
    lay = _jload(ROOT / "data" / "layout.json")
    for w in lay["warehouses"]:
        assert all(not rk.get("rack_type") for rk in w.get("racks") or [])
        assert all(not cv.get("style") for cv in w.get("conveyors") or [])
        assert all(not st.get("sorter") for st in w.get("stations") or [])


def test_probe_covers_nine_racks_and_four_conveyors():
    r = _jload(ROOT / "out" / "rack_mesh_probe.json")
    v = _jload(ROOT / "out" / "variant_probe.json")
    assert r["distinct_geoms"] == 9
    assert v["distinct_conveyor"] == 4
    assert len(v["sorters"]) == 7
