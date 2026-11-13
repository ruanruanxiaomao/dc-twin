"""rack_mesh_probe.py — R8.2 渲染层探针（Blender 内运行）。

纯 Python 的门已经能证明"9 类几何的**数据**不同"；这里再往下一层：**真的在 Blender 里
建出 mesh**，量顶点数、面数、包围盒，证明"数据不同"确实变成了"模型不同"，而不是
在 `bmesh` 那一步被压成一样。

用法：

    blender -b --factory-startup -P scripts/rack_mesh_probe.py

产物：``out/rack_mesh_probe.json``，带 ``source_sha``（rack_assets.py 的哈希）——
探针和几何源码对不上时，``check_asset_dispatch.py --probe`` 会判它过期，
避免"跑的是三个月前的探针"这种假证据。
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

import bpy          # noqa: E402
import build_dc     # noqa: E402  (顶层只读数据、不建场景；main() 在 __main__ 下才跑)
import rack_assets  # noqa: E402


def _bbox(mesh):
    if not mesh.vertices:
        return None, None
    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]
    zs = [v.co.z for v in mesh.vertices]
    return [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]


def _geom_sha(mesh):
    """顶点坐标排序后取哈希：盒子数相同的两类（横梁式 vs 压入式）也能分开。"""
    co = sorted((round(v.co.x, 4), round(v.co.y, 4), round(v.co.z, 4))
                for v in mesh.vertices)
    raw = json.dumps(co, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:16]


def main():
    rows = {}
    for mech in rack_assets.MECHANISMS:
        mesh = (build_dc.unit_rack_mesh(4, 'rack_post') if mech == 'selective_pallet'
                else build_dc.rack_type_mesh(mech, 4))
        lo, hi = _bbox(mesh)
        rows[mech] = {
            "mesh": mesh.name,
            "verts": len(mesh.vertices),
            "polys": len(mesh.polygons),
            "materials": len(mesh.materials),
            "bbox_min": [round(v, 4) for v in lo] if lo else None,
            "bbox_max": [round(v, 4) for v in hi] if hi else None,
            "geom_sha": _geom_sha(mesh),
        }
    src = open(os.path.join(HERE, 'rack_assets.py'), 'rb').read()
    rep = {
        "schema_version": "1.0",
        "kind": "rack_mesh_probe",
        "blender": bpy.app.version_string,
        "source_sha": hashlib.sha256(src).hexdigest(),
        "levels": 4,
        "mechanisms": rows,
        "distinct_verts": len({r["verts"] for r in rows.values()}),
        "distinct_geoms": len({r["geom_sha"] for r in rows.values()}),
    }
    out = os.path.join(ROOT, 'out', 'rack_mesh_probe.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('PROBE_OK', json.dumps({k: (v["verts"], v["polys"]) for k, v in rows.items()},
                                 ensure_ascii=False))


if __name__ == '__main__':
    main()
