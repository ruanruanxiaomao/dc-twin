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
  - **engine 结构衍生声明（A 阶段，2026-11-09）**：`scripts/sim_engine.py` 的
    tick 循环顺序、对象化确定性 PRNG、事件环缓冲、等待队列上限、KPI 采样
    与 `ENERGY_TICK` 窗口聚合，参照 `backend/app/sim/engine.py`
    （源 commit `81f365f323e1a097e5819ca9045637544163f219`）。
    属 **MIT 许可下的结构衍生**，数值函数均以 Python 原生重写。
    本项目物理阈值 `MIN_SEP=1.4592 m`（2×AGV_RADIUS）**未沿用** WareTwin 的 0.9 m。
  - **过时表述更正**：旧版本文件称「尚未实现 tick 级 cell reservation」；
    A 阶段已实现 tick 级仿真 + 逐 tick 格点占用复核 + 1 s 间距采样验证
    （`out/sim_validation.json` S6）。该表述已作废。

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
- `_research/repos/` 下的五个参考仓库（`cargotwin`、`WareTwin`、`smart-warehouse`、
  `MAPF-CBS`、`layout-editor`）保留各自原始 `LICENSE`，不参与运行。

## 第十阶段新增（依据工作区 `DESIGN_PLAN.md`）

- **设计文档 `DESIGN_PLAN.md`**（工作区根目录，2026-11-08）：本项目内部设计文档，
  本轮按其 §5/§6/§7/§9/§15 新增四层配置、场景矩阵、KPI 与 A22–A40 验收；
  仅引用其**方法与字段规范**，未复制其正文或任何第三方代码。
- **UCI Online Retail II**（https://archive.ics.uci.edu/dataset/502/online+retail+ii，
  **CC BY 4.0**）：`scripts/adapters/uci_retail.py` 只实现字段映射逻辑，
  **不打包、不分发该数据集**；企业自行下载并按 docs/REAL_DATA_SOURCES.md 引用。
  当前 `config/orders_sample.csv` 是本项目生成的合成样例，不是 UCI 数据。
- **jsonschema 4.23.0**（MIT）、其传递依赖 `rpds-py`、`referencing`、
  `jsonschema-specifications`：仅用于 `spec_layout.py` 的可选 schema 增强校验，
  许可证随 PyPI 安装包提供，本项目不分发其源码。
- **VDA 5050 / ISO 3691-4**：仅作为术语与接口预留引用（见
  docs/REAL_DATA_SOURCES.md）；本项目**未实现** VDA 5050 兼容，
  也**不复制** ISO 标准全文，不声称通过任何安全认证。
- **Pillow 13.0.0（HPND）** / **imageio-ffmpeg 0.8.1（BSD-2-Clause）**：
  版本已在 `requirements.txt` 锁定；许可证随安装包提供。

---

## A/B/C 阶段新增（依据 `docs/COMPLETION_PLAN.md` 与 `docs/GITHUB_REFERENCE_AUDIT.md`）

- **GavinPHR/Multi-Agent-Path-Finding**（https://github.com/GavinPHR/Multi-Agent-Path-Finding，
  **MIT**，`Copyright (c) 2018 YOUR NAME`）：仅做源码级对照
  （`cbs_mapf/planner.py` 的 CBS / Space-Time A*、`constraint_tree.py` 的 SIC 代价），
  **未复制任何代码、未引入依赖**。实测其依赖外部 `stastar` 包且文件名
  `LICENSE.txt ` 带尾随空格导致 Windows 工作树为空，故 L3 求解器触发 D3 降级，
  标 experimental，不进默认链。
- **Stegocode/warehouse_layout_editor**（https://github.com/Stegocode/warehouse_layout_editor，
  **MIT**，`Copyright (c) 2026 Scott Tegenfeldt`）：许可证已核实（方案原标「未核实」）。
  仅参照其交互与架构（工具栏 / 双端同规则校验 / JSON 导入导出 / 校准背景描图），
  **代码全部自研**，不打包其前端文件。
- **GPL 隔离声明**：`frappe/erpnext`（GPL-3.0）、`beancount/beancount`（GPL-2.0）、
  `bekirbostanci/vda5050_lif_editor`（GPL-3.0，本次核实）、`OCA/wms`（AGPL-3.0）
  仅作为**概念 / 接口语义参照**，本项目账本、LIF 解析器、适配器均为自研，
  **无任何代码衍生**，亦不分发其源码。
- **openTCS/opentcs**：GitHub API `license` 字段为 `null`，不能按 MIT 处理；
  仅借「transport order → drive order + 资源锁」架构概念，不嵌入 Java 栈。
- **salabim/salabim**：许可证仍未被 GitHub 识别，维持「核实前不引入」。
- **VDA5050/VDA5050**（**MIT**）：仅事件命名语义对齐，不构成代码衍生。
