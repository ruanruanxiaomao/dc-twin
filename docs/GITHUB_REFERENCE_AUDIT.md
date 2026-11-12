# GitHub 对照审计（COMPLETION_PLAN 落地补录）

> 日期：2026-11-09 · 触发问题：「进行修改完善的时候，有去 github 上参考推荐的项目吗」
> 结论先行：**本轮（22:30 方案落地）开始时没有**；本次补做后，已形成「API 实测 → clone → 源码级复核 → 采用/降级决策 → THIRD_PARTY_NOTICES」完整证据链。
> 上游：`docs/COMPLETION_PLAN.md` §2（方案里的项目总表）、`docs/OPEN_SOURCE_PLAN.md`、`THIRD_PARTY_NOTICES.md`

---

## 0. 直接回答（分时段，不掩盖）

| 时段 | GitHub 侧动作 | 证据 |
|---|---|---|
| 项目早期（调研阶段） | REST API 检索 5 类主题、WebFetch 读 README、clone `cargotwin` / `WareTwin` / `smart-warehouse` 三个仓库做源码级核查 | `配送中心开源项目调研_Hy4_第一阶段.md` §9–§18；`_research/repos/{cargotwin,WareTwin,smart-warehouse}` |
| 审计 / 设计阶段 | 未新 clone；只本地对照 WareTwin `engine.py` 常量与 `reserved_by`，并检索 WHCA*/CBS/Cooperative A*/WarehouseSim 等范式名称 | `docs/PROJECT_REPORT.md`、`docs/OPEN_SOURCE_PLAN.md` |
| 22:30 方案 A 阶段开工至被问及前 | **未新 clone、未再检索**。tick 引擎属自研 + 已有本地副本对照，没有「参考了哪个项目哪段代码」的可追溯记录 | 本次补录前的 git 工作树 |
| 本次补录（2026-11-09） | 16 个仓库 API 实测；新 clone 2 个（MAPF-CBS、warehouse_layout_editor）；3 个仓库源码级复核；纠正方案 §2 的 5 处事实；更新合规声明 | 本文 §1–§6 |

**因此原问题的诚实答案是：问之前，这一轮基本没有。** 下面是补做的结果。

---

## 1. API 实测（2026-11-09，`https://api.github.com/repos/<owner>/<repo>`）

复现命令见 §7。下表 `stars` / `pushed` / `license` 均为当日实测，非方案文档转抄。

| 仓库 | stars | SPDX | 最近推送 | 默认分支 | 方案 §2 标注 | 核查结论 |
|---|---:|---|---|---|---|---|
| WayneChou-bot/WareTwin | 159 | MIT | 2026-09-11 | main | star 栏为「—」 | 补全：159★，MIT 确认 |
| Jiaoyang-Li/RHCR | 223 | 无 SPDX | 2024-12-17 | master | USC Research 非商业 | 一致，仅借概念 |
| GavinPHR/Multi-Agent-Path-Finding | 448 | MIT | 2024-08-30 | master | 447 / MIT | star +1，MIT 确认 |
| Kei18/lacam3 | 139 | MIT | 2026-10-11 | main | 139 / MIT | 一致 |
| PathPlanning/Continuous-CBS | 272 | MIT | 2025-05-30 | master | 272 / MIT | 一致 |
| open-rmf/mapf | 82 | Apache-2.0 | 2026-10-26 | main | 82 / Apache-2.0 | 一致 |
| openTCS/opentcs | 601 | **GitHub 未识别** | 2026-09-17 | master | 「MIT（代码）」 | **需修正**：API `license: null`；引入前必须逐目录核 LICENSE，本期不引入 |
| frappe/erpnext | 41113 | GPL-3.0 | 2026-11-09 | develop | 41085 / GPL-3.0 | star +28，GPL 确认 |
| beancount/beancount | 6155 | GPL-2.0 | 2026-08-23 | master | 6153 / GPL-2.0 | star +2，GPL 确认 |
| mozman/ezdxf | 1523 | MIT | 2026-11-08 | master | 1522 / MIT | star +1，MIT 确认 |
| **Stegocode/warehouse_layout_editor** | 3 | **MIT** | 2026-06-24 | main | 「未核实（引入前必查）」 | **已核实 MIT** → G5 降级解除，可放心参照交互与架构 |
| **bekirbostanci/vda5050_lif_editor** | 62 | **GPL-3.0** | 2026-06-19 | main | 「需核实」 | **已核实 GPL-3.0** → 只可概念参考，**不得抄代码**；LIF 解析器必须自研 |
| openboxes/openboxes | 922 | EPL-1.0 | 2026-11-06 | develop | 922 / EPL-1.0 | 一致 |
| OCA/wms | 226 | AGPL-3.0 | 2026-11-06 | 18.0 | 「AGPL 模块为主」 | 确认 AGPL-3.0 |
| VDA5050/VDA5050 | 536 | **MIT** | 2026-10-29 | main | 只写「官方规范」 | 补全：规范仓库本身 MIT |
| salabim/salabim | 412 | **GitHub 未识别** | 2026-05-29 | master | 「需核实」 | 仍未识别 → 维持不引入 |
| wh200720041/warehouse_simulation_toolkit | 316 | BSD-3-Clause | 2024-02-18 | master | 316 / BSD-3 | 一致，仍不采纳（ROS/Gazebo 物理级） |

