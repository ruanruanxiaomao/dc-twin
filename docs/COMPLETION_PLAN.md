# 配送中心数字孪生完善方案（COMPLETION PLAN）

> 版本：v1.0 · 2026-11-08 · 状态：**方案稿（未动代码）**
> 范围：A 真仿真引擎 · B 运营账本与盈亏 · C 定制深化
> 上游文档：`OPEN_SOURCE_PLAN.md` §5.3–5.5（本方案是其深化与扩展）、`PROJECT_REPORT.md`、`ENTERPRISE_SETUP.md`
> GitHub 调研快照日期：2026-11-08（star 数/推送日期均为当日 API 实测）

---

## 0. 现状核查结论（为什么需要本方案）

用户提出三个质疑，经源码级核查，结论如下（证据索引见附录 §8）：

| 质疑 | 核查结论 | 关键证据 |
|---|---|---|
| 不能按用户自己的配送中心实际情况调整 | **半属实**。四层 JSON 配置在「矩形仓 + 规则/显式货架」范围内真实可用；但控制台只放行 14 个渲染/运动参数，改不了布局/车队/需求；`fleet_spec.json` 未接入主链（车数实际来自布局 spawn）；WMS 适配器是纯 mock；仓库 footprint 只支持矩形单层 | `console/server.py:403–419`；`scripts/adapters/wms_api.py:24–25`；`config/layout_spec.json` meta.todo 自认 placeholder |
| 不能记录运营盈亏 | **完全属实**。25 个 KPI 无任何成本/收入/利润字段；代码中 "cost" 全部是 A* 路径代价；无成本模型、无账本、无损益报表 | `scripts/kpi.py:95–164` KPI 清单 |
| 不具备模拟功能 | **基本属实**。主链是一次性 21 单静态 A* 演示；`fleet_sim.py` 是离线回放（FAULT/CHARGING 只在迁移表里，`charging_events: 0`）；8 个场景是 M/M/c 排队论计算器——`robot_failure` 只把故障车折算成公式服务台数，`scale_50` 只断言 `fleet_size==50` 而无 50 车实际规划 | `scripts/scenario_run.py:6–7,88–116`；`scripts/kpi.py:68–69`；`scripts/fleet_sim.py:66–70,96` |

**本方案的目标**：把项目从「可复现的静态演示」升级为「可定制、可仿真、可算账的运营数字孪生」，同时保持项目既有的工程文化——单一真相源、派生数据禁手改、验证器必须能失败、降级如实声明。

---

## 1. 总体架构与依赖关系

### 1.1 三阶段依赖

```
C 定制深化 ──喂真实参数──┐
（布局/车队/需求/订单）    ▼
                    A 真仿真引擎 ──产生运营事件流──▶ B 账本与盈亏
                    （tick 级 DES）                 （成本/收入/损益）
                          │
                          └──▶ 既有 Blender 表现层（.blend / MP4 / GLB / 控制台）
```

- **A 是地基**：盈亏必须记在真实运营事件上（走了多少米、充了多少电、误了多少单），what-if 必须有真实执行。
- **B 消费 A 的事件流**：但 B 可以先做「简版」挂在现有 `fleet_sim.py` 回放上（降级路径 B0），不必等 A 全部完成。
- **C 相对独立**：配置编辑、数据接入可并行开发；其产出（真实布局/订单）最终喂给 A。
- **推荐执行顺序**：A 核心 → B 简版 → A 完整 + C 并行 → B 完整版。若用户更想先看到「钱」，可 B0 先行（见 §4.8）。

### 1.2 与现有 31 步链路的关系

现有链路（`dc_twin/runner.py`）：DATA_CHAIN（11 步）→ SCENARIO_CHAIN（5 步）→ BLENDER_CHAIN（5 步）→ POST_CHAIN（4 步）→ ACCEPT_CHAIN（6 步）。

**不重写主链**（遵守 runner.py 头部声明与 OPEN_SOURCE_PLAN §3.2），新增步骤以同样方式注册：

```
SCENARIO_CHAIN 追加（在 scenario_run --all 之后）：
  scripts/sim_engine.py        # A：tick 级仿真，输出 runs/<id>/<ts>/sim_events.jsonl + sim_kpi.json
  scripts/ledger.py            # B：事件 → 账本分录 → pnl.json
  scripts/pnl_report.py        # B：损益表 markdown/csv + 控制台数据

DATA_CHAIN 变更（C 阶段）：
  scripts/gen_tasks.py         # 改为消费 fleet_spec.deployment 的车数与车型（现状只读 spawns）
  scripts/import_layout.py     # C：DXF/LIF → layout_spec 草稿（可选步骤，不进默认链）
```

**既有验收 A1–A40 不动**。新能力用新验收段（A41+ / S 系列 / F 系列 / C 系列，见各阶段），同样要求独立复算 + `sys.exit(1)` + 负测试能失败。

### 1.3 全局设计原则

1. **单一真相源**：新增输入只有 `config/sim_spec.json`、`config/cost_spec.json`、`config/integrations.json`（均为四层配置的延伸，带 JSON Schema）。所有 `sim_events.jsonl` / `ledger.jsonl` / `pnl.json` 都是派生物，禁手改。
2. **KPI 必须带 source**：沿用 `kpi.py` 的 `definition/formula/source` 三元组；仿真产出的标 `source: "sim_engine measured"`，解析近似的继续标 `analytic`，**禁止混在一个数字里**（OPEN_SOURCE_PLAN §5.3 已有此要求）。
3. **确定性可复现**：仿真引擎用显式 seed 的独立 PRNG（参照 WareTwin 的 Mulberry32 对象化设计，可 deepcopy 做 what-if 分支），同 seed + 同配置 → 逐 tick 一致。
4. **物理阈值不降级**：`AGV_RADIUS=0.7296`、`MIN_SEP=1.4592` 禁止为通过测试调小（README §6 已有明令）。降级降的是**能力范围**，不是判据。
5. **降级如实声明**：每级降级对应 README「已知限制」的一条更新与 UI 徽章（`engine: analytic | replay | sim`、`prices: demo | user | live`）。

---

## 2. GitHub 参考项目总表（2026-11-08 实测）

### 2.1 仿真引擎方向（服务阶段 A）

