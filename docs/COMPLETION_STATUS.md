# COMPLETION_PLAN 实施状态（2026-11-09）

> 结论先行：A 阶段（真仿真）与 B 阶段（运营盈亏）**已完整落地并通过独立验收**；
> C 阶段的**自定义场景、fleet_spec 接入、适配器框架、LIF 导入导出**已落地，
> 布局拖拽编辑器按 G1 降级为「表单 + 场景面板」，DXF 按 G2 降级，真实 WMS 对接按 G3 标 experimental。
> 既有 A1–A40 验收不回退；主链 21 步全绿。

---

## 1. 阶段 A：tick 级真仿真

| 项 | 实现 | 证据 |
|---|---|---|
| A1–A3 | `scripts/sim_engine.py`：对象化 Mulberry32、tick 循环（含故障事件跳步修正）、持续订单流、nearest-first 派单、9 态 FSM、四档电量 | `out/sim_validation.json` S1/S3/S5 |
| A4 | L1 时空预约 + 逐 tick 格点占用 + 1 s 全对采样，`MIN_SEP=1.4592 m` 未降级 | S6：433650 对采样、0 违规 |
| A5 | 8 场景真执行；`robot_failure` 采用场景声明的 `fault_target_policy: preempt_busy`，真实中断在途任务 | S4：3 个故障窗口、1 次 TASK_REASSIGNED、窗口内 0 完成 |
| A6 | 27 项 measured KPI，全部带 definition/formula/source；analytic 与 measured 分开 | S8：0 处复算偏差 |
| A7 | scale_50 真 50 车 | S7：50 条独立轨迹，其中 30 台实际移动（**空闲车如实报告，不伪造**） |
| A8 | **D3 降级**：L3 CBS 标 experimental，不进默认链 | `docs/GITHUB_REFERENCE_AUDIT.md` §2.1 实测：MAPF-CBS 依赖外部 `stastar` 包 + numpy/multiprocessing，且 `LICENSE.txt ` 尾随空格导致 Windows 检出失败 |

**修复的三个真实缺陷**（本轮实跑发现，非纸面推演）：
1. `idle_rate` 原按「是否有任意车在忙」计算，恒为 0 → 改为车辆级 `Σidle_robots/Σfleet`（night 现为 0.8336）。
2. `ENERGY_TICK` 原记累计值 → 改为窗口增量并补尾部 flush，S2 误差 0.0001%。
3. tick 跳步原忽略故障起止时间 → t=0 故障被跳过、恢复不触发；现把 fault start/end 纳入调度事件（`fault_downtime_s` 精确 1800 s）。

**S 系列验收**：22 项检查 + 4 项负测试全过（`python scripts/validate_sim.py`）。S1 为两次独立 900 s 运行逐字节一致。

---

## 2. 阶段 B：运营账本与盈亏

| 项 | 实现 | 证据 |
|---|---|---|
| B0/E1 | `ledger.py --source replay` 降级输入，明确标注「基于离线回放的演示测算」 | `out/pnl.md` |
| B1 | `config/cost_spec.json` + `config/schemas/cost_spec.schema.json` | 20 个可编辑字段白名单 |
| B2/B3 | `scripts/ledger.py`（事件→分录、幂等键 `(seq, account)`、`RUN_SETTLED` 结算事件）、`scripts/pnl_report.py`（pnl.json/md/csv） | 9 个运行共 3,395 笔分录 |
| B4 | 双输入源：sim 事件流 + replay 降级；固定成本按实际车队规模摊 | F1–F7 全过 |
| B5 | 控制台「运营」面板 + `/api/pnl`、`/api/ledger`、`POST /api/cost-spec`、prices/engine 徽章 | `console/static/ops.js`；API 实测通过 |
| B6 | `dc compare` 增加盈亏 delta | `out/pnl_compare.md`：baseline −637.31 → peak −543.06，利润差 +94.25 |

**F 系列验收**：9 个运行 × 6 项 = 54 项检查 + 4 项负测试全过（`python scripts/validate_pnl.py`）。
F4 电费与 S2 能量守恒闭合（误差 <0.004%）；F6 `prices_source=demo` 时报表头部强制含「演示价格」。