### 1.1 对方案 §2 的五处纠正

1. `Stegocode/warehouse_layout_editor` = **MIT**（方案标「未核实」）→ C2 的 G5 降级条件不再触发。
2. `vda5050_lif_editor` = **GPL-3.0**（方案标「需核实」）→ C6 LIF 解析器必须自研，不可 vendored。
3. `openTCS/opentcs` GitHub API **无 SPDX 许可字段**（方案写「MIT（代码）」）→ 不能直接按 MIT 处理；本项目本就不嵌入 Java 栈，维持「仅借架构」。
4. `salabim` 仍无 SPDX → 维持「许可证核实前不引入」。
5. `VDA5050/VDA5050` 规范仓库为 **MIT**（方案未标许可）→ 事件命名对齐不构成代码衍生。

---

## 2. 本轮新 clone 与源码级复核

### 2.1 GavinPHR/Multi-Agent-Path-Finding（→ A8 / L3 求解器）

- clone：`_research/repos/MAPF-CBS`（`--depth 1`，master @ 2024-08-30），MIT。
- **实证障碍**：仓库含文件名 `LICENSE.txt `（**尾部带空格**），Windows 无法创建该文件，导致 `git clone` 后工作树为空（`git status` 显示全部文件为 `D`）。本次改用 `git show HEAD:<path>` 读取源码。
- `cbs_mapf/planner.py:1–40`：CBS 高层依赖 `from stastar.planner import Planner as STPlanner`（**外部包 Space-Time-AStar**）、`numpy`、`multiprocessing.Manager()`；`plan()` 先做 `min_cost` 目标分配，再为每个 agent 求低层路径，最后建约束树。
- `cbs_mapf/constraint_tree.py:11–30`：`CTNode.cost = sic(solution)`（Sum-of-Individual-Costs），最小堆按 cost 展开。
- `LICENSE.txt ` 内容：MIT，`Copyright (c) 2018 YOUR NAME`（原文占位符未替换）。
- **决策**：方案 §3.6 L3 原写「可直接移植/依赖，作为碰撞求解升级首选」，实测**不是自包含单文件**，且引入 numpy/multiprocessing 与缺失的外部包违背「主链保持 bpy + 标准库」原则（方案 §7.1）。据此**触发 D3 降级**：`traffic.level` 封顶 `L1_reservation`，L3 标 `experimental`，不进默认链。
- 本项目未复制其任何代码；仅登记「约束树 / SIC / 滚动窗口」为概念参考。

### 2.2 Stegocode/warehouse_layout_editor（→ C2 布局编辑器）

- clone：`_research/repos/layout-editor`（`--depth 1`，main @ 2026-06-24），**MIT，Copyright (c) 2026 Scott Tegenfeldt**（许可证全文已核实）。
- 技术栈与方案描述吻合：静态客户端（HTML/CSS/ES modules + vendored three.js r128）、**零构建步骤**、Python 3.10+ 仅作 dev server / 可选 Postgres 层。
- `README.md:34–43`：工具栏 `Select/Move, +Zone, +Rack Row, +Node/Door, +Path Edge, Delete`；快捷键 `V/Z/R/N/E/X`；2D 计划 / 3D 预览切换；Export/Import JSON；localStorage 自动保存。
- `app/index.html`：`data-tool` 实为 `select|zone|rack|node|edge|delete` 六类。
- `server/layout_schema.py:1–60`：`SCHEMA_VERSION = 6`，节点种类 `door/ramp/junction/dock/staging/charge/access_point/waypoint/staging_area/reference_marker`，`validate_layout()` 返回人类可读错误列表，浏览器 `app/js/schema.js` 与服务端同规则。
- **决策**：方案 §5.3 的「交互与架构参照首选」成立，且许可证障碍已解除（G5 不触发）。本项目 C2 可学其「工具栏 + 双端同规则校验 + JSON 导入导出 + 校准背景描图」；**代码不自抄，保持自研**。