| 项目 | Star | 许可证 | 可借鉴内容 | 使用方式 |
|---|---|---|---|---|
| **WayneChou-bot/WareTwin**（已 clone 至 `_research/repos/WareTwin`） | — | MIT | `backend/app/sim/engine.py` 是完整 tick 引擎：`TICK_S=0.1`、9 态 FSM、电量模型（移动/负载/待机/充电四档）、任务间隔生成、等待队列上限、拥堵区/封锁区注入、充电桩占用表、`(floor,col,row)→robot` 占用格、对象化 Mulberry32 PRNG（deepcopy 后随机流一致）、KPI 时间序列、事件环形缓冲 | **可直接移植代码**（MIT，需更新 THIRD_PARTY_NOTICES）。本项目已借过其 `astar.py`，是最低风险参照 |
| **Jiaoyang-Li/RHCR** | 223 | USC Research（非商业） | Rolling-Horizon Collision Resolution：每 h tick 重规划、只解未来 w tick 冲突（w≥h）；内置 KIVA/SORTING 两种任务派发器——正是「持续订单流」的标准范式 | **仅借算法思想**，不抄代码（许可证限制）；自研滚动时域层 |
| **GavinPHR/Multi-Agent-Path-Finding** | 447 | MIT | Python 实现的 CBS + Space-Time A*，含匿名 MAPF | **可直接移植/依赖**，作为碰撞求解升级首选 |
| **Kei18/lacam3** | 139 | MIT | LaCAM*：大规模（数百车）近实时 MAPF，C++ | 备选；需 C++ 编译链，降级时退回 Python CBS |
| **PathPlanning/Continuous-CBS** | 272 | MIT | 连续时间 CBS（动作含 move/wait，非纯格点） | 二期替换 0.05s 采样验证的候选 |
| **Jiaoyang-Li/EECBS** | 142 | NOASSERTION（USC 研究许可） | 有界次优 CBS，速度/质量权衡 | 仅概念 |
| **open-rmf/mapf** | 82 | Apache-2.0 | ROS2 生态的多智能体规划框架（含 traffic schedule  negotiated 协议） | 仅架构参考；引入 ROS2 成本过高，不采纳 |
| **openTCS/opentcs**（Fraunhofer IML） | 600 | MIT（代码） | 工业级 AGV 调度内核：routing/dispatching/scheduling 分层、transport order→drive order 分解、资源锁、VDA5050 内置支持、Java 21 | **借架构不嵌系统**：其「订单/工序/资源锁」模型是调度器设计的工业标准分解；整体嵌入需 Java 栈，违背本项目纯 Python 原则 |
| **SimPy**（PyPI 4.1.2，官方在 GitLab team-simpy/simpy） | — | MIT | 进程式离散事件仿真：`Environment/Process/Resource/Store`，纯 Python 零编译 | **引擎基座候选**（见 §3.2 决策矩阵） |
| **salabim/salabim** | 412 | 仓库未标 SPDX（需核实） | Python DES，自带动画/队列/统计 | 备选基座；许可证核实前不引入 |
| **jaamsim/JaamSim** | 242 | Apache-2.0 | Java DES 引擎，自带 3D 动画与拖拽建模 | 仅概念参考（Java 栈） |
| wh200720041/warehouse_simulation_toolkit | 316 | BSD-3 | ROS/Gazebo 仓储环境 + 单车 SLAM/导航 | **不采纳**：物理级单车仿真，Ubuntu/ROS 依赖，与本项目多车运营仿真目标不符 |
| **VDA5050/VDA5050** | 536 | 官方规范 | AGV 与主控的标准通信协议（order/state/visualization 主题，MQTT） | C 阶段真车对接的协议目标；A 阶段事件命名可对齐其语义 |

### 2.2 运营/盈亏方向（服务阶段 B）

| 项目 | Star | 许可证 | 可借鉴内容 | 使用方式 |
|---|---|---|---|---|
| **frappe/erpnext** | 41,085 | GPL-3.0 | Stock Ledger Entry（每笔库存移动带估值 rate）+ GL Entry（复式分录）联动；永续库存制；成本中心维度 | **仅借模式，严禁抄代码**（GPL 传染）。本项目账本自研，结构参照 SLE→GL 的「业务事件→财务分录」映射 |
| **beancount/beancount** | 6,153 | GPL-2.0 | 文本复式记账：交易必须借贷平衡、账户树（Income/Expenses/Assets）、商品定价指令 | 仅借概念；账本校验「每笔分录平衡」规则可自研实现 |
| **deb-sig/double-entry-generator** | 734 | （需核实） | 规则驱动的「原始流水→记账分录」导入器（支付宝/微信→beancount） | 「仿真事件→分录」映射规则引擎的设计参照 |
| rv-online/warehouse-kpi-mart | 0 | 无许可证 | 原始订单→财务/运营双口径聚合的 KPI mart 管道 | 仅概念（无许可证不可抄） |
| 其余 warehouse-kpi/dashboard 类 | 0–1 | 多为 portfolio 项目 | 无工程价值 | 不采纳 |

**结论**：盈亏方向没有可直接复用的成熟开源组件，正确做法是**自研轻量账本**（JSONL 事件溯源 + 可选复式校验），模式参照 ERPNext/beancount 的公开概念。这符合管理会计口径（内部决策用），不做对外财报/税务合规。

### 2.3 定制/接入方向（服务阶段 C）

| 项目 | Star | 许可证 | 可借鉴内容 | 使用方式 |
|---|---|---|---|---|
| **Stegocode/warehouse_layout_editor** | 3 | 未核实（引入前必查） | 浏览器布局编辑器：zone/rack row/node/edge 工具栏、2D 平面+3D 预览、**校准背景描图**、JSON 导入导出、vendored three.js、零构建步骤——与本项目 console 技术栈几乎一致 | **交互与架构参照首选**；代码借鉴前核实许可证，否则只学交互自研 |
| **mozman/ezdxf** | 1,522 | MIT | Python 读写 DXF：LWPOLYLINE/LINE/INSERT/图层 | **可直接依赖**：真实 CAD 平面图→layout_spec 导入器 |
| **IfcOpenShell/IfcOpenShell** | 2,943 | LGPL-3.0 | IFC/BIM 模型解析与几何引擎 | 二期可选（真实建筑 BIM 导入）；LGPL 动态链接边界需法务确认，降级为不采纳 |
| **bekirbostanci/vda5050_lif_editor** | 62 | （需核实） | VDA5050 **LIF（Layout Interchange Format）**编辑器：节点/边/站点的工业标准布局交换格式 | 布局导入/导出对齐 LIF 语义，便于与真车系统互通 |
| **openboxes/openboxes** | 922 | EPL-1.0 | 真实开源 WMS（医疗供应链），Groovy/Grails，REST API，库存移动追踪 | **真实 WMS 适配器首选对接目标**（有公开 API 与 demo 服务器） |
| **openwms/org.openwms** | 761 | （多模块） | Java 开源 WMS，微服务化 | 备选对接目标 |
| **OCA/wms** | 226 | （Odoo 生态 AGPL 模块为主） | Odoo 高级仓储模块；Odoo 本体有 JSON-RPC/XML-RPC API | 适配器目标之二（国内 Odoo 用户多） |
| **erzhongxmu/JeeWMS** | 153 | （需核实） | 国产 Spring MVC WMS | 国内企业对接备选 |
| MarioDelgadoSr/MyWarehouseVisualizer | 61 | 无许可证 | 布局+数据可视化 | 仅概念 |
| samirsaci/pareto-warehouse-layout | 23 | 无许可证 | Pareto 货位分析文章代码 | 仅概念（slotting 优化二期参考） |
| 本地 `_research/repos/cargotwin`、`smart-warehouse` | — | MIT | 资产生成 / MCP→GLB | 已用于资产层，与本方案无新增交集 |