**一个真实运营结论**：1 小时班次内，baseline 仅完成 25 单、收入 ¥119.50，固定分摊 ¥750.73，运营利润 **−¥637.31**，盈亏平衡需 166 单/班。演示价格下所有场景均亏损；peak 提高单量后亏损收窄 ¥94.25。这不是 bug，是模型给出的方向性结论——**演示价格不可用于真实决策**。

---

## 3. 阶段 C：定制深化

| 步 | 状态 | 说明 |
|---|---|---|
| C1 自定义场景 | **完成** | `scripts/scenario_tools.py` + 控制台「场景」面板：新建/复制内置/校验/600 s 试算/保存/运行/删除；自定义场景只存 `config/scenarios/custom/`，不污染 8 个冻结场景；运行隔离在 `runs/custom/`，不覆盖 `out/sim_index.json` |
| C2 布局编辑器 | **已补齐（2D）** | `submission/editor.js`：选择/移动、加分区/货架排/月台门/待命位、删除、撤销、实时校验红框、背景描图两点标定、导出草稿；3D 拖拽预览仍未实现（G1 保留） |
| C3 适配器框架 | **完成核心 / G3 降级** | `scripts/adapters/base.py`（接口、幂等、指数退避×3、只读 ack）、`generic_rest.py`、`config/integrations.json`（密钥仅环境变量名）；OpenBoxes/Odoo 标 `unverified`，**未经真实系统验证、未向生产写入** |
| C4 DXF 导入 | **已补齐（ASCII 子集）** | `scripts/import_dxf.py`：零依赖解析 LWPOLYLINE/LINE/INSERT + `config/layer_mapping.json` 图层映射 + 两点标定比例尺；样例 DXF 实测 5 实体 / 2 货架排 / 100×70 m，乱图层明确报错并提示转人工描图。复杂图纸 / 二进制 DXF 仍走 G2 降级（ezdxf 为可选 MIT 增强，未引入必需依赖） |
| C5 fleet_spec 接入 | **完成** | `spec_layout.py --fleet-spec` + 新 L13 规则；默认 12/3/3/3=21 与冻结基线**逐字节一致**（已做哈希核对），缩减到 18 台生效，超配 30 台明确报 L13 而不是让 AGV 重叠 |
| C6 LIF | **完成（自研）** | `scripts/lif.py`：导出 74 节点/70 边、导入草稿、往返自测 PASS；**GPL-3.0 隔离**，未复制 vda5050_lif_editor 代码；明确「非 VDA 5050 认证、边为示意连通关系」 |
| C7 文档 | **完成** | 本文件 + `docs/GITHUB_REFERENCE_AUDIT.md` + `THIRD_PARTY_NOTICES.md` + README/CHANGELOG |

**C 系列契约测试**：`tests/test_stage_c.py` 11 项全过，覆盖 LIF 往返与悬空边拒绝、适配器幂等/dry-run/本地 mock HTTP、场景校验负测试、fleet_spec 基线不变与超配拒绝、S/F 验收报告状态。

---

## 4. 用户自定义场景怎么用

界面（推荐）：启动控制台 → 「场景」面板 → 点内置场景生成副本（内置只读）→ 改到达率/车队/混合比/干扰/断言 → **校验** → **试算 600s** → **保存** → **运行并出损益**。

命令行：

```bash
python scripts/scenario_tools.py list
python scripts/scenario_tools.py template --id my_peak > my_peak.json
python scripts/scenario_tools.py validate --file my_peak.json
python scripts/scenario_tools.py dry-run --file my_peak.json --horizon 600
python scripts/scenario_tools.py save --file my_peak.json
python scripts/scenario_tools.py run --id my_peak
python scripts/sim_engine.py --all --include-custom   # 把自定义场景也纳入批量
```

