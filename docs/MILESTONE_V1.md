# v1.0 冻结（里程碑）

> 冻结锚：`config/milestone_v1.json`　｜　守卫：`scripts/check_milestone.py`（`MILESTONE_V1_OK`）+ `tests/unit/test_milestone.py`
> 本文件说明"v1 是什么、凭什么算冻结、变了要走什么流程"。

## 一、冻结锚

| 产物 | sha256（前 8 位） | 字节 |
|---|---|---|
| `data/layout.json`（默认布局） | `9dcaf196` | 264,148 |
| `out/build_report.json`（默认构建报告） | `f86ae7a2` | 3,613 |
| `out/dc_preview.glb`（默认 GLB） | `a5e90872` | 929,260 |

计数：**4 仓 / 196 架 / 441 库位 / 2345 对象 / 394 mesh / 86 材质**。
链：`dc run --scope refactor` = **13 步**；`STEP_BASELINE` = **8 场景 / 24 份产物逐字节一致**；
验收 = **A1–A40 / 40 PASS**。

这三个产物代表三件事：布局是"几何真相"，构建报告是"装配真相"，GLB 是"视觉真相"。
三者同时钉住，才叫冻结——只钉 layout 会被"布局没变但 3D 变了"绕过。

## 二、v1 包含什么

| 层 | 内容 |
|---|---|
| 数据/仿真（R0–R5） | 四份 spec + schema、解析库容、货架/设备机理、单元层、密度重排；**R5 库存策略**（EOQ / ROP+SS / 报童 / GMROI，闭式，默认关） |
| 3D 资产（R8.1–R8.5） | 货架 9 类、设备 10 类、输送 4 型、分拣 7 型；GLB 规整（字节可复现） |
| 面积（R8.6–R8.10） | 深度轴求解器（1022→0 冲突、441→525 库位）、交互组装（D10a + D12 令牌）、整场重排（R8.8）、**宽度轴**（R8.10，东列 +6 跨） |
| 治理（R8.9 / R8.11） | 双校验器提交事务；**实体登记册**（9 类实体、一份现实多处消费） |
| 视觉兑现（D11-A） | 关卡场景带 9 类机理 / 4 型输送，**出厂冻结**（另出 `layout_spec_typed.json`） |

## 三、v1 明确**不含**什么

* **B 方案**：出厂默认本身变成 typed。那需要一次四锚受控重冻 + STEP_BASELINE / A1–A40 重验，
  属 v1 之后的独立签署动作。
* **仿真里打开 ROP+SS**：公式与开关联锁已就位，但切换会改 `check_reorder` 的数字。
* 其余欠账见下节 backlog。

## 四、重冻流程（唯一合法路径）

1. 改 `config/milestone_v1.json` 的锚（sha 前缀 + bytes）与 counts；
2. 写一份 ADR 说明**为什么变**（哪条轴/哪类实体/哪个默认开关）；
3. 同一次提交里跑 `python -m dc_twin run --scope full` 与 `python scripts/validate.py`；
4. 跑 `python scripts/step_baseline.py --check`；若变，同一次提交重冻基线并说明；
5. `python -m pytest -q` 全绿后提交。

**只改锚不写原因**会被审查打回：锚是证据，不是橡皮擦。

## 五、v1.1 backlog（显式欠账，不阻塞 v1）

1. VNA 窄巷道车（设备层第 11 类，需 AST 联动与 3D 几何）
2. R2 上架半层（库位粒度 + 拣选路径联动）
3. conveyor 进 L2/L3（现只挡导航不判重叠；补它要重跑默认 L3 并签署重冻）
4. 宽度缩小卡一跨无专例（现由 `AREA_SHRINK_NEG` 的 L2/L3/L6 兜底）
5. 逐 SKU 需求方差（现用统一 `cv=0.3`，数据里没有逐 SKU 样本）
6. 报童离散分布（泊松/经验分布需分位表，现只支持 normal / uniform）
7. 7 型分拣同场（功能带放不下 7 个站，几何差异现由 R8.2 探针承担）
8. 编辑器前端接 typed 关卡 spec（`console` 仍读出厂布局）
9. 分型常量单一真相源（`SORTER_KINDS` / `CONVEYOR_STYLES` 进 `asset_spec`，避免 `cross_belt` 这类笔误）

## 六、怎么复核这份冻结

```bash
python scripts/check_milestone.py            # 三锚 + 计数 + 链步数 + backlog
python scripts/check_milestone.py --self-test # 证明它会红（改锚/改计数/清空 backlog）
python -m pytest tests/unit/test_milestone.py -q
```

以上三条任一红，说明"v1 已经不是 v1 了"——要么重冻，要么回退。