### 2.4 合规红线汇总

- **可抄代码**（更新 THIRD_PARTY_NOTICES 即可）：WareTwin、GavinPHR/MAPF、lacam3、Continuous-CBS、ezdxf、SimPy、openTCS（MIT 代码部分）。
- **仅借概念**：RHCR、EECBS（USC 研究许可）；ERPNext、beancount（GPL）；无许可证仓库一律只看不抄。
- **引入前必查许可证**：Stegocode 编辑器、vda5050_lif_editor、salabim、JeeWMS、double-entry-generator。
- **依赖新增原则**：主链保持 bpy+标准库；新依赖只允许 MIT/Apache/BSD 且锁版本进 `requirements.txt`（候选：simpy、ezdxf；CBS 优先 vendored 单文件而非 pip 依赖）。

---

## 3. 阶段 A：真仿真引擎

### 3.1 目标与非目标

**目标**
1. tick 级离散事件引擎：订单持续到达 → 任务队列 → 在线派单 → 多单/车 → 电量消耗 → 充电决策 → 故障注入与恢复 → 全程可观测事件流。
2. 8 个场景从「公式输入」变成「真实执行」：`robot_failure` 真的让指定车趴窝并触发重派单；`scale_50` 真的规划并运行 50 台车；`dock_blocked` 真的封月台改道。
3. KPI 全部来自实测事件（`source: measured`），与解析引擎并列展示、显式区分。
4. 碰撞解决从「优先级错峰 + 采样验证」升级为「时空预约表 + 可选 CBS 求解」，验证口径不变（MIN_SEP 不动）。

**非目标（本期不做，如实声明）**
- 物理级仿真（动力学/激光雷达/Gazebo）——参照项目 warehouse_simulation_toolkit 属此类，不采纳。
- 连续时间零碰撞的形式化证明——继续用 sampled verification + reservation check 措辞。
- 多楼层/电梯——WareTwin 有现成实现（LIFT_* 参数族），留作 C 阶段后的可选扩展。
- 真实 AGV 通信（VDA5050 MQTT）——C 阶段后期目标。

### 3.2 引擎基座选型（决策矩阵）

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **自研 tick 引擎（移植 WareTwin 模式）** | 与现有数据契约（layout/nav_grid/paths）无缝；MIT 可抄；确定性 PRNG、deepcopy what-if 已验证；无新依赖 | 需自己写队列/统计（WareTwin 已有范式） | **首选** |
| SimPy 基座 | 成熟 DES 语义（Resource/Store/抢占）、MIT、pip 即装 | 进程式模型与现有「关键帧时间线」数据契约需双向适配；随机流控制不如显式 tick 直观 | **备选**：若自研 tick 循环复杂度失控则切换 |
| 嵌入 openTCS | 工业级调度完备 | Java 21 栈、进程间通信、体量巨大 | 不嵌入，仅借其订单分解模型 |
| JaamSim/salabim | 现成统计/动画 | Java / 许可证未核实 | 不采纳 |

**决策**：自研 `scripts/sim_engine.py`（纯标准库），核心模式移植 WareTwin `engine.py`（MIT）；把 SimPy 列为降级备选（§3.10 D2）。

### 3.3 引擎架构

```
sim_engine.py
├─ SimConfig        ← config/sim_spec.json（四层配置延伸，带 schema）
├─ World            ← data/layout.json + nav_grid.json + slotting.json（只读）
├─ Fleet            ← config/fleet_spec.json（车型/电量/部署数，C 阶段全量接入）
├─ DemandStream     ← config/demand_spec.json（per_hour/waves/calendar）或 csv_events.jsonl
├─ TaskQueue        优先级队列（deadline/优先级/类型权重），上限 max_waiting
├─ Dispatcher       派单策略：nearest-first（默认）→ 可选 auction/ regret-insert
├─ RobotFSM         IDLE→ASSIGNED→MOVING_PICK→PICKING→MOVING_DROP→DROPPING→RETURNING
│                   + CHARGING / FAULT / WAITING（沿用 fleet_sim 9 态并真正激活后三态）
├─ TrafficLayer     0.5m 栅格时空预约表 cell_reserved[(x,z,t_bucket)]
│                   L1: 优先级预约（现状增强）→ L2: WHCA* 窗口 → L3: CBS（GavinPHR MIT）
├─ BatteryModel     移动/负载/待机/充电四档（参数来自 fleet_spec.models，已有字段）
├─ FaultInjector    场景驱动：robot_down / dock_blocked / demand_surge / charger_down
├─ EventLog         append-only sim_events.jsonl（B 阶段的唯一输入）
└─ KPICollector     每 N tick 采样时间序列 + 终值汇总 → sim_kpi.json（25 项对齐 + 新增项）
```

**tick 循环伪代码**（参照 WareTwin tick 顺序，参数默认值取自其 SIM 常量并按本项目标定）：

```python
TICK_S = 0.1                     # WareTwin 同款；可配
for tick in range(horizon_ticks):
    t = tick * TICK_S
    demand.maybe_arrive(t)       # 泊松到达 × waves 乘数 × calendar 班次
    faults.apply(t)              # 场景注入：趴窝/封锁/恢复
    dispatcher.assign()          # 队列→空闲车（电量≥reserve 才可派）
    for robot in fleet:
        robot.fsm_step(t)        # 移动沿 A* 折线；到点 FACE/PICK/DROP 停靠
        battery.update(robot)    # 四档能耗；低于 critical → 中断任务去充电
    traffic.resolve(t)           # 预约表冲突 → 等待/重规划（L1/L2/L3）
    events.flush(t)              # 状态迁移/任务完成/充电/故障 → JSONL
    kpi.sample(t)                # 每 KPI_EVERY tick
```

