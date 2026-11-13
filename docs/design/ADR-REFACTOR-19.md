# ADR-REFACTOR-19 · 分型进关卡场景，出厂保持冻结（D11-A）

- 状态：已实施（D11-A）
- 日期：2026-11-13
- 关联：ADR-REFACTOR-12（GLB 不可复现 → 四锚）、`scripts/build_typed_spec.py`、
  `scripts/check_typed_variant.py`、`scripts/zone_detail.py`

## 背景

R8.2–R8.5 把 9 类货架、4 型输送、7 型分拣的几何做出来了，探针也证明了它们两两不同。
但**出厂布局一个 `rack_type` 都没声明**——这是 D4/D5 的默认关闭原则，本身没错；
错在没有第二条路：玩家、评审、编辑器里看到的永远是同一种货架、同一种输送带。

D11 要解决"看得见"。它有两条路：

* **A（本轮）**：分型只在关卡/组装器场景启用，出厂配置与金标准**保持冻结**；
* **B（v1 之后）**：出厂默认本身变 typed，需要一次四锚受控重冻 + STEP_BASELINE / A1–A40 重验。

选 A 的理由不是"怕"，是**顺序**：方案书三处修订还没落盘，此时做签署重冻会让
"什么算 v1"说不清。A 把视觉兑现与金标准冻结解耦，风险面小一个数量级。

## 决策一：另出一份 spec，而不是改默认

`config/layout_spec_typed.json` 是 `layout_spec.json` 的**派生**：W 仓四个组各拆两半
（bays 平分，origin 按 `bays × pitch` 顺延），每组一类机理，共 8 类；F1 补第 9 类
（`push_back`），F2/F3 不声明（与出厂同形，留作对照）。

拆组是必要的：只有 7 个组却要展示 9 类，不拆就得改名不副实。拆完 11 组、196 架、
1033 库位、0 issue。

## 决策二：变体由脚本生成，手改立刻红

手写 11 组的 origin / bays / lane_depth，改一个数就会和 `bays × pitch` 对不上；
而 `spec_layout` 只会在 L3 里报"重叠"——没人能一眼看出是"算错"还是"写错"。

所以 `build_typed_spec.py` 把分配规则写死，`--check` 与文件逐字节比对，门在链上跑它。
手改过的 spec 会在 `TYPED_SPEC_OK` 判红，提示重跑脚本。

## 决策三：3D 输出路径可被环境变量接管

`build_dc.py` / `export_glb.py` 原来硬编码 `data/layout.json` → `out/`。变体构建要
读另一份 layout、写另一个目录，所以加 `DC_LAYOUT / DC_OUT / DC_BLEND / DC_GLB`。
**默认值与原来逐字相同**——不设变量时构建产物一字节不变（金标准仍归 `out/`）。

这是"能构建变体"和"出厂被污染"之间唯一的一条线，门用 `FACTORY_FROZEN_OK` 守住它：
重新生成出厂布局，与磁盘**逐字节**比对，且要求 layout 里 `rack_type` / `style` /
`sorter` 一个键都没有。

## 决策四：分拣与输送的"看得见"分两层

* 4 型输送：场景里**真的都在**——W 的两条输送线按中点各拆两段，四段四种 style。
  拆段不新增几何风险（仍在同一条直线上），代价是多两个对象。
* 7 型分拣：场景里只放 `crossbelt` 一种。要放 7 个分拣站，功能带宽度不够，
  硬塞会把 PACK 挤掉。**其余 6 型的几何差异由 R8.2 探针证明**（7 种指纹两两不同），
  场景只负责"分型真的被用上"，不重复证明几何。

这条取舍写在门的 detail 里，不静默：`TYPED_LAYOUT_OK` 只要求"至少一种 sorter 被声明"，
`PROBE_DISTINCT_OK` 要求"7 型几何两两不同"。两者合起来才是完整证据。

## 门（`TYPED_VARIANT_OK`，12 步链的第 12 步）

| 子门 | 断言 |
|---|---|
| `TYPED_SPEC_OK` | 关卡 spec 与脚本生成结果逐字节一致（手改即红） |
| `TYPED_LAYOUT_OK` | 9 类机理全用到、4 型输送全用到、有 sorter、0 issue、exit 0、库位 > 441 |
| `FACTORY_FROZEN_OK` | 出厂重生成 ≡ 磁盘（逐字节），且无 `rack_type`/`style`/`sorter` 键 |
| `PROBE_DISTINCT_OK` | 9 类货架指纹两两不同、4 型输送、7 型分拣（复用 R8.2 探针，校验 source_sha） |

自检 2 条反例：`factory_typed_leak`（出厂被偷偷加一个 rack_type）、
`spec_hand_edited`（生成函数被替换 → 与文件不符）。

## 实测

| 项 | 数值 |
|---|---|
| 关卡 spec | 11 组 / 196 架 / 9 类机理 / 4 型输送 |
| 关卡布局 | 1033 库位（出厂 441），0 issue，exit 0 |
| 关卡 GLB | 1,084,312 字节 / 2716 节点 / 385 meshes，sha `29040adc…` |
| 构建派发 | 9 类各 20 架（push_back 12），fallback 24（F2/F3 各 12，设计如此） |
| 出厂布局 | sha `9dcaf196`，264,148 字节，typed 键 0 个 |
| 出厂 GLB | sha `a5e90872…`，未动 |

## 未做（诚实记账）

- **7 型分拣不在同一场景里**（见决策四），几何差异由探针承担。
- **B 方案（出厂 typed）不做**：那是 v1 之后的独立签署重冻，要四锚同提交 + STEP_BASELINE
  / A1–A40 重验。
- **编辑器/关卡前端没接这份 spec**：本轮产出 spec + 布局 + GLB，`console` 编辑器仍读
  出厂布局。接它属于前端工作（拖拽页已有一套加载逻辑）。
- **关卡 spec 的 `options.typed_showcase` 目前只是标记**，没有代码读它——登记它是为了
  让"这份文件为什么存在"可被检索，不假装它驱动了什么。
