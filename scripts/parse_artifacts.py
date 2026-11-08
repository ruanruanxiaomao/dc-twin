"""parse_artifacts.py — 独立解析 GLB / PNG / MP4 二进制，不信任生成器自报。

依据 AUDIT_REPORT.md §3.6(2) / §8 A11：
  · GLB header + JSON chunk（节点/网格/材质/动画/禁用节点/DC_ROOT）
  · PNG IHDR（真实宽高，而非"文件 > 5 KB"）
  · MP4 mvhd box（timescale/duration → 秒）
产出：out/artifact_report.json
"""
import json
import os
import struct

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'out')
RENDERS = os.path.join(OUT, 'renders')
EXPECTED = {
    '01_overview.png': (1920, 1080), '02_interior.png': (1920, 1080),
    '03_dock.png': (1920, 1080), '04_agv_route.png': (1920, 1080),
    '05_slotting_heatmap.png': (1920, 1080), '06_corridor_check.png': (1920, 1080),
    '07_nav_plan.png': (1032, 1104), '08_corridor_plan_zoom.png': (1464, 858),
    '09_time_space.png': (1400, 900), '10_snapshot_sequence.png': (1680, 900),
}
FORBIDDEN = ('NAV_CORRIDORS', 'AGV_ROUTE_LINES')


def parse_glb(path):
    d = open(path, 'rb').read()
    magic, version, total = struct.unpack('<4sII', d[:12])
    if magic != b'glTF' or total != len(d):
        return {'valid': False, 'reason': 'bad header/truncated', 'bytes': len(d)}
    n = struct.unpack('<I', d[12:16])[0]
    j = json.loads(d[20:20 + n])
    names = [x.get('name', '') for x in j.get('nodes', [])]
    return {
        'valid': True, 'bytes': len(d), 'version': version,
        'generator': j.get('asset', {}).get('generator'),
        'nodes': len(names), 'meshes': len(j.get('meshes', [])),
        'materials': len(j.get('materials', [])),
        'animations': len(j.get('animations', [])),
        'scene_roots': len(j.get('scenes', [{}])[0].get('nodes', [])),
        'has_DC_ROOT': 'DC_ROOT' in names,
        'forbidden_nodes': [x for x in names if any(f in x for f in FORBIDDEN)],
    }


def parse_png(path):
    d = open(path, 'rb').read(33)
    if d[:8] != b'\x89PNG\r\n\x1a\n':
        return {'valid': False, 'bytes': os.path.getsize(path)}
    w, h = struct.unpack('>II', d[16:24])
    return {'valid': True, 'width': w, 'height': h,
            'bytes': os.path.getsize(path)}


def parse_mp4(path):
    d = open(path, 'rb').read()
    i = d.find(b'mvhd')
    if i < 0:
        return {'valid': False, 'bytes': len(d)}
    ver = d[i + 4]
    if ver == 0:
        ts, dur = struct.unpack('>II', d[i + 16:i + 24])
    else:
        ts, dur = struct.unpack('>IQ', d[i + 20:i + 32])
    return {'valid': True, 'bytes': len(d), 'timescale': ts,
            'duration_units': dur, 'duration_s': round(dur / ts, 3) if ts else 0}


def main():
    glb = parse_glb(os.path.join(OUT, 'dc_preview.glb'))
    pngs = {}
    for name, expect in EXPECTED.items():
        p = os.path.join(RENDERS, name)
        r = parse_png(p) if os.path.exists(p) else {'valid': False, 'bytes': 0}
        r['expected'] = list(expect)
        r['size_ok'] = r.get('valid') and (r['width'], r['height']) == expect
        pngs[name] = r
    mp4 = parse_mp4(os.path.join(OUT, 'anim_dc.mp4'))
    rr = json.load(open(os.path.join(OUT, 'render_report.json'), encoding='utf-8'))
    mp4['expected_frames'] = rr['expected_frames']
    mp4['frames_rendered'] = rr['frames_rendered']
    mp4['frame_count_ok'] = rr['frames_rendered'] == rr['expected_frames']
    report = {'glb': glb, 'pngs': pngs, 'mp4': mp4,
              'all_png_sizes_ok': all(v.get('size_ok') for v in pngs.values()),
              'verdict': 'PASS' if (glb.get('valid') and not glb['forbidden_nodes']
                                    and glb['has_DC_ROOT'] and mp4['valid']
                                    and all(v.get('size_ok') for v in pngs.values()))
                          else 'FAIL'}
    with open(os.path.join(OUT, 'artifact_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('ARTIFACTS', report['verdict'], 'glb_nodes', glb.get('nodes'),
          'png_ok', report['all_png_sizes_ok'], 'mp4_s', mp4.get('duration_s'))


if __name__ == '__main__':
    main()