**关键参数默认值**（WareTwin 实测常量 → 本项目标定）：

| 参数 | WareTwin 原值 | 本项目默认 | 说明 |
|---|---|---|---|
| TICK_S | 0.1 | 0.1 | 与 0.05s 采样验证兼容（2 tick/采样） |
| MAX_SPEED | 1.5 m/s | 1.5 | 与主链物理时间一致，**不再时间压缩** |
| PICK/DROP 停靠 | 40/30 tick | 2.0 s（沿用 dwell_pick_s） | 与主链 FACE/PICK 关键帧一致 |
| MIN_SEP | 0.9 m | **1.4592 m（本项目硬阈值，不改）** | 2×AGV_RADIUS |
| 电量四档 | 0.010/0.004/0.0008/0.06 %/tick | 改用 fleet_spec 物理单位：0.0008 kWh/m、idle 0.05 kW、charge 3 kW、电池 1.6 kWh | fleet_spec.models 已有这些字段，直接消费 |
| 任务到达 | 固定 70 tick | demand_spec.orders.per_hour（默认 45，placeholder 需用户改）+ waves | 已有配置字段 |
| 等待队列上限 | 12 | 12（可配） | 超限订单记 LOST（进 KPI 与账本罚金） |

### 3.4 数据契约（新增）

**输入 `config/sim_spec.json`**（schema 进 `config/schemas/sim_spec.schema.json`）：

```json
{
  "schema_version": "1.0",
  "engine": "tick",
  "tick_s": 0.1,
  "horizon_s": 3600,
  "seed": 42,
  "traffic": {"level": "L1_reservation", "replan_window_s": 10, "max_wait_s": 60},
  "dispatch": {"policy": "nearest_first", "max_waiting": 12, "battery_reserve_pct": 20},
  "battery": {"charge_to_pct": 95, "charge_policy": "opportunistic"},
  "faults": [],
  "output": {"event_ring": 5000, "kpi_every_ticks": 10}
}
```

**输出 `runs/<scenario>/<ts>/sim_events.jsonl`**（每行一个事件，B 阶段唯一输入）：

```json
{"seq":1024,"t_s":123.4,"type":"TASK_COMPLETED","agent":"W-R03","order_id":"ORD-0087",
 "legs_m":{"empty_out":12.5,"loaded":26.2,"empty_back":9.8},"energy_kwh":0.031,
 "tardiness_s":0,"pick_loc":"W-A03-2","drop_loc":"W-DOCK-02"}
```

事件类型全集：`ORDER_ARRIVED / ORDER_LOST / TASK_ASSIGNED / TASK_REASSIGNED / STATE_CHANGED / PICK_DONE / DROP_DONE / TASK_COMPLETED / CHARGE_START / CHARGE_END / BATTERY_CRITICAL / FAULT_START / FAULT_END / DOCK_BLOCKED / DOCK_REOPENED / REPLAN / WAIT_CONFLICT`。每个事件带 `seq`（全局单调）与 `t_s`，账本按 seq 幂等消费。

**输出 `sim_state.json`**（终态快照：每车位置/电量/状态、队列深度、库存增减——沿用 `cargo_state.json` 语义）。
**输出 `sim_kpi.json`**：25 项既有 KPI 的 measured 版本 + 新增 `orders_lost / charge_wait_s / fault_downtime_s / recovery_s / replans / availability / energy_kwh_total`，每项带 definition/formula/source。

### 3.5 场景逐个改造表

| 场景 | 现状（解析） | 改造后（实测） | 断言示例 |
|---|---|---|---|
| baseline | M/M/c | 引擎跑 1h 合成订单流 | completed>0、strict_collisions=0 |
| peak | λ×1.5 进公式 | waves 乘数真实生效，队列堆积可见 | max_waiting>baseline、tardiness>0 |
| night | λ×0.3 | 低到达率，多数车 IDLE→PARK | idle_rate>0.6 |
| holiday | 混合比改 | mix 真实改变任务类型分布 | 类型计数匹配 ±5% |
| dock_blocked | 服务台数折算 | 指定月台格点封锁→A* 改道/换月台 | 受影响订单 drop_loc≠被封月台 |
| robot_failure | `effective = fleet - faults×(600/duration)` | 指定 agent FAULT_START/END，任务重派 | 该车 TASK_COMPLETED=故障窗口内 0；reassigned>0 |
| scale_50 | 只查 fleet_size==50 | **真 50 车**：fleet_spec.deployment 覆盖→50 个 HOME→50 车 FSM | agents==50 且 50 车均有轨迹；墙钟记录 |
| layout_variant_f4 | 换 spec 重算公式 | 换 layout_spec_f4 真跑 | layout_issues=0 |

每个场景保留 `engine: analytic | sim` 双跑能力（`scenario_run --engine sim --id robot_failure`），控制台加徽章；`dc compare` 可对比两引擎差异（OPEN_SOURCE_PLAN §5.2 已规划）。

### 3.6 碰撞求解升级路线

| 级别 | 方法 | 来源 | 何时启用 |
|---|---|---|---|
| L1（默认） | 栅格时空预约表 + 优先级 + 等待/backoff | WareTwin occupancy/reserved_by 模式（MIT） | A 阶段 MVP |
| L2 | WHCA*（窗口 w 内合作 A*） | 自研，参照 RHCR 滚动时域思想（仅概念） | 50 车出现死锁/等待超限时 |
| L3 | CBS / Space-Time A* | GavinPHR/Multi-Agent-Path-Finding（MIT，可 vendored） | 需要近优解的对比实验 |
| L4（二期） | 连续时间 CBS / SIPP | PathPlanning/Continuous-CBS（MIT） | 替换采样验证的学术口径时 |

验收措辞纪律：任何级别都只宣称 `sampled verification` / `reservation check`，不宣称 formal guarantee（OPEN_SOURCE_PLAN §5.4 明令）。

### 3.7 实施步骤（A1–A8）

