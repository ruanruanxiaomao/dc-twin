"""encode_video.py — 用 imageio-ffmpeg 提供的 ffmpeg 把 out/frames/f_%04d.png 合成 MP4。

本机 Blender 5.2.3 构建不带 FFMPEG 输出枚举，因此视频走「PNG 帧序列 + 外部编码」。
运行： python scripts/encode_video.py
"""
import glob
import json
import os
import subprocess

import imageio_ffmpeg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'out')
FRAMES = os.path.join(OUT, 'frames')
# 审计 P0-3：fps/帧数来自 render_report.json，不再硬编码 18 s
RR = json.load(open(os.path.join(OUT, 'render_report.json'), encoding='utf-8'))
FPS = RR['fps']
TARGET = os.path.join(OUT, 'anim_dc.mp4')


def main():
    files = sorted(glob.glob(os.path.join(FRAMES, 'f_*.png')))
    if not files:
        raise SystemExit('NO_FRAMES: 先运行 render.py')
    if len(files) != RR['expected_frames']:
        raise SystemExit('FRAME_COUNT_MISMATCH: %d rendered vs %d expected'
                         % (len(files), RR['expected_frames']))
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, '-y', '-framerate', str(FPS), '-start_number', '1',
           '-i', os.path.join(FRAMES, 'f_%04d.png'),
           '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '23',
           '-movflags', '+faststart', TARGET]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:])
        print(r.stderr[-1500:])
        raise SystemExit('FFMPEG_FAILED %s' % r.returncode)
    print('VIDEO_OK', TARGET, os.path.getsize(TARGET), 'bytes',
          'frames', len(files), 'fps', FPS, 'duration_s', round(len(files) / FPS, 2))


if __name__ == '__main__':
    main()
