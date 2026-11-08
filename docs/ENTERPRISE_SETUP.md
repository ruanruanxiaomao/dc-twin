# 企业落地五步操作手册

> 依据 `DESIGN_PLAN.md` §14，结合本项目 **实际实现**（不是理想设计）编写。
> 当前版本：`layout_spec.json` + `spec_layout.py` 已落地；场景层为解析 what-if；
> 运营层为离线回放。边界见本文最后一节。

## Step 1 · 采集现场数据

| 项 | 怎么量 | 落到哪个字段 |
|---|---|---|
| 建筑内净尺寸 / 柱网 | CAD 图纸、激光测距 | `site.size`、`warehouses[].size`、`pillars` |
| 月台门数量/宽/高/中心距 | 现场量门洞 | `docks.zones[].doors`、`defaults.dock.*` |
| 货架长/深/层数/层高 | 货架图纸 | `defaults.rack.*`、`warehouses[].racks[].rack` |
| 通道净宽（不是轴线距） | 现场量净宽 | `warehouses[].racks[].aisle_width` |
| 托盘标准 | 现场 | `defaults.pallet` |
| AGV 尺寸/速度/电量 | 厂商手册 | `defaults.agv.*`、`fleet_spec.json` |
| 订单行 / SKU 频次 | 最近 3–6 个月 WMS 导出 | `demand_spec.json` |
| 班次 / 波次 / SLA | 业务 | `demand_spec.calendar`、`scenarios[].simulation` |

**先写 `site_survey.md` 记原始值 + 来源 + 测量日期，不要直接填 JSON。**

## Step 2 · 填 `config/layout_spec.json`

1. `site`：尺寸、`seed`、`coordinate_system`。
2. `defaults`：AGV、托盘、货架、通道、AP、月台、栅格。
3. `warehouses[]`：
   - `origin` 是仓库左下角在**站点**中的坐标；
   - `racks[].origin` 是**仓库局部**坐标（展开时会再加 `warehouse.origin`，最容易写错）；
   - `grid` 模式填 `rows/bays/aisle_width/bay_gap/margin`，`explicit` 模式填 `items[]`；
   - `docks/stations/chargers/spawns` 按实际位置填。
4. `meta.sources` 标 `measured/vendor/standard/derived/assumption/placeholder`；
   `meta.todo` 列出所有 `placeholder`。

> 本项目的 `config/layout_spec.json` 就是一份可抄的完整示例（4 仓 / 196 货架 / 38 扇门）。

## Step 3 · 干跑校验（不要覆盖主数据）

```powershell
python scripts/spec_layout.py --spec config/layout_spec.json
```

期望：`issues 0`；`warnings` 逐条判断（本项目 148 条全是 `AP_MUTEX`）。
打开 `out/spec_layout_report.json` 看 `summary` 与 `mutex_groups`。

**只有这一步 PASS，才允许**：

```powershell
python scripts/spec_layout.py --spec config/layout_spec.json --write data/layout.json --quiet
```

`--write` 会自动把旧 `layout.json` 备份成 `.bak`。

## Step 4 · 场景校准

```powershell
python scripts/scenario_run.py --all
python scripts/adapters/csv_orders.py --csv <你的订单.csv> --out out/csv_events.jsonl
python scripts/kpi.py --run runs/baseline/<时间戳>
```

对比：订单数/小时、SKU 频次、数量分布、路径长度、吞吐、准时率。
误差 >20% → 回 Step 1/2 查参数，**不要改阈值**（§0.2 规则 3）。
写 `calibration_report.md`：哪些已校准、哪些仍是假设。

## Step 5 · 试点运行

1. 选一个仓 / 一个班次，跑 `baseline` + `peak` + `robot_failure`。
2. 看 `runs/<id>/<ts>/assertions.json` 与 `kpi.json`（每个 KPI 带公式）。
3. 不满足业务目标 → 调布局/车队/策略，不改阈值。
4. 输出 `pilot_report.md`：结论、边界、下一步。

## 当前版本的真实边界

- **场景层不是逐车 A\* 仿真**：`scenario_run.py` 用 M/M/c 解析排队做 what-if；
  逐车路径与碰撞由主链 `plan_paths.py` / `traffic_check.py` 负责。
- **运营层是离线回放**：`fleet_sim.py` 基于主链 21 单路径回放状态机与电量，
  尚未把电量/故障/任务队列接进调度循环（DESIGN_PLAN Step 7 未做）。
- **交通层是独立复核**：`reservation_check.py` 在 0.05 s / 0.5 m 粒度上验证
  顶点/边冲突与 AP 互斥；**没有**实现 CBS/WHCA* 或 tick 级预约表。
- **需求仍是合成**：Zipf + 泊松到达；CSV/UCI/WMS 适配器已提供接口与校验，
  但未接入企业真实数据。
- **安全**：仿真只验证几何/时间冲突，不能替代 ISO 3691-4 风险评估。