| 步 | 内容 | 产出 | 验收 |
|---|---|---|---|
| A1 | 移植 WareTwin tick 骨架 + 确定性 PRNG；World 只读现有 data/ | `sim_engine.py` 骨架、`sim_spec.schema.json` | 同 seed 双跑逐 tick 一致（diff=0） |
| A2 | DemandStream + TaskQueue + Dispatcher（nearest-first） | 持续订单流、多单/车 | 1h 仿真 completed ≥ per_hour×0.9；LOST 有记录 |
| A3 | RobotFSM 全 9 态激活 + BatteryModel（消费 fleet_spec 物理参数） | CHARGING/FAULT 真实发生 | charge_events>0；电量不低于 0；critical 触发充电 |
| A4 | TrafficLayer L1 预约表 | cell_reserved 全程 | reservation_check 同口径 0 顶点冲突；MIN_SEP 采样≥1.4592 |
| A5 | FaultInjector + 8 场景改造 | scenario_run --engine sim | §3.5 每场景断言全过；负测试：故意断言错误能 FAIL |
| A6 | KPICollector measured + 双引擎徽章 | sim_kpi.json、控制台徽章 | 25+7 KPI 全带 source；analytic/sim 差异显式展示 |
| A7 | scale_50 真 50 车 + 性能基准 | runs/scale_50 | 50 车全轨迹；记录墙钟/内存；死锁率<1% |
| A8 | （可选）L2/L3 求解器接入 | traffic.level 可配 | L3 与 L1 同场景对比报告（里程/等待/冲突） |

### 3.8 阶段 A 验收标准（S 系列，独立复算 + 可失败）

- S1 确定性：同配置同 seed 两次运行 `sim_events.jsonl` 逐行一致。
- S2 能量守恒：Σ(移动×0.0008 + idle×0.05kW + 充电输入) 与电量曲线闭合，误差<1%。
- S3 多单真实性：至少 1 台车完成 ≥3 单（现状每车 1 单）。
- S4 故障真实性：robot_failure 窗口内故障车 0 完成任务，且存在 TASK_REASSIGNED。
- S5 充电真实性：charge_events>0，充电期间该车 0 移动。
- S6 交通：全程采样两车距 ≥1.4592 m；预约表 0 双占。
- S7 规模：scale_50 有 50 条独立轨迹且 0 布局违规。
- S8 KPI 溯源：每个 measured KPI 可由 sim_events.jsonl 独立复算（验证器重算，不信引擎自报）。
- S9 负测试：篡改事件流后 S2/S4/S6 必须 FAIL（延续 tests/test_validator_can_fail.py 文化）。

### 3.9 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 50 车 L1 预约死锁 | 中 | scale_50 卡死 | 等待超时 backoff+重规划（WareTwin WAIT_REPLAN/BACKOFF 已有）；升级 L2 |
| tick 引擎性能不足（1h×50 车×10tick/s=180 万 tick） | 中 | 墙钟过长 | 事件驱动跳 tick（空闲期大步进）；KPI 采样降频；基准实测后定 |
| 与主链 21 单演示语义冲突 | 低 | 既有验收破坏 | 引擎走独立 SCENARIO_CHAIN 新步骤，主链 DATA_CHAIN 不动 |
| WareTwin 代码移植带入 TS/Python 双实现怪癖（jsround 等） | 低 | 数值微差 | 只移植结构，数值函数用 Python 原生；THIRD_PARTY_NOTICES 登记 |

### 3.10 降级方案（逐级，触发条件明确）

| 级 | 触发条件 | 降级动作 | 保留能力 | 明示文案 |
|---|---|---|---|---|
| D1 | L1 预约在 50 车死锁率>1% 且 L2 两周内未解 | scale_50 退回 21 车实测 + 50 车解析，双徽章 | 其余 7 场景真仿真 | 「50 车为解析规模测试」（现状文案保留） |
| D2 | 自研 tick 循环复杂度失控（A1–A3 超预算 2 倍工时） | 切换 SimPy 基座（MIT，pip 锁版本），事件契约不变 | 全部目标，仅基座换 | requirements.txt 新增 simpy |
| D3 | CBS vendored 集成失败 | traffic.level 封顶 L1，L3 标记 experimental 不进默认链 | L1 预约实测 | 「碰撞为采样验证+预约复核，非形式化证明」 |
| D4 | 性能不达标（1h 仿真墙钟>10min） | horizon 默认 1800s + 时间加速选项（显式标注压缩倍率） | 功能完整，时长缩短 | 「仿真时长 30min，时间压缩×1」 |
| D5 | 全部引擎工作受阻 | 保留现状解析引擎 + 本方案文档归档 | 现状能力不退化 | README 已知限制不变 |

**降级纪律**：任何降级不得调小 MIN_SEP、不得伪造 measured 标签、不得删除负测试。

---

## 4. 阶段 B：运营账本与盈亏

### 4.1 目标与非目标

**目标**：每次仿真/回放运行自动产出一套管理会计口径的损益表——收入、变动成本、固定成本分摊、毛利、单均成本、盈亏平衡点；支持 what-if 对比（改电价/改车数/改布局后盈亏怎么变）。

**非目标**：对外财务报表、税务合规、总账/应收应付（那是 ERP 的事）；本项目只做**运营决策口径的管理会计**，并在报表头部固定声明此口径。

### 4.2 成本/收入模型（`config/cost_spec.json`）

```json
{
  "schema_version": "1.0",
  "currency": "CNY",
  "prices_source": "demo",
  "revenue": {
    "per_order": {"INBOUND": 3.5, "OUTBOUND": 6.0, "PICK": 4.0, "REPLENISH": 2.5, "TRANSFER": 3.0},
    "per_line_extra": 0.8,
    "sla_penalty_per_tardy_s": 0.02,
    "lost_order_penalty": 5.0
  },
  "variable_costs": {
    "energy_tariff_per_kwh": 0.85,
    "consumables_per_order": 0.15
  },
  "fixed_costs_per_day": {
    "labor": {"operators": 4, "hourly_rate": 35, "shift_h": 8, "supervision_ratio": 0.25},
    "robot_depreciation": {"capex_per_unit": 120000, "life_h": 12000, "units": 21},
    "maintenance_pct_of_capex_per_year": 0.06,
    "space_rent_per_m2_month": 28,
    "it_systems_per_day": 150
  },
  "allocation": {"basis": "orders", "shift_hours": 8}
}
```

字段来源说明：
- 能耗成本**不拍脑袋**：kWh 来自引擎/回放的实测里程×fleet_spec 能耗系数（`consumption_kwh_per_m=0.0008` 等已有字段）×电价。
- 折旧按运行小时摊：`capex/life_h × 运行h`，与利用率挂钩（车闲置也摊——这是固定成本，报表单列）。
- 全部默认值是**演示价格**（`prices_source: "demo"`），用户填真实价后改 `"user"`；C 阶段接 ERP 后可 `"live"`。UI 与报表头部必须显示该徽章。

### 4.3 事件溯源账本（`scripts/ledger.py`）

**模式参照**（仅概念，不抄代码）：ERPNext 的「每笔库存移动→Stock Ledger Entry→GL Entry」与 beancount 的「交易必须平衡」。

