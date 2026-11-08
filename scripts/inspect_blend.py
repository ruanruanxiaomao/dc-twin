"""inspect_blend.py — 在 Blender 里重开 .blend，独立实测文件内容（审计 §3.6 要求）。

产出：out/blend_audit.json
运行： blender -b --factory-startup -P scripts/inspect_blend.py
"""
import bpy
import json
import os
from mathutils import Vector

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'out')
BLEND = os.path.join(OUT, 'distribution_center.blend')
LAYOUT = json.load(open(os.path.join(ROOT, 'data', 'layout.json'), encoding='utf-8'))


def fcurves_of(obj):
    if not obj.animation_data or not obj.animation_data.action:
        return []
    act = obj.animation_data.action
    try:
        return list(act.fcurves)
    except AttributeError:
        out = []
        for layer in act.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    out.extend(cb.fcurves)
        return out


def main():
    bpy.ops.wm.open_mainfile(filepath=BLEND)
    scene = bpy.context.scene
    scene.frame_set(1)
    mn, mx = [1e9] * 3, [-1e9] * 3
    for o in bpy.data.objects:
        if o.type != 'MESH':
            continue
        for corner in o.bound_box:
            v = o.matrix_world @ Vector(corner)
            for i in range(3):
                mn[i], mx[i] = min(mn[i], v[i]), max(mx[i], v[i])
    agv = {}
    for w in LAYOUT['warehouses']:
        for rb in w['spawn']['robots']:
            o = bpy.data.objects.get(rb['id'])
            if o:
                agv[rb['id']] = [round(v, 4) for v in o.location[:2]]
    # 与 build_dc.py 同口径：只统计被场景对象实际使用的数据块。
    # bpy.data.meshes 会多出 extract_asset() 设了 use_fake_user 的资产母版残留。
    used_meshes = {o.data for o in bpy.data.objects if o.type == 'MESH'}
    used_mats = {m for mesh in used_meshes for m in mesh.materials if m}
    report = {
        'objects': len(bpy.data.objects),
        'meshes': len(used_meshes),
        'materials': len(used_mats),
        'datablock_meshes': len(bpy.data.meshes),
        'datablock_materials': len(bpy.data.materials),
        'cameras': sum(1 for o in bpy.data.objects if o.type == 'CAMERA'),
        'lights': sum(1 for o in bpy.data.objects if o.type == 'LIGHT'),
        'actions': len(bpy.data.actions),
        'mesh_objects': sum(1 for o in bpy.data.objects if o.type == 'MESH'),
        'font_labels': sum(1 for o in bpy.data.objects if o.type == 'FONT'),
        'bbox_min': [round(v, 4) for v in mn],
        'bbox_max': [round(v, 4) for v in mx],
        'bbox_size': [round(mx[i] - mn[i], 4) for i in range(3)],
        'fps': scene.render.fps,
        'frame_start': scene.frame_start,
        'frame_end': scene.frame_end,
        'agv_first_frame_xy': agv,
        'cargo_objects': sum(1 for o in bpy.data.objects
                             if o.name.startswith('CARGO_DYN_')),
        'cargo_with_action': sum(1 for o in bpy.data.objects
                                 if o.name.startswith('CARGO_DYN_')
                                 and o.animation_data and o.animation_data.action),
        'home_bays': sum(1 for o in bpy.data.objects
                         if o.name.startswith('HOME_BAY_')),
        'agv_with_hide_render_keyframe': sum(
            1 for rb_id in agv
            for o in [bpy.data.objects.get(rb_id)]
            if o and any(fc.data_path == 'hide_render' for fc in fcurves_of(o))),
    }
    with open(os.path.join(OUT, 'blend_audit.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('BLEND_AUDIT_OK', json.dumps({k: v for k, v in report.items()
                                        if k != 'agv_first_frame_xy'},
                                       ensure_ascii=False))


if __name__ == '__main__':
    main()
