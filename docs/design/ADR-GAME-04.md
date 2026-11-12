# ADR-GAME-04 · 经济单一事实来源

- 状态：已接受
- 日期：2026-11-12
- 关联：计划报告 §6 ADR-04、方案书 §6.1

## 背景

现状经济是**事后结算**：跑完 `sim_events.jsonl`，`ledger.py` 消费事件生成分录，`pnl_report.py` 出损益。
游戏需要**运行中现金**。若在引擎里另写一套现实时记账，同一笔收入会被记两遍：
实时 HUD 一份、事后台账一份，且两套口径必然漂移（谁先改谁对不上）。

## 决策

**复用 `config/cost_spec.json` 的科目与 `ledger.py` 的金额公式，实时账本与事后账本同源。**

| 触发事件 | 科目 | 金额 |
|---|---|---|
| `TASK_COMPLETED` | 贷 `Revenue:<type>` | `per_order[type] + per_line_extra`（`SET_PRICE` 可覆盖） |
| `TASK_COMPLETED` | 借 `Cost:Variable:Consumables` | `consumables_per_order` |
| `TASK_COMPLETED`（tardiness>0） | 借 `Cost:SLA_Penalty` | `tardiness_s × sla_penalty_per_tardy_s` |
| `ORDER_LOST` | 借 `Cost:LostOrder` | `lost_order_penalty` |
| `ENERGY_TICK` | 借 `Cost:Variable:Energy` | `(motion+idle)_kwh × energy_tariff_per_kwh` |
| 每仿真小时 | 借 `Cost:Fixed:*` | `ledger.fixed_costs()` |
| `BUY_AGV` | 借 `Capex:Robot` | `capex_per_unit`（现金一次性支出） |

幂等键：`(source_event_seq, account)`。实时账本已消费过的事件 seq 记在 `economy.ledger_tail_seq`。

## 理由

1. 避免双重计账（R3，风险登记册里影响"高"的一条）。
2. 复用已被 F1–F6 验证过的公式，不重新发明会计。
3. 事后 `ledger.py` 仍是审计轨（第二轨），可以独立复算实时账本的每一笔。

## 后果

- `TASK_COMPLETED` 事件的 payload 必须带足够的字段（订单类型、行数、tardiness、event seq），
  否则实时侧无法复算——这是对事件契约的收紧，需同步 `docs/data-contracts.md`。
- 现金守恒变成可测命题：`Δcash == Σ收入 − Σ成本 − Σcapex`（容差 1e-6），即 `ECON_OK` 门。
- 固定成本按**仿真小时**计提（而非一天一次性），因此短场景会按比例摊薄——这与 `ledger` 的
  日摊口径不同，必须在 HUD 上标注"按小时计提"，否则同一场景两个利润数会被质疑。