```
sim_events.jsonl / fleet_report.json（B0 降级输入）
        │  映射规则（rules 内置，可配）
        ▼
runs/<id>/<ts>/ledger.jsonl   append-only，每行一笔分录
        │  聚合
        ▼
pnl.json + pnl.md + cost_per_order.csv
```

分录结构（单式为主，可选复式校验模式）：

```json
{"seq":1024,"run_id":"peak/20261108-2100","t_s":123.4,"source_event_seq":1024,
 "account":"Revenue:Outbound","amount":6.0,"direction":"credit",
 "order_id":"ORD-0087","cost_center":"W","memo":"TASK_COMPLETED"}
```

账户树（自研，概念对齐 beancount）：
```
Revenue:{INBOUND,OUTBOUND,PICK,REPLENISH,TRANSFER}
Revenue:SLA_Penalty（负收入）
Cost:Variable:{Energy,Consumables}
Cost:Fixed:{Labor,Depreciation,Maintenance,Rent,IT}
Cost:SLA_Penalty / Cost:LostOrder
```

映射规则表（事件→分录，幂等按 source_event_seq）：

| 事件 | 分录 |
|---|---|
| TASK_COMPLETED | Revenue:<type> +per_order；Cost:Variable:Consumables |
| 每 tick 能耗累计（引擎 KPI 事件） | Cost:Variable:Energy = kWh×tariff |
| TASK_COMPLETED tardiness>0 | Cost:SLA_Penalty = tardiness_s×rate |
| ORDER_LOST | Cost:LostOrder |
| 运行结束 | 固定成本按 shift 分摊一次性入账 |

**账本校验器**（可失败）：分录总额 = 报表总额；每笔 source_event_seq 可在事件流中找到；复式模式下借贷平衡；重复 seq 拒绝（幂等）。

### 4.4 报表与控制台

**`pnl.md` / `pnl.json`**（班次/日口径）：
```
收入合计        ¥XXXX   （单量×单价，分类型明细）
变动成本        ¥XXXX   （电费实测 kWh×电价 + 耗材）
边际贡献        ¥XXXX   = 收入 − 变动成本
固定成本分摊    ¥XXXX   （人工/折旧/维护/仓租/IT，按 orders 或工时摊）
运营利润        ¥XXXX   = 边际贡献 − 固定分摊
单均成本        ¥XX.XX  / 单均收入 ¥XX.XX
盈亏平衡点      XXX 单/班 （固定成本 ÷ 单均边际贡献）
敏感性          电价±20% / 单量±20% / 车数±2 的利润区间
```

**控制台新增「运营」面板**（第 6 面板，沿用标准库+Canvas 无新依赖）：
- 损益瀑布图（收入→变动→固定→利润）、单均成本趋势、场景对比（baseline vs peak 的利润差）。
- API：`GET /api/pnl?run=...`、`GET /api/ledger?run=...&account=...`、`POST /api/cost-spec`（白名单写 cost_spec.json，备份+范围校验，沿用现有 /api/config 模式）。
- 深链接：`?tab=ops&run=peak/20261108-2100`。

### 4.5 实施步骤（B1–B6）

| 步 | 内容 | 产出 | 验收 |
|---|---|---|---|
| B0（可先行） | 简版账本挂 fleet_sim 回放：里程→电费、21 单→收入、固定成本分摊 | ledger/pnl 最小闭环 | 报表数字可由 fleet_report.json 复算 |
| B1 | cost_spec.json + schema + 演示默认值 | 输入契约 | schema 校验负测试能失败 |
| B2 | ledger.py 事件映射 + 幂等 + 校验器 | ledger.jsonl | F 系列验收（下） |
| B3 | pnl_report.py：损益表/单均/盈亏平衡/敏感性 | pnl.json/md/csv | 报表总额=账本总额 |
| B4 | 接入 sim_events.jsonl（A 完成后），B0 输入保留为降级 | 双输入源 | 同事件流两种输入结果一致 |
| B5 | 控制台运营面板 + API + prices_source 徽章 | 第 6 面板 | 浏览器实测（golden path + 空账本边界） |
| B6 | dc compare 扩展：两 run 的盈亏 delta | 对比报告 | baseline vs peak 利润差可复算 |

### 4.6 阶段 B 验收标准（F 系列）

- F1 可复算：pnl.json 每个数字可由 ledger.jsonl 独立聚合得出（验证器重算）。
- F2 溯源：每笔分录的 source_event_seq 存在于事件流；抽查 10 笔金额=公式值。
- F3 幂等：ledger.py 重跑同一事件流，账本不重复入账。
- F4 能量衔接：电费 = 实测 kWh × 电价，kWh 与 S2 守恒值一致。
- F5 盈亏平衡自洽：平衡点单量 × 单均边际贡献 = 固定成本（±1 单舍入）。
- F6 徽章诚实：prices_source=demo 时报表头部必须有「演示价格」声明；缺失则 FAIL。
- F7 负测试：篡改一笔分录金额 / 删除一个 source 事件 / 重复 seq，校验器必须 FAIL。

### 4.7 风险登记

| 风险 | 缓解 |
|---|---|
| 用户误把演示价格当真实决策依据 | prices_source 徽章 + 报表固定声明 + README 已知限制 |
| 固定成本分摊口径争议（按单/按工时/按面积） | allocation.basis 可配，报表注明口径；不做「唯一正确」宣称 |
| 与真实 ERP 数字对不上 | 明确管理会计口径；C 阶段提供 ERP 导出对账接口（只读比对，不写入） |
| GPL 概念借鉴被误读为抄代码 | 账本全自研；THIRD_PARTY_NOTICES 注明「概念参照，无代码衍生」 |

### 4.8 降级方案

| 级 | 触发条件 | 降级动作 | 保留能力 | 明示文案 |
|---|---|---|---|---|
| E1 | A 引擎延期 | B0 先行：账本挂 fleet_sim 回放（21 单/轮） | 完整报表结构，数据为回放级 | 「基于离线回放的演示测算」 |
| E2 | 用户无真实价格 | 演示默认值 + 敏感性区间（±20%）代替点估计 | 决策方向仍可用 | 「演示价格，区间仅供参考」 |
| E3 | 复式校验复杂度超预算 | 单式账本 + 总额校验（放弃借贷平衡检查） | 损益表完整 | 「单式管理账本」 |
| E4 | 控制台面板工期紧 | 先出 pnl.md/csv 文件 + 现有产物面板挂链接 | 报表可用，无可视化 | 面板标「即将上线」 |
| E5 | 全部受阻 | 仅交付 cost_spec.json 模板 + 本报告 | 现状不退化 | — |