### 2.3 WareTwin（→ A 阶段 tick 引擎，本地已有副本）

- 本地 commit `81f365f323e1a097e5819ca9045637544163f219`，MIT，Copyright (c) 2026 Wayne Chou。
- `backend/app/sim/engine.py:19–33`：`SIM` 常量族 `TICK_S=0.1`、`MAX_SPEED=1.5`、`PICK_TICKS=40`、`DROP_TICKS=30`、`MAX_WAITING_TASKS=12`、`WAIT_REPLAN_TICKS=25`、`WAIT_BACKOFF_TICKS=80`、`MIN_SEP=0.9`、`KPI_EVERY=10`、`EVENT_RING=500`；`TH` 常量族 `EVENT_RING_SIZE=500`、`THROUGHPUT_SERIES_SIZE=120`。
- `:42–58`：对象化 `Mulberry32`（注释明写「做成物件让引擎可以 deepcopy → What-if 复制后随机流也一致」）、`_imul` 位级模拟。
- **本项目对应关系**（`scripts/sim_engine.py`）：`Mulberry32` 类（`next/expovariate/choice`）、`tick_s=0.1`、`max_waiting=12`、`event_ring=5000`、`kpi_every_ticks=100`（放大 10 倍以降采样频率）、`MIN_SEP=1.4592`（**本项目硬阈值，未沿用 0.9**）、`ENERGY_TICK` 窗口聚合事件替代逐 tick 事件。
- **与原声明的差异**：`THIRD_PARTY_NOTICES.md` 旧版写「本项目采用优先级错峰的轻量近似，尚未实现 tick 级 cell reservation」——该表述在 A 阶段落地后**已过时**，本次同步更正（见 §5）。

---

## 3. 其它推荐项目的处置（未 clone，仅 API + README/文档核对）

| 项目 | 阶段 | 处置 | 理由 |
|---|---|---|---|
| SimPy | A / D2 | 不引入 | 自研 tick 循环未失控，D2 未触发 |
| Jiaoyang-Li/RHCR、EECBS | A L2 | 仅概念 | USC 研究许可，非商业限制 |
| openTCS | A 架构 | 仅概念（transport order→drive order、资源锁） | Java 21 栈 + GitHub 无 SPDX |
| ERPNext、beancount | B | **仅概念，无代码衍生** | GPL 传染；账本自研，账户树/分录平衡规则自研 |
| double-entry-generator | B | 仅概念 | 许可证未核实 |
| warehouse-kpi-mart 等 0★ 项目 | B | 不采纳 | 无工程价值 |
| mozman/ezdxf | C4 | MIT，可作为可选依赖 | 见 §5，本期按降级实现自研 LIF + DXF 可选 |
| IfcOpenShell | C4 | 不采纳 | LGPL 边界待法务确认 |
| OpenBoxes / OCA wms / JeeWMS | C3 | 适配器目标（只读） | 未向任何生产系统写入 |
| VDA5050/VDA5050 | C3 / 事件命名 | 语义对齐 | MIT 规范，不构成代码衍生 |
| wh200720041/warehouse_simulation_toolkit | A | 不采纳 | ROS/Gazebo 物理级单车仿真，与目标不符 |

---

## 4. 采用 / 降级决策汇总

| 编号 | 决策 | 依据 |
|---|---|---|
| A-01 | tick 引擎结构移植 WareTwin（MIT），数值函数用 Python 原生，MIN_SEP 保持 1.4592 | §2.3；方案 §3.2、§3.9 风险表 |
| A-02 | **D3 生效**：L3 CBS 不进默认链，标 experimental | §2.1 实测：非自包含 + 外部包 + Windows 检出失败 |
| A-03 | tick 跳步必须把 fault start/end 视为调度事件 | 本次实跑发现 t=0 故障被跳过，已修 |
| C-01 | **G5 不触发**：layout editor MIT 已核实，交互可参照 | §2.2 |
| C-02 | **LIF 解析器自研**（GPL-3.0 隔离），不 vendored vda5050_lif_editor | §1.1 |
| B-01 | 账本自研，ERPNext/beancount 概念参照、GPL 隔离声明 | 方案 §4.3、§4.7 |

---

## 5. 合规声明同步

`THIRD_PARTY_NOTICES.md` 本次同步：

1. WareTwin 条目补「engine 结构衍生」声明 + 源 commit hash，并更正过时的「尚未实现 tick 级预约」表述。
2. 新增 MAPF-CBS 条目：MIT，仅源码对照，**未复制代码**（含 `LICENSE.txt ` 尾随空格导致的 Windows 检出问题记录）。
3. 新增 warehouse_layout_editor 条目：MIT，交互/架构参照，**代码自研**。
4. 新增 GPL 隔离声明：ERPNext / beancount / vda5050_lif_editor 概念参照，**无代码衍生**。
5. `_research/repos/` 参考仓库数量由 3 更新为 5。

