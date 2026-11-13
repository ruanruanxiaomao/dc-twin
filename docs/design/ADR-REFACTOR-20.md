# ADR-REFACTOR-20 · 库存策略：先钉闭式，再谈接线（R5）

- 状态：已实施（R5）
- 日期：2026-11-13
- 关联：`config/inventory_policy_spec.json`、`scripts/inventory_policy.py`、
  `scripts/check_inventory_policy.py`、`scripts/sim_engine.py`（默认关分支）

## 背景：编号漂移留下的最大缺口

方案书 R5 一直挂着"库存策略"，但排期里没有它——它是最早给出编号、最晚被排进
的一项。R1 里已有 `check_reorder`（固定阈值 × factor），那是"补货点会动"，
不是"补货点算得有依据"：它只说"更积极 → 缺货更少、库存更高"，不给任何公式。

## 决策一：先做纯函数层，不先碰仿真

库存策略的错误在仿真里几乎不可辨认——"缺货多了一点"与"需求涨了一点"分不开。
所以在接线之前，先把每个公式钉在一张**手算表**上：

| 量 | 手算值 |
|---|---|
| EOQ(12500, 180, 2.16) | 1443.376 |
| z(0.95) / z(0.975) / z(0.99) | 1.6449 / 1.9600 / 2.3263 |
| 报童 fractile(c=8, p=18, v=2) | 0.625 |
| 报童 Q(normal μ=100, σ=25) | 107.966 |
| ROP(d=12.5/h, L=0.5h, cv=0.3, SL=0.95) | 10.612 |
| GMROI(42, 30) | 1.4 |

手算表是**外部推导值**，不是调用被测函数算出来的——否则门就是恒等式。
实现值与此表差超过 5e-4 就红。

## 决策二：定义域即断言

四类"看起来能用"的返回值才是最危险的，所以它们在定义上就不允许存在：

* `H ≤ 0` → 抛错。H=0 时 EOQ 数学上是无穷大，返回 0 或 inf 都会让下游 quietly 错；
* `σ_L < 0` → 抛错。标准差没有负数，`max(0, σ)` 会把输入错误吞掉；
* 服务水平取 0 或 1 → 抛错。端点对应 z=±∞，安全库存无界；
* `v ≥ p` → 抛错。残值不低于售价时分母 ≤0，参数自相矛盾。

`p ≤ c` 是**合法输入下的合理解**：卖一件亏一件，最优动作是不备货（fractile=0），
所以返回 0 而不是抛错——把"合理的边界解"当成参数错误，会逼调用方加 try/except。

## 决策三：`σ_L = σ_h · √L`，不是 `σ_h · L`

独立同分布假设下提前期需求的标准差按 √L 放大。写成乘 L 是最常见的"安全库存偏大"
来源，而且在 L<1（本引擎 horizon 是 1 小时，L 取 0.5 小时）时反而偏小——
方向都不对，所以单独一条反例钉住它。

## 决策四：两个开关必须联锁，不允许静默降级

`reorder.policy = "rop_ss"`（`inventory_spec.json`）与
`enabled: true`（`inventory_policy_spec.json`）是两个文件里的两个开关。
只打开一个的结果是**跑出旧阈值却以为用了新策略**——比直接报错危险得多。

联锁规则写在 `inventory_policy.rop_settings()` 一处，仿真与门调的都是它；
半开状态抛 `PolicyError`。这与 R3 的"开了 AST 耦合却没声明 equipment_type 就报错"
是同一条纪律。

## 门（`POLICY_CLOSED_FORM_OK`，13 步链的第 13 步）

| 子门 | 断言 |
|---|---|
| `POLICY_CLOSED_FORM_OK` | 6 个量对手算表（容差 5e-4）+ z 对服务水平单调 + EOQ 对需求单调 |
| `POLICY_BOUNDARY_OK` | 13 条定义域用例（含 H=0、σ<0、SL 端点、v≥p、成本=0、未知分布） |
| `POLICY_SPEC_OK` | 配置过 schema、`enabled` 必须为 false、plan 出来的数与手算一致 |
| `POLICY_DETERMINISM_OK` | 同输入两次结果严格相等 |

自检 4 条反例：`z_constant_one`（0.95 当成 1σ）、`fractile_cost_ratio`（用 c/p）、
`ss_sigma_times_lead`（σ·L）、`negative_sigma_swallowed`（σ<0 吞成 0）。

## 决策五：默认关，金标准不动

出厂 `inventory_spec.json` 的 `reorder.policy` 不改（仍是 `reorder_point`），
`inventory_policy_spec.enabled=false`。所以：

* `check_reorder` 数字与标定记录一致：stockout 8→4、holding 0.3653→0.3725、triggers 5→8；
* `check_inventory` 392 对账 0 失配、期初 10512 / 期末 8845 不变。

`schema` 的 `reorder.policy` 枚举加了 `rop_ss`——**加值是向后兼容的**，默认文件没变，
所以 schema 校验的存量行为不变。

## 实测

| 项 | 数值 |
|---|---|
| EOQ | 1443.375673 |
| ROP | z=1.644854、μ_L=6.25、SS=4.361577、ROP=10.611577 |
| 报童 | fractile=0.625、Q=107.965984 |
| GMROI | 1.4 |
| 链 | 12 → **13 步** RUN_OK 54.9s |

## 未做（诚实记账）

- **仿真里没有打开 ROP+SS**：本轮的成果是"公式与联锁已经就位，且默认关"。
  真正切换会改 `check_reorder` 的数字与仿真产物，属于**签署重冻**，留给 v1 之后。
- **多 SKU 的 σ 用的是统一 cv**（`demand_cv=0.3`），没有按 SKU 估方差——
  数据里没有逐 SKU 的需求样本，硬编一个会更假。这条进入 backlog。
- **报童的 dist 只支持 normal / uniform**（闭式解存在的那两种）；离散分布
  （泊松/经验分布）需要分位查表，本轮不做，未知分布直接抛错而不是近似。
- **GMROI 用的是单值毛利/库存成本**，没有按品类或 SKU 聚合；它是"这个仓值不值"
  的粗指标，不是逐 SKU 的选品依据。
