# ADR-GAME-07 · 关卡 = 场景 + 经营目标（配置驱动）

- 状态：已接受
- 日期：2026-11-12
- 关联：计划报告 §6 ADR-07、方案书 §2.4.3 / §7

## 背景

现有 8 个场景各带 `assertions`，但那是**工程不变量**（零碰撞、事件覆盖、能量守恒），
不是**经营目标**（利润≥0、准时率≥95%、单量≥300）。而且场景彼此独立，资金/解锁不继承，
跑满 `horizon` 永远"完成"，没有输。

## 决策

**关卡走 JSON 配置（`config/levels/<id>.json`），不改 Python。**

```json
{
  "schema_version": "1.0", "id": "level_01",
  "scenario": "../scenarios/baseline.json",
  "initial_capital": 500000,
  "objectives": [{"metric":"operating_profit","op":">=","value":0,"primary":true}],
  "lose_conditions": [{"type":"bankrupt","grace_s":300}],
  "stars": {"1":"primary 全过","2":"…","3":"…"},
  "unlocks_on_win": ["level_02"],
  "carry_over": {"cash":true,"reputation":true,"unlocks":true}
}
```

- 经营目标与工程 `assertions` **两套口径、分开评估**，不混算。
- 胜负：`primary` 全过 = 胜；破产或违约 = 负；1–3 星由 `stars` 规则判定。
- 元进度（`cash/reputation/unlocks`）存独立元存档，跨关继承。

## 理由

1. 沿用四层配置文化（"改 JSON 不改 Python"），难度曲线可热调，不必改引擎。
2. 文案/数值/星级门槛都是内容，内容不该编译进代码。
3. 场景复用：一个场景可被多个关卡以不同目标复用，避免重复维护仿真输入。

## 后果

- 新增 `config/levels/` 与 `config/schemas/level.schema.json`，纳入 schema 数量统计。
- 关卡判定器必须能失败：未达 primary 必判负、破产必 game over、未解锁关卡不可进入（负测试）。
- 先做 3–5 关验证曲线，扩关是内容工作，不是工程工作。
