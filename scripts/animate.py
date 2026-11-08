"""animate.py — 用 data/paths.jsonl + cargo_state.json 写 AGV 与货物关键帧。

第六阶段（闭环 + 全程在场 + 货物状态机）：
  · **不再隐藏任何 AGV**：所有车从 t=0 到 t_end 都在场，未派出/已回程的车
    停在 HOME 待命位，与 traffic_check.py 的严格口径一致；
  · 货物对象在取货前隐藏，t_pick 起跟随 AGV 轨迹，t_drop 落在交付点，
    之后保持可见（已交付），实现 PICK attach / DROP detach；
  · 同一帧多个关键帧按 frame 去重；hide_render 用 CONSTANT 插值，
    位置/旋转用 LINEAR；frame_end 由最后一个关键帧决定。

运行： blender -b --factory-startup -P scripts/animate.py
"""
import bpy
import json
import math
import os
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
BLEND = os.path.join(ROOT, 'out', 'distribution_center.blend')
PATHS = os.path.join(ROOT, 'data', 'paths.jsonl')
CARGO = os.path.join(ROOT, 'data', 'cargo_state.json')


def fcurves_of(obj):
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
    fps = scene.render.fps
    samples = [json.loads(l) for l in open(PATHS, encoding='utf-8') if l.strip()]
    cargo_state = json.load(open(CARGO, encoding='utf-8'))
    by = defaultdict(list)
    for p in samples:
        by[p['agent_id']].append(p)
    for k in by:
        by[k].sort(key=lambda p: p['t'])

    def frame(t):
        return int(round(t * fps)) + 1

    f_end = frame(max(p['t'] for p in samples))
    touched = set()
    # --- AGV：全程在场，不写任何 hide_render ---
    for aid, ps in by.items():
        o = bpy.data.objects.get(aid)
        if o is None:
            continue
        seen = set()
        for p in ps:
            f = frame(p['t'])
            if f in seen:
                continue
            seen.add(f)
            o.location = (p['x'], p['y'], 0.08)
            o.rotation_euler[2] = p['theta'] + math.pi / 2
            o.keyframe_insert(data_path='location', frame=f)
            o.keyframe_insert(data_path='rotation_euler', frame=f)
        touched.add(o.name)

    # --- 货物：取货前隐藏 → 随车 → 交付后落地 ---
    n_cargo = 0
    for cg in cargo_state['cargos']:
        o = bpy.data.objects.get(cg['cargo_id'])
        if o is None:
            continue
        aid, tp, td = cg['agent_id'], cg['t_pick_s'], cg['t_drop_s']
        px, pz = cg['pickup_ap']
        dx, dz = cg['drop_ap']
        f0, fp, fd = frame(0.0), frame(tp), frame(td)
        o.hide_render = True
        o.keyframe_insert(data_path='hide_render', frame=f0)
        o.hide_render = False
        o.keyframe_insert(data_path='hide_render', frame=fp)

        def put(f, x, z, h):
            o.location = (x, z, h)
            o.keyframe_insert(data_path='location', frame=f)

        seen = set()
        put(fp, px, pz, 0.62)
        seen.add(fp)
        for p in by[aid]:
            if tp + 1e-6 < p['t'] < td - 1e-6:
                f = frame(p['t'])
                if f in seen:
                    continue
                seen.add(f)
                put(f, p['x'], p['y'], 0.62)
        if fd not in seen:
            put(fd, dx, dz, 0.55)
        put(f_end, dx, dz, 0.55)
        touched.add(o.name)
        n_cargo += 1

    for name in touched:
        o = bpy.data.objects[name]
        if not o.animation_data or not o.animation_data.action:
            continue
        for fc in fcurves_of(o):
            for kp in fc.keyframe_points:
                kp.interpolation = 'CONSTANT' if fc.data_path == 'hide_render' else 'LINEAR'

    scene.frame_start = 1
    scene.frame_end = f_end
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    print('ANIMATE_OK', 'samples', len(samples), 'agents', len(by),
          'cargo', n_cargo, 'frames', scene.frame_start, '->', scene.frame_end,
          'fps', fps)


if __name__ == '__main__':
    main()
