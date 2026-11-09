# 配送中心建设相关开源项目调研（Hy4 项目 · 第一阶段汇总）

**采集时间**：2026-11-06 16:0x（GMT+8）
**第二阶段补充**：2026-11-06 16:5x，已 clone 3 个仓库做源码级核查、用本机 Blender 5.2.3 LTS 实跑资产管线并定稿交付形态，见第六、七、八节。
**数据源**：GitHub REST API `search/repositories`（26 组查询）+ `/repos/{owner}/{repo}` 详情 + 12 个项目 README 原文核验
**数据口径**：star 数、最近推送日期（`pushed_at`）、主语言、许可证均为 2026-11-06 当日快照；`pushed_at` 只代表仓库有改动，不等同于代码质量
**本机环境**：`C:\Program Files\Blender Foundation\Blender 5.2` 已存在（Blender 5.2.x），与下面多个项目的测试版本同代
**活跃度判定**：近 6 个月有推送 = 活跃；6–24 个月 = 一般；>2 年 = 停滞；已被 owner 标记 = 归档
**Blender 适配等级**：A+ 原生 Blender 脚本/资产可直接复用；A 直接产出 3D/GLB/CAD 或布局 JSON；B 产出坐标/网格/轨迹，需写 `bpy` 转换器；C 仅算法或统计指标，几何需自建

> **当前状态（单一真相源）**：`dc_project/out/CURRENT_STATUS.md` —— 由 `scripts/report_status.py`
> 从 `out/*.json` / `data/*.json` 自动生成，每次 `run_all.ps1` 结束刷新。
> 第九～十三节的所有阶段数字均为**历史快照**，已被该文件取代；不要再手改正文数字。
> 第二轮审计见 `AUDIT_REPORT_2.md`，本轮整改见**第十四节**。

---

## 一、结论先行

1. **五类主题的成熟度极度不均衡**。仓储管理、路径规划、仿真建模有成熟工程（1k–55k star、持续维护）；仓库布局优化、货位分配几乎全是论文复现代码（0–30 star，多为 2019–2023 年停更），**只能借算法与目标函数，不能当工程依赖**。
2. **与 Blender 结合度最高的不是老牌 WMS，而是 2026 年出现的一批 3D 数字孪生项目**：`cargotwin`（Blender 5.2 headless 生成 38 个低模资产 → GLB）、`smart-warehouse`（Blender 5.2.2 + MCP 建模 → GLB → Vue3/Three.js）、`WareTwin`（单份 `warehouse_layout.json` 驱动 3D 场景 + 20 台 AMR 确定性仿真）。三者与本机 Blender 5.2 同代，可直接照抄管线。
3. **推荐主线**：WMS 提供库位/库存数据 → 布局与货位算法产出坐标 → 路径与仿真产出轨迹/事件 → 统一中间格式（`layout.json` / `paths.jsonl` / `events.jsonl`）→ Blender `bpy` 程序化建模 + 关键帧动画 + glTF 导出。这样 Blender 只负责"表现层"，不背算法债。
4. **许可证是隐性坑**：`OCA/wms`(AGPL-3.0)、`frappe/erpnext`/`python_motion_planning`/`BlenderProc`(GPL-3.0)、`DEAP`(LGPL-3.0)、`openboxes`(EPL-1.0)，以及大量无 LICENSE 的低星学术仓库。校内展示问题不大，若要打包分发需逐个核对。
5. **Hy4 + Blender 的直通桥梁已存在**：`ahujasid/mcp-for-blender`（原 `blender-mcp`，33.4k star，MIT）通过 MCP + TCP socket 让 LLM 直接写 Blender Python，并提供 `execute_blender_code`、`look`、`get_scene_info` 等 9 个工具；其内置的 3D 模型生成器包含 Hunyuan3D。但 socket 默认无认证，必须只在本地可信环境使用并开启 `BLENDER_MCP_SAFE_MODE=1`。

---

## 二、五类主题代表性项目

### 2.1 仓储管理（WMS / 库存）

