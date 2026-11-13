"""export_glb.py — 导出网页预览 GLB（静态快照，明确不含动画）。

审计 P1-4 整改：
  · use_selection=True，排除调试层 NAV_CORRIDORS / AGV_ROUTE_LINES 与文字角标；
  · 加 DC_ROOT empty 作为层级根；
  · 输出 out/glb_report.json（节点/网格/材质/动画数、禁用节点检查）；
  · 文档明确「静态预览」，不再暗示可动画。

运行： blender -b --factory-startup -P scripts/export_glb.py
"""
import bpy
import hashlib
import json
import os
import struct
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 与 build_dc 同一套环境变量：默认值不变，变体构建设 DC_OUT 即可全部落位。
OUT = os.environ.get('DC_OUT') or os.path.join(ROOT, 'out')
BLEND = os.environ.get('DC_BLEND') or os.path.join(OUT, 'distribution_center.blend')
GLB = os.environ.get('DC_GLB') or os.path.join(OUT, 'dc_preview.glb')
FORBIDDEN = ('NAV_CORRIDORS', 'AGV_ROUTE_LINES')


def main():
    bpy.ops.wm.open_mainfile(filepath=BLEND)
    scene = bpy.context.scene
    root = bpy.data.objects.new('DC_ROOT', None)
    scene.collection.objects.link(root)

    selected = []
    for o in list(bpy.data.objects):
        o.select_set(False)
        if o.type != 'MESH' or any(x in o.name for x in FORBIDDEN):
            continue
        o.parent = root
        o.matrix_parent_inverse = root.matrix_world.inverted()
        o.select_set(True)
        selected.append(o.name)
    root.select_set(True)

    bpy.ops.export_scene.gltf(
        filepath=GLB,
        export_format='GLB',
        export_yup=True,
        export_apply=True,
        export_materials='EXPORT',
        export_cameras=False,
        export_lights=False,
        export_animations=False,
        use_selection=True,
    )

    # R8.4：导出器本身不是字节可复现的（三角化顺序 + accessor 是否复用都随内部哈希走，
    # 实测同一场景两次运行差 1864 字节 / 一个 accessor）。这里原地规整一次：
    # 索引规范化 + 未引用数据剔除 + 相同数据合并。只改表示，不改几何（见 ADR-13）。
    import glb_canonical
    raw = open(GLB, 'rb').read()
    canon = glb_canonical.canonicalize(raw)
    sig_same = glb_canonical.geometry_signature(raw) == glb_canonical.geometry_signature(canon)
    if not sig_same or not canon.startswith(b'glTF'):
        raise SystemExit('GLB 规整改变了几何，拒绝写出（glb_canonical.py 回归）')
    if canon != raw:
        with open(GLB, 'wb') as f:
            f.write(canon)

    d = canon
    n = struct.unpack('<I', d[12:16])[0]
    j = json.loads(d[20:20 + n])
    names = [x.get('name', '') for x in j.get('nodes', [])]
    report = {
        'bytes': len(d),
        'generator': j.get('asset', {}).get('generator'),
        'nodes': len(names),
        'meshes': len(j.get('meshes', [])),
        'materials': len(j.get('materials', [])),
        'animations': len(j.get('animations', [])),
        'has_DC_ROOT': 'DC_ROOT' in names,
        'scene_roots': len(j.get('scenes', [{}])[0].get('nodes', [])),
        'forbidden_nodes': [x for x in names if any(f in x for f in FORBIDDEN)],
        'selected_meshes': len(selected),
        'canonical': True,
        'canonicalized_bytes': len(raw) - len(canon),
        'geometry_signature': glb_canonical.geometry_signature(d),
        'sha256': hashlib.sha256(d).hexdigest(),
        'note': '静态预览快照（export_animations=False）；动画请见 anim_dc.mp4 / .blend；'
                '字节已规整（ADR-13），同一场景两次导出应逐字节相同',
    }
    with open(os.path.join(OUT, 'glb_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('EXPORT_OK', GLB, report['bytes'], 'bytes',
          'nodes', report['nodes'], 'forbidden', report['forbidden_nodes'])


if __name__ == '__main__':
    main()