---

## 5. 阶段 C：定制深化

### 5.1 目标

让「改 JSON 不改 Python」升级为「改界面/导图纸/接系统，不碰 JSON 也能定制」，并让 fleet_spec 真正生效。

### 5.2 控制台四层配置编辑（C1）

现状：`console/server.py:403–419` 白名单仅 14 个渲染参数，只写 `data/config.json`。

改造（沿用现有安全模式：白名单+范围校验+写前备份）：
- 新端点：
  - `POST /api/spec/validate`：只读校验任意 spec（调 spec_layout.py L1–L12 / schema），返回 issues。
  - `POST /api/spec/dry-run`：临时工作区试算数据层（10 步纯 Python，约 10s），返回 KPI/布局图预览，**不落盘**。
  - `POST /api/spec/save`：显式「保存并重跑」，备份原文件为 `*.bak.json`，触发分级重跑。
- 前端参数面板分 4 组：布局（layout_spec 关键字段：仓尺寸/排数/bay/通道宽/月台门）、车队（fleet_spec：车型数/速度/电量阈值）、需求（demand_spec：per_hour/mix/waves）、渲染（现有 14 项）。
- 每组编辑→实时 validate→diff 预览（与当前 spec 的字段级差异）→保存重跑。
- **安全边界**：路径类字段禁止 `..`/绝对路径；spec 写入仅限 config/ 下白名单文件；dry-run 用临时目录（参照 tests/test_cleanroom.py 的隔离模式）。

### 5.3 可视化布局编辑器（C2）

参照 **Stegocode/warehouse_layout_editor** 的交互（ MIT 核实前只学交互自研）：
- 工具栏：选择/移动、+分区、+货架排、+月台门、+AP/待命位、删除；快捷键 V/Z/R/N/E/X。
- 2D 画布拖拽 + 实时 L1–L12 校验红框提示 + 3D 预览（复用现有 viewer3d.js）。
- **校准背景描图**：用户上传真实平面图 PNG/PDF 截图，两点标定比例尺，在底图上描货架/墙——这是「不会 DXF 也能定制」的关键降级路径。
- 导出 = 生成 layout_spec.json 草稿 → validate → diff → 保存重跑（走 C1 端点）。
- 数据模型对齐 VDA5050 LIF 语义（节点/边/站点），为 C5 互通打基础。

### 5.4 真实订单/WMS 接入（C3）

现状：`wms_api.py` 纯 mock；CSV 适配器真实但样例合成。

**适配器框架**（自研接口，参照 openTCS communication adapter 的解耦模式）：
```python
class WmsAdapter:                      # scripts/adapters/base.py
    def fetch_orders(since) -> list[Order]   # 拉单（幂等键 order_id）
    def ack_order(order_id, status)          # 回执
    def sync_locations() -> list[Location]   # 库位主数据同步
    # 认证：token/basic；重试：指数退避×3；schema 版本协商；只读优先
```

| 适配器 | 目标系统 | 协议 | 优先级 |
|---|---|---|---|
| csv（增强） | 任意 ERP 导出 | 文件 | P0：已有，补列映射 UI + 增量导入 |
| openboxes | OpenBoxes（922★，EPL，有公开 REST API 与 demo） | REST | P1：首选真实对接验证对象 |
| odoo | Odoo/OCA wms（国内用户多） | JSON-RPC | P1 |
| generic_rest | 企业自研 WMS | 用户配 endpoint/字段映射 | P2 |
| vda5050 | 真车主控 | MQTT | P3（后期，非本期承诺） |

`integrations.json` 存连接配置（**密钥不进 git**：环境变量引用 + .gitignore；SECURITY.md 同步）。
验收用 OpenBoxes 公开 demo 或本地 mock server 做契约测试；**不向任何生产系统写入**（ack 默认 dry-run 模式）。

### 5.5 布局导入（C4）

降级链（从自动到手工）：
1. **DXF 导入**（ezdxf，MIT，pip 锁版本）：读 LWPOLYLINE/LINE/INSERT + 图层映射配置 `layer_mapping.json`（墙层→实体、货架层→racks、标注层→尺寸）→ 生成 layout_spec 草稿 → L1–L12 校验 → 人工确认。两点标定比例尺（图纸单位≠米时）。
2. **VDA5050 LIF 导入/导出**：JSON 节点/边格式，与真车系统互通（参照 vda5050_lif_editor 的数据结构，许可证核实前自研解析）。
3. **背景描图**（C2 编辑器内置）：DXF 太乱/没有 CAD 时的兜底——上传平面图截图，手工描。
4. **IFC/BIM**（IfcOpenShell，LGPL，二期可选）：真实建筑模型导入；法务确认 LGPL 边界前不引入。

所有导入产出都是**草稿**，必须过 spec_layout 校验 + 人工「保存并重跑」，绝不静默覆盖现有布局。

### 5.6 fleet_spec 接入主链（C5）

现状：`gen_tasks.py:24–28` 车数来自布局 spawns，fleet_spec.deployment 只被 fleet_sim/kpi 消费。

改造：
- `spec_layout.py` 按 fleet_spec.deployment 的每仓 count 生成 HOME/spawn（布局提供待命带容量上限，超配报 L7 校验错误）。
- 车型参数（length/width/safety_margin）→ `geometry.py` 的 AGV_RADIUS 按车型计算（现状单车型硬编码 0.7296）。
- **异构车队降级**：本期只支持单车型全仓统一；多车型混跑（不同半径/速度）留二期，fleet_spec schema 预留但校验器拒绝混配并给出明确错误。
- 电量参数已在 A 阶段消费（§3.3），此处补齐「车数/几何」侧。

### 5.7 实施步骤（C1–C7）与验收

| 步 | 内容 | 验收（C 系列） |
|---|---|---|
| C1 | 控制台四层配置编辑 + dry-run | C-a：改排数→validate→dry-run→保存重跑全链 PASS；越界/路径注入被拒（负测试） |
| C2 | 布局编辑器（拖拽+描图） | C-b：描图导出 spec 过 L1–L12；浏览器实测拖拽/撤销/导出 |
| C3 | 适配器框架 + CSV 增强 + OpenBoxes 契约测试 | C-c：mock server 契约测试过；幂等重复拉单不重单；密钥不落 git |
| C4 | DXF 导入器 | C-d：样例 DXF→spec→校验 PASS；乱图层→明确报错不静默 |
| C5 | fleet_spec 接入 gen_tasks/geometry | C-e：deployment.count=30→实际 30 车；混配车型被拒（负测试） |
| C6 | LIF 导入/导出 | C-f：LIF 往返（round-trip）布局一致 |
| C7 | 文档同步：PARAMETER_GUIDE/ENTERPRISE_SETUP/README 已知限制更新 | 文档与代码一致（report_status 生成核对） |

