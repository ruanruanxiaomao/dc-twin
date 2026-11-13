# ADR-REFACTOR-02 · 设备类型库 + AST 驱动巷道宽（默认关闭）

- 状态：已采纳（R0，2026-11-13）
- 相关：G2；`config/equipment_spec.json`；D5
- 前置：ADR-REFACTOR-01

## 背景

`fleet_spec.json` 只有一个车型 `AMR-250`，`sim_engine.py:532` 硬编码
`self.model = fleet["models"][0]`；速度、载荷、耗电、充电功率全来自这一个 model。
`layout_spec.json:108-113` 的巷道宽是写死的几何量（one_way 1.8 / two_way 3.0 /
cross 4.0 / main 6.0），**不由设备决定**。

结果是：玩家"换设备"这件事在仿真里不存在。而资料里最有说服力的一句权衡恰恰是
**「省 1m 巷道 ≈ 多一排货架」**（M4:326）——巷道宽由设备 AST 决定，AST 决定密度。

## 决策

1. `equipment_spec.types` 建类型库（叉车各型 / 堆垛机 / 穿梭车 / AGV·AMR / 提升机 /
   输送 / 分拣 / 机械臂），每个类型带 `class` + 关键几何与时间参数 + `source`/`level`。
2. `fleet_spec.models[]` 改为**引用类型 + 实例参数**（R3 落地），不再自带一套物理量。
3. **AST 驱动巷道宽**：`aisle_width = max(layout_min_aisle, ast_mm/1000 + clearance)`，
   `ast_drives_aisle_width` 开关**默认 false**（D5）。
   理由：改巷道宽会重算导航 grid，进而改变既有 8 场景的路径与事件流。
   这个机制只能在新关卡里显式启用，否则"金标准字节不变"立刻破。
4. 时间模型统一到**一套口径**（B2/B3）：
   - 堆垛机 `T_c = 0.6(D/v + H/vv) + 2·tp·tf`
   - 叉车 `T_c = 1.5(D/v) + 2·tp·tf`，其中 `D = (L_a/2)·d_f`，**不再乘 d_f**
   - 可服务高度检查对**所有类型**生效
5. `BUY_AGV/SELL_AGV` 泛化为 `BUY_EQUIPMENT/SELL_EQUIPMENT`，旧名保留为别名（R3）。

## 后果

- 正向：设备选型第一次真的影响库容与节拍；解析层（G4）与仿真（G2）用同一套
  时间模型，交叉验证才有意义。
- 负向：类型库字段大量 `level: "typical"`；`forklift_reach` / `vna` 的具体参数资料
  只给区间，落地前必须复核。资料未覆盖的（插腿式、侧面叉车、四向换向时间）标
  `gap`，不臆造。
- 风险：`ast_drives_aisle_width` 一旦被误开，8 场景字节会变、但**看起来只是数字变了**。
  所以 R3 的门要求"默认关闭时字节不变"与"显式开启时库容变化可归因"两条同时成立。

## 拒绝过的方案

- **默认就开 AST 耦合**：更"真实"，但会一次性打破全部历史基准，且归因混在
  "换了设备"与"换了巷道"两件事里，说不清。D5 已定默认关闭。
- **把设备参数继续放在 fleet_spec**：类型库与实例混在一起，后续"同一类型不同实例"
  （比如 3 台 VNA 中 1 台检修）无法表达。
