# 重构施工协议（R0 起执行）

> 对应方案书 §10 / §11 / §12。这是**给执行方的约束**，也是收检方独立复核时的核对清单。
> 一句话：**先立门、再动代码；默认关闭、开了就要能归因。**

## 1 每个阶段的固定五步

1. **先写门**，并构造一个反例证明它会失败（不能失败的门不算门）。
2. **配置先行**：新行为由 `config/*.json` + schema 描述，缺文件/`enabled=false`
   时引擎退回上一阶段行为。
3. **实现**：Strangler-Fig 包裹，不改既有算术；改动点带注释说明"为什么必须动这里"。
4. **验证**：
   - 关闭态：与上一代基准**逐字节一致**（`step_baseline --check`）；
   - 开启态：新门全绿 + 归因报告 + A1–A40 全过。
5. **文档**：ADR（决策与代价）+ CHANGELOG（改了什么、为什么）+ 状态表（数字现算）。

## 2 默认关闭的判据

- 默认配置下 `data/*`、`runs/*`、`out/*` 的哈希都不变；
- 只有**显式**在 scenario / level / config 里打开的局，行为才变；
- 关闭态的实现不是"注释掉"，而是走旧分支（可在测试里断言走了哪条）。

## 3 重冻协议

```bash
# 1) 确认工程不变量没有退化（重冻不许洗白真回归）
python -m dc_twin run --scope scenarios
python scripts/validate.py            # A1–A40 必须 40/40

# 2) 冻结新一代（旧的一律不覆盖）
python scripts/step_baseline.py --freeze-v4
python scripts/kpi_baseline.py  --freeze-v4

# 3) 归因报告：变了什么、为什么变
python scripts/report_refreeze.py --base v3 --target v4
```

`REFREEZE_OK` 的硬判据（缺一条即红）：

| 判据 | 含义 |
|---|---|
| `target_byte_identical` | 新基准可复现 |
| `base_expected_to_differ` | 旧基准**必须**不同；若一致说明新机制没进事件流 |
| `target_kpi_consistent` | KPI 基准一致（含新增项） |
| `self_test_still_fails` | 门仍能失败 |
| `every_scenario_changed` | 每个场景都有可归因变化（不是"某个场景碰巧没变"） |
| `engineering_invariants_pass` | A1–A40 全过 |

**一次行为变更只允许一次重冻**。R2（货架）与 R3（设备）各冻各的，不许合并。

## 4 SNAPSHOT 升版协议

| 版本 | 内容 | 状态 |
|---|---|---|
| 1.0 | 基础（机器人/队列/事件日志） | 历史 |
| 2.0 | 库存账 | 历史 |
| 3.0 | 批次 / 效期 / lot_seq / 策略计数 | 历史 |
| 3.1（R2） | 补 `rack_blocked_n` / `rack_reshuffle_n` / `rack_conflict_n` 三个计数器 | 现行 |
| 4.0（R3） | 设备类型、每台车 `model_id`、设备-巷道绑定 | 计划 |

**R2 为什么只升 3.1 而不是 4.0**：R2 的货架行为本身**没有新增持久状态**——货道索引每次
从 `layout` 重建，槽位占用从库存账（已在快照里）推出。唯一新增的持久内容是三个**计数器**。
所以只做一个次版本号，而不是假装有一次结构变更。

**教训**：计数器也算状态。第一版把它们漏在快照外，默认关闭时看不出来（恒为 0），
一旦新关卡启用深位货架并**续玩**，三个 KPI 就会从 0 重新计数——报表少一截，却没有任何
门会红。凡是"会进 KPI 的数字"，进快照时一个都不能少。

规则：

1. **旧档显式拒绝**，不写迁移脚本（D2）。错误信息要说清"版本不符 + 当前支持哪个"。
2. 新字段一律 `required`：缺字段 → `raise`，**不许补空账**（P6a 的教训）。
3. 恢复后必须做**重放对账**：用事件流重建状态，与快照比对；任何一项不一致即拒绝。
4. 存档里出现"半新半旧"（例如有 `lots` 无 `expiry_enabled`）时，判为非法，不猜。

## 5 口径守卫（新增项必须同步）

- `check_claims.py`：文档里的数字（步数、KPI 项数、验收项）与产物一致；
  **新增文档必须进 `TARGETS`**，否则守卫看不见它（这个坑已经踩过：`CURRENT_STATUS.md`
  一度不在清单里，"31 步"活了很久）。
- `check_doc_drift.py`：文档与产物一致。
- `check_capacity_spec.py`（R0 新增）：四份新 spec 过 schema + 口径自洽 +
  B1–B5 负对照。**改回资料原式会被它当场抓住**。
- KPI 基准：新增 KPI 必须同时进 `kpi_baseline.METRICS`，否则"基准变 0 项"
  只是门没在看（P6a 的教训）。

## 6 阶段交付清单（R0 已完成的部分）

| 项 | 路径 | 状态 |
|---|---|---|
| 四份 spec | `config/{unit_load,rack,equipment,capacity}_spec.json` | ✅ 骨架 |
| 四份 schema | `config/schemas/*_spec.schema.json` | ✅ |
| 解析库容公式 | `scripts/capacity_model.py`（FIX/LEGACY 双版） | ✅ 不接主链 |
| 口径门 | `scripts/check_capacity_spec.py`（R0_SCHEMA/SEMANTIC/BUGFIX） | ✅ 已进 ACCEPT_CHAIN |
| 负对照 | `tests/unit/test_capacity_model.py`（25 项） | ✅ |
| ADR | `docs/design/ADR-REFACTOR-01..05.md` | ✅ |
| 默认字节不变 | `step_baseline --check`（v3） | ✅ |

**R0 不做**：把 `capacity_model` 接进关卡/仪表盘（R4）、改 `spec_layout`（R2）、
改 `Robot`/`fleet_spec`（R3）、任何 `data/*` 重新生成。
