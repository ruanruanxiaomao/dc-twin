# 当前状态（自动生成，请勿手改）

> 由 `scripts/report_status.py` 从 `out/*.json` 与 `data/*.json` 生成。
> 主报告与 README 只引用本文件；历史阶段数字一律视为快照。

| 指标 | 当前值 |
|---|---|
| 生成时间 | 2026-11-06 22:20:21 |
| Blender | 5.2.3 LTS |
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
| 验收 | 21/21 PASS；负测试 7/7 |

## 仍保留的边界（如实声明）

- **仍非 tick 级 cell reservation**：用「优先级 + 出发错峰 + HOME 避让软代价」逼近，严格口径 0 冲突是采样验证结果（0.05 s），不是形式化证明。
- **订单仍是合成场景**：SKU 周转率为 Zipf 合成，21 张订单按前后区派单，每种类型 4–5 单，不是真实订单分布。
- **货物为订单级箱体**：一件货 = 一张单，未模拟拆箱/托盘层级/批次/叉臂升降。
- **动画为 8 fps 匀速**：无加减速、无 24 fps；GLB 仍是静态预览。
- **运营层未做**：WMS/车队管理/电量故障/站台排队/运营 KPI/真实数据校准。
- **clean-room 只做到数据层**：全新空目录复现的 5 份关键 JSON 与主工程逐字节一致；Blender 场景层全新机器一键跑通 / venv 自动创建 / CI 仍未做。
- **03 月台图**：已拍到月台门、月台平台与黄色月台边线；园区卡车停在北侧 yard，不在月台门前（如实记录，未摆拍）。
