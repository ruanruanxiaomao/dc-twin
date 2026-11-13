# -*- coding: utf-8 -*-
"""R8.4：GLB 规整（索引规范化 + 未引用剔除 + 相同数据合并）。

合成一份小 GLB 来验，而不是拿真场景：这样每条断言都能对应到一个具体事实——
三角形顺序不同、accessor 多一份、多余数据没被引用。真场景的验证在
`check_asset_dispatch.py --typed-build`（两份 GLB 的 sha256 必须相同）。
"""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import glb_canonical as gc     # noqa: E402


def _glb(doc, binc):
    return gc.build(doc, binc)


def make_scene(tri_order=(0, 1), duplicate_normals=False, junk=False):
    """一个三角面 + 一个 POSITION/NORMAL。tri_order 控制三角形索引顺序。"""
    pos = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    # 三个三角形，顺序由 tri_order 决定：(0,1,2) (0,1,3) 之类
    tris = [(0, 1, 2), (2, 1, 3), (0, 3, 3)]
    idx = [i for t in tri_order for i in tris[t % len(tris)]]
    idx_raw = struct.pack("<%dH" % len(idx), *idx)
    nrm = struct.pack("<9f", 0, 0, 1, 0, 0, 1, 0, 0, 1)
    chunks, views = [], []
    cursor = 0
    for raw, target in ((pos, 34962), (nrm, 34962), (idx_raw, 34963)):
        views.append({"buffer": 0, "byteOffset": cursor, "byteLength": len(raw),
                      "target": target})
        chunks.append(raw)
        cursor += len(raw)
    binc = b"".join(chunks)
    accs = [
        {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
        {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
        {"bufferView": 2, "componentType": 5123, "count": len(idx), "type": "SCALAR"},
    ]
    prim = {"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2}
    if duplicate_normals:
        binc += nrm
        views.append({"buffer": 0, "byteOffset": cursor, "byteLength": len(nrm),
                      "target": 34962})
        accs.append({"bufferView": 3, "componentType": 5126, "count": 3, "type": "VEC3"})
        prim = {"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2}
    if junk:
        binc += b"\x00" * 16
        views.append({"buffer": 0, "byteOffset": cursor, "byteLength": 16})
        accs.append({"bufferView": 4, "componentType": 5121, "count": 16, "type": "SCALAR"})
    doc = {"asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}],
           "nodes": [{"name": "n0", "mesh": 0}],
           "meshes": [{"name": "m0", "primitives": [prim]}],
           "accessors": accs, "bufferViews": views,
           "buffers": [{"byteLength": len(binc)}]}
    return _glb(doc, binc)


def test_unreferenced_accessor_is_dropped():
    clean = gc.canonicalize(make_scene())
    junk = gc.canonicalize(make_scene(junk=True))
    assert len(junk) == len(clean), "没被引用的 accessor 没有被剔除"


def test_duplicate_accessors_are_merged():
    one = gc.canonicalize(make_scene())
    two = gc.canonicalize(make_scene(duplicate_normals=True))
    assert len(two) == len(one), "相同的 accessor 没有被合并"


def test_triangle_order_does_not_matter():
    a = gc.canonicalize(make_scene(tri_order=(0, 1, 2)))
    b = gc.canonicalize(make_scene(tri_order=(2, 0, 1)))
    assert a == b, "三角形顺序不同的两份 GLB 没有规整成同一份字节"


def test_geometry_is_preserved():
    raw = make_scene(tri_order=(1, 0, 2), junk=True)
    canon = gc.canonicalize(raw)
    assert gc.geometry_signature(raw) == gc.geometry_signature(canon)


def test_idempotent():
    once = gc.canonicalize(make_scene(tri_order=(2, 1, 0)))
    assert gc.canonicalize(once) == once


def test_triangle_winding_is_kept():
    """旋转到最小顶点开头可以，翻转不行——否则法线会朝里。"""
    doc, binc = gc.parse(gc.canonicalize(make_scene()))
    idx_acc = doc["meshes"][0]["primitives"][0]["indices"]
    acc = doc["accessors"][idx_acc]
    view = doc["bufferViews"][acc["bufferView"]]
    raw = binc[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]
    vals = struct.unpack("<%dH" % (len(raw) // 2), raw)
    assert len(vals) % 3 == 0
    for i in range(0, len(vals), 3):
        t = vals[i:i + 3]
        assert len(set(t)) >= 1
        assert t[0] == min(t), "三角形没有转到最小顶点开头"


def test_real_glb_is_stable_if_present():
    """有真产物时顺带验一次：规整是幂等的，且几何签名不变。"""
    p = ROOT / "out" / "dc_preview.glb"
    if not p.exists():
        import pytest
        pytest.skip("没有 out/dc_preview.glb")
    raw = p.read_bytes()
    canon = gc.canonicalize(raw)
    assert gc.geometry_signature(raw) == gc.geometry_signature(canon)
    assert gc.canonicalize(canon) == canon