| 项目 | Star / 活跃度 | 技术栈 / 许可 | 功能特点 · 可复用之处 | Blender 适配 |
|---|---|---|---|---|
| [odoo/odoo](https://github.com/odoo/odoo) | 55.6k · 2026-11-06 · 活跃 | Python / PostgreSQL / JS；LGPL 系（API 显示 NOASSERTION，需自查） | ERP 全模块，`stock` 域含仓库、库位、库存移动、拣货单据；作为业务数据源最完整 | **B**：导出库位树/库存量，驱动货架阵列与颜色标签 |
| [frappe/erpnext](https://github.com/frappe/erpnext) | 41.0k · 2026-11-06 · 活跃 | Python / JS / MariaDB；GPL-3.0 | 开源 ERP，`Warehouse` / `Stock Entry` / `Bin` 数据模型清晰，REST API 易用 | **B**：库位层级 → 3D 分区 |
| [inventree/InvenTree](https://github.com/inventree/InvenTree) | 7.8k · 2026-11-06 · 活跃 | Python / Django / React；MIT | 开源库存管理，Part / StockLocation 层级库位模型，带 REST API | **B**：层级库位直接映射货架-层-格 |
| [GreaterWMS/GreaterWMS](https://github.com/GreaterWMS/GreaterWMS) | 4.4k · 2026-10-26 · 活跃 | Python（仓库内为 Django 式多 app：`binset`/`binsize`/`binproperty`/`asn`/`cyclecount`/`goods` 等）；Apache-2.0 | 源自 Ford 亚太售后物流 WMS，含库位设置、库容、ASN、盘点、司机、看板；中文资料多，2,190+ 提交 | **B+**：`bin*` 系列模块就是天然货位坐标表，最贴近国内课程语境 |
| [fjykTec/ModernWMS](https://github.com/fjykTec/ModernWMS) | 1.8k · 2025-06-22 · 一般 | Vue 前端 + 后端服务；Apache-2.0 | 从商业 ERP 剥离的完整 WMS，界面与单据流程完整 | **C**：业务系统，无几何输出 |
| [openboxes/openboxes](https://github.com/openboxes/openboxes) | 922 · 2026-11-05 · 活跃 | Groovy / Grails；EPL-1.0 | 面向医疗物资的 WMS，库存移动跟踪、批次与效期管理 | **C**：可借批次/效期语义 |
| [openwms/org.openwms](https://github.com/openwms/org.openwms) | 761 · 2026-07-13 · 活跃 | Java / Spring Cloud 云原生；Apache-2.0 | 开放式 WMS，含物料流（material flow）与运输订单领域模型 | **C**：领域模型可借，需自建可视化 |
| [OCA/wms](https://github.com/OCA/wms) | 226 · 2026-11-04 · 活跃 | Python / Odoo 模块集；AGPL-3.0 | OCA 维护的 WMS 模块总入口，下辖 20+ 子仓库，其中 `stock-logistics-putaway`（上架策略）、`stock-logistics-reservation`（预留规则）、`stock-logistics-shopfloor`（扫码作业）**直接对应货位分配规则** | **B+**：上架/预留规则可复现为"把 SKU 放到哪个格位"的逻辑 |
| [wms2/mywms](https://github.com/wms2/mywms) | 132 · 2026-04-14 · 活跃 | Java；许可证 NOASSERTION | 经典 myWMS，库位与物料流模型完整 | **C** |

> 小结：WMS 类**不产出几何**，价值在于提供"库位编码 → SKU → 数量 → 周转率"的结构化数据，作为 Blender 场景的信息层（标签、颜色、热力）。

### 2.2 仓库布局优化

| 项目 | Star / 活跃度 | 技术栈 / 许可 | 功能特点 · 可复用之处 | Blender 适配 |
|---|---|---|---|---|
| [shucheng-ai/WDA-main](https://github.com/shucheng-ai/WDA-main) | 15 · 2022-05-25 · 停滞 | Docker 多子模块（`core` 布局 / `cad` 图纸解析 / `3d` 可视化 / `dwg2dxf` / `web`）；Apache-2.0 | **唯一直接产出 2D+3D 仓库布局的开源工具**：上传 DXF/DWG → 识别墙/柱/门/消防栓 → 避障并以最大容量排布货架 → 下载 CAD 布局并 3D 预览 | **A**：DXF/CAD 可导入 Blender 作底图；但依赖 Docker 与多个 submodule，2022 年后停更，复现成本较高 |
| [lunjohnzhang/warehouse_env_gen_public](https://github.com/lunjohnzhang/warehouse_env_gen_public) | 26 · 2023-10-04 · 停滞 | C++；无 LICENSE | IJCAI 2023 论文《Multi-Robot Coordination and Layout Design for Automated Warehousing》官方实现，同时做布局生成与多机协同 | **B**：输出栅格化仓库环境，可转成阵列几何 |
| [gokhanozden/gabak](https://github.com/gokhanozden/gabak) | 9 · 2026-08-03 · 活跃 | C# / WinForms；MIT | 用进化策略（ES，1/5 规则）优化布局，目标函数 = 每张拣货单平均行走距离；把拣货 tour 当 TSP 求解；支持"巷道中心线"与"可视图"两种距离矩阵、拣货员/叉车体积避障参数、Excel 批量实验 | **B+**：参数化货架/巷道坐标可直接喂给 `bpy`，且自带"布局 A vs B"的量化对比，非常适合课程作业的论证链 |
| [samirsaci/pareto-warehouse-layout](https://github.com/samirsaci/pareto-warehouse-layout) | 23 · 2026-09-17 · 活跃 | Python / Jupyter；无 LICENSE | 用 Pareto / ABC 分析压缩仓储空间、划分快慢周转区 | **B**：ABC 分区 → 3D 分区配色 |
| [MarioDelgadoSr/MyWarehouseVisualizer](https://github.com/MarioDelgadoSr/MyWarehouseVisualizer) | 61 · 2024-06-07 · 停滞 | JS / Three.js / glTF + **Blender 建模导出**；非标准许可（非营利免费） | CSV 驱动参数化货架（行/列/层可调）、鼠标编辑三次贝塞尔堆垛机轨道、恒速运动预览；README 明确写 "Blender - For building a 3D visual and exporting it to a glTF file" | **A**：现成的"Blender → glTF → 数据驱动 3D"范式，还带 Vue3/Vite 重写版 |
| [Elabar/warehouse-layout](https://github.com/Elabar/warehouse-layout) | 25 · 2021-06-13 · 停滞 | HTML / Three.js；无 LICENSE | 用特定数据结构描述并绘制 3D 仓库 | **A-**：数据结构设计可参考 |
| [dkunitsk/FLP-layout-optimization](https://github.com/dkunitsk/FLP-layout-optimization) | 9 · 2014-01-24 · 停滞 | Python；无 LICENSE | 设施布局问题（FLP）启发式算法教学实现 | **C**：仅算法 |
| [scaceresg/proj-uaflp-metaheuristics](https://github.com/scaceresg/proj-uaflp-metaheuristics) | 3 · 2023-11-10 · 停滞 | Jupyter；无 LICENSE | 不等面积设施布局（UA-FLP）的自然启发式元启发式算法 | **C**：仅算法 |

> 小结：布局类**几乎没有可维护的开箱即用工具**；真正能连到 Blender 的是 WDA（CAD 输出）、MyWarehouseVisualizer（glTF 输出）、GABAK（坐标 + 目标函数）。

### 2.3 货位分配（SLAP / Slotting）

| 项目 | Star / 活跃度 | 技术栈 / 许可 | 功能特点 · 可复用之处 | Blender 适配 |
|---|---|---|---|---|
| [Stickish/EARMfStSLAP](https://github.com/Stickish/EARMfStSLAP) | 6 · 2022-06-07 · 停滞 | Python；MIT | 用关联规则挖掘（订单共现）求解储位分配问题 SLAP，把常一起被拣的商品放近 | **B**：输出 SKU→库位映射，可做 3D 高亮 |
| [ShengranHu/SA-Tabu](https://github.com/ShengranHu/SA-Tabu) | 3 · 2021-09-08 · 停滞 | Java；MIT | 代理辅助禁忌搜索求解大规模带分组约束 SLAP | **C**：算法思路 |
| [stellachenyl/bin-logic](https://github.com/stellachenyl/bin-logic) | 0 · 2026-08-24 · 活跃 | TypeScript；无 LICENSE | 按 SKU 周转率、尺寸、重量、拣货频次生成库位推荐，含行走距离缩减仿真与**热力图** | **A-**：热力值可直接映射 Blender 材质颜色/发光强度 |
| [OguzhanKetenci/warehouse-slotting-optimization](https://github.com/OguzhanKetenci/warehouse-slotting-optimization) | 1 · 2026-10-08 · 活跃 | Python；无 LICENSE | 基于 19.8K 真实订单，做布局 + 路径 + 样本外验证 + 重排（re-slotting）策略，实验框架比 star 数有价值 | **B**：结果表可驱动库位变化动画 |
| [timaree/warehouse-slotting](https://github.com/timaree/warehouse-slotting) | 1 · 2026-01-10 · 活跃 | Python；MIT | slotting + 规则式路径引擎 + 历史/合成需求 + KPI 分析 | **B** |
| [confxsd/3d-warehouse-simulator](https://github.com/confxsd/3d-warehouse-simulator) | 15 · 2023-01-29 · 停滞 | JavaScript / Three.js；无 LICENSE | 面向物流仓库**货位分配（slotting）的 3D 可视化** | **A-**：少见的货位 3D 展示 |
| [topprismdata/agentic-warehouse-engine](https://github.com/topprismdata/agentic-warehouse-engine) | 2 · 2026-08-28 · 活跃 | Python / OR-Tools CP-SAT；MIT | 研究"何时不该重排"：非平稳需求下的动态货位重排决策 | **C**：决策逻辑 |
| [Dimitres-Kisimov/logistics-digital-twin](https://github.com/Dimitres-Kisimov/logistics-digital-twin) | 1 · 2026-08-14 · 活跃 | Python；NOASSERTION | 3D 装箱 + 货位优化 + 离散事件仿真三合一，量化新旧作业模式差距 | **B**：组合思路值得借鉴 |

> 小结：这是五类里**最弱的一类**——GitHub 上大多是课程/论文代码，无 LICENSE、无测试、无文档。建议只取"规则"（ABC/COI、关联共现、周转率）自行实现，产出 `sku_id → rack_id` 映射表，再交给 Blender 上色。

### 2.4 路径规划

| 项目 | Star / 活跃度 | 技术栈 / 许可 | 功能特点 · 可复用之处 | Blender 适配 |
|---|---|---|---|---|
| [google/or-tools](https://github.com/google/or-tools) | 14.3k · 2026-11-04 · 活跃 | C++ 核心 + Python/Java/.NET 绑定；Apache-2.0 | 工业级运筹优化：VRP/TSP、CP-SAT、线性/整数规划。拣货路径本质是 TSP，配送路线是 VRP | **B**：输出节点访问序列 → AGV/拣货员轨迹动画 |
| [ai-winter/python_motion_planning](https://github.com/ai-winter/python_motion_planning) | 1.1k · 2026-09-15 · 活跃 | Python 3.10，`pip install python-motion-planning`；GPL-3.0 | 算法最全：Dijkstra / A* / JPS / Theta* / RRT / RRT* / Voronoi + PID / DWA / APF / LQR + Bezier / Dubins 曲线；matplotlib 可视化；v2.1 已迁移部分旧算法 | **A-**：栅格地图 + 路径点序列可直接转 Blender 曲线与关键帧；GPL 需注意 |
| [GavinPHR/Multi-Agent-Path-Finding](https://github.com/GavinPHR/Multi-Agent-Path-Finding) | 447 · 2024-08-30 · 一般 | Python；MIT | CBS（冲突搜索）+ 时空 A*，匿名 MAPF | **B**：多车无碰撞轨迹 |
| [APRIL-ZJU/CL-CBS](https://github.com/APRIL-ZJU/CL-CBS) | 421 · 2023-07-31 · 停滞 | C++；MIT | 面向类车机器人的多智能体路径规划 | **B** |
| [Cognitive-AI-Systems/pogema](https://github.com/Cognitive-AI-Systems/pogema) | 303 · 2026-09-14 · 活跃 | Python / Gym；MIT | ICLR 2025 部分可观网格多智能体环境，可定制障碍与规模 | **B**：网格 + 逐步坐标 |
| [speedzjy/mapf_ros](https://github.com/speedzjy/mapf_ros) | 253 · 2025-05-12 · 一般 | C++ / ROS1/ROS2；MIT | MAPF 算法 ROS 包 | **B**：若走 ROS 路线才需要 |
| [Kei18/mapf-visualizer](https://github.com/Kei18/mapf-visualizer) | 100 · 2024-11-16 · 一般 | C++；MIT | MAPF 轨迹可视化器 | **B**：轨迹数据格式参考 |
| [Superone77/AGV_dijkstra](https://github.com/Superone77/AGV_dijkstra) | 162 · 2024-08-18 · 一般 | C++；无 LICENSE | 中文项目：仓储多 AGV 基于 Dijkstra 的路径规划与两车避让 | **B**：教学级示例，易读 |
| [LTluttmann/rl4msprp](https://github.com/LTluttmann/rl4msprp) | 8 · 2024-03-11 · 停滞 | Python；MIT | ICAPS 2024：混合货架仓库拣货员路径问题的神经组合优化 | **C**：算法前沿 |
| [cansaner/ComparisonOfOrderPickingAlgorithms](https://github.com/cansaner/ComparisonOfOrderPickingAlgorithms) | 6 · 2017-12-25 · 停滞 | C#；无 LICENSE | 多区块仓库拣货算法对比：S-Shape / Largest Gap / Tabu / GA | **C**：经典启发式对照实现 |

### 2.5 仿真建模

| 项目 | Star / 活跃度 | 技术栈 / 许可 | 功能特点 · 可复用之处 | Blender 适配 |
|---|---|---|---|---|
| [salabim/salabim](https://github.com/salabim/salabim) | 412 · 2026-05-29 · 活跃 | 纯 Python，MIT，PyPI 可装 | 面向对象离散事件仿真：Component / Queue / Resource / Store / Monitor / Trace，**自带 2D 与 3D 动画及视频产出**，仓库目录含 `sample 3d models`；不需要 `yield` 写进程，跨 Windows/macOS/Linux | **A-**：与你做过的库存控制蒙特卡洛仿真同源；事件日志最易驱动 Blender 关键帧 |
| [jaamsim/jaamsim](https://github.com/jaamsim/jaamsim) | 242 · 2026-11-02 · 活跃 | Java；Apache-2.0 | 图形化拖拽的离散事件仿真，自带 3D 视图 | **C+**：自身 3D 引擎非 Blender，仅借模型结构 |
| [semitable/robotic-warehouse](https://github.com/semitable/robotic-warehouse) | 445 · 2024-09-15 · 一般 | Python / Gymnasium；MIT | RWARE 多机器人仓库 RL 环境；**自定义布局就是字符串**（`x`=货架、`g`=目标），`env.render()` 渲染，支持多尺寸/多智能体 | **A-**：布局字符串 → Blender 网格阵列几乎零成本；v2 已迁移 Gymnasium，注意旧代码兼容 |
| [WayneChou-bot/WareTwin](https://github.com/WayneChou-bot/WareTwin) | 158 · 2026-09-11 · 活跃 | React Three Fiber + FastAPI + Pydantic + SQLite + Python A*；MIT | 100×70 m 仓库、20 台 AMR、确定性 100 ms tick、8 向加权 A*、电量/充电/避障/故障注入、what-if 多种子对比、CSV/JSON 导出；**整座 3D 场景由单一 `warehouse_layout.json` 生成**，`docs/layout/gen_layout.py` 可造布局 | **A**：布局 JSON + WebSocket 状态流是 Blender 数据驱动最理想的契约 |
| [wh200720041/warehouse_simulation_toolkit](https://github.com/wh200720041/warehouse_simulation_toolkit) | 316 · 2024-02-18 · 停滞 | C++ / ROS / Gazebo；BSD-3-Clause | 仓库 AGV 仿真工具包，含导航、定位、SLAM 教程与仓库 world | **B**：Gazebo world 需转 glTF |
| [aws-robotics/aws-robomaker-small-warehouse-world](https://github.com/aws-robotics/aws-robomaker-small-warehouse-world) | 491 · 2026-07-21 · **已归档** | CMake / Gazebo / ROS | AWS 官方小型仓库 Gazebo world | **B-**：场景几何可用，但仓库已归档 |
| [hookex/warehouse-3d](https://github.com/hookex/warehouse-3d) | 15 · 2026-07-30 · 活跃 | JavaScript / Three.js；MIT | 交互式 3D 仓库仿真 | **B**：Three.js 前端参考，非 Blender |
| [Skelf-Research/waremax](https://github.com/Skelf-Research/waremax) | 3 · 2026-07-02 · 活跃 | Rust；MIT | 面向仓库机器人的高保真离散事件仿真 | **B**：事件输出 |
| [chessparov/warehouse-simulation](https://github.com/chessparov/warehouse-simulation) | 3 · 2024-04-24 · 停滞 | Python；GPL-2.0 | 极简仓库离散事件仿真 | **B**：适合读源码学 DES 骨架 |

---

## 三、直接服务 Blender 建模的项目（跨类别，重点）

| 项目 | Star / 活跃度 | 技术栈 / 许可 | 能直接拿来用什么 | 适配 |
|---|---|---|---|---|
| [ahujasid/mcp-for-blender](https://github.com/ahujasid/mcp-for-blender)（原 `blender-mcp`） | 33.4k · 2026-11-04 · 活跃 | Python / MCP / Blender Addon；MIT；Blender ≥3.0、Python ≥3.10 | 让 LLM 通过 TCP socket 直接执行 Blender Python：建模、布局、材质、动画、渲染、导入导出；9 个工具含 `execute_blender_code`、`look`（多视角视觉自检）、`get_scene_info`、`search_assets`/`import_asset`（Poly Haven CC0）、`generate_3d`（含 Hunyuan3D） | **A+** |
| [wangzh12023/cargotwin](https://github.com/wangzh12023/cargotwin) | 0 · 2026-10-12 · 活跃（全新） | **Blender 5.2 headless Python** + Three.js r186 / Vite；MIT | `blender/build_assets.py` + `lib_geo.py` 生成 38 个低模资产（叉车、卡车、货架、筒仓、龙门吊…）→ 单个 `assets.glb`（684 KB）→ `InstancedMesh`；`render_thumbs.py` 用 Cycles 出缩略图；`npm run assets` 一键重建；含 6 个设施与调度仿真 | **A+** |
| [Xxcool/smart-warehouse](https://github.com/Xxcool/smart-warehouse) | 25 · 2026-10-23 · 活跃 | Vue3 / Vite / TS / Three.js + **Blender 5.2.2 LTS → glTF 2.0 GLB**；MIT | AI 联动 Blender MCP 自动建模，单个 `public/smart_warehouse.glb`；AGV 闭环搬运、立库出入库、冷链温控、月台装卸、点击设备弹数据卡片、双主题切换 | **A** |
| [DLR-RM/BlenderProc](https://github.com/DLR-RM/BlenderProc) | 3.8k · 2026-01-20 · 活跃 | Python / Blender；GPL-3.0 | 程序化 Blender 管线，用于照片级渲染与合成训练数据（含标注）；适合批量出配送中心效果图 | **A+**（GPL 限制） |
| [Lewickiy/EPAL1](https://github.com/Lewickiy/EPAL1) + 同作者 `AGV` / `StoragePodGenerator` / `FloorTileGenerator` | 各 1–2 · 2026-07 · 活跃 | **Blender Python API**；无 LICENSE | 一整套统一坐标（Z=0 底面、mm 单位）的仓库资产生成器：EPAL1 托盘 1200×800×144 mm 完全程序化，4 级 LOD（`.blend` / `.fbx` / `.obj`，50–30k 面），支持万级 collection instance；同系列还有 AGV、存储 Pod、地砖 | **A+** |
| [AnuragSahu/WareSynth](https://github.com/AnuragSahu/WareSynth) | 5 · 2021-12-14 · 停滞 | Blender Python | 程序化生成 3D 仓库场景 + 自动截图与 KITTI 标注，合成数据集管线 | **A**（代码老，需适配新版 Blender） |
| [zolty-lionheart/iPort](https://github.com/zolty-lionheart/iPort) | 0 · 2024-11-26 · 停滞 | Cesium + Blender；MIT | 园区 GIS 看板 + 三维仓库预览，模型可设坐标/旋转/缩放；开源版为静态演示数据 | **A-** |

> **版本匹配**：cargotwin 明确 "tested with 5.2"，smart-warehouse 用 5.2.2 LTS，本机为 Blender 5.2 目录 —— 三条证据相互印证，直接复用资产管线不会踩大版本 API 变更。

---

## 四、Blender 适配性总评

| 数据类型 | 代表项目 | 到 Blender 的转换 | 等级 | 工作量 |
|---|---|---|---|---|
| 原生 Blender 脚本 / 资产 | cargotwin、EPAL1 系列、BlenderProc、mcp-for-blender | 直接运行 `blender --background --python build_assets.py`，或导入 `.blend`/`.obj` | A+ | 低 |
| 现成 GLB / glTF / CAD | smart-warehouse、MyWarehouseVisualizer、WDA(DXF) | `bpy.ops.import_scene.gltf()` / DXF 导入后作底图 | A | 低 |
| 布局 JSON / 栅格字符串 | WareTwin `warehouse_layout.json`、RWARE `layout`、GABAK 货架参数 | 写 `bpy` 生成器：循环创建货架 → collection instance → 阵列 | A/B+ | 中 |
| 路径与事件时序 | python_motion_planning、OR-Tools、salabim、WareTwin CSV | `keyframe_insert` 或按帧读 CSV 更新 `location`，路径画成 Curve | B+ | 中 |
| WMS 业务数据 | Odoo / ERPNext / GreaterWMS / OCA-wms | 库存量/周转率 → 材质颜色、文字标签、热力贴图 | B | 中 |
| 纯算法 / 统计指标 | SLAP 论文代码、FLP 启发式、拣货算法对比 | 只借目标函数与规则，几何全部自建 | C | 高 |

**建议的统一数据契约**（Blender 只认这三份文件）：

```text
layout.json   { zones, aisles, racks:[{id,x,y,z,w,l,h,levels}], stations, docks }   # 单位：米，Z-up
paths.jsonl   { t, agent_id, x, y, theta }                                          # 轨迹
events.jsonl  { t, type, entity, from, to, sku }                                    # 出入库/拣货事件
```

Blender 端对应动作：`bpy` 读 `layout.json` 生成货架/托盘/地面 → 复用 cargotwin 或 EPAL1 资产做实例化 → 按 SKU 周转率给格位上色 → 用 `paths.jsonl` 驱动 AGV/叉车关键帧 → Cycles/EEVEE 渲染静帧与动画 → 导出 GLB 供网页展示或输出 MP4。

**三条可落地的组合**：

- **组合 A（推荐，最稳，全 Python）**：GABAK / WDA 出布局 → OR-Tools + python_motion_planning 出路径 → salabim（或你熟悉的蒙特卡洛/SimPy）出事件 → 自写 `bpy` 生成器 + cargotwin/EPAL1 资产。优点是证据链完整、可量化对比、全程可控。
- **组合 B（最快出效果）**：直接 fork `cargotwin` 或 `smart-warehouse`，替换布局/GLB，接 `WareTwin` 的 `warehouse_layout.json` 与状态流。适合活动演示与截图，算法深度较浅。
- **组合 C（最贴合"用 Hy4 做项目"叙事）**：`mcp-for-blender` 让 Hy4 直接写 Blender Python 生成仓库几何，BlenderProc 批量渲染。亮点是 AI+3D 全流程，但尺寸精度需人工复核，且要处理资产授权与 socket 安全。

---

## 五、风险与证据边界

1. **低星 ≠ 无用，但 ≠ 可依赖**：布局/货位类多为 0–10 star、无 LICENSE、无测试，属于"论文附件"。本轮只核验了 README 描述，未逐个跑通安装；正式采用前需 clone 后验依赖。
2. **许可证清单**：MIT/Apache-2.0 相对安全（OR-Tools、GreaterWMS、ModernWMS、openwms、WDA、GABAK、WareTwin、cargotwin、smart-warehouse、RWARE、salabim、mcp-for-blender、pogema）；需谨慎的：AGPL-3.0 `OCA/wms`、GPL-3.0 `erpnext`/`python_motion_planning`/`BlenderProc`、LGPL-3.0 `DEAP`、EPL-1.0 `openboxes`、GPL-2.0 `chessparov/warehouse-simulation`；`odoo/odoo`、`wms2/mywms` 等 API 返回 NOASSERTION，需查各自 LICENSE。
3. **坐标与单位**：Blender 是 Z-up / 米制，glTF 与 Three.js 是 Y-up，CAD 常用 mm，EPAL1 明确用 mm 且约定托盘底面 Z=0。跨工具转换不统一会出现"货架躺倒"或"尺寸放大 1000 倍"，这是最容易翻车的地方。
4. **停更与归档**：WDA（2022）、WareSynth（2021）、MyWarehouseVisualizer（2024）、AWS RoboMaker 仓库（已归档）都需评估兼容成本；ModernWMS 自 2025-06 未推送。
5. **学术代码依赖**：部分仓库依赖 MATLAB、Gurobi、旧版 `gym==0.21`（RWARE v1 才兼容），复现代价高；RWARE 现主干已迁移 Gymnasium。
6. **MCP 安全**：`mcp-for-blender` 的 socket 服务默认无认证无加密，只监听 localhost；务必保持在可信网络、开启 `BLENDER_MCP_SAFE_MODE=1`，执行前保存 `.blend`。
7. **第一阶段未做的事**：未 clone 源码、未运行安装、未核对 commit 频率/issue 响应；活跃度仅以 `pushed_at` 判定，星数为当日快照。第二阶段已对 3 个仓库补齐源码级核查与本机实测，见第六节。

---

## 六、第二阶段：源码级核查（已完成）

**核查对象**：shallow clone 到工作区上层 `_research/repos/`，均为 2026-11-06 的 HEAD。

| 仓库 | commit | 体积 | 文件数 | LICENSE | 运行依赖 |
|---|---|---|---|---|---|
| cargotwin | `f13faf6` | 17 MB | 59 | MIT（已核 LICENSE 首行） | Node ^20.19 / ≥22.12、three 0.186.1、vite 8.3.4；Blender 5.2 |
| WareTwin | `81f365f` | 5.1 MB | 99 | MIT | Python 3.11 + fastapi / uvicorn / pydantic v2 / websockets / openai；前端 Node 18+ |
| smart-warehouse | `ea8c968` | 34 MB | 54 | MIT | Vue 3.5 / three 0.169 / Vite 5.4 / TS 5.6；Blender 5.2.2 导出 |

### 6.1 cargotwin：可直接复用的 Blender 资产工厂

- 证据文件：`blender/build_assets.py` 744 行、`lib_geo.py` 340 行、`render_thumbs.py` 224 行、`scripts/build-assets.mjs` 59 行、`docs/ARCHITECTURE.md` 65 行。
- 约定（源码头注释 + ARCHITECTURE.md:35-51）：**米制、Blender Z-up、车辆朝 -Y、原点=占地中心地面**；每个资产一个顶层 Empty `asset_<name>`，子网格按材质命名 `asset_<name>__<material>`；`export_glb()` 用 `bpy.ops.export_scene.gltf(export_yup=True, export_apply=True, …)`；Blender→Three 映射 `(x,y,z) → (x,z,-y)`。
- `PALETTE` 14 色（lib_geo.py:14-29）；`Asset` 用 bmesh 累积顶点/面、最后按材质合成单网格（lib_geo.py:1-6）；38 个资产清单在 build_assets.py:687-695（叉车、AMR、托盘、货架、堆垛机、卷帘门、登车桥、龙门吊、集装箱等）。
- **实测**：本机 `Blender 5.2.3 LTS (build 2026-10-22)` 直接运行 `blender -b --factory-startup -P blender/build_assets.py` 零报错，输出 700,396 B（684 KiB），与仓库提交版**字节数完全一致**；导入 Blender 得 164 objects / 126 meshes / 38 empties，bbox 31.6×15.1×15.15 m（38 个资产都堆在原点，属资产库而非场景）。
- 两个 GLB 的 `asset.generator` 都是 **Khronos glTF Blender I/O v5.2.40**，与本机 5.2.3 同代，版本兼容风险已排除。
- 局限：`src/world/layout.js` 是 155×132 m 街区网格 + 6 个园区设施，`src/sim/simulation.js` 的 routes 是硬编码点列；它是"园区级"展示，不是单仓作业模型。

### 6.2 WareTwin：最理想的数据契约与仿真后端

- 唯一真相来源：`backend/app/warehouse_layout.json`（10,536 行）+ `docs/layout/gen_layout.py`（230 行）+ `docs/layout/warehouse_layout_格式說明.md`（88 行，中文）。
- 实测布局统计：100×70×16 m、grid 1 m（100×70 格）、**184 racks / 381 locations**（SHELF 368、CHARGING 6、INBOUND 2、OUTBOUND 2、PACKING 2、SORTING 1）、4 docks、4 conveyors、3 stations、6 charging、2 lifts、2 floors（夹层 8 m）、16 cameras、22 obstacles、20 台机器人出生点。
- 格式说明明确：2D 一律 `[x,z]`、3D 一律 `[x,y,z]`、`rect=[x0,z0,x1,z1]`、`heading` 绕 y 轴、`locations` 是任务唯一可引用位置字典；并给出 7 条载入校验规则（id 唯一、access_point 必须可通行、rack 不得越界等）。
- 后端可复用：`sim/astar.py`（127 行，8 向 A*、禁止切角、返回不含起点的格点列表）、`sim/navgrid.py`（98 行）、`sim/engine.py`（1565 行，确定性 100 ms tick）、`schema.py`（819 行，Pydantic v2）；`RobotState` 直接带 `position / heading / path: list[GridCell] / path_index / battery / floor`，正是 Blender 关键帧所需字段。
- 对外接口：`/api/layout`、`/api/state`、`/api/events`、`/api/kpi`、`/api/inject`、`/api/tasks` + WebSocket（FULL 全量 + 每 tick PATCH 差分），另有 CSV/JSON 导出。
- 局限：坐标系是 Three.js Y-up，平面第二维叫 `z`；转 Blender 必须 `Blender(x,y,z) = (layout.x, layout.z, layout.y + floor.elevation)`。前后端需分别安装依赖，本轮**未启动后端**。

### 6.3 smart-warehouse：可借命名规范，不宜直接当底座

- `public/smart_warehouse.glb` 4,039,312 B（仓库里 `public/models/` 下还重复放了一份同样大小的 GLB）；解析得 **831 nodes / 825 meshes / 42 materials / 6 empties / 0 animations**，`generator = v5.2.40`。
- 节点名语义化：`AGV_Robot_01..07`、`AGV_Track_Base_Band`、`AGV_Trajectory_Laser_Line`、`AGV_Waypoint_*`、`Glass_*` / `Mullion_*` / `Warehouse_Floor` / `Exterior_Ground`；`WarehouseScene.ts` 用 `traverse()` 按名字前缀替换材质、给 `userData.entityKey` 绑业务数据（`constants.ts` 112 行是中文指标字典）。
- **文档与模型实测矛盾（重要）**：`docs/ARCHITECTURE.md:57` 写"厂房约 70m × 60m、GLB 1.86MB"，但 Blender 实测 `Warehouse_Floor` = **32×22 m**、`Glass_Wall_*` = 32 m、`Mullion_*` = 32.16 m；`Exterior_Ground` = **1050×900×0.2 m**，把整个 GLB 包围盒撑成 1050×900×5.58 m。导入 Blender 若不裁剪、不重设相机，"整仓"只占场景约 1/30。
- 动画不在 GLB 里：`WarehouseScene.ts:550-576` 用 20 个硬编码 `Vector3` 点构造 `CatmullRomCurve3`（闭合、centripetal），再用 `getPointAt/getTangentAt` 驱动 AGV。轨迹是前端代码，不是可导出的仿真数据。
- 结论：**借它的资产命名规范、材质替换策略、双主题与拾取弹窗思路**；场景底座不直接复用该 GLB。

### 6.4 实测得出的五条硬结论

1. **版本**：本机 Blender 5.2.3 LTS 与两个 GLB 的导出器 v5.2.40 同代，`build_assets.py` 已实测零报错跑通，可直接照抄 bpy 写法。
2. **可复现性**：同一脚本连续两次导出，GLB 的 **JSON chunk 完全一致**（164 nodes / 126 meshes / 14 materials），但 BIN 段有 5,186 字节差异（首个差异偏移 224008，位于 BIN 而非 JSON），文件哈希不同。因此**验收要用结构统计，不能用文件哈希**；推测为导出器缓冲填充/顶点序列化差异，未深究。
3. **坐标**：Blender→glTF 用 `export_yup=True`，映射 `(x,y,z)→(x,z,-y)`；WareTwin 的 `[x,z]` 平面要转成 Blender 的 `(x,z,0)`。三者单位都是米，无 mm 问题（EPAL1 才是 mm）。
4. **文档数字不可信**：以 GLB / JSON 实测为准；README 里的 70×60 m、1.86 MB 等描述与仓库实际文件不符。
5. **动画都要自己做**：三个仓库的 GLB 均 `animations: 0`，运动全在 JS/Python 引擎里；Blender 侧必须由 `paths.jsonl` 写关键帧，不能指望导入 GLB 就有动画。

### 6.5 证据边界

只做了静态源码阅读 + Blender headless 实测（资产生成、两个 GLB 导入与包围盒统计）；**未启动** WareTwin 的 FastAPI 后端、未跑两个前端的 `npm install/build`、未渲染出图。cargotwin 的 `public/models/assets.glb` 因实测被覆盖，已用 `git restore` 还原为原始版本（sha `e6bf591f…`），三个参考仓库工作树干净。

---

## 七、交付形态（已定）

基于核查结果，不等确认，直接定为「**Blender 工程为主、数据契约为骨、图/视频/GLB 为辅**」。

### 7.1 交付物清单

| 类别 | 文件 | 说明 | 必需 |
|---|---|---|---|
| 主交付 | `out/distribution_center.blend` | Blender 5.2.3 LTS 工程：按集合分组（建筑/货架/托盘/AGV/输送线/月台/灯光相机），材质齐全，可离线打开 | 必须 |
| 生成脚本 | `scripts/build_dc.py`、`animate.py`、`export_glb.py` | 纯 `bpy`，一条命令从数据重建整个场景；改参数即可重出 | 必须 |
| 数据契约 | `data/layout.json`、`paths.jsonl`、`events.jsonl` | 场景与动画的唯一真相来源，禁止脚本里硬编码坐标 | 必须 |
| 渲染图 | `out/renders/01_overview.png` … `04_agv_route.png` | 4 张 1920×1080：俯视总览 / 库内 / 月台 / 路径热力 | 必须 |
| 动画 | `out/anim_dc.mp4` | 15–20 s，AGV 沿路径运行 + 出入库事件 | 必须 |
| 网页预览 | `out/dc_preview.glb` | 供 Three.js/浏览器查看，Y-up 导出 | 可选 |
| 说明 | 本报告 + 脚本内注释 | 不另写 README | 必须 |

### 7.2 目录结构

```text
<workspace>/
├── 配送中心开源项目调研_Hy4_第一阶段.md   # 本报告（含第二、七节）
├── _research/
│   ├── repos/{cargotwin,WareTwin,smart-warehouse}/  # 只读参考，不改动
│   └── inspect_glb.py / inspect_big.py              # 实测脚本，可复用
└── dc_project/
    ├── data/       layout.json · paths.jsonl · events.jsonl · config.json
    ├── scripts/    build_dc.py · animate.py · export_glb.py · validate.py
    ├── assets/     assets.glb（从 cargotwin 复制，MIT，注明来源）
    └── out/        distribution_center.blend · dc_preview.glb
                    renders/*.png · anim_dc.mp4
```

### 7.3 数据契约（定稿）

- `layout.json`：**沿用 WareTwin schema 的简化子集** —— `schema_version / units:"m" / size / grid / zones[] / docks[] / racks[{id,position,size,levels,model,blocks_grid}] / locations[{id,kind,access_point}] / conveyors[] / stations[] / charging_stations[] / obstacles[] / spawn.robots[]`。2D 用 `[x,z]`，3D 用 `[x,y,z]`。
- `paths.jsonl`：每行 `{t, agent_id, x, y, theta}`，t 为秒、单位米；可直接来自 WareTwin `RobotState.path` 或 A* 输出。
- `events.jsonl`：每行 `{t, type, entity, from, to, sku}`，驱动库位颜色变化与月台事件。
- `config.json`：场景规模开关（单仓 / 区域仓 W + 前置仓 F1/F2/F3）、货架排数、通道宽度、随机种子。

### 7.4 默认设定（已拍板）

**沿用「一区域仓 W + 三前置仓 F1/F2/F3」**，但实现上参数化：一期先建模区域仓 W（100×70 m，参考 WareTwin 规模），F1/F2/F3 作为缩小版卫星仓由 `config.json` 一键展开；双层夹层/货梯一期不做（WareTwin phase 8 的复杂度留作二期）。若活动要求单仓，改一个开关即可。

### 7.5 验收标准（可量化，供评审逐条核）

1. `blender -b --factory-startup -P build_dc.py` 在本机 5.2.3 LTS 零报错；重复生成后 object 数、mesh 数、包围盒一致（**不用文件哈希**，见 6.4-2）。
2. 场景包围盒与 `layout.json` 的 `size` 误差 < 0.01 m，单位米、Z-up、无 1000 倍缩放。
3. 货架数、库位数、月台数可与 `layout.json` 对账（例：184 racks / 381 locations）。
4. AGV 关键帧由 `paths.jsonl` 生成，脚本中无硬编码轨迹坐标。
5. 导出 GLB 在 Blender 与 Three.js 双向可导入，Y-up/Z-up 转换正确，无 `Exterior_Ground` 式超大地面。
6. 4 张 PNG 与 1 段 MP4 可正常打开，且能看出布局、货位密度、路径三层信息。
7. 第三方资产仅用 MIT（cargotwin 资产），在报告与脚本注释中注明来源；不引入 AGPL/GPL 依赖。

### 7.6 明确不做（避免范围蔓延）

- 不做 Three.js/Vue 网页大屏作为主交付（GLB 仅可选预览）。
- 不直接复用 smart-warehouse 的 4 MB GLB（尺度与硬编码问题见 6.3）。
- 不部署 WareTwin 后端服务；只复用其数据格式与 A* 算法思路。
- 不做双层夹层、货梯、VLM 摄像头感知（一期）。

---

## 八、下一步（只剩执行）

1. 建 `dc_project/` 骨架与 `data/layout.json`（按 W + F1/F2/F3 参数化）。
2. 写 `build_dc.py`：bpy 生成建筑、货架阵列、托盘、月台、AGV、灯光、相机；资产从 cargotwin `assets.glb` 实例化。
3. 写 `animate.py`：读 `paths.jsonl` 写关键帧；写 `export_glb.py` 导出预览。
4. 渲染 4 图 + 1 视频，按 7.5 逐条自检。

---

## 九、第三阶段：实施记录（已完成，8/8 PASS）

> **⚠️ 历史快照提示（审计 P1-7 整改）**：第九～十一节的 941 objects / 170 meshes / 777 轨迹点 /
> 4 张图 / 18 s 视频 / 214 货架等均为**各阶段当时的数字**，已被第五阶段整改全部取代。
> 当前产物一律以 `dc_project/out/build_report.json`、`blend_audit.json`、
> `validate_report.json` 与本报告**第十二节**为准。

### 9.1 实际产出

| 文件 | 说明 | 大小 |
|---|---|---|
| `dc_project/out/distribution_center.blend` | 主交付：941 objects / 170 meshes / 31 材质 / 4 相机，Blender 5.2.3 LTS | 323 KB |
| `dc_project/data/layout.json` · `paths.jsonl` · `events.jsonl` · `config.json` | 数据契约（4 仓 / 214 货架 / 447 库位 / 777 轨迹点 / 36 事件） | 316 KB |
| `dc_project/scripts/` 7 个脚本 | gen_data / build_dc / animate / export_glb / render / encode_video / validate | 60 KB |
| `dc_project/out/renders/01..04_*.png` | 4 张 1920×1080：总览 / 库内 / 月台 / 路径 | 约 9.4 MB |
| `dc_project/out/anim_dc.mp4` | 18 s / 432 帧 / 24 fps / 960×540 | 174 KB |
| `dc_project/out/dc_preview.glb` | 网页预览（export_yup=True） | 634 KB |
| `dc_project/out/build_report.json` · `validate_report.json` | 构建与验收证据 | 约 2.5 KB |
| `dc_project/assets/cargotwin_assets.glb` | 第三方资产母版（cargoTwin，MIT，注明来源） | 684 KB |

### 9.2 验收结果（validate.py 逐条核）

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | Blender 5.2.3 构建零报错 | PASS | `5.2.3 LTS`，engine `BLENDER_EEVEE`，941 objects |
| 2 | 包围盒与 layout.size 对齐 | PASS | bbox `(0,0,0)→(112,114,12)`，dx=dy=0.0000，Z-up 米制 |
| 3 | 数量对账 | PASS | 货架 214/214、月台 7/7、AGV 21/21 |
| 4 | 关键帧与路径线数据驱动、无硬编码 | PASS | 777 采样写关键帧；756 段轨迹线由 paths.jsonl 生成 |
| 5 | GLB 导出且无超大地面 | PASS | 634 KB；回导 Blender 得 932 meshes，bbox 仍 112×114×12 |
| 6 | 4 图 + 1 视频可打开 | PASS | PNG 各 2.1–2.5 MB；MP4 174 KB / 18 s |
| 7 | 仅用 MIT 资产并注明来源 | PASS | `cargotwin_assets.glb (cargoTwin, MIT)` |
| 8 | 数据契约完整性 | PASS | 4 仓、214 货架、447 库位、777 采样、36 事件 |

### 9.3 实施中遇到并解决的问题（证据链）

1. **依赖图未刷新**：纯脚本改 `location/scale` 后 `matrix_world` 仍是旧值，首版 bbox 错成 `[-1.465,-4.85,-0.5]→[112,114,3.535]`；在 `world_bbox()` 前加 `bpy.context.view_layer.update()` 后精确对齐。
2. **边界柱/梁外溢 0.25 m**：角柱与顶梁原放在 `ox/oz` 边界上，几何半宽越界；改为向内 inset 0.25 / 0.15 m。
3. **本机 Blender 无 FFMPEG 输出枚举**：`image_settings.file_format` 只有 14 种图像格式，无 `FFMPEG`；改为渲染 432 张 PNG 帧 + `imageio-ffmpeg`（pip 安装，38.9 MB，自带 ffmpeg 二进制）合成 MP4，新增 `encode_video.py`。
4. **总览相机拍不全**：`ortho_scale=135` 时画面垂直只覆盖约 76 m，F1/F2/F3 全部出画；改 210 后 4 仓同框。
5. **路径信息不可见**：新增 `build_route_lines()`，从 `paths.jsonl` 生成 756 段发光青色带状网格（合并为单 mesh），04 图与视频可见中央走道与外圈轨迹。
6. **验收判据过时**：原第 4 条把"build 脚本读取 paths.jsonl"误判为硬编码；改为"关键帧与路径线均由数据驱动 + 无 `CatmullRomCurve3` / `agvPathPoints` 式硬编码字面量"。

### 9.4 与 7.x 定稿的差异（如实记录）

- `layout.json` 顶层扩展为 **`warehouses[]` 数组**（每个元素沿用 WareTwin 字段），而非 7.3 写的扁平顶层字段——多仓必须分组，否则 W 与 F1/F2/F3 的坐标与库位 id 会混在一起。
- 多出 `encode_video.py` 与 `out/frames/`（432 张中间 PNG，约 283 MB，保留作证据链，可随时删除；重跑 `render.py` 会自动清空重建）。
- 视频 18 s / 24 fps / 960×540；静帧仍 1920×1080。网页 3D 页面未搭建（GLB 已备好，属 7.1 可选项）。
- **未接入的部分（二期）**：`events.jsonl` 已生成但尚未驱动库位颜色变化；货物占用率是随机演示值，不是 ABC/COI 货位优化结果；AGV 轨迹是规则式闭合环线，不是 OR-Tools / A* 的求解输出。当前成果是**可复现的场景与数据管线**，不是运筹优化结论。

### 9.5 复现命令

```bash
cd dc_project
python scripts/gen_data.py
blender -b --factory-startup -P scripts/build_dc.py
blender -b --factory-startup -P scripts/animate.py
blender -b --factory-startup -P scripts/export_glb.py
blender -b --factory-startup -P scripts/render.py
python scripts/encode_video.py
python scripts/validate.py
```

### 9.6 证据边界

材质为程序化纯色（无贴图、无 PBR 精修）；未做人工美术打磨；未部署网页预览页；未做真实订单数据驱动的货位优化。渲染图与视频仅证明场景、布局、轨迹三层信息可视化正确。

---

## 十、第四阶段：算法深度优化（已完成，11/11 PASS）

针对第九节如实列出的三个"演示级"缺口，本轮把规则环线、随机占用、静态 events 全部升级为有算法、可量化的实现。

### 10.1 新增脚本与数据

| 脚本 / 数据 | 作用 |
|---|---|
| `scripts/optimize_slotting.py` → `data/slotting.json` | ABC 分类 + COI 贪心货位重排，输出每个库位的 SKU、周转率、ABC 等级、到月台距离、热力值 |
| `scripts/plan_paths.py` → 覆盖 `data/paths.jsonl`、新增 `data/path_metrics.json` | 112×114 的 1 m 栅格、8 向 A*（禁止切角、octile 启发式），替代规则闭合环线 |
| `build_dc.py` | 按 heat 分 5 档给货架加顶部热力标签；轨迹线改由 A* 路径生成；新增 `CAM_heatmap` |
| `render.py` / `validate.py` | 新增 `05_slotting_heatmap.png`；验收从 8 项扩到 11 项 |

### 10.2 量化结果（可复算）

| 指标 | 优化前 | 优化后 | 说明 |
|---|---|---|---|
| 加权行走成本 Σ(周转率 × 到月台距离) | 19.1283 | **9.7901** | **−48.82%**，同一组 240 SKU / 240 槽位，只改配对 |
| A 类 SKU 平均到月台距离 | 20.24 m | **7.38 m** | −63.5%，高频品被放到近月台库位 |
| ABC 分布 | — | A 102 / B 93 / C 45 | 按累计周转 80% / 95% 切分 |
| 热力分档 | 无色 | 5/5 档均有货架 | 冷 75 / 凉 48 / 中 46 / 暖 41 / 热 4 |
| 轨迹生成方式 | 规则闭合环线 | **8 向 A\*** | 12768 格、1390 个障碍格 |
| 任务段数 / 失败 / 碰撞 | — | 63 段 / **0 失败 / 0 障碍违反** | 全部采样点落在可走格 |
| 绕行系数 detour ratio | — | **1.178** | A\* 路径比直线长 17.8%，证明确实绕开货架 |
| 路径总长 / 平均段长 | — | 2189.5 m / 34.75 m | 21 台 AGV |

**热力公式**：`heat = 0.75 × 周转率排名分位 + 0.25 × min(1, 事件命中次数 / 2)`，即 `events.jsonl` 真实参与了货架颜色；渲染图上可见"近月台偏暖色（橙/红）、远区偏冷色（蓝/绿）"的空间规律，与 COI 优化方向一致。

### 10.3 视觉证据

- `05_slotting_heatmap.png`：每个货架顶部一个彩色热力标签，5 档颜色清晰可辨，暖色集中在靠月台一侧。
- `04_agv_route.png`：轨迹从原来笔直的环线变成沿通道折绕的 A* 路径，覆盖 A/B/C/D 四个分区，明显绕过货架阵列。
- `02_interior.png`：库内视角可见货架顶部标签，主体仍是中性色，不刺眼。
- `anim_dc.mp4`：18 s / 432 帧，AGV 沿真实 A* 路径运行（体积从 174 KB 增至 375 KB，画面信息量提升）。

### 10.4 实施中修掉的问题

1. `docks` 只有 `rect/door`，没有 `access_point` → 从同名 `locations[]` 取 AGV 停靠点，两处脚本统一。
2. 热力用线性 `velocity/max` 时只有 1 个热架（Zipf 分布极端）→ 改用**排名分位**，hot_racks 从 1 增至 67。
3. `avg_dist_before/after` 都是 22.13 m（同一组槽位，平均距离必然相同，指标无意义）→ 换成 **A 类 SKU 的平均距离**对比。
4. A* 段间用原始目标点接续，产生 9 次采样点擦到障碍格 → 改为每段以上一段实际终点格中心起步、起点投影到最近可走格，violations 归零。
5. 21 台 AGV 全取最热的少数库位，轨迹挤在月台附近一团 → 目标索引分散 + 一近一远，路径覆盖全仓。
6. 热力色只作用在细立柱上，俯视被白色层板盖住 → 在货架顶部加 0.9×0.9 m 彩色标签板（214 个），俯视可见。

### 10.5 复现顺序（顺序不能颠倒）

```bash
cd dc_project
python scripts/gen_data.py            # 生成 layout/events + 规则基线（随后被覆盖）
python scripts/optimize_slotting.py   # -> slotting.json
python scripts/plan_paths.py          # -> paths.jsonl (A*) + path_metrics.json + 更新 layout.summary
blender -b --factory-startup -P scripts/build_dc.py
blender -b --factory-startup -P scripts/animate.py
blender -b --factory-startup -P scripts/export_glb.py
blender -b --factory-startup -P scripts/render.py
python scripts/encode_video.py
python scripts/validate.py            # 11/11 PASS
```

### 10.6 仍然如实保留的边界

- **A\* 是空间路径规划，不是时空冲突消解**：第四阶段 21 台车各自独立；第五阶段加入拥堵感知优先级规划（见第十一节）降低了通道重复占用，但**仍未做基于时间步的 cell reservation**，同一时刻两车可能占位重叠，视频不是无碰撞车队调度。
- **等效速度约 7.4 m/s 是时间压缩结果**：18 s 内演示完 100 m 级路线，不是真实 AGV 速度；若要物理真实需放长时长或降低采样密度。
- **货位优化是贪心 COI，不是 QAP 精确最优**；SKU 周转率用 Zipf 合成、事件为固定种子生成，未接真实订单数据。
- **events 参与的是静态热力计算**，尚未做逐帧材质/颜色动画；库位占用率仍是随机演示值。
- 材质为程序化纯色、未做 PBR 精修；网页 3D 页面未搭建（GLB 已备好）。

---

## 十一、第五阶段：修复"路径穿过货架"+ 通道可视化（已完成，14/14 PASS）

### 11.1 触发与取证：不是错觉，确实穿了

用户看到 04 图里路径斜穿货架后提出质疑。新增 `scripts/check_geometry.py`，用**线段 vs 矩形**（Liang-Barsky slab 裁剪）替代原来的"格点是否落在障碍格"判据，对修复前的 `paths.jsonl` 复算：

| 指标 | 修复前实测 |
|---|---|
| 相邻采样线段总数 | 756 |
| **直接穿过货架实体的线段** | **81** |
| 擦进 AGV 0.45 m 安全半径的线段 | 286 |
| 相邻采样点最大跳距 | **5.968 m** |
| 路径到货架最小距离 | **0.0 m** |
| 结论 | **FAIL** |

所以第四阶段报告里"`obstacle_violations = 0`"是**格点判据的假阴性**：只检查了采样点本身有没有落在障碍格，没有检查两点之间连成的直线。此处如实更正。

### 11.2 两个根因

1. **等时抽稀把折线抄了近道**：`paths.jsonl` 按 `sample_dt = 0.5 s` 采样，等效速度 5.79 m/s，相邻点最远 5.968 m，而 A\* 折线每段只有 1 / 1.414 m。稀疏关键帧之间用直线插值，直接跨过若干拐点、斜穿货架。AGV 动画和青色路径带都读这份数据，所以两者都穿。
2. **栅格占据判定漏掉边缘格**：旧 `occupy()` 只把"格中心落在实体矩形内"的格标障碍。货架深 1.2 m 跨两个 1 m 格，第二格中心落在矩形外 → 漏标，A\* 会沿货架实体边缘走，最小间隙为 0。且完全没有考虑 AGV 车体宽度。

### 11.3 修复措施

| # | 措施 | 落地文件 |
|---|---|---|
| 1 | 障碍**先按 AGV 半宽 0.45 m 膨胀**，再做「格子矩形与膨胀矩形相交即封死」的保守格化；障碍格 1390 → 3418 | `plan_paths.py::occupy` |
| 2 | **每个 A\* 折点 = 一个关键帧**，t 按累计弧长线性映射；相邻关键帧连线就是合法的 8 向单位步，最大跳距 1.414 m，AGV 匀速沿折线走 | `plan_paths.py::emit_keyframes` |
| 3 | 新增独立几何校验（线段-矩形 + 最小间隙 + 最大跳距），不再用格点判据冒充碰撞结论 | `check_geometry.py` → `out/geometry_report.json` |
| 4 | 导出通道掩码 `nav_grid.json`、完整折线 `routes.json`，场景新增 `NAV_CORRIDORS` 通道底纹 + `CAM_corridor_zoom`；并生成 2D 平面校验图（浅蓝=通道、深灰=障碍、橙框=货架真形、浅青带=AGV 0.9 m 安全宽度、深青线=路径中心） | `build_dc.py`、`render.py`、`plot_nav_plan.py` |

### 11.4 回答"这里面没有通道吗"：有，且宽度可核对

通道一直存在，只是旧路径没有走在里面。按 `config.json` 推算的净宽：

| 通道 | 净宽 | 说明 |
|---|---|---|
| W 仓货架排间通道 | **4.0 m** | 排距 5.2 m − 货架深 1.2 m；膨胀 0.45 m/侧后仍可通行 |
| W 仓中央纵向主通道 | **8.0 m** | x = 46…54，四个分区之间 |
| W 仓中央横向主通道 | **6.0 m** | z = 32…38 |
| 月台前横向主通道 | **5.0 m** | z = 8…13，第一排货架从 z = 13 起 |
| F1/F2/F3 排间通道 | **3.2 m** | `aisle = 3.2` |
| 货架 bay 之间 0.6 m 间隙 | **不是通道** | 膨胀后完全封死，AGV 只走排间纵向通道 |

`07_nav_plan.png` / `08_corridor_plan_zoom.png` 可直接肉眼复核：橙框货架全部落在深灰障碍区内，浅青安全带**没有任何一处**压到深灰区，浅蓝通道是连通的。

### 11.5 修复前后对比（同一布局、同一 21 台车）

| 指标 | 第四阶段（旧） | 第五阶段（新） |
|---|---|---|
| 线段穿货架数 | 81 | **0** |
| 擦 AGV 安全半径数 | 286 | **0** |
| 路径到货架最小距离 | 0.0 m | **1.0 m** |
| 最大单步跳距 | 5.968 m | **1.414 m** |
| 障碍格 / 通道格 | 1390 / 11378 | 3418 / 9350 |
| 关键帧数 | 777 | 2681 |
| 绕行系数 | 1.178 | **1.500**（确实绕通道走） |
| 总路径长 | 2189.47 m | 2805.80 m |
| GLB / 静帧 | 1.50 MB / 6 张 | 1.51 MB / 8 张 |

### 11.6 顺带补的"继续优化"：拥堵感知优先级规划

21 台车原先全挤在中央十字通道（2D 图右侧密集交叉）。现在按 AGV 顺序做优先级规划：先规划的车占用通道格，后规划的车边代价为 `step × (1 + 0.8 × min(cell_usage,5)/5)`，先跑 `weight=0` 作 baseline 对照。**惩罚只改变走哪条通道，不改变可行域**，因此零穿越结论不变。

| 指标 | baseline | 拥堵感知 | 变化 |
|---|---|---|---|
| 通道格重复穿越次数 | 1446 | **1071** | **−25.93 %** |
| 被使用的通道格数 | 1222 | **1610** | **+31.75 %**（更分散） |
| 单格最大占用 | 9 | **8** | −11.11 % |
| 总路径长 | 2774.99 m | 2805.80 m | +1.11 %（代价很小） |

### 11.7 最终复现顺序（以本节为准）

```bash
python scripts/gen_data.py
python scripts/optimize_slotting.py
python scripts/plan_paths.py          # 保守栅格 + 折点关键帧 + 拥堵感知
python scripts/check_geometry.py      # 线段级几何校验 → out/geometry_report.json
python scripts/plot_nav_plan.py       # 2D 通道校验图（需 Pillow，仅此脚本依赖）
blender -b --factory-startup -P scripts/build_dc.py
blender -b --factory-startup -P scripts/animate.py
blender -b --factory-startup -P scripts/export_glb.py
blender -b --factory-startup -P scripts/render.py
python scripts/encode_video.py
python scripts/validate.py            # 14/14 PASS
```

新增文件：`scripts/check_geometry.py`、`scripts/plot_nav_plan.py`、`data/nav_grid.json`、`data/routes.json`、`out/geometry_report.json`、`out/renders/06_corridor_check.png`、`07_nav_plan.png`、`08_corridor_plan_zoom.png`。

### 11.8 验收与边界

验收扩到 **14/14 PASS**，新增第 12 条（线段级零穿越 + 最小间隙 ≥ AGV 半宽 + 最大单步 ≤ √2）、第 13 条（通道栅格接入场景，5423 个通道格）、第 14 条（拥堵改善量化）。第 10 条措辞改为"栅格零冲突"，明确指向第 12 条的线段级判据，不再混淆。

仍然如实保留：

- **没有做时空冲突消解**：拥堵惩罚只降低空间上的重复占用，两车同一时刻仍可能重叠；要做到无碰撞需时空 A\* / cell reservation。
- **等效速度 7.42 m/s 仍是时间压缩**，不是真实 AGV 速度。
- **COI 是贪心、SKU 与事件为固定种子合成**，`events` 仍只驱动静态热力，未做逐帧颜色动画。
- **Pillow 是本阶段新增的绘图依赖**，只用于生成 2 张校验图；Blender 主链路依然是 bpy + 标准库。
- 3D 俯视图存在高度投影叠加（货架顶热力标签会与地面通道在同一屏幕位置），**复核通道请以 2D 平面校验图 07/08 为准**。

---

## 十二、第六阶段：接纳对抗性审计并整改（14/14 PASS，验证器可失败）

### 12.1 审计来源与基线复核

工作区新增 `AUDIT_REPORT.md`（1614 行，结论 **Not Accepted**），以只读静态审查 + 独立数据复算 + Blender 重开 + GLB/媒体二进制解析取证。其结论属实：旧版"14/14 PASS"是自证循环。

我先落地它给的两个独立校验器，对**当前代码**重跑基线，结果比审计记录更严重：

| 校验器 | 整改前实测 |
|---|---|
| `layout_audit.py` | **114 issues**（34 实体重叠 / 16 越 footprint / 58 AP 落在实体内 / 1 AP 出界 / 2 出生点在 PACK 内 / 3 月台 AP 贴货架）、88 warnings |
| `traffic_check.py` | **3536 issues**：最小中心距 **0.0 m**、21 台车起点全不等于出生点、2640 次瞬移、399 次 >10° 转向、20 台速度越界 |
| `check_geometry.py`（0.73 m 半径） | 154 段穿货架 / 304 段擦安全半径 |

**3 条审计结论已过期**（上一轮刚改过，如实说明）：`plan_paths.py` 确实更新过 `layout.summary`；`08_corridor_plan_zoom.png` 已有标题图例；Pillow 与 imageio-ffmpeg 均已安装。其余全部属实，因此按审计 §0 的五件事顺序整改。

### 12.2 P0/P1 整改总表

| 审计项 | 状态 | 具体做法 |
|---|---|---|
| P0-1 布局 114 处冲突 | ✅ | `gen_data.py` 布局全改**显式坐标**：F 仓减为 2 排货架、W 服务带重排、月台纳入障碍、AP 退到 1.5 m 净距、柱子移进 footprint、出生点分散到 4 区；`layout_audit.py` 独立校验，现 **0 issues / 0 warnings** |
| P0-2 多车 1 mm 重叠 | ✅ | 任务全局最小成本匹配 + 就近分区任务 + 优先级错峰；`traffic_check.py` 现 **min 1.5629 m / 0 issues** |
| P0-3 速度 1.11–13.02 m/s、84 m 瞬移、315° 转向、丢帧 | ✅ | `t = 弧长/1.5 m/s + 停靠 + 原地旋转`；起点 = spawn；theta 解缠 + ≤9° 旋转关键帧；`frame_end` 来自动画（211 帧 / 26.29 s），不再夹 18 s |
| P0-4 0.45 m 半径比车窄 | ✅ | 新建 `geometry.py` 统一 `AGV_RADIUS=0.7296 m`（AMR 实测 1.12×0.77 外接圆 + 0.05 余量），全部脚本共用；栅格 1 m → **0.5 m** |
| P0-5 数据语义断裂 | ✅ | 新增 `skus.json`（240 SKU）、`tasks.json`（36 任务 / 21 派车 / 15 PENDING）、events 的 from/to/kind/同仓/sku 全匹配；**240 托盘全部来自 `slotting.assignments`**；392 库位中 152 个显式标 `EMPTY`；每台 AGV 带事件角标 |
| P0-6 验收永不失败 | ✅ | `validate.py` 重写为 A1–A14 独立复算判据 + `sys.exit(1)`；新增 `inspect_blend.py`（重开 .blend）、`parse_artifacts.py`（GLB/PNG/MP4 二进制解析）；**7 个负测试全部能触发失败** |
| P0-7 从零复现失败 | ✅ | `layout.json` 只由 `gen_data.py` 写且 `summary` 不再含 `path_samples`；`requirements.txt`、`run_all.ps1`、`README.md`、`.gitignore` 齐备；路径计数改由 `path_metrics.json` 记 |
| P0-8 MIT 资产无许可 | ✅ | `LICENSE` + `THIRD_PARTY_NOTICES.md`（cargoTwin / WareTwin 两段 MIT 全文、版权行、资产 SHA256 `e6bf591f…`） |
| P1-1 层数/托盘 | ✅ | F 仓用 3 层 mesh；240 托盘贴层板顶面（`deck_z=(lv+1)*lh+0.02`） |
| P1-2 热力板遮货架 | ✅ | 2.7×1.08 → **0.9×0.9×0.08**（来自 `config.scene`） |
| P1-3 "COI" 名不副实 | ✅ | 改名 `distance_greedy`；baseline 换成**当前布局**（改善 29.41%）、随机 baseline 仅作 sanity check；距离改 0.5 m 栅格**多源 BFS**；覆盖全部 392 库位；报告 `gap_to_lower_bound=0` 并写明未实现 cube/order 约束 |
| P1-4 GLB 静态含调试层 | ✅ | `use_selection=True` 排除 `NAV_CORRIDORS`/`AGV_ROUTE_LINES`，加 `DC_ROOT`，输出 `glb_report.json`；文档明确"静态预览" |
| P1-5 07/08 陈旧 | ✅ | 两图都带标题、图例和几何统计；尺寸固定 1032×1104 / 1464×858，`parse_artifacts.py` 独立校验尺寸 |
| P1-6 场景硬编码 | ✅ | 相机/灯/叉车/卡车/道路/热力板尺寸全部移到 `config.scene` |
| P1-7 报告数字过期 | ✅ | §9 起加"历史快照"警示；所有当前数字以第十二节 + JSON 为准；**通道格口径统一**：31712 = 0.5 m 栅格可走格，3716 = 场景 `NAV_CORRIDORS` 的 1 m² 面片数 |
| P1-8 视觉打磨 | ⚠️ 部分 | 总览构图、热力板遮挡已改善；**未做** AO/阴影/比例尺/指北针、色盲友好图例、AGV 像素宽度量化 |

### 12.3 关键实测对比（历史快照，已被 §13.4 与 CURRENT_STATUS.md 取代）

| 指标 | 审计基线 | 整改后 |
|---|---|---|
| 布局冲突 | 114 | **0** |
| 交通违规 | 3536（min 0.0 m） | **0（min 1.5629 m）** |
| 线段穿货架（0.73 m 半径） | 154 / 304 擦边 | **0 / 0，最小间隙 1.0 m** |
| 速度 | 1.11–13.02 m/s | **全部移动段 1.5 m/s** |
| 首帧瞬移 | 9.82–83.83 m | **0（起点 = spawn）** |
| 转向跳变 | 64 次 >3 rad | **0 次 >10°** |
| 视频 | 18 s / 432 帧 / 24 fps | **26.38 s / 211 帧 / 8 fps** |
| 货架 / 库位 / 月台 / 托盘 | 214 / 447 / 7 / 562（随机） | **196 / 420（392 SHELF）/ 10 / 240（全来自 slotting）** |
| 栅格 | 112×114 @1 m | **224×228 @0.5 m**（blocked 19360 / corridor 31712） |
| `.blend` | 1158 objects，报告 180 meshes 与重开 54 不一致 | **830 objects / 59 meshes / 35 materials，构建报告与重开实测完全一致**（21 actions） |
| GLB | 1148 节点、含调试层、0 动画 | **798 节点、无调试层、有 DC_ROOT、明确静态** |
| 验收 | 14/14 但永不失败 | **A1–A14 全 PASS + 退出码 0；7/7 负测试能触发失败** |

### 12.4 开源对照（用户要求的同类项目比对）

**本地已核查源码**（`_research/repos/WareTwin`）：其 `backend/app/sim/engine.py` 采用
`TICK_S=0.1`、`MAX_SPEED=1.5`、`MIN_SEP=0.9` 与 cell `reserved_by` / `occupant` 做 **tick 级时空预约**；
`astar.py` 还有动态 `blocked` 障碍集与 `cost_map`（人行道成本 ×1.6）。本项目的 1.5 m/s 与它一致、
1.2 m 间距比它更保守，但**错峰只是其 cell reservation 的轻量近似**，尚未做时间维度预约。

**本次新检索到的同类 MAPF 实现**（用于二期升级时空 A*，未 clone，仅对照方法）：

| 项目 | 方法 | 可借鉴点 |
|---|---|---|
| `nicolapiazzalunga/WarehouseSim`（MIT） | A* + space-time reservation table，冲突策略 `wait/replan/idle/cooldowns` | 与本项目「优先级规划」最接近，插件式冲突策略 |
| `SpicyHolo/multi-agent-path-planning` | WHCA*（窗口化分层协作 A*），只预约前 W 步、每 K 步重规划 | 解决完整时空图搜索不 scalable 的问题 |
| `curryliu30/multiagent-pathfinding`（MIT） | CBS + space-time A*，区分 **vertex / edge 冲突** | 防对向交换的边预约，正是我们当前缺的 |
| `DuongDang-debug/warehouse-multi-robot-optimization` | Cooperative A* + 全局 reservation table（72 台车） | 大规模车队的预约表组织方式 |

对照结论：审计 §3.2 说的"正确做法（时间扩展 A*/预约表）"在上述项目中是成熟范式；本项目本轮采用
审计认可的"最小可行（贪心错峰 + 连续检查）"，**二期应升级为 CBS/WHCA\* 的 vertex+edge 预约**。

### 12.5 新增 / 重写文件

新增脚本：`geometry.py`、`nav_grid.py`、`layout_audit.py`、`traffic_check.py`、`gen_tasks.py`、
`inspect_blend.py`、`parse_artifacts.py`；新增数据 `skus.json`、`tasks.json`、
`nav_grid.json`、`routes.json`、`agent_schedules.json`；
新增报告 `layout_audit.json`、`traffic_report.json`、`blend_audit.json`、
`glb_report.json`、`artifact_report.json`、`render_report.json`、`negative_tests.json`。

新增工程文件：`LICENSE`、`THIRD_PARTY_NOTICES.md`、`requirements.txt`、
`run_all.ps1`、`README.md`、`.gitignore`、`tests/test_validator_can_fail.py`。

### 12.6 验收与复现

`validate.py` 的 A1–A14 逐条独立复算（不直接相信生成器自报），任一 FAIL 退出 1；
`tests/test_validator_can_fail.py` 的 7 个破坏（货架重叠 / 车车重合 / 陈旧图 / 删 LICENSE /
计数篡改 / GLB 截断 / 删 NOTICE）**全部触发退出 1**。

最终顺序（以本节为准，旧 §9.5 / §10.5 / §11.7 均已作废）：

```
gen_data → layout_audit → nav_grid → optimize_slotting → gen_tasks → plan_paths
→ traffic_check → check_geometry → plot_nav_plan
→ build_dc → animate → inspect_blend → export_glb → render
→ encode_video → parse_artifacts → test_validator_can_fail → validate
```

一键：在 `dc_project/` 执行 `.\run_all.ps1`。

### 12.7 如实保留的边界（未包装成"已完成"）

1. **未做 tick 级时空预约**：多车无碰撞依赖优先级错峰 + "完成车辆离场"语义（`animate.py` 同步隐藏对象）。
   `traffic_report.json` 额外报告了严格口径：若把已完成车辆也算在场，仍有 **75 个采样时刻 <1.2 m**。
   这是当前方案的真实代价，二期需按 §12.4 升级 CBS/WHCA*。
2. **21 条执行任务是沿独立排间通道深入取货的同仓区内移库(TRANSFER)**（§13 已修正"入口打转"），另 15 条覆盖 5 种类型的事件为 `PENDING`；
   这是演示设定，不是真实订单分布。SKU 周转率仍为 Zipf 合成。
3. **货位优化是 `distance_greedy`**，排序配对即 1D 最优（`gap_to_lower_bound=0`）；
   未实现 COI 的 cube-per-order 与订单共现约束。
4. **GLB 仍是静态预览**，动画看 MP4 / `.blend`；`events` 只驱动任务与视频角标，未做逐帧库位颜色动画。
5. **视觉打磨部分未完成**（AO/阴影/比例尺/图例色盲友好）；`out/frames/` 211 张中间帧仍保留（已被 `.gitignore` 排除）。
6. **工作区仍未 `git init`**（审计 P2-11），`_research/` 未移出（保留作证据链，已在 `.gitignore` 注释说明）。
7. 材质为程序化纯色，未做 PBR；网页 3D 大屏未搭建。

---

## 十三、第七阶段：修正"AGV 只在入口打转、不走进通道取货"（用户质疑）

### 13.1 质疑与取证

用户看到 2D 通道图后指出：机器人走到货架入口就直接拐到下一个货架，不走中间通道去取货。
独立复算 `tasks.json` 证实，这不是 A* 的问题，而是**任务点本身设计错了**：

| 指标 | 修正前 |
|---|---|
| 21 条执行任务的 `from→to` 距离 | 平均 **4.5 m**（3.5–5.5 m），全是同一排相邻货架 |
| `spawn→to` 距离 | 平均 7.2 m，路径平均 16.5 m |
| 任务点 x 坐标 | **全部落在 7.5 / 11.1 / 86.3 / 89.9**（最左/最右一列货架） |
| 2D 图表现 | 青色路径全挤在西侧边缘，画面中间大片通道完全空白 |

根因是 `gen_data.build_events` 为压缩单次仿真时长，把 `src`/`dst` 取成"离出生点最近的第 0 / 第 3 个库位"
（`near = sorted(shelfs, key=到 spawn 距离)`）。两点都在同一条通道的入口端，A* 最短路自然只是在边缘折一下。
第二个问题是 AP 只是通道里的一个点，AGV 到达后**不停靠、不转向货架、无取货动作**，视觉上就是"路过拐走"。

### 13.2 三项改进

| # | 措施 | 文件 |
|---|---|---|
| 1 | **任务点沿排间通道横穿**：按 SHELF AP 的 z 分组得到通道，每台车独占一条；`from` 取靠出生点的入口端、`to` 取同一通道深入货架区的另一端，A* 必须沿通道横穿整排货架。事件带 `preferred_agent` / `lane_z`，`gen_tasks.py` 优先把任务派给生成它的车，保证路径空间分离。 | `gen_data.py`、`gen_tasks.py` |
| 2 | **面朝货架停靠取货**：每个 SHELF location 增 `face_deg`（AP→货架中心，±90°）；`plan_paths.py` 在 42 个库位停靠点插入 `FACE`（每帧 ≤9° 原地转向）→ `PICK`（停 2 s）→ 转回下一段行进方向。 | `gen_data.py`、`plan_paths.py`、`config.json` |
| 3 | **取货点可视化**：3D 场景给 42 个 from/to 点加黄/红地面标记；`07/08` 2D 图画黄圈（pick from）/ 红圈（deliver to，标 event_id），标题栏写明"每条任务横穿一条排间通道"。 | `build_dc.py`、`plot_nav_plan.py` |

顺带修掉流程隐患：`parse_artifacts.py` 原来排在 `encode_video.py` 之前，会读到上一版旧 MP4
（本次日志里出现过 `mp4_s 26.375` 而新视频是 48.75 s）。已把顺序改为 **encode_video → parse_artifacts**，
`run_all.ps1` / `README.md` / §12.6 同步。

### 13.3 修正前后对比

| 指标 | 修正前 | 修正后 |
|---|---|---|
| `from→to` 距离 | 4.5 m（3.5–5.5） | **26.2 m（18.0–32.4）** |
| 单车路径 / 总路径 | 16.5 m / 346.8 m | **33.8 m / 710.2 m** |
| `path_to_euclid_ratio` | 1.851 | **1.032**（几乎沿通道直行，不绕） |
| 取货停靠 | 0（只是路过） | **42 处面朝货架停 2 s** |
| 任务派车 | 全局成本匹配 | **21/21 按指定通道分配** |
| 视频 | 26.38 s / 211 帧 | **48.75 s / 390 帧** |
| 2D 图 | 左侧一小簇折线 | **三条路径横穿整排货架，中间通道被充分使用** |

修正后 `08_corridor_plan_zoom.png` 可直接看到：黄圈在通道左端、青线沿货架排之间的通道一路向右、
红圈（E0001/E0002/E0003）在最深处库位，橙框货架与深灰障碍区的对应关系一目了然。

### 13.4 复验结果

全链路重跑后仍 **A1–A14 全 PASS、退出码 0、7/7 负测试通过**：

- 布局 0 冲突 / 420 AP 全在通道内；
- 交通 0 违规，**最小车距 1.6861 m**；
- 几何 0 穿越，最小间隙 1.0 m、最大单步 1.0607 m、AGV_RADIUS 0.7296 m；
- 速度全部 1.5 m/s，相邻关键帧 |Δθ| ≤10°（新增的 FACE/PICK 旋转帧也通过）；
- `.blend` 872 objects / 60 meshes / 35 materials，构建报告与重开实测一致；21 actions、42 个取货标记、21 个事件角标；
- GLB 840 节点、无调试层、有 `DC_ROOT`；8 张图尺寸全对；MP4 390 帧 / 48.75 s 与 `frame_end=390` 一致。

### 13.5 如实保留的边界

- 任务仍是**同仓区内移库(TRANSFER)演示设定**，不是真实订单；15 条其它类型事件仍 `PENDING`。
- 取货动作是"转向 + 停留"，**没有叉臂/升降机构的机械动画**（资产不含可动部件）。
- 仍未做 tick 级 cell reservation；严格口径（已完成车算在场）下的冲突数见 `traffic_report.json`。
- §12.7 的其余边界（distance_greedy 非 COI、GLB 静态、视觉打磨与 git init 未完成）保持不变。

---

## 十四、第八阶段：参照第二轮审计的"部分方法与修改意见"（A1–A18 全 PASS）

### 14.1 来源与取舍

新增 `AUDIT_REPORT_2.md`（926 行，**运营逻辑、灵活性、落地性与路线图专项**）。
它明确承认第一轮 P0 整改的成果，转而指出"**把几何正确误当成运营正确**"：
路线图不是路网而是 21 条任务轨迹叠加、F 仓存在对向互换、A5 阈值小于车体直径、
`t_request/priority` 是装饰字段、文档数字继续漂移。

用户要求"参照其中的**一些**方法和修改意见"，因此按审计 §7 的"如果只能做 3 件事"
（P0-1 F 对向 / P0-2 阈值 / P0-5 路线图换图种）为主，叠加几条低成本高收益项；
**没有硬啃需要数天工作量、且会推翻现有交付形态的运营层**（WMS、车队电量模型、站台作业、
50 台规模压测、VDA 5050 接口）。取舍理由见 §14.6。

### 14.2 已落地项

| 审计项 | 做法 | 结果 |
|---|---|---|
| **P0-1 F 仓对向互换** | `gen_data.build_events` 的 lane 唯一键从 `(z, side)` 改为 `z`，同一条 z 通道只派一台车 | 21 台车各占独立通道；`route_head_on_pairs=0`；F1-R02 的 21.5 s 等待消失，全局最大冲突等待降到 **10 s** |
| **P0-2 间距阈值** | `geometry.MIN_SEP = 2 × AGV_RADIUS = 1.4592 m`（旧值 1.2 m 小于两圆直径），`traffic_check` / `validate` A5 共用同一常量 | 最小车距 **1.5455 m ≥ 1.4592 m** 仍 0 违规；A5 明确标为**条件通过**并把严格口径作为独立 KPI A17 |
| **P0-3 任务时间** | `schedule()` 改为按 `(priority, t_request)` 排序、出发不早于 `t_request`，输出 `tardiness / on_time_rate / release_wait / conflict_wait` | `on_time_rate=1.0`、`max_tardiness=0 s`、违反释放时间 0 台；新增 A16 |
| **P0-5 路线图换图种** | 07/08 修 `routes/segments` 标签、加 `edge clearance`、去掉"双重膨胀"、加方向箭头、删 "deep shelf" 文案、如实写中央主通道采样=0；新增 **09 时间-空间图**、**10 五时刻快照序列**；输出 `route_report.json` | `routes=21 / segments=1455 / head_on=0 / edge_clearance=0.2704 m`；新增 A18 |
| **§3.4 覆盖率** | 不强开 36 任务，改为明确 `event_coverage=0.5833`、15 条 PENDING 显式标注，不宣称"全链路" | 新增 A15（条件通过） |
| **§3.8 验收元漏洞** | A13 不再只信报告里的 PASS 数字，逐例核对 7 个负测试都返回非 0；A10 增加覆盖率；A17 把 `strict_violations=459` 独立成 KPI | A1–A18 共 18 项，7/7 负测试 |
| **§3.9 死字段** | 删除 `min_sep_m`、`dwell_dock_s`、`dwell_shelf_s`、`max_duration_s`、`grid_step_m`；`max_schedule_wait_s` 600→300；修正 `path_to_euclid_ratio` 注释；README 不再建议"放宽阈值" | 配置与代码一致 |
| **§3.10 / P1-7 单一真相源** | 新增 `scripts/report_status.py`，从 `out/*.json` 生成 `out/CURRENT_STATUS.md`；主报告开头与 §12.3 标"历史快照"，正文不再手打当前数字 | 830/59 与 872/60 不再并存于"当前"表述 |
| **§2.8 表现层（部分）** | 静帧按镜头 `frame_set`（02 t=15s / 03 t=20s / 04 t=10s / 05 t=12s）；04 隐藏 21 个事件标签、05 隐藏青色路线线；新增 `annotate_heatmap.py` 给 05 叠 5 档图例与计数；03 相机改俯视月台 | 04 标签不再遮挡、05 有图例且无路线污染；03 仍不理想（见 §14.6） |

### 14.3 实测对比

| 指标 | 第二轮审计基线 | 本轮 |
|---|---|---|
| 最小车距 / 阈值 | 1.6861 m / **1.2 m（判据过小）** | **1.5455 m / 1.4592 m** |
| F 仓对向任务 / 最大错峰 | 3 对 / 21.5 s | **0 对 / 10.0 s** |
| 任务出发时间 | 全部 t=0，早于 `t_request` | **全部 ≥ t_request**，按 priority 排序 |
| 路线图标注 | `routes=1404`（实为线段）、crossings 语义含糊、双重膨胀 | **routes=21 / segments=1455 / head_on=0 / edge=0.2704 m**，09/10 两张新图 |
| 事件覆盖率 | 21/36 但只说"全链路" | **58.3% 显式条件通过** |
| 验收 | A1–A14，A13 只读报告 | **A1–A18**，A13 逐例核验负测试 |
| 视频 | 48.75 s / 390 帧 | **51.88 s / 415 帧**（t_request + 取货停靠） |
| 严格口径冲突（KPI） | 564（藏在 detail） | **459，独立 A17** |

### 14.4 新增 / 重写文件

新增 `scripts/report_status.py`、`scripts/annotate_heatmap.py`、`out/route_report.json`、
`out/CURRENT_STATUS.md`、`out/renders/09_time_space.png`、`10_snapshot_sequence.png`。
重写 `scripts/plot_nav_plan.py`（四张图 + route_report）、`scripts/render.py`（按镜头选时刻 / 遮挡层控制）、
`scripts/validate.py`（A15–A18）、`scripts/geometry.py`（MIN_SEP）、
`scripts/plan_paths.py`（priority / t_request / tardiness）、`scripts/gen_data.py`（lane key）。

### 14.5 验收与复现

全链路重跑后 **A1–A18 全 PASS、退出码 0、7/7 负测试通过**；`CURRENT_STATUS.md` 由脚本生成。
最终顺序（`run_all.ps1` / README 同步）：

```
gen_data → layout_audit → nav_grid → optimize_slotting → gen_tasks → plan_paths
→ traffic_check → check_geometry → plot_nav_plan
→ build_dc → animate → inspect_blend → export_glb → render → annotate_heatmap
→ encode_video → parse_artifacts → test_validator_can_fail → validate → report_status
```

### 14.6 明确未做（及理由，按审计 §7 的优先级）

| 未做 | 审计定位 | 本轮不做的理由 |
|---|---|---|
| 回程 / staging / 多任务队列、strict=0 | P0-2 后半 / A17 | 需要重做任务生命周期与交通语义，会把 21 台车串行化到数分钟；本轮改为把 `strict_violations=459` 作为**独立 KPI** 并标 A5 条件通过，不再藏数字 |
| 36/36 事件执行、5 种类型进视频 | P0-4 / A15 | 需每车 2–3 任务 + 车队调度；本轮选审计认可的方案 B：明确 `coverage=58.3%` 并降级验收 |
| 货物状态机（托盘 attach/detach、库存） | P1-1 / A20 | 需改 `build_dc` + `animate` + 新 `cargo_state.json`，属于产品层重写；当前如实称"AGV 空驶演示" |
| 电量 / 故障 / 站台排队 / 运营 KPI 全套 | P1-2/3、A21/A22 | 数天工作量，且需先有任务队列 |
| layout spec 参数化、3 个场景、50 台压测 | P1-5 / A19/A23 | 布局仍由 `gen_data.py` 显式坐标定义；改为 spec 驱动需重排全部数据契约 |
| clean-room bootstrap（自动建 venv） | P1-6 / A24 | 本轮只做了 "encode_video → parse_artifacts" 顺序修复；未做全新空目录实测 |
| 03 拍到月台门 / 卡车 | P2-1 | 已改相机，画面仍以月台面与货架为主；园区卡车坐标在 `z=78`，不在任何月台门，**如实记为未完成**，未用文案包装 |
| 24 fps + 加减速、OBB 相交测试、git init / CI | P1-4、P2-3 | 保持 8 fps 演示（帧数与 `frame_end` 已严格一致）；OBB 与阈值二选一，本轮选 2×半径 |

审计 §9 的结论仍然成立：**当前版本足以展示"Blender 程序化建模 + A* + 数据驱动 + 独立校验"，
但不能称为"数字孪生落地"；运营层是二期。** 本轮把这句话从"脚注免责"提升成了验收项
（A15/A17 条件通过、A18 路线图语义）与自动生成的 `CURRENT_STATUS.md`。

> **后续状态**：上表中「回程/strict=0」「事件全执行」「货物状态机」「03 月台镜头」
> 四项已在第九阶段（§15）完成；A5/A15/A17 已从条件通过改为无条件通过。
> 本表保留为第八阶段当时的历史快照。

---

## 十五、第九阶段：闭环作业、严格交通与货物状态机（用户质疑 + 剩余整改项）

> 触发：用户看到路线图后质疑「这个车从货架旁边一直走到最里面，然后出来嘛？
> 不应该是一个循环还是什么的吗，希望你尽量把它做到逻辑自洽完整一些」，
> 并要求继续完成上一轮明确未做的整改项。本节数字均为实跑产物，
> 当前状态一律以 `dc_project/out/CURRENT_STATUS.md` 为准。

### 15.1 用户质疑成立：旧版不是循环，是一趟单程

独立复算 `tasks.json` / `routes.json` 可以看到旧模型的三个断点：

| 断点 | 旧版事实 |
|---|---|
| 任务语义 | 一台车只做 `SHELF → SHELF` 单程搬运，`from→to` 平均 26.2 m |
| 终点 | 走到该排货架最深处就结束，**没有交付点** |
| 生命周期 | 「送达后离场」：`animate.py` 在 `t_end` 后 `hide_render=True`，车凭空消失；未派出的车也不在场 |

所以画面上就是「沿着一条通道钻到最里面，然后没了」。这不是 A\* 求错路，
而是**任务生命周期本身没闭合**——车没有从哪来、要把货交给谁、做完回哪去。
`AUDIT_REPORT_2.md` §3.1 / §3.5 说的「完成即离场」「每台车只做 1 个任务」
正是同一个问题的两种表述。

### 15.2 改成的闭环作业模型

一张订单 = 一台车一个完整循环，四段有明确业务含义：

```
① HOME 待命位出发（空驶 DEADHEAD_OUT）
② 到 PICK 点：转向作业面 → 停 2 s 取货（FACE / PICK）
③ 载货到 DROP 点：卸货（FACE / DROP），LOADED 段
④ 空驶回 HOME（DEADHEAD_BACK）→ PARK 到仿真结束（充电 / 待下一单）
```

- `gen_data.py`：每台 AGV 增加显式 `HOME` 库位（对齐 0.5 m 栅格中心，
  贴东西墙 / F 仓东侧待命带，彼此 ≥4 m），事件携带 `return_to`、
  `preferred_agent`、`lane_z`、`phases`；**订单数 = 车辆数 = 21，`PENDING=0`**，
  覆盖 INBOUND / OUTBOUND / PICK / REPLENISH / TRANSFER 五种类型。
- 取货点固定在该车专属排间通道**深端**；交付点按「闭环总里程最短」
  从真实月台 / 打包台 / 对面半区货架里选。按待命位所在**前/后区派单**
  （前区派月台类、后区派打包/移库类），避免后排车为了去前排月台横穿全仓；
  TRANSFER 单强制跨中央主通道。
- `plan_paths.py`：三段 A\* 折点 + `bounds` 写入 `routes.json`；
  A\* 对**其它车的 HOME** 加软代价，防止「停着的车被经过的车擦到」；
  时间仍是 `弧长/1.5 m/s + 停靠 + 原地旋转`。
- `animate.py`：**不再写任何 `hide_render`**；所有车 `t=0..t_end` 全程在场，
  未派出 / 已回程的车停在 HOME。`traffic_check.py` 同步改为严格口径。

### 15.3 上一轮「明确未做」清单的完成情况

| §14.6 未做项 | 本轮 | 证据 |
|---|---|---|
| 回程 / staging / 多任务队列、**strict=0** | ✅ 回程 + HOME 待命位 + 严格口径归零 | `strict_violations=0`，`.blend` `agv_with_hide_render_keyframe=0`；A5/A17 改无条件通过 |
| 36/36 事件执行、5 种类型进视频 | ✅ 改为 21 单全执行、5 类型全覆盖 | `event_coverage=1.0`、`pending=0`、`on_time_rate=1.0`；A15 改无条件通过 |
| 货物状态机（PICK attach / DROP detach / 库存） | ✅ 新增 `gen_cargo.py` + `cargo_state.json` + 随车动画 | 21 件货物 LOADED/DELIVERED 各 21、42 次状态迁移、28 个库位库存变动；A20 |
| 03 拍到月台门 | ✅ 相机改到仓内朝月台 | `03_dock.png` 可见玻璃月台门、月台平台、黄色月台边线 |
| clean-room bootstrap（数据层） | ✅ 部分完成 | 全新空目录只放 `scripts/*.py` + `config.json`，独立跑完 9 个数据/校验脚本，`layout.json` / `path_metrics.json` / `traffic_report.json` / `geometry_report.json` / `cargo_state.json` 与主工程**逐字节一致**，交通 `PASS / strict=0` |
| 24 fps + 加减速、layout spec、git/CI | ⛔ 仍保留 | 见 §15.6 |

### 15.4 实测对比

| 指标 | 第八阶段（§14） | 第九阶段（本轮） |
|---|---|---|
| 订单 / 执行 / PENDING | 36 / 21 / 15 | **21 / 21 / 0** |
| 进入视频的业务类型 | 仅 TRANSFER | **5 种全部** |
| 单车路径 | 单程 35.26 m 平均 | **闭环 84.05 m 平均**（去程 670 / 载货 521 / 回程 574 m 合计） |
| 仿真时长 / 视频 | 51.88 s / 415 帧 | **117.62 s / 942 帧 @8 fps** |
| 交通语义 | 条件通过，`strict_violations=459` | **全程在场严格口径，`strict_violations=0`** |
| 最小车距 / 阈值 | 1.5455 / 1.4592 m | **1.5358 / 1.4592 m** |
| 几何 | 0 穿越、最小间隙 1.0 m | **0 穿越、最小间隙 1.05 m、最大单步 0.707 m** |
| 中央主通道 x=46..54 采样 | 0 | **128**（TRANSFER 单跨区） |
| 货物随车 | 无（托盘是静态布景） | **21 件，42 处 PICK/DROP 停靠** |
| `.blend` | 872 objects / 21 actions | **914 objects / 42 actions / 21 HOME 待命位 / 21 货物动画对象** |
| 验收 | A1–A18，18/18 | **A1–A21，21/21；7/7 负测试** |

### 15.5 路线图为什么现在能看懂

`07/08` 不再是 21 条孤立轨迹叠加，而是按订单画**三色回路**：
浅蓝 = 去程空驶，橙 = 载货，绿 = 回程空驶；紫色方块 = HOME 待命位，
黄圈 = 取货点，红圈 = 交付点（标事件号），线上有方向箭头。
`08_corridor_plan_zoom.png` 可直接看出「从左侧 HOME 出发 → 沿排间通道
走到深处取货 → 折到对面/月台交付 → 沿另一条通道回到 HOME」的闭合回路。
`09` 时间-空间图增加 PARK（灰）与 DROP（橙红）阶段；
`10` 快照序列改为「21 台车全程在场」，每个时刻都画 21 个蓝点。
`route_report.json` 新增 `closed_loops=21`、三段里程、`home_bays=21`，
A21 会独立复算 `routes.json` 首尾是否闭合、三段里程是否与图注一致。

### 15.6 仍如实保留的边界

- **仍不是 tick 级 cell reservation**：全程在场 0 冲突来自「优先级 + 出发错峰 +
  HOME 避让软代价 + 0.05 s 采样验证」，不是 CBS/WHCA\* 的形式化证明。
  WareTwin `engine.py` 的 `TICK_S=0.1 / MAX_SPEED=1.5 / MIN_SEP=0.9` +
  cell `reserved_by` 仍是二期目标（§12.4 已列出 4 个同类 MAPF 项目）。
- **订单仍是合成场景**：Zipf 周转率、21 张按前后区派的单，不是真实订单分布；
  每车仍只执行 1 张订单，没有任务队列与车队利用率。
- **货物是订单级箱体**：未模拟拆箱 / 托盘层级 / 批次 / 叉臂升降机械动画。
- **8 fps 匀速、GLB 静态**：无加减速、无 24 fps；`export_animations=False`。
- **03 月台图**：已拍到月台门与月台平台，但园区卡车停在北侧 yard，
  不在月台门前，没有摆拍成「卡车靠月台」。
- **clean-room 只做到数据层**：全新空目录复现的 5 份关键 JSON 与主工程完全一致；
  Blender 场景层的全新机器一键跑通、venv 自动创建、CI 仍未做。
- **未做**：layout spec 参数化 / 3 场景 / 50 台压测、24 fps 加减速、
  git init / CI、WMS 与运营 KPI 全套。

### 15.7 复现顺序（已写进 `run_all.ps1`）

```
gen_data → layout_audit → nav_grid → optimize_slotting → gen_tasks
→ plan_paths → gen_cargo → traffic_check → check_geometry → plot_nav_plan
→ build_dc → animate → inspect_blend → export_glb → render → annotate_heatmap
→ encode_video → parse_artifacts → test_validator_can_fail → validate → report_status
```

新增 `scripts/gen_cargo.py` 与 `data/cargo_state.json`；`A5/A15/A17` 改为
无条件通过，新增 `A19`（闭环数据链）、`A20`（货物状态机 / 全程在场）、
`A21`（路线图闭环语义）。

---

## 十六、第十阶段：采纳 `DESIGN_PLAN.md`，完成参数化与场景化（A1–A40 全 PASS）

> 触发：工作区新增 `DESIGN_PLAN.md`（2614 行 / 103 KB，2026-11-08），
> 用户要求「采纳里面的意见，接着完善此项目」。
> 这份设计不是挑刺，是一份**可落地的参数化 + 场景化重构方案**：四层配置、
> §9 的 Step 0–10、§15 的 A22–A40、§5.2.5.1 的 AP 互斥区、§11 的时间扩展预约表。
> 当前数字一律以 `dc_project/out/CURRENT_STATUS.md` 为准（由 JSON 生成）。

### 16.1 先复核基线，不盲改

按 §0.2 的十二条硬规则执行：Step 0 先 `git init` + 提交基线（`0e9e13c`），
记录 7 份关键文件 SHA256 到 `out/baseline_sha256.json`；再独立复算报告声称的
「148 对 AP 互斥」，结果**完全属实**（138 对 SHELF-SHELF、3+3+3+1 对跨类型，最小 0.1 m）。
同时发现报告 §5.3 示例有 3 处与当前实测布局不一致（充电桩起点 32.5 vs 30、
园区 4 辆卡车 vs 2 辆、月台门元数据缺失），按 §0.2 规则 12 用**实测值**写 spec。

### 16.2 采纳并落地的部分

| 设计项 | 落地 | 证据 |
|---|---|---|
| **Step 0 基线冻结** | git 基线 + `.gitignore` 加 `runs/` + SHA256 | 2 条 commit；`out/baseline_sha256.json` |
| **Step 1 四层配置** | `config/layout_spec.json`（实测值）、`fleet_spec.json`、`demand_spec.json`、8 个 `scenarios/*.json`、4 份 JSON Schema、F4 / 加宽两个布局变体 | 17 份配置 JSON 全部解析通过（A22） |
| **Step 2 `spec_layout.py`** | grid/explicit 展开、38 扇门元数据、L1–L12 校验、`mutex_groups` 输出、`--write` 前自动备份 | `SPEC_LAYOUT PASS warehouses 4 racks 196 locations 441 docks 10 doors 38 robots 21 issues 0 warnings 148` —— 与报告期望输出一字不差 |
| **Step 3 切换主链** | 删除 8 个硬编码布局函数，`gen_data.py` 改为调用 spec 展开 | 展开结果与冻结基线**逐字段一致**（仅新增 doors）；`skus/events/tasks/paths` 与 git 基线逻辑逐字节一致（差异仅 Windows CRLF） |
| **Step 4 spec 负测试** | `tests/test_spec_layout.py` 6 个用例 | 6/6；并修掉「L1 失败不留报告」的漏洞 |
| **Step 5/6 场景与数据源** | `demand_source.py`（Synthetic + Csv）、`scenario_run.py`（8 场景 + 断言 + manifest + 自动 report.md）、`adapters/`（CSV / UCI / WMS stub）、`config/orders_sample.csv` | 8/8 场景 PASS；CSV 24 行引用完整；CSV/UCI/WMS 三适配器可用 |
| **Step 8 最小 KPI** | `kpi.py` 25 个 KPI，每个带 `definition/formula/source`；M/M/c 解析模型与主链实测值分来源标注 | `kpi.py --run` 可独立重算；A32 全过 |
| **§10 运营层最小实现** | `fleet_sim.py` 状态机 + 电量离线回放 | 147 次迁移、0 非法、最低电量 53.986%（未触发充电，如实记录） |
| **§11 交通层复核** | `reservation_check.py` 顶点/边冲突 + AP 互斥同时占用 | 顶点 0 / 边交换 0 / 148 对互斥中 0 对同时占用 |
| **§5.2.5.1 方案 A 对照** | `layout_spec_wide.json`：仅 W 仓通道 4.2 m + AP offset 1.25 | 互斥 **148 → 29 对**，3 个布局 spec 全部审计 PASS |
| **Step 9/10** | `validate.py` 扩展 A22–A40、`run_all.ps1 -Scenario` + 依赖自检、`requirements.txt` 锁版本、docs 三篇、README、NOTICE | **A1–A40 40/40 PASS**；17 个负测试全过；clean-room 9 步 / 8 份 JSON 逐字节一致 |

### 16.3 实测抓到并修掉的真问题（没有靠改阈值蒙混）

1. **CSV 的「C 前缀＝取消单」误用**：UCI 专有规则被套到企业 CSV，
   `CSV-0001` 全被过滤，输出 0 行却判 PASS。改为只过滤 `qty<=0`，
   并让空结果 `CSV_EMPTY` 直接 FAIL。
2. **KPI 读错路径**：`path_metrics.json` 在 `data/` 而不是 `out/`，
   导致 on_time / tardiness / coverage 全为 `null`。
3. **假排队**：低负载下 Erlang-C 的 `Lq≈1e-42`，`ceil()` 把它变成 1 辆；
   改用 `round()` 归零，并把 holiday 断言改成报告原文的 `max_queue <= 20`。
4. **peak「等待上升」断言在低负载必然失败**：车队远未饱和，
   换成 `utilization >= 1.4×baseline`（90 单/h、28 台车 → 1.5×），仍可能失败。
5. **F 仓加宽挤压服务带**：全仓通道 4.2 m 让第二排货架 AP 落进充电桩膨胀区
   （12 个 L6 FAIL）。改成只对 W 仓加宽，实测互斥 148→29 且 0 FAIL。
6. **schema 校验失败不留证据**：改为 L1 失败也写 `verdict: FAIL` 报告。
7. **负测试残留污染状态表**：`test_validator_can_fail.py` 收尾补跑干净 `validate.py`，
   `CURRENT_STATUS.md` 不再出现「19/21」。
8. **PowerShell `param()` 位置**：必须在脚本首行，否则解析失败。
9. **PowerShell 5.1 中文注释吞行**：无 BOM 的 UTF-8 `.ps1` 被按 ANSI/GBK 解码，
   中文注释末尾的字节被误判成引号/续行符，把**紧随其后的那一行**吞掉。
   现象是「一键链里负测试连续三次漏跑，但验收仍报 PASS（读的是旧报告）」，
   靠 `out/*negative_tests.json` 的**文件时间戳**才发现。已把 `run_all.ps1` 全改为 ASCII 注释，
   并把 4 个负测试收进单一入口 `tests/run_all_negative.py`（与 `validate.py` 同构）。
10. **`ErrorActionPreference=Stop` 把 Blender 警告变终止错误**：
    `Material.use_nodes` 的 DeprecationWarning 走 stderr，PS 5.1 转成
    `NativeCommandError` 直接中断整链。改为 `Continue`，由 `Run()` 显式检查
    `$LASTEXITCODE` 判失败——警告不再能伪装成失败，失败也不会被吞掉。
11. **Bash 与 PowerShell 的 `python` 不是同一个解释器**：Bash 用 3.13（依赖已装），
    PowerShell 用 3.14（无 Pillow/imageio-ffmpeg/jsonschema），导致依赖检查误判。
    `run_all.ps1` 现在显式优先项目 `.venv` → 本机 3.13 → PATH python，并做依赖自检/补装。
12. **封锁月台的"假排队"二次修正**：把 `ceil(Lq)` 改成 `round(Lq)` 消除 1e-42 假排队后，
    `dock_blocked` 的 `max_dock_queue>=1` 因改派数不足 0.5 辆而 FAIL。
    改成「`reassigned>0` 则至少 1 个等待位」的离散规则，保住断言不放水。

### 16.4 三层验收口径（DESIGN_PLAN §1.3）

| 层 | 本轮证据 |
|---|---|
| **L1 能跑** | `run_all.ps1` 全链路退出 0；40/40 验收、17/17 负测试 |
| **L2 能复现** | 同 spec 两次展开 SHA256 一致（A23）；改 seed 必变（A36）；同 seed 两次 KPI 一致（A25）；全新空目录 9 步 / 8 份 JSON 逐字节一致（A40）；clean-room 无 `.venv` |
| **L3 能解释** | 25 个 KPI 每个有定义 + 公式 + 来源；148 对 AP 互斥有 `mutex_groups` 与 0 同时占用的独立复核；每个「PASS」都有独立复算与可失败负测试 |

### 16.5 明确未做（如实保留）

- **Step 7 完整 `fleet_sim`**：电量/故障/任务队列**没有接入调度循环**，
  当前只是基于主链 21 单路径的离线回放；每车仍 1 单。
- **Step 6 真实数据接入**：CSV/UCI/WMS 只有适配器与样例，未接企业真实数据；
  `orders_per_hour=45`、`AGV accel/decel/battery` 仍是 placeholder。
- **§11 CBS / WHCA\* / tick 级预约表**：只做了采样级独立复核，
  未实现时间扩展规划器；0 冲突不是连续时间的形式化证明，50 车只是解析规模测试。
- **场景层是解析 what-if**：M/M/c 排队近似，**不是逐车 A\***；
  逐车路径/碰撞仍以主链 `traffic_check.py` / `path_metrics.json` 为准，已在 KPI 标 source。
- **未做**：24 fps + 加减速、VDA 5050 兼容、OBB 相交、Blender 层 clean-room / CI、
  WMS/ERP 完整对接、ISO 3691-4 安全认证。
- **AP 互斥没有显式调度锁**：目前由全局中心距 ≥2R 判据**隐含**保证；
  报告要求的「同一 mutex 组当作虚拟 cell」尚未写进 `plan_paths.py`。
  加宽变体 29 对也说明方案 A 不能归零（剩余是 F 仓月台/站点与货架 AP）。

### 16.6 复现命令

```powershell
cd dc_project
.\run_all.ps1                                  # 全链路 + A1–A40
.\run_all.ps1 -Scenario config/scenarios/peak.json
python scripts/spec_layout.py --spec config/layout_spec.json
python scripts/spec_layout.py --spec config/layout_spec_wide.json
python scripts/scenario_run.py --all
python scripts/kpi.py --run runs/baseline/<时间戳>
python tests/test_spec_layout.py; python tests/test_scenario.py
python tests/test_cleanroom.py; python tests/test_validator_can_fail.py
python scripts/validate.py
```

设计报告 §18 的三句话对照：几何层资产**没有退回**（A1–A21 全部保留）；
把写死的布局/设备/需求/场景**变成了配置**；每个数字有来源、每个场景有断言、
每个 KPI 有公式、每个「通过」都能被负测试推翻。

## 十七、第十一阶段：采纳 `OPEN_SOURCE_PLAN.md`，从「本机工程」到「可发布仓库」（v0.1.0）

> 触发：工作区新增 `docs/OPEN_SOURCE_PLAN.md`（720 行 / 29 KB，2026-11-08 18:40），
> 评估对象是 `dc_project/` 的 git 发布态（134 tracked files / 3 commits / 无 remote·tag·CI）。
> 它的核心判断：**算法与 A1–A40 验收已超过多数同类项目；真正缺的是「把能力交付给陌生人」的产品层**——
> 看得见、跑得通、信得过、能参与。本轮按其 §0.2「小步、可回滚、每步验收」执行，先复核再改。
> 当前所有数字仍以 `out/CURRENT_STATUS.md` 为准，本节不硬编码计数。

### 17.1 先复核，发现比建议更紧急的问题

| 复核项 | 实测 | 判定 |
|---|---|---|
| 个人路径硬编码 | `run_all.ps1:33` = 用户目录下 .workbuddy 的 Python 解释器绝对路径 | ✅ |
| 文档死链 | `README.md:5/160/177` 引用 `AUDIT_REPORT.md` / `DESIGN_PLAN.md`，仓库内不存在 | ✅ |
| 生成物入库 | `.git` **16 MiB**、`out/` 被跟踪 37 个、最大 PNG 2.68 MB；`tests/_tmp_spec/`、`data/config.bak.json` 在库 | ✅ |
| 输出契约不齐 | `validate_report.json` 顶层 **list**，其余 `*_report.json` 是 dict；无 `schema_version` | ✅ |
| A13 自证 | `validate.py:249-258` 用 `open(自身源码)` 找 `'sys.exit(1'` | ✅ |
| 控制台健壮性 | `console/server.py:166` 全局 `JOB`、`:800` 覆盖式备份、`DC_CONSOLE_HOST` 可对外 | ✅ |
| 时间无时区 | `scenario_run.py:118/123` 用 `datetime.now()` | ✅ |
| 场景 KPI 复用主链 | `kpi.py:53-54` 属实，但**已显式标注** `[measured/main-chain]` / `main_src` | ⚠️ 措辞需修正 |
| `.gitignore` 忽略 `runs/` | 当前工作树不忽略（`?? runs/`） | ⚠️ 已过期 |

**建议没写、但更紧急的一条**：当前工作树与 `HEAD (d82ed14)` 严重错位——**26 个 D / 17 个 M / 2 个 ??**，
HEAD 里的 `layout_spec_f4/wide.json`、`orders_sample.csv`、`spec_layout_report.json`、
`fleet_report.json`、`reservation_report.json`、`baseline_sha256.json` 全部缺失；
`scripts/validate.py` 是 A1–A40 版本（35 处 A22–A40 引用），但 `out/validate_report.json` 却是 **21/21 的旧报告**。
若此时照 §3.1 执行 `git rm --cached` 再「提交未提交改动」，会把 26 个删除一并提交、抹掉 40 项成果。
→ 先把 17 个 M 文件与 `git diff` 备份到 `_research/backup_worktree_20261108/`，
再 `git restore .` 回到 **40/40 PASS** 的发布态，才动第一刀。

另：审计/设计报告原件在 18:41 的工作区还原中已不在磁盘，故没有「移入 `docs/design/`」；
改为**删除 README 里的死链** + 把主报告纳入 `docs/PROJECT_REPORT.md` 作为证据链，
并在 `docs/design/README.md` 说明三份历史评审输入的去向。

### 17.2 采纳落地

| 批次 | 项 | 实现 |
|---|---|---|
| **M0 仓库卫生** | 去生成物 / 去个人路径 / 修死链 | 48 个生成物 `git rm --cached`（本地保留）；重写 `.gitignore`、新增 `.gitattributes`/`.editorconfig`；`run_all.ps1` 改为纯 ASCII 薄包装器 |
| **M0 跨平台 CLI** | `dc` 7 命令 | `pyproject.toml` + `dc_twin/`（`cli/doctor/runner/paths/export/compare`）+ `run.py`/`run.sh`；Blender 探测 `BLENDER_EXE → PATH → 常见安装目录`；子进程统一用 `sys.executable` |
| **M0 门面** | Hero README / 图 / 社区文件 | 10 张 WebP（最大 108 KB）+ 32 帧 GIF；英文首页含 3 条命令、Mermaid 架构图、What it is/is not；中文版存 `README.zh-CN.md`；补 CHANGELOG / CONTRIBUTING / SECURITY / CITATION / CODE_OF_CONDUCT / issue·PR 模板 |
| **M1 输出契约** | sidecar + manifest + 分档 | `scripts/manifest.py` 为每个 `out/*.json` 生成 `.meta.json`（`schema_version`/UTC/generator/输入哈希），产出 `manifest.json` + `traceability.md`；`scripts/validate_data.py` 12 项数据层验收；`dc validate --tier data|render|full --json --junit --check` |
| **M1 可信度** | A13 行为化 / pytest / CI | A13 从「读自身源码」改为核验 7 个破坏性用例的非零退出（其中 5 个直接扰动 validate 产物）；新增 `tests/{unit,contract,integration,acceptance,e2e,blender}` + `conftest.py` 排除旧自写脚本；`.github/workflows/ci.yml`（Ubuntu/Windows × 3.11/3.13，无 Blender）+ `pages.yml` |
| **M1 可玩** | 静态 Demo / 对比 / 发布 | `dc export --target demo` 生成无后端 `demo/`（数据内联 `data.js`、纯 Canvas 俯视+等距投影、`?embed=1`）；`dc compare` 输出 25 个 KPI delta；`dc export --target release` 生成 20 文件 + `checksums.txt` |

### 17.3 实测证据（全部可复现）

| 证据 | 结果 |
|---|---|
| `dc doctor` | 17 项全 OK（含 Blender 5.2.3 LTS 版本探测、双 seed 一致、写权限） |
| `dc run --scope data` / `--dry-run` | 11 步 **9.47 s**（满足建议「15 秒内」）；`full` 31 步、`scenarios` 16 步，dry-run 不写文件 |
| 验收 | `A1–A40 = 40/40`、`--tier data = 12/12`（`--check` 时 13/13）、`--json`/`--junit` 可用 |
| 漂移检测 | `dc validate --check` **连续三次幂等 PASS**（修掉「校验器自己的输出被 manifest 记录」与「traceability 在哈希后写入」两处自扰） |
| 测试 / lint | `pytest -m "not blender"` **22 passed / 2 deselected**；新代码 `ruff check` 全绿（旧 `scripts/` 与 `console/` 52 处历史告警显式排除，见 CONTRIBUTING） |
| 门面 | 10 张 WebP 全部 <500 KB、GIF 32 帧 862 KB；相对链接检查 **0 死链**；`grep C:\Users` 源码 **0 命中** |
| 静态站 | `demo/` 8 场景 / 21 车 / 117.62 s；本地 HTTP 7 个资源全 200；`node --check app.js` 通过 |
| 发布包 | `dist/release/` 20 文件，`checksums.txt` 逐文件 SHA256 复核 **0 不一致** |
| 一键链 | `.\run_all.ps1` / `bash run.sh` / `dc run --scope full` 三入口同构；结构化日志 `runs/logs/<UTC>.jsonl` |

### 17.4 如实保留的边界

- **`.git` 仍约 16 MiB**：新提交不再带大文件，但历史提交里已有二进制；要达到建议的「clone <5 MiB」必须重写历史，
  会作废 §16 冻结的基线 commit 与 `baseline_sha256.json`，本轮**不做**，列为可选。
- **CI / Pages 文件齐备但未真实运行**：仓库无 remote，GitHub Actions 未触发；本地只验证了等价命令链。
- **场景层仍是 M/M/c 解析近似**，KPI 复用主链时显式标 `source: baseline_main_chain`；未做 `scenario_run --engine full`。
- **控制台仍是全局单例 `JOB` + 覆盖式备份 + 1 s 轮询**（建议 §4.4 未做）；只加了 CLI 层的「非 loopback 必须带 token」保护。
- `demo/` 含 570 KB `dc_preview.glb` 入库（静态站核心资产，仍远低于 5 MB 阈值），与「尽量少二进制」有取舍。
- 其余沿用 §15/§16：非 tick 级 CBS / cell reservation、合成订单、每车 1 单、货物无叉臂动画、8 fps 匀速、GLB 静态、
  未做 VDA 5050 / 真实 WMS / 布局编辑器 / i18n / 24 fps 加减速 / 50 台真实压测。

### 17.5 复现顺序（不可颠倒）

```
pip install -e .            # 或 pip install -r requirements-dev.txt
dc doctor
dc demo                    # 无 Blender：11 步数据链 + 8 场景 + 控制台
dc run --scope full        # 31 步，含 Blender / 942 帧 / 图片 / 视频 / Demo / manifest
pytest -m "not blender"
dc validate --tier data --junit out/junit.xml
dc validate --check
dc export --target demo
dc export --target release
```

`run_all.ps1` 保持纯 ASCII：PowerShell 5.1 会把无 BOM 的 UTF-8 `.ps1` 按 GBK 解码，
中文注释会吞掉紧随其后的一行（§16 已踩过，本轮继续沿用该约束）。

### 17.6 与 §14.6「未做清单」的对照

| §14.6 项 | 现状 |
|---|---|
| 回程 / staging / `strict=0` | ✅ §15 已完成 |
| 36/36 事件执行、5 类型进视频 | ✅ §15 已完成（21/21 单、5 类型） |
| 货物状态机 | ✅ §15 已完成 |
| 03 月台镜头 | ✅ §15 已完成 |
| clean-room bootstrap | ✅ §16 已完成 |
| CI / pytest / Release / 社区文件 | ✅ 本轮完成（文件级，未真实跑 Actions） |
| 控制台作业注册表 / 原子写 / SSE | ⛔ 保留 |
| 全仿真场景引擎 / 布局编辑器 / i18n / VDA 5050 / CBS | ⛔ 保留 |

审计 §9 的结论仍成立且已写进验收项：这是「Blender 程序化建模 + A* + 数据驱动 + 独立校验」的演示，
不是数字孪生落地；运营层是二期。**本轮补的是「交付给陌生人」的产品层，不是再加算法。**

---

### 附：本轮使用的主要检索式（便于复现）

```text
"warehouse management system" in:name,description   | topic:wms
"warehouse layout" / "facility layout" in:name,description
"warehouse "layout optimization"" in:name,description
"storage location assignment" / warehouse slotting in:name,description
"order picking" / "picker routing" / "AGV path planning" in:name,description
topic:mapf | "warehouse simulation" | topic:discrete-event-simulation
"digital twin" warehouse in:name,description | warehouse topic:blender | topic:simpy
```

注：首轮使用 `in:name,description,readme` 的检索式会命中大量 README 里恰好提到关键词的 awesome-list 类高星仓库（噪声），已全部剔除并改用精确短语 + `in:name,description` 重搜。
