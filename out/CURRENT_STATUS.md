# 当前状态（自动生成，请勿手改）

> 由 `scripts/report_status.py` 从 `out/*.json` 与 `data/*.json` 生成。
> 主报告与 README 只引用本文件；历史阶段数字一律视为快照。

| 指标 | 当前值 |
|---|---|
| 生成时间 | 2026-11-11 21:53:17 |
| Blender | 5.2.3 LTS |
| 布局来源 | config/layout_spec.json → spec_layout.py（L1–L13 PASS，issues=0 warnings=148；38 扇门） |
| AP 互斥 | 148 对（WARN，两 AP <2R 不能同时占用）；预约表复核同时占用 0 次；加宽变体互斥 29 对 |
| 四层配置 | layout_spec / fleet_spec / demand_spec / 8 个 scenarios + 7 份 schema；4 个布局 spec（当前 / areas / f4 / wide）；本次审计 4 个变体，均 PASS |
| 场景/KPI | 8 个冻结场景（config/scenarios）× 17 次运行；解析层 25 项 KPI + tick 层 39 项 KPI（去重 55 项），每项带 definition/formula/source |
| 布局 | 4 仓 / 196 货架 / 441 库位（392 SHELF + 21 HOME）/ 10 月台 / 6 站 / 21 AGV |
| 布局审计 | issues=0 warnings=0 verdict=PASS |
| 功能区 | 21 个（物理 3 / 逻辑 18）；种类 BATTERY×1/EMPTY_CONTAINER×1/PACKING×2/PICK_FACE×4/QA×1/RECEIVING×1/REPLENISH×1/RETURNS×1/SORTING×1/STAGING_IN×4/STAGING_OUT×4；见 data/areas.json |
| 功能区变体 | config/layout_spec_areas.json：W 深度 88.0 m、13 个物理功能区（用户勾选 13 个）；用 run.py --spec 运行，主链不受影响 |
| 过程配置 | 6 工位 / 11 流程节点 / 2 班次；L14–L22 见 out/process_report.json |
| 导航栅格 | 224x228 @0.5 m；blocked=19360 corridor=31712；AGV_RADIUS=0.7296 m |
| 订单 | 21 事件 → 21 任务（21 ASSIGNED / 0 PENDING），覆盖率 1.0，类型 INBOUND/OUTBOUND/PICK/REPLENISH/TRANSFER |
| 闭环 | HOME→PICK→DROP→HOME；三段里程 [669.97, 521.39, 573.75] m；取货/交付停靠 42 处；首尾闭合 21/21 |
| 路径 | 总长 1765.08 m / 平均 84.05 m / 5421 关键帧 / path_to_euclid=1.099 |
| 时间 | 仿真 117.62 s / 视频 117.75 s / 942 帧 @8 fps |
| 调度 | on_time_rate=1.0 / max_tardiness=0.0 s / max_conflict_wait=19.5 s |
| 交通 | 全程在场严格口径；min_pair=1.5358 m（阈值 1.4592 m）/ issues=0 / strict_violations=0 |
| 几何 | 5400 段 / 穿越 0 / 最小间隙 1.05 m / 最大单步 0.7071 m |
| 货物状态机 | 21 件货物 LOADED/DELIVERED 各 21/21；42 次状态迁移；28 个库位库存变动；.blend 随车动画对象 21 |
| .blend | 1327 objects / 187 meshes / 58 materials / 42 actions（构建报告 1327/187/58，重开实测一致）；HOME 待命位 21；隐藏 AGV=0 |
| GLB | 1274 节点 / 185 meshes / 56 materials / 0 animations；禁用节点=[]；DC_ROOT=True |
| 图像 | 6 张 3D 静帧 + 2 张 2D 闭环路线图 + 时间-空间图 + 快照序列；尺寸全对=True |
| 路线图 | closed_loops=21 / routes=21 / segments=3444 / head_on=0 / edge_clearance=0.3204 m / 中央主通道采样=128 |
| 车队/交通复核 | 状态迁移 147 次/非法 0；最低电量 53.986%；预约表顶点 0 / 边交换 0 |
| 数据适配/复现 | CSV 24 行 schema=PASS；全新空目录复现 9/9 步、8 份 JSON 逐字节一致 |
| 验收 | 40/40 PASS（A1–A40）；负测试 17/17（3 个测试文件） |
| 开源交付层 | dc CLI 7 命令 / 31 步链（data 11 · scenarios 16 · full 31）；输出契约 sidecar 37 份 / manifest 72 个产物；静态 Demo 8 个场景 · 21 台车 |
| 分档验收 | data tier 12/12 PASS；pytest -m "not blender" 与 CI 矩阵见 `.github/workflows/ci.yml` |

