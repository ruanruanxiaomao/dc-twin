# 参数速查表

> 数据来源等级（DESIGN_PLAN §4.1），**每个关键参数都要标**：
> `measured` 现场实测 / `vendor` 厂商手册 / `standard` 国标行标 /
> `derived` 由其他参数算出 / `assumption` 明确假设 / `placeholder` 临时占位。
> `placeholder` 必须写进 `meta.todo`，并在报告里标「未校准」。

## L0 站点 · `layout_spec.json → site`

| 参数 | 当前值 | 单位 | 等级 | 校验 |
|---|---|---|---|---|
| `size` | `[112,114,12]` | m | placeholder | L12 >0 |
| `seed` | `20261106` | — | assumption | 决定 battery / 订单随机序列 |
| `floor_flatness_mm_per_3m` | `3` | mm/3m | standard | VNA 建议 ≤3 |
| `yard.trucks/forklifts/roads` | 2 / 3 / 3 | — | placeholder | 参与 `geometry.obstacle_rects` |

## L1 布局 · `layout_spec.json → defaults / warehouses[]`

| 参数 | 当前值 | 说明 |
|---|---|---|
| `rack.length/depth` | 3.0 / 1.2 m | bay 间距 = length + bay_gap |
| `rack.levels/level_height/top_height` | 4 / 1.5 / 6.0 m | L9：`levels*level_height ≤ top_height` |
| `aisle_width` | W 4.0 / F 3.2 m | L8：≥`2R+0.3=1.7592`；双向建议 ≥`4R+0.2=3.1184` |
| `ap.offset` | 1.5 m（wide 变体 W 用 1.25） | L5 ≥0.5 净距、L6 可走格 |
| `dock.depth / door_width / door_pitch` | 8 / 3.0 / 4.0 m | L10 门宽 ≥2.4、门距 ≥门宽+0.6 |
| `docks.zones[].doors` | W 5×4、F 3×2 → 共 38 扇 | 门只做元数据，不参与地面碰撞 |
| `spawns.points` | 21 个，对齐 0.5 m 栅格中心 | L7 两两 ≥1.5 m、不在实体内 |

**坐标规则**：`racks[].origin` 是**仓库局部坐标**，展开时 `+ warehouse.origin`。
写错这一行会让所有货架叠在原点，产生几百个假 `OVERLAP`。

**AP 互斥区（本项目实测 148 对）**：两侧 AP 距离 `A - 2*off`；
当前 W `4.0-3.0=1.0 m` < `2R=1.4592 m`，所以两台车不能同时停在这两个 AP。
- 方案 A（新建仓）：`A ≥ 2*off + 2R + 0.2`。`config/layout_spec_wide.json`
  仅对 W 仓用 `A=4.2, off=1.25` → 互斥降到 **29 对**（PASS）。
- 方案 C（当前）：由 `traffic_check.py` 的全局中心距判据隐含保证，
  `reservation_check.py` 独立复算「148 对中 0 对同时占用」。
- 注意：只加宽排间通道无法消除 F 仓「月台 AP ↔ 首排货架 AP」的互斥；
  若对 F 仓也加宽，会把第二排 AP 挤进充电桩膨胀区（L6 FAIL，已实测）。

## L2 设备 · `config/fleet_spec.json`

| 参数 | 当前值 | 等级 |
|---|---|---|
| `models[0].length/width/height` | 1.12 / 0.77 / 0.435 m | measured（cargoTwin 资产包围盒） |
| `speed_mps` | 1.5 | assumption（现场限速） |
| `accel/decel/omega` | 0.3 / 0.5 / 1.4 | vendor 级假设，未校准 |
| `battery_kwh / consumption_kwh_per_m / charge_kw` | 1.6 / 0.0008 / 3.0 | assumption |
| `mtbf_h / mttr_min` | 200 / 15 | placeholder |
| `deployment[].count` | 12 + 3×3 = **21** | 必须等于 layout 的 HOME 数 |
| `assignment.max_tasks_per_agent` | 3 | **场景层目标值**；主链 demo 仍为 1 单/车 |

## L3 需求 · `config/demand_spec.json`

| 参数 | 当前值 | 等级 |
|---|---|---|
| `source.type` | `synthetic` | 明确标注，不是真实数据 |
| `sku.count / velocity` | 240 / Zipf s=0.85 | placeholder |
| `orders.per_hour` | 45 | **placeholder**，需 WMS 校准 |
| `orders.mix` | 5 类合计 1.0 | assumption |
| `priority / deadline_s` | P1 10% / 900 s … | assumption |
| `csv_mapping` | 9 个必填列 | 见 `REAL_DATA_SOURCES.md` |

## L4 场景 · `config/scenarios/*.json`

| 场景 | 订单/h | 时长 | 车队 | 扰动 | 关键断言 |
|---|---|---|---|---|---|
| baseline | 45 | 8 h | 21 | — | 0 碰撞、准时 ≥0.95、360 单 |
| peak | 90 | 4 h | 28 | — | 吞吐 ≥1.5×baseline、利用率上升 |
| holiday | 135 | 4 h | 35 | — | 吞吐 ≥120、队列 ≤20 |
| night | 10 | 8 h | 12 | — | 利用率 ≤0.4 |
| robot_failure | 45 | 8 h | 21 | 3 台车各停 600 s | fault≥3、replans≥1 |
| dock_blocked | 45 | 8 h | 21 | 1 个出库月台封 1800 s | 改派≥1、排队≥1 |
| layout_variant_f4 | 45 | 8 h | 24 | 加 F4 仓 | 布局 0 FAIL、5 仓 |
| scale_50 | 90 | 4 h | 50 | — | 规划墙钟 <300 s |

`assertions[].value` 支持跨场景表达式，如 `"1.4*baseline:utilization"`。

## L6 渲染 · `data/config.json`

`render.width/height`、`anim_width/height`、`fps`、`samples`、
`scene.cameras`（6 个）、灯光、园区车辆、道路。静帧时刻按仿真时长比例取。

## 校验规则 L1–L12（`scripts/spec_layout.py`）

| ID | 内容 | 级别 |
|---|---|---|
| L1 | 必填字段 / 类型 / schema_version | FAIL |
| L2 | 实体在仓库 footprint 内 | FAIL |
| L3 | 实体两两不重叠 | FAIL |
| L4 | AP 不在任何实体内 | FAIL |
| L5 | AP 到最近实体 ≥0.5 m | WARN |
| L6 | AP 在 0.5 m 栅格可走 | FAIL |
| L7 | 待命位合法、两两 ≥1.5 m | FAIL |
| L8 | 通道 ≥2R+0.3；<4R+0.2 警告 | FAIL/WARN |
| L9 | `levels*level_height ≤ top_height` | FAIL |
| L10 | 门宽 ≥2.4、门距 ≥门宽+0.6 | WARN |
| L11 | 两侧 AP <2R → `AP_MUTEX` | WARN（必须输出 `mutex_groups`） |
| L12 | 尺寸 >0、层数 ≥1 | FAIL |

有 FAIL → 退出 1；只有 WARN → 退出 0 但报告必须列出。
6 个能失败的负测试见 `tests/test_spec_layout.py`。