安全边界：id 必须 `[A-Za-z0-9_-]{1,40}` 且不能与 8 个内置场景同名；spec 路径只允许三个相对白名单；所有数值有范围；`*.bak.json` 备份不会被误当场景；试算用临时目录，不写 `config/`；运行含**可失败断言**（`custom_assertions.json`）。

---

## 5. 诚实声明与已知限制

1. 利润为负是演示价格 + 1 h 班次的模型结果，不是计算错误；`prices_source=demo`。
2. `scale_50` 有 50 台车，但只有 30 台实际接单移动；空闲车如实报告。
3. LIF 是「语义对齐的自研草稿格式」，**非 VDA 5050 认证**，边为示意连通、非可行驶路网。
4. 未对接任何真实 WMS；ack 默认 dry-run；`integrations.json` 不含密钥。
5. 2D 拖拽布局编辑器与 ASCII DXF 导入**已实现**；3D 拖拽预览、复杂/二进制 DXF 自动导入、多楼层/电梯、物理级仿真、连续时间零碰撞形式化证明仍**未实现**。
6. 碰撞结论口径仍是 `sampled verification + reservation check`，不宣称形式化保证。
7. `MIN_SEP=1.4592 m` 全程未调小以迁就测试。
8. 问之前这一轮**没有**新 clone 推荐项目；补做记录见 `docs/GITHUB_REFERENCE_AUDIT.md`（16 个仓库 API 实测、2 个新 clone、5 处许可证事实纠正）。

---

## 6. 复现与验收

```bash
python run.py --scope scenarios          # 主链 21 步（含 A/B 步骤与 S/F 验收）
python scripts/validate_sim.py           # S1-S9 + 8 场景断言
python scripts/validate_pnl.py           # F1-F7
python scripts/validate.py               # 既有 A1-A40
python -m pytest -q                      # 全量测试
python scripts/lif.py self-test          # LIF 往返
python -m dc_twin.cli doctor             # 环境诊断
python -m dc_twin.cli serve              # 控制台（默认 127.0.0.1:8765）
```

主链新注册步骤（`dc_twin/runner.py` SCENARIO_CHAIN）：`sim_engine --all` → `ledger --all --rebuild` → `pnl_report --all` → `validate_sim` → `validate_pnl`。既有 DATA_CHAIN 第一步增加 `--fleet-spec config/fleet_spec.json`。

---

## 7. 活动提交作品页（2026-11-09 追加）

针对活动表单「05 WorkBuddy 创作对话记录」，新增**零后端静态作品应用** `submission/`：

| 文件 | 作用 |
|---|---|
| `submission/index.html` / `style.css` / `app.js` | 7 个标签页：总览、实时孪生、场景实验室、运营盈亏、布局编辑、验收证据、提交材料 |
| `submission/data.js` | 由 `scripts/build_submission.py` 从 `out/*.json` 与 `data/layout.json` 生成的内联真实数据快照（8 场景、9 损益运行、4 仓布局、**5421 条真实 AGV 关键帧降采样**） |
| `submission/editor.js` | G1 可视化布局编辑器（拖拽 / 背景描图 / 校验 / 导出草稿） |
| `submission/assets/*.webp` | 8 张 Blender 渲染图（共 416 KB） |
| `submission/提交说明.md` | 作品简介、分享链接获取步骤、隐私检查清单、评审导览、复现命令 |

特点：**不联网、不上传数据、无 CDN、无构建步骤**；`file://` 直接打开或静态托管均可。
作品页明确标注「场景 KPI 与动画不是同一次运行」「演示价格不可用于真实决策」「LIF 非 VDA5050 认证」。
**在线分享链接**：https://dc-twin-studio.app.workbuddy.host/?sharecode=SRGROMS4ZA
（已回填 `submission/提交说明.md` §2 / §6；线上页面「作品信息」表保留占位文字，
因为发布后不再改动已发布内容，避免线上/本地不一致。后续若要同步需重新发布。）

G1/G2 补齐后的契约测试：`tests/test_stage_g.py`（DXF 样例导入 / 标定 / 乱图层拒绝、
数据快照字段、静态资源引用完整性、无 CDN）。
