"""export_glb.py — 导出网页预览 GLB（静态快照，明确不含动画）。

审计 P1-4 整改：
  · use_selection=True，排除调试层 NAV_CORRIDORS / AGV_ROUTE_LINES 与文字角标；
  · 加 DC_ROOT empty 作为层级根；
  · 输出 out/glb_report.json（节点/网格/材质/动画数、禁用节点检查）；
  · 文档明确「静态预览」，不再暗示可动画。

运行： blender -b --factory-startup -P scripts/export_glb.py
"""
import bpy
import json
import os
import struct

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'out')
BLEND = os.path.join(OUT, 'distribution_center.blend')
GLB = os.path.join(OUT, 'dc_preview.glb')
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

    d = open(GLB, 'rb').read()
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
        'note': '静态预览快照（export_animations=False）；动画请见 anim_dc.mp4 / .blend',
    }
    with open(os.path.join(OUT, 'glb_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('EXPORT_OK', GLB, report['bytes'], 'bytes',
          'nodes', report['nodes'], 'forbidden', report['forbidden_nodes'])


if __name__ == '__main__':
    main()
