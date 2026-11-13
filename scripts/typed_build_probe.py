"""typed_build_probe.py — R8.3 带机理的整场构建（Blender 内运行）。

默认构建不需要它：R8.2 已经证明「不声明 rack_type 时产出逐字节不变」。这个探针要回答
另一个问题：**真的把机理声明进布局之后，场景到底变成什么样**——以及这个结果能不能被
钉成一个可复现的锚点。

做法是"打补丁而不是改产品代码"：导入 build_dc / export_glb 后替换它们的
``LAYOUT`` / ``OUT`` / ``BLEND`` / ``GLB`` 全局，再调 ``main()``。跑出来的场景落在
``out/exp/typed_build/``，**不碰 out/ 下的默认产物**。

用法：

    blender -b --factory-startup -P scripts/typed_build_probe.py -- \
        <typed_layout.json> <out_dir>
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

import bpy          # noqa: E402
import build_dc     # noqa: E402
import export_glb   # noqa: E402


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if len(argv) < 2:
        print('TYPED_BUILD_FAIL 用法：-- <typed_layout.json> <out_dir>')
        sys.exit(2)
    layout_path, out_dir = argv[0], argv[1]
    os.makedirs(out_dir, exist_ok=True)

    layout = json.load(open(layout_path, encoding='utf-8'))
    typed = sum(1 for w in layout['warehouses'] for r in w['racks'] if r.get('rack_type'))
    if not typed:
        print('TYPED_BUILD_FAIL 布局里没有任何 rack_type，这份数据不该走这个探针')
        sys.exit(1)

    # 1) 整场构建：把数据与输出目录换掉
    build_dc.LAYOUT = layout
    build_dc.OUT = out_dir
    build_dc.BLEND = os.path.join(out_dir, 'dc_model.blend')
    build_dc.main()

    # 2) 导出 GLB：同样换路径，避免覆盖 out/dc_preview.glb
    export_glb.OUT = out_dir
    export_glb.BLEND = build_dc.BLEND
    export_glb.GLB = os.path.join(out_dir, 'dc_preview.glb')
    export_glb.main()

    rep = json.load(open(os.path.join(out_dir, 'build_report.json'), encoding='utf-8'))
    counts = build_dc.COUNTS
    dispatch = counts.get('rack_dispatch') or {}
    summary = {
        'typed_racks': typed,
        'dispatch': dispatch,
        'fallback': counts.get('rack_dispatch_fallback', 0),
        'racks_built': counts.get('racks_built'),
        'pallets_built': counts.get('pallets_built'),
        'objects': len(bpy.data.objects),
        'meshes': len(bpy.data.meshes),
        'asset_meshes': sorted(rep.get('asset_meshes') or []),
        'glb': json.load(open(os.path.join(out_dir, 'glb_report.json'), encoding='utf-8')),
    }
    with open(os.path.join(out_dir, 'typed_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('TYPED_BUILD_OK', json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