## 仍保留的边界（如实声明）

- **两套 KPI 并存，引用时必须写清是哪一层**：解析层 `kpi.json`（`kpi.py`，M/M/c 近似）25 项；tick 层 `sim_kpi.json`（`sim_engine.py`，逐 tick 实测）39 项；去重 55 项。只报一个数字而不说层，就是口径错误。
- **交通层是"反应式门控 + 退让 + 绕行 + 清障"，不是预约表**：`advance()` 只在候选点与已更新位置距离 < MIN_SEP 时让行；被同一台车挡住超 8 s 由 id 较大的一方后退让路，超 20 s 尝试绕行，仍无解则在 300 s 触发人工清障（`JAM_CLEARED`）。实测清障 0–5 次/场景，说明这不是形式化无死锁，而是"有界可观测"的工程折中。0 冲突是 0.05 s 采样复核结果，不是形式化证明。
- **过程层两层口径不同**：解析层用 Erlang-C 近似给出 `station_utilization` / `dock_utilization` / `max_dock_queue`，服务时间取 `fleet_spec.station_service`；tick 层则是**实测**——6 个 process_spec 工位 + 4 个拣选池按服务台运行，有 `STATION_ENTER/QUEUED/EXIT` 事件与会话级等待时长。仍缺的是：月台门没有占用/装卸工时（封门改派已实现），人员班次未建模。
- **功能区三层已打通**：21 个功能区（物理 3 / 逻辑 18）见 `data/areas.json`；tick 层把 PICK_FACE 当拣选池、把 process_spec 工位当服务台（服务时长采样 + 并行位 + FIFO）；3D 层已按 `areas.json` 画地面色块与中文名称牌（物理区 3 块真实占位，逻辑区 18 个挂靠标牌）。仍未做：功能区不参与导航栅格（它不该改变可通行性），也没有按功能区做人员排班。
- **瓶颈在巷道通行，不在工位**：实测工位利用率 约 18%，把并行位从 4 收到 1 会出现排队（等待 70.7 s）但吞吐反而从 40 升到 62/h —— 因为放开容量后同时上路的车更多、巷道更堵（累计受阻 36004 s → 26674 s）。见 `scripts/check_station.py`。
- **库存层空缺**：`cargo_state.json` 是订单级货物状态，不是 SKU / 批次 / 货位库存；没有安全库存、补货点、效期、周转。
- **订单仍是合成**：SKU 周转率为 Zipf 合成，`orders_per_hour=45` 仍是 placeholder，不是真实订单分布。
- **多单已支持、多腿未支持**：tick 层一车可背多单（实测 `max_orders_per_robot=4`），但订单仍是单行单腿，没有订单头/行拆分与合单。
- **货物为订单级箱体**：一件货 = 一张单，未模拟拆箱/托盘层级/批次/叉臂升降。
- **交通层无 CBS / WHCA\***：`reservation_check.py` 在 0.05 s / 0.5 m 粒度独立复核顶点/边冲突；0 冲突不等于连续时间安全证明。
- **需求未接真实系统**：CSV / UCI / WMS 适配器已提供接口与校验，未接企业真实数据。
- **clean-room 只做到数据层**：9 步 / 8 份 JSON 逐字节一致；Blender 场景层全新机器一键跑通与 CI 仍未做。
- **动画为 8 fps 匀速**：无加减速、无 24 fps；GLB 仍是静态预览。
- **03 月台图**：已拍到月台门、月台平台与黄色月台边线；园区卡车停在北侧 yard，不在月台门前（如实记录，未摆拍）。

## 口径纪律

1. 场景数只认 `config/scenarios/*.json`，**不数 `runs/` 目录**（`runs/` 里还有 `custom/` 与 `logs/`）。
2. KPI 数必须写明层（解析 / tick）；两套不合并成一个数字。
3. Schema 数、场景数、运行数一律现算，不写死。
4. 本文件由 `scripts/report_status.py` 生成；要改表述请改脚本，不要手改本文件。
