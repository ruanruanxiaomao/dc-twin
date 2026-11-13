# ADR-REFACTOR-01 · 货架按「存取机理」分型，不按自动化分型

- 状态：已采纳（R0，2026-11-13）
- 相关：G1；`config/rack_spec.json`；§4.1 / 附录 A

## 背景

现状 `layout_spec.json:100-107` 的 `defaults.rack.model = "rack_double"` 是**3D 资产名**，
不是存取机理；`spec_layout.py` 把四类仓区展开成几何同构的货架（rows 4 / bays 10 /
aisle 4.0），`sim_engine.World.by_kind` 只按 `SHELF` 索引，`_pick_loc` 从 SHELF 池随机取。
也就是说：**引擎眼里只有一种货架**，双深的"后位被挡"、驶入式的 LIFO、流利式的
物理 FIFO 全都无法表达。

资料（货架参考:4）给了一条明确的审校原则：**不把自动化作为货架结构类别**；
六个描述维度（存储单元 / 货位组织与存取机理 / 存取设备 / 库存顺序 / 结构安装 /
自动化数智化）正交、不可互相替代。

## 决策

1. `rack_spec.types` 以**存取机理**为主键，`family` 只做粗分类
   （direct_access / high_density / dynamic_flow / …）。
2. **`n_depth` 不再是常量**（B5）：整数表示固定深位；`"aisle_depth"` 表示整巷道深度，
   配 `aisle_depth_positions: [min, max]` 给范围。
3. **库存顺序分两种**：`inventory_order`（物理事实）与 `fifo_forced`（是否结构强制）。
   `drive_in` 单端 → LIFO；`drive_through` 双端 → FIFO；`push_back` → LIFO；
   `pallet_gravity` → FIFO。**压入式与托盘重力式必须分开命名**（§5.5），
   它们外观像、机理相反。
4. **有效货位独立于名义货位**：`effective = nominal·(1−tail_loss)·batch_mix_factor
   − frozen − maintenance − disabled`，每一项单独可审计。把五项折成一个"经验系数"
   是这类模型最常见的假精确，验收时无法归因。
5. **先做 9 类**（D8）：横梁基线 + 双深 / 驶入 / 贯通 / 托盘穿梭 / 移动 / 料箱流利 /
   托盘重力 / 压入。回转 / VCM / VLM / 阁楼 / 悬臂 / 搁板留后续——它们的差异主要在
   拣选站与提升机构，和"密度-选择性"这条主线关系较弱。

## 后果

- 正向：R2 可以按 `mechanism_key` 实现"深位阻挡 / 重排 / 通道开闭 / 穿梭交接"，
  并让 3D 外观（R8）与行为**共用同一个键**，不会出现"长得像双深、跑起来是横梁"。
- 负向：`rack_spec` 里相当多字段只有定性来源（`level: "typical"`），
  `open_close_s` / `shuttle_change_aisle_s` 在资料里**没有数值**（`level: "gap"`）。
  这些数字进游戏前必须被当成"可调假设"，不能当实测写进结论。
- 未做：15 类全量；货架结构/荷载/消防计算（N2）。

## 拒绝过的方案

- **在现有 `model` 字段上打补丁**（`rack_double_deep` 之类）：仍然是把型号当机理，
  且会让 `spec_layout` 的几何展开逻辑与型号名纠缠，后面每一类都要特判。
- **只加 `n_depth` 不加 `inventory_order`**：双深能翻倍容量，但"后位被挡"表达不了，
  等于把最容易出错的那部分藏起来。