---

## 6. 复现命令

```bash
# 1) API 实测（无需 token，仅公开元数据）
python - <<'PY'
import json, urllib.request
for r in ["WayneChou-bot/WareTwin","GavinPHR/Multi-Agent-Path-Finding",
          "Stegocode/warehouse_layout_editor","bekirbostanci/vda5050_lif_editor"]:
    req = urllib.request.Request("https://api.github.com/repos/"+r,
                                 headers={"User-Agent":"dc-twin-audit"})
    d = json.load(urllib.request.urlopen(req, timeout=12))
    print(r, d["stargazers_count"], (d.get("license") or {}).get("spdx_id"),
          d["pushed_at"], d["default_branch"])
PY

# 2) clone（Windows 上 MAPF-CBS 工作树会为空，属预期，见 §2.1）
git clone --depth 1 https://github.com/GavinPHR/Multi-Agent-Path-Finding.git MAPF-CBS
git clone --depth 1 https://github.com/Stegocode/warehouse_layout_editor.git layout-editor

# 3) 源码级复核
git -C _research/repos/MAPF-CBS show HEAD:cbs_mapf/planner.py
git -C _research/repos/WareTwin rev-parse HEAD
sed -n '19,58p' _research/repos/WareTwin/backend/app/sim/engine.py
sed -n '1,60p' _research/repos/layout-editor/server/layout_schema.py
```

---

## 7. 边界与诚实声明

- 本文是本轮**补做**的对照记录，不是事后追认「之前参考过」。问之前这一轮确无新 clone。
- API 的 star / push 数据是 2026-11-09 快照，会随时间变化；许可证以仓库 LICENSE 文件为准，GitHub `license` 字段为 `null` 时一律按「未核实」处理。
- 「参考」分三档，本文严格区分：**代码衍生**（仅 WareTwin，MIT 已声明）、**概念/架构参照**（RHCR、openTCS、ERPNext、beancount、layout-editor 等）、**仅核查未采用**（MAPF-CBS、salabim、warehouse_simulation_toolkit 等）。
- 本项目未向任何第三方生产系统写入数据；`_research/repos/` 下仓库不参与运行。

---

## 8. 游戏化改造阶段（ADR-GAME-06）· GPL 隔离声明

> 日期：2026-11-12 · 触发：把数字孪生改造为模拟运营游戏，参考项目池里 GPLv3 占比高

游戏改造阶段新引入的对照项目，按 `docs/design/ADR-GAME-06.md` 的隔离规则登记：

| 仓库 | 许可 | 借鉴点 | 采用方式 | 是否产生代码衍生 |
|---|---|---|---|---|
| tobspr/shapez.io | **GPL-3.0** | 实时 tick 循环、存档系统、实体/组件组织 | **仅架构阅读** | 否 |
| Anuken/Mindustry | **GPL-3.0** | 暂停/倍速节奏、战役解锁、传送带物流玩法 | **仅玩法设计阅读** | 否 |
| OpenTTD/OpenTTD | **GPL-2.0** | 车辆调度、订单路线、送达结算 | **仅设计阅读** | 否 |
| simutrans/simutrans | **GPL-2.0** | 同 OpenTTD | **仅设计阅读** | 否 |
| OpenRCT2/OpenRCT2 | **GPL-3.0** | 经营评分、失败态、结算画面 | **仅设计阅读** | 否 |
| kittensgame/KittensGame | **MIT** | 资源/购买/解锁/存档导入导出范式 | 可借鉴代码（须署名） | 本期未复制 |
| pmotschmann/Evolve | 宽松 | 生产链驱动解锁 | 参考设计 | 否 |
| SimPy / Salabim | 宽松（逐个核对） | DES 事件调度结构、仿真与可视化共用状态 | 参考架构 | 本期未引入 |

**结论**：游戏层的 `step()` 拆分、命令队列、快照、实时经济、关卡判定**全部自研**。
GPLv3 项目只贡献"可以这么做"的判断，不贡献任何表达式（函数体、常量表、注释、命名）。
若后续确需借鉴 MIT 代码，必须先在 `THIRD_PARTY_NOTICES.md` 增加条目，再在本表第 5 列改为「是」。

复核方式：`scripts/validate.py` 的 A39（许可登记）与 `scripts/check_doc_drift.py`（口径守卫）会兜底；
新增 `requirements.txt` 条目须先在本节登记。
