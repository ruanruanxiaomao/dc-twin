"""render.py — 输出 6 张静帧 + PNG 帧序列（由 encode_video.py 合成 MP4）。

审计整改：
  · 不再用固定 18 s 夹断 frame_end，帧范围完全来自动画关键帧；
  · 输出 out/render_report.json（实际 engine / fps / 帧范围 / 帧数 / 每张图尺寸）；
  · 动画分辨率、samples、通道底纹可见范围全部来自 config；
  · 07/08 由 plot_nav_plan.py 生成，不在本脚本内。

运行： blender -b --factory-startup -P scripts/render.py
"""
import bpy
import glob
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'out')
RENDERS = os.path.join(OUT, 'renders')
BLEND = os.path.join(OUT, 'distribution_center.blend')
CFG = json.load(open(os.path.join(ROOT, 'data', 'config.json'), encoding='utf-8'))

CAMS = [
    ('CAM_overview', '01_overview.png'),
    ('CAM_interior', '02_interior.png'),
    ('CAM_dock', '03_dock.png'),
    ('CAM_route', '04_agv_route.png'),
    ('CAM_heatmap', '05_slotting_heatmap.png'),
    ('CAM_corridor_zoom', '06_corridor_check.png'),
]
CORRIDOR_SHOTS = set(CFG['scene']['corridor_visible_in'])


def set_engine(scene):
    for eng in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'CYCLES', 'BLENDER_WORKBENCH'):
        try:
            scene.render.engine = eng
            return eng
        except (TypeError, ValueError):
            continue
    return scene.render.engine


def main():
    bpy.ops.wm.open_mainfile(filepath=BLEND)
    scene = bpy.context.scene
    eng = set_engine(scene)
    if eng.startswith('BLENDER_EEVEE'):
        try:
            scene.eevee.taa_render_samples = CFG['render']['samples']
        except AttributeError:
            pass
    elif eng == 'CYCLES':
        scene.cycles.samples = 8
    scene.render.resolution_x = CFG['render']['width']
    scene.render.resolution_y = CFG['render']['height']
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    print('RENDER_ENGINE', eng)

    fps = scene.render.fps
    corridor = bpy.data.objects.get('NAV_CORRIDORS')
    route_lines = bpy.data.objects.get('AGV_ROUTE_LINES')
    labels = [o for o in bpy.data.objects
              if o.type == 'FONT' and o.name.startswith('LABEL_')]
    # 静帧按「仿真时长比例」取时刻，闭环时长变化后仍能拍到在途 / 取货的车
    dur_s = (scene.frame_end - 1) / fps
    shot_frac = {'01_overview.png': 0.0, '02_interior.png': 0.25,
                 '03_dock.png': 0.35, '04_agv_route.png': 0.18,
                 '05_slotting_heatmap.png': 0.22, '06_corridor_check.png': 0.12}
    shot_t = {k: round(v * dur_s, 2) for k, v in shot_frac.items()}
    shots = []
    for cam, fname in CAMS:
        c = bpy.data.objects.get(cam)
        if c is None:
            print('MISSING_CAM', cam)
            continue
        if corridor:
            corridor.hide_render = fname not in CORRIDOR_SHOTS
        # 04 的 21 个事件标签会盖住 AGV；05 热力图不该被青色路线线污染
        for o in labels:
            o.hide_render = fname in ('03_dock.png', '04_agv_route.png',
                                      '05_slotting_heatmap.png')
        if route_lines:
            route_lines.hide_render = fname == '05_slotting_heatmap.png'
        frame = min(max(1, int(round(shot_t[fname] * fps)) + 1), scene.frame_end)
        scene.frame_set(frame)
        scene.camera = c
        scene.render.filepath = os.path.join(RENDERS, fname)
        bpy.ops.render.render(write_still=True)
        shots.append({'camera': cam, 'file': fname,
                      'bytes': os.path.getsize(scene.render.filepath),
                      'width': CFG['render']['width'],
                      'height': CFG['render']['height'],
                      'frame': frame, 't_s': round((frame - 1) / fps, 2)})
        print('RENDER_OK', fname, shots[-1]['bytes'], 'frame', frame)

    scene.render.resolution_x = CFG['render']['anim_width']
    scene.render.resolution_y = CFG['render']['anim_height']
    if eng.startswith('BLENDER_EEVEE'):
        try:
            scene.eevee.taa_render_samples = CFG['render']['anim_samples']
        except AttributeError:
            pass
    scene.camera = bpy.data.objects.get('CAM_route')
    if corridor:
        corridor.hide_render = False
    # 审计 P0-3：不夹断，frame_end 就是 animate.py 写下的最后一个关键帧
    frame_start, frame_end = scene.frame_start, scene.frame_end
    frames = os.path.join(OUT, 'frames')
    os.makedirs(frames, exist_ok=True)
    for stale in glob.glob(os.path.join(frames, 'f_*.png')):
        os.remove(stale)
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = os.path.join(frames, 'f_')
    bpy.ops.render.render(animation=True)
    n = len(glob.glob(os.path.join(frames, 'f_*.png')))
    print('FRAMES_OK', n, 'frames', frame_start, '->', frame_end)

    report = {
        'engine': eng, 'fps': fps,
        'frame_start': frame_start, 'frame_end': frame_end,
        'expected_frames': frame_end - frame_start + 1,
        'frames_rendered': n,
        'anim_width': CFG['render']['anim_width'],
        'anim_height': CFG['render']['anim_height'],
        'still_shots': shots,
        'duration_s': round((frame_end - frame_start + 1) / fps, 3),
    }
    with open(os.path.join(OUT, 'render_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
