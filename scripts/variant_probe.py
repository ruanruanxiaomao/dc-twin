"""variant_probe.py — R8.5 分型探针（Blender 内运行）。

纯 Python 能证明"盒子数据不同"，但输送/分拣是**沿折线画**的（`zone_detail.conveyor`），
分型差异要靠真的建一遍才知道有没有被台面逻辑吃掉。所以这里在 Blender 里：

* 4 种输送带台面各建一段 6 m 直线 → 量对象数/顶点数；
* 7 种分拣末端各建一个 → 量对象数/顶点数；
* 3 种程序化设备车体 → 量顶点数/包围盒。

产物 `out/variant_probe.json`，带 `source_sha`（三个源文件的哈希）。源码改了但没重跑
探针，`check_asset_dispatch.py --variant-probe` 会判它过期——证据不能靠旧文件充数。

用法：
    blender -b --factory-startup -P scripts/variant_probe.py
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

import bpy          # noqa: E402
import build_dc     # noqa: E402
import zone_detail  # noqa: E402
import equipment_assets  # noqa: E402


def _measure(prefix):
    """对象数、顶点数，以及**世界坐标摘要**。

    只比数量会被骗：第一版探针里 gravity 和 roller 都是 61 对象/488 顶点（坡度只改
    Z 坐标），slider/swing_wheel/tilt_tray/put_wall/chute 全是 6 对象/48 顶点，
    shuttle_car 与 ctu_robot 都是 9 个盒子。数量相同不等于长得一样，所以必须落到
    坐标上——这和货架探针用 `geom_sha` 是同一个教训。
    """
    # 后台模式下新建对象的 matrix_world / dimensions 还是旧的，必须先更新依赖图，
    # 否则量出来的全是"单位立方体"——第一版探针就因此把 4 个分型判成同一份几何。
    bpy.context.view_layer.update()
    objs = [o for o in bpy.data.objects if o.name.startswith(prefix) and o.type == 'MESH']
    pts = []
    for o in objs:
        mw = o.matrix_world
        for v in o.data.vertices:
            w = mw @ v.co
            pts.append((round(w.x, 3), round(w.y, 3), round(w.z, 3)))
    pts.sort()
    verts = sum(len(o.data.vertices) for o in objs)
    geom = hashlib.sha256(json.dumps(pts).encode('utf-8')).hexdigest()[:16]
    return {"objects": len(objs), "verts": verts, "geom": geom}


def _purge(prefix):
    for o in [o for o in bpy.data.objects if o.name.startswith(prefix)]:
        bpy.data.objects.remove(o, do_unlink=True)


def _sha(*names):
    h = hashlib.sha256()
    for n in names:
        h.update(open(os.path.join(HERE, n), 'rb').read())
    return h.hexdigest()


def main():
    # zone_detail 需要注入 build_dc 作为 D（与生产同一个注入方式）
    zone_detail.D = build_dc
    zone_detail.palette()
    coll = build_dc.collection('PROBE')

    conveyors = {}
    for style in zone_detail.CONVEYOR_STYLES:
        p = 'CV_%s_' % style.upper()
        zone_detail.conveyor(p + 'X', [(0.0, 0.0), (6.0, 0.0)], 0.9, coll, style=style)
        conveyors[style] = _measure(p)
        _purge(p)

    sorters = {}
    for kind in zone_detail.SORTER_KINDS:
        p = 'SR_%s_' % kind.upper()
        zone_detail.sorter(p + 'X', 0.0, 0.0, coll, rot=0.0, kind=kind)
        sorters[kind] = _measure(p)
        _purge(p)

    bodies = {}
    for name in equipment_assets.MECHANISMS:
        m = build_dc.equipment_mesh(name)
        pts = sorted((round(v.co.x, 3), round(v.co.y, 3), round(v.co.z, 3))
                     for v in m.vertices)
        bodies[name] = {"verts": len(m.vertices), "polys": len(m.polygons),
                        "geom": hashlib.sha256(json.dumps(pts).encode('utf-8')).hexdigest()[:16]}

    rep = {
        "schema_version": "1.0",
        "kind": "variant_probe",
        "blender": bpy.app.version_string,
        "source_sha": _sha('zone_detail.py', 'equipment_assets.py', 'rack_assets.py'),
        "conveyors": conveyors,
        "sorters": sorters,
        "equipment_bodies": bodies,
        "distinct_conveyor": len({v["geom"] for v in conveyors.values()}),
        "distinct_sorter": len({v["geom"] for v in sorters.values()}),
        "distinct_body": len({v["geom"] for v in bodies.values()}),
    }
    out = os.path.join(ROOT, 'out', 'variant_probe.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('VARIANT_PROBE_OK', json.dumps({
        "conveyors": rep["distinct_conveyor"], "sorters": rep["distinct_sorter"],
        "bodies": rep["distinct_body"]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
