# Third-Party Notices

本项目使用了下列第三方开源资产 / 代码。依据 MIT 许可证要求，在此保留其版权声明与许可全文。

---

## cargoTwin

- **Source**：https://github.com/wangzh12023/cargotwin
- **License**：MIT
- **Copyright (c) 2026 Zihan Wang**
- **Used files**：
  - `assets/cargotwin_assets.glb` — 原始文件 `public/models/assets.glb`，
    SHA256 `e6bf591fa0a69038bb9ccf782b43a1a2489d3e768637232edfc6647628fd0584`（700,396 bytes），未修改。
  - `scripts/build_dc.py` 中的低模几何构造方法参考 `blender/lib_geo.py` 的
    「共享 mesh + 实例化对象 + 按材质累积网格」思路。
- **用途**：AMR / 托盘 / 叉车 / 卡车 / 月台调节板 5 个资产母版，以及 14 色调色板约定。

```
MIT License

Copyright (c) 2026 Zihan Wang

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## WareTwin

- **Source**：https://github.com/WayneChou-bot/WareTwin
- **License**：MIT
- **Copyright (c) 2026 Wayne Chou**
- **Used files / derived code**：
  - `scripts/plan_paths.py` 的 8 向 A*（禁止切角、octile 启发式、
    `to_cell` / `cell_center` / `nearest_walkable`）移植自
    `backend/app/sim/astar.py` 的算法结构。
  - `data/layout.json` 的仓库 / 库位 / 事件字段设计参考
    `backend/app/warehouse_layout.json` 与 `docs/layout/warehouse_layout_格式說明.md`。
  - **对照结论**：WareTwin `backend/app/sim/engine.py` 采用
    `TICK_S=0.1`、`MAX_SPEED=1.5`、`MIN_SEP=0.9` 与 cell `reserved_by`
    做 tick 级时空预约；本项目采用 1.5 m/s、1.2 m 最小间距的
    **优先级错峰**作为轻量近似，尚未实现 tick 级 cell reservation（见 README 已知限制）。

```
MIT License

Copyright (c) 2026 Wayne Chou

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## three.js

- **Source**：https://github.com/mrdoob/three.js
- **Version**：r160（0.160.0）
- **License**：MIT
- **Copyright © 2010-2024 three.js authors**
- **Used files**（`console/static/vendor/`，原样分发，未修改）：
  - `three.module.js` — `build/three.module.js`
  - `addons/loaders/GLTFLoader.js` — `examples/jsm/loaders/GLTFLoader.js`
  - `addons/utils/BufferGeometryUtils.js` — `examples/jsm/utils/BufferGeometryUtils.js`
  - `addons/controls/OrbitControls.js` — `examples/jsm/controls/OrbitControls.js`
- **用途**：控制台的 3D 透视视图渲染 `out/dc_preview.glb`。本地内置，不依赖 CDN。

```
MIT License

Copyright © 2010-2024 three.js authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 其他

- `Pillow`、`imageio-ffmpeg` 仅在生成 2D 校验图与编码 MP4 时使用，运行时依赖见 `requirements.txt`；
  它们各自的许可证随安装包提供，本项目不再分发其源码。
- `_research/repos/` 下的三个参考仓库保留各自原始 `LICENSE`，不参与运行。
