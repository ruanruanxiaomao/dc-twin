# ADR-GAME-08 · overrides 白名单必须与引擎取值同源

- 状态：已接受
- 日期：2026-11-12
- 关联：方案书 §2.3 / §4.5、风险登记册 R10

## 背景（已复现的事实）

`scripts/sim_engine.py` 的 `Simulation.__init__` 直接读三个策略键：

```python
self.dispatch_rule = self.overrides.get("dispatch.policy", ...)
self.charge_policy = self.overrides.get("battery.charge_policy", ...)
self.reserve_pct   = self.overrides.get("dispatch.battery_reserve_pct", ...)
```

但 `scripts/scenario_tools.py:validate_scenario` 的 overrides 白名单只有 5 个键
（`demand.orders.per_hour` / `fleet.size` / `fleet.deployment` / `demand.orders.mix` /
`fleet.deployment.count`）。实测三个键全部被判非法：

```
dispatch.policy                -> ["override 'dispatch.policy' is not whitelisted"]
battery.charge_policy          -> ["override 'battery.charge_policy' is not whitelisted"]
dispatch.battery_reserve_pct   -> ["override 'dispatch.battery_reserve_pct' is not whitelisted"]
```

后果不是报错，而是**功能静默缺失**：引擎认识这些策略、跑得出结果，但玩家永远存不进场景。
项目对外宣称的"存策略 = 存场景"在代码里是假的。游戏化以后这条更致命——
P3 的 `SET_POLICY` 命令要把策略写回 `overrides` 才能随存档落地。

## 决策

**扩白名单，并做取值校验，取值来源与引擎同源**（不另抄一份列表）。

- `scenario_tools` 直接 `from sim_engine import DISPATCH_RULES, CHARGE_POLICIES`，
  白名单键与合法值都由此派生；引擎加一种策略，校验自动跟上。
- `dispatch.battery_reserve_pct` 纳入 `RANGES`，`[0, 95]`。
- 取值非法仍然报 issue（不是放过）：`dispatch.policy` 必须是 `DISPATCH_RULES` 的键，
  `battery.charge_policy` 必须是 `CHARGE_POLICIES` 之一。

实测双向都成立：

| 输入 | 结果 |
|---|---|
| `dispatch.policy=highest_soc` | PASS |
| `battery.charge_policy=threshold` | PASS |
| `dispatch.battery_reserve_pct=30` | PASS |
| `dispatch.policy=random_walk` | issue：must be one of [...] |
| `battery.charge_policy=always` | issue：must be one of [...] |
| `dispatch.battery_reserve_pct=150` | issue：outside (0, 95) |

## 备选方案与否决理由

- **命令走独立通道（不进 overrides）**：会让"策略"和"场景"变成两套存档，
  违反 ADR-GAME-05 的双轨原则，且 P5 快照要额外维护一份策略状态。否决。
- **照抄一份策略名列表到 scenario_tools**：两份列表必然分叉（R10 的成因就是"两处判断"）。
  否决。

## 后果

- 既有 8 个场景不受影响（它们没用到这三个键），`STEP_OK` / `KPI_BASELINE_OK` 复验通过。
- 新增能力：场景 JSON 现在可以真正携带策略，"策略实验"可以只改一个 JSON 就复现。
- 回归保护：`tests/unit/test_scenario_policy_overrides.py` 对上述 6 种输入逐条断言，
  正负两向都钉住。
