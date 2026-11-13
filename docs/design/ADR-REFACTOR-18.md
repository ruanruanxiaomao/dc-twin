# ADR-REFACTOR-18 · 实体登记册：一份现实，多处消费

- 状态：已实施（R8.11）
- 日期：2026-11-13
- 关联：ADR-REFACTOR-16（areas 不在碰撞集）、ADR-REFACTOR-17（待命位不在宽度轴）、
  `scripts/solid_registry.py`、`scripts/check_solid_registry.py`

## 背景

R8.9 抓到"areas 不在 `spec_layout` 的碰撞集里"，R8.10 又抓到"待命位不在宽度轴的
上限里"。收检方指出这是**同一个失败模式的两次发作：约束集是一份手写清单，
每加一条轴就漏一类实体**。

查代码时还发现更糟的一层：同一份现实被手写了**两次**且已经不一致——

| 位置 | 含什么 | 用途 |
|---|---|---|
| `build_grid()` 的 solids | 货架/月台/站/充电位/障碍/**输送线**/室外叉车卡车 | 烘导航网格 |
| `validate()` 的 rects | 货架/月台/站/充电位/障碍 | L2 越界 + L3 重叠 |

输送线在导航里挡路，却不在 L3 里"算重叠"；室外装备在导航里，也从不在 L2 里。
这两条差异此前只存在于代码的形态里，没人写下来过。

## 决策一：登记册只登记"占位的实体"，不替校验器决定语义

`solid_registry.KINDS` 每条写三样东西：几何形态（`space`）、消费者（`guards`）、
以及一句 note 说明"它为什么这样被管"。**"该不该撞"不归登记册管**——那是语义，
必须显式写在 guards 里，改它要改代码、跑金标准。

登记册提供的保证只有一条：**没有任何实体是隐身的**。一个种类若 guards 为空，
门判"孤儿"；布局里出现未登记的实体键，门判"未登记"。

## 决策二：L2/L3 是导航的子集，差异要显式

```
NAV_KINDS   = rack, dock, station, charger, obstacle, conveyor
L2L3_KINDS  = rack, dock, station, charger, obstacle        # 不含 conveyor
```

输送线只按折线加宽烘进导航（`blocks_grid: true`），不参与 L3 两两重叠。理由是它
本来就是"地面上的线"，与货架重叠在几何上不构成两体互斥，但它占的格子必须挡车。
这个取舍写在 `KINDS["conveyor"]["note"]` 里，且 `REGISTRY_SCOPE_OK` 把两个集合
**写死**——想把 conveyor 塞进 L2/L3，必须改门、重跑金标准，不能悄悄加一行。

## 决策三：功能区单独入口，逻辑区不许有 rect

`areas` 只存在于 `data/areas.json`，**根本不在 layout 的 warehouse 里**——这是
R8.9 那个洞的结构性原因。所以登记册给 `area` 一个独立取数函数，门单独验。

`areas.json` 21 条里只有 **3 条物理区**（`physical=true` 且有 rect），另外 18 条是
逻辑区（拣选面/补货面/暂存合流）。门只对物理区硬要求 rect，并**反过来**检查
"逻辑区有没有偷偷带 rect"——否则会逼着人给逻辑区编一个矩形，那是给审美写约束。

## 决策四：几何有两份实现，就必须有逐架比对

`spec_layout.rect_of_rack` 与 `solid_registry.rect_of_rack` 是同一段 3 行代码。
不共用是为了避免循环 import（spec_layout 依赖登记册）。代价是可能漂移，所以
`REGISTRY_RECT_PARITY_OK` 对默认布局的 **196 架货架 + 46 个其它实体**逐项比对，
连**顺序**也比——`build_grid` 按这个顺序烘格，顺序变了网格可能一样，
但"为什么一样"就没人说得清了。

## 门（`REGISTRY_COVERAGE_OK`，12 步链的第 11 步）

| 子门 | 断言 |
|---|---|
| `REGISTRY_COVERAGE_OK` | 每个实体键都登记；每类都有消费者；guards 无笔误；无孤儿 |
| `REGISTRY_RECT_PARITY_OK` | 登记册几何与 `spec_layout` 逐架、逐项、同序一致（196 货架 / 242 实体） |
| `REGISTRY_SCOPE_OK` | L2/L3 与导航的对象集**等于**门里写死的预期 |
| `REGISTRY_AREAS_OK` | 3 个物理区全部进册；逻辑区不带 rect |

自检 5 条反例：`unknown_entity_key`（塞 `elevators`）、`orphan_kind`（guards 空）、
`guard_typo`（消费者名写错）、`scope_widened`（conveyor 塞进 L2/L3）、
`area_missing`（物理区缺 rect）。

## 实测

- 默认布局：W 仓 160 货架 / 4 月台 / 3 站 / 6 充电位 / 4 障碍 / 2 输送线 / 12 待命位；
  四仓合计 196 货架、242 个实体 rect。
- 替换前后 `data/layout.json` **逐字节相同**（sha `9dcaf196`，warnings 148 不变）——
  登记册是"把已有的现实写清楚"，不是"改现实"。
- `dc run --scope refactor`：**11 步 RUN_OK**（40.0s）。

## 未做（诚实记账）

- ~~**深度轴未接登记册**~~：**已由 WP1 补齐**（同一天，随 D11-A 一起提交）。
  `solid_registry.spec_solids / spec_band` 提供 spec 阶段视图（areas/stations/pillars/
  chargers/conveyors/spawns），`layout_solver.functional_band` 改为直接调它，
  并由 `REGISTRY_SPEC_VIEW_OK` 断言"深度轴的输入逐项来自册子"。
- **室外装备不进 L2/L3**：它们在厂房外，放进 L2 footprint 会全红；只进导航与
  `check_area_param` 的残留归因。
- **`locations` / `zones` 不在册**：前者是取货点（不是占位体），后者是渲染颜色块
  （ADR-15 已裁定不参与碰撞）。两者都写进 `STRUCT_KEYS`，出现新键仍会被抓。
- **`conveyor` 的 L3 缺口没补**：本轮只把它**显式记账**，没有改语义——补它会让
  默认布局的 L3 结果变化，属于重冻阶段的事。