### 5.8 降级方案

| 级 | 触发条件 | 降级动作 | 明示文案 |
|---|---|---|---|
| G1 | 布局编辑器工期超预算 | 只做 C1 表单编辑 + 背景描图（砍拖拽 3D 预览） | 「表单+描图定制」 |
| G2 | DXF 图纸质量差（图层混乱） | 自动导入标记 best-effort，强制转人工描图确认 | 「导入草稿需人工核对」 |
| G3 | OpenBoxes/Odoo 无测试环境 | 契约测试用本地 mock；真实对接标 experimental | 「未经真实系统验证」 |
| G4 | 异构车队需求出现 | schema 拒绝混配+明确错误，二期实现 | 「本期单车型」 |
| G5 | Stegocode 许可证不可用 | 交互自研（其交互无专利风险，代码不抄） | — |
| G6 | 全部受阻 | 保留现有 JSON 手改路径 + 本方案文档 | 现状不退化 |

---

## 6. 里程碑与总验收

| 里程碑 | 内容 | Definition of Done |
|---|---|---|
| **M-A1 引擎 MVP** | A1–A4 + B0 | S1–S3 过；1h 仿真多单/车+充电真实发生；B0 简版损益表可复算 |
| **M-A2 场景真仿真** | A5–A7 + B1–B4 | S4–S9 过；8 场景 measured；F1–F7 过；负测试全新增用例能失败 |
| **M-B 运营可视** | B5–B6 | 控制台运营面板浏览器实测；dc compare 盈亏 delta |
| **M-C 定制闭环** | C1–C7 | C-a–C-f 过；真实 DXF/CSV 走通「导入→校验→仿真→损益」全链 |
| **M-终 企业试用** | 用户真实数据端到端 | 用户自己的布局+订单+价格跑出损益表；README 已知限制如实更新；A1–A40 既有验收不回退 |

**统一 DoD**（沿用 OPEN_SOURCE_PLAN §8）：新公开行为有测试；schema/CHANGELOG/文档同步；CI 绿；`dc doctor` 干净环境可用；从零复现命令进 README；manifest 哈希覆盖所有新报告。

**总工期估算**（单人全职等价，含测试与文档）：M-A1 约 2–3 周；M-A2 约 2–3 周；M-B 约 1–2 周；M-C 约 3–4 周。合计 8–12 周。降级路径可在每里程碑砍 30–50% 范围。

---

## 7. 合规与依赖清单

### 7.1 新增依赖（候选，均锁版本）

| 包 | 许可证 | 用途 | 阶段 |
|---|---|---|---|
| simpy 4.1.2 | MIT | 仅 D2 降级时引入 | A 备选 |
| ezdxf | MIT | DXF 导入 | C4 |
| （vendored）GavinPHR CBS 单文件 | MIT | L3 求解器，进 THIRD_PARTY_NOTICES | A8 可选 |

主链继续保持 bpy+标准库；Pillow/imageio-ffmpeg/jsonschema 现状不变。

### 7.2 THIRD_PARTY_NOTICES 更新项

- WareTwin engine.py 移植：MIT 全文已有，补「engine 结构衍生」声明 + 源 commit hash。
- GavinPHR CBS（若 vendored）：MIT 全文 + sha256。
- ezdxf：MIT，pip 依赖声明即可。
- ERPNext/beancount：注明「概念参照，无代码衍生」（GPL 隔离声明）。

### 7.3 引入前必查清单

Stegocode 编辑器、vda5050_lif_editor、salabim、JeeWMS、double-entry-generator 的许可证——本方案已标注「未核实」，实施第一步就是核实，不可用则走对应降级（G5 等）。

---

## 8. 附录：现状证据索引

| 结论 | 证据位置 |
|---|---|
| 控制台白名单仅 14 渲染参数 | `console/server.py:403–419`（白名单）、`:582,608–620`（只写 data/config.json）、`:592`（白名单外拒绝） |
| WMS 纯 mock | `scripts/adapters/wms_api.py:3–4,24–25`（明写不连真实系统） |
| 布局仅矩形单层 | `scripts/spec_layout.py:140–144`（width/depth）、`:181`（floor 硬编码 1）；`config/layout_spec.json:906–911`（meta.todo placeholder） |
| fleet_spec 未接主链 | `config/fleet_spec.json:59`（自认 1 单/车 demo）；`scripts/gen_tasks.py:24–28`（车数来自 spawns） |
| 场景纯解析 | `scripts/scenario_run.py:6–7,88–116`；`scripts/kpi.py:31–47`（Erlang-C）、`:68–69`（故障折算）、`:98–100`（吞吐 min(λ,c/S)） |
| scale_50 仅数字 | `config/scenarios/scale_50.json:10,12–15`（断言只查 fleet_size 与墙钟） |
| 回放非调度 | `scripts/fleet_sim.py:22–32`（9 态）、`:66–70`（硬编码 8 步）、`:78–83`（电量=里程×能耗）、`:96`（offline replay 自认）、`:104–107`（charging_events:0） |
| 25 KPI 无财务字段 | `scripts/kpi.py:95–164` |
| 主链一次性 21 单 | `scripts/gen_tasks.py:3–6`（事件数=车辆数全 ASSIGNED）；`scripts/plan_paths.py:252–290`（优先级错峰） |
| 实测 KPI 混拼 | `runs/peak/20261108-192635/kpi.json`（on_time_rate=1.0 复用主链 21 单报告，throughput=90 标 analytic） |
| 既有升级计划 | `docs/OPEN_SOURCE_PLAN.md:500–560`（§5.3 真仿真/§5.4 故障电量/§5.5 布局编辑器）、`:579–588`（P3 连续时间预约/VDA5050/多楼层） |
| WareTwin tick 引擎参照 | `_research/repos/WareTwin/backend/app/sim/engine.py:19–33`（SIM 常量）、`:42–58`（Mulberry32）、`:94–120`（占用格/充电桩/注入队列） |
| runner 步骤注册 | `dc_twin/runner.py:22–66`（DATA/SCENARIO/BLENDER/POST/ACCEPT_CHAIN） |

---

*本方案为文档交付，未修改任何代码。实施时按 §6 里程碑顺序执行，每步先写负测试再写实现（延续本项目「验证器必须能失败」文化）。*