# 当前状态（自动生成，请勿手改）

> 由 `scripts/report_status.py` 从 `out/*.json` 与 `data/*.json` 生成。
> 主报告与 README 只引用本文件；历史阶段数字一律视为快照。

| 指标 | 当前值 |
|---|---|
| 生成时间 | 2026-11-09 18:27:52 |
| Blender | 5.2.3 LTS |
| 布局来源 | config/layout_spec.json → spec_layout.py（L1–L12 PASS，issues=0 warnings=148；38 扇门） |
| AP 互斥 | 148 对（WARN，两 AP <2R 不能同时占用）；预约表复核同时占用 0 次；加宽变体互斥 29 对 |
| 四层配置 | layout_spec / fleet_spec / demand_spec / 8 个 scenarios + 4 份 schema；3 个布局 spec（当前 / F4 / 加宽）全部审计 PASS |
| 场景 | 10 个场景 × 115 次运行；25 个 KPI 每个带 definition/formula/source |
| 布局 | 4 仓 / 196 货架 / 441 库位（392 SHELF + 21 HOME）/ 10 月台 / 6 站 / 21 AGV |
| 布局审计 | issues=0 warnings=0 verdict=PASS |
| 导航栅格 | 224x228 @0.5 m；blocked=19360 corridor=31712；AGV_RADIUS=0.7296 m |
| 订单 | 21 事件 → 21 任务（21 ASSIGNED / 0 PENDING），覆盖率 1.0，类型 INBOUND/OUTBOUND/PICK/REPLENISH/TRANSFER |
| 闭环 | HOME→PICK→DROP→HOME；三段里程 [669.97, 521.39, 573.75] m；取货/交付停靠 42 处；首尾闭合 21/21 |
| 路径 | 总长 1765.08 m / 平均 84.05 m / 5421 关键帧 / path_to_euclid=1.099 |
| 时间 | 仿真 117.62 s / 视频 117.75 s / 942 帧 @8 fps |
| 调度 | on_time_rate=1.0 / max_tardiness=0.0 s / max_conflict_wait=19.5 s |
| 交通 | 全程在场严格口径；min_pair=1.5358 m（阈值 1.4592 m）/ issues=0 / strict_violations=0 |
| 几何 | 5400 段 / 穿越 0 / 最小间隙 1.05 m / 最大单步 0.7071 m |
| 货物状态机 | 21 件货物 LOADED/DELIVERED 各 21/21；42 次状态迁移；28 个库位库存变动；.blend 随车动画对象 21 |
| .blend | 914 objects / 61 meshes / 36 materials / 42 actions（构建报告 914/61/36，重开实测一致）；HOME 待命位 21；隐藏 AGV=0 |
| GLB | 882 节点 / 59 meshes / 35 materials / 0 animations；禁用节点=[]；DC_ROOT=True |
| 图像 | 6 张 3D 静帧 + 2 张 2D 闭环路线图 + 时间-空间图 + 快照序列；尺寸全对=True |
| 路线图 | closed_loops=21 / routes=21 / segments=3444 / head_on=0 / edge_clearance=0.3204 m / 中央主通道采样=128 |
| 车队/交通复核 | 状态迁移 147 次/非法 0；最低电量 53.986%；预约表顶点 0 / 边交换 0 |
| 数据适配/复现 | CSV 24 行 schema=PASS；全新空目录复现 9/9 步、8 份 JSON 逐字节一致 |
| 验收 | 40/40 PASS（A1–A40）；负测试 17/17（3 个测试文件） |
| 开源交付层 | dc CLI 7 命令 / 31 步链（data 11 · scenarios 16 · full 31）；输出契约 sidecar 34 份 / manifest 58 个产物；静态 Demo 8 个场景 · 21 台车 |
| 分档验收 | data tier 12/12 PASS；pytest -m "not blender" 与 CI 矩阵见 `.github/workflows/ci.yml` |

## 仍保留的边界（如实声明）

- **仍非 tick 级 cell reservation**：用「优先级 + 出发错峰 + HOME 避让软代价」逼近，严格口径 0 冲突是采样验证结果（0.05 s），不是形式化证明。
- **订单仍是合成场景**：SKU 周转率为 Zipf 合成，21 张订单按前后区派单，每种类型 4–5 单，不是真实订单分布。
- **货物为订单级箱体**：一件货 = 一张单，未模拟拆箱/托盘层级/批次/叉臂升降。
- **动画为 8 fps 匀速**：无加减速、无 24 fps；GLB 仍是静态预览。
- **场景层是解析 what-if**：M/M/c 排队近似算吞吐/等待/利用率，不是逐车 A\* 仿真；碰撞与路径以主链 `traffic_check.py` / `path_metrics.json` 为准。
- **运营层是离线回放**：`fleet_sim.py` 回放状态机与电量，尚未把电量/故障/任务队列接入调度循环（DESIGN_PLAN Step 7 未做）；每车仍 1 单。
- **交通层是独立复核**：`reservation_check.py` 在 0.05 s / 0.5 m 粒度验证顶点/边冲突，未实现 CBS / WHCA\* / tick 级预约表；0 冲突不是连续时间的形式化证明。
- **需求仍是合成**：CSV / UCI / WMS 适配器已提供接口与校验，未接企业真实数据；`orders_per_hour=45` 仍是 placeholder。
- **clean-room 只做到数据层**：9 步 / 8 份 JSON 逐字节一致；Blender 场景层全新机器一键跑通与 CI 仍未做。
- **03 月台图**：已拍到月台门、月台平台与黄色月台边线；园区卡车停在北侧 yard，不在月台门前（如实记录，未摆拍）。
