# ADR-GAME-03 · 命令模式 + tick 边界应用

- 状态：已接受
- 日期：2026-11-12
- 关联：计划报告 §6 ADR-03、方案书 §4.5 / §2.4.1

## 背景

要"中途介入"，最直接的做法是让 HTTP 线程直接改 `Simulation` 的字段（`sim.robots[...] =
...`）。但引擎正跑在另一个线程的 `while` 循环里，任何异步写入都会落在"半个 tick"的中间态上：
派单读到一半的车队、改到一半的队列。结果不可复现，`sim_events.jsonl` 也会出现时序倒挂。

## 决策

**玩家动作一律入命令队列，在 `step()` 开头（tick 边界）统一应用，并各发一条事件入 `EventLog`。**

```python
COMMANDS = {"BUY_AGV","SELL_AGV","SET_PRICE","SET_POLICY",
            "SET_DEMAND","INJECT_FAULT","BUILD_STATION","MANUAL_ASSIGN"}
```

- 未知 `type` → `raise ValueError`（沿用策略层"未知即抛错"，不静默回落）。
- 非法 payload → `raise ValueError`。
- 每条命令应用后 `log.emit(t, "CMD_<TYPE>", ...)`，使"命令流 + 种子"可复现整局。
- 尚未实现的命令注册为 handler 并 `raise NotImplementedError`，**不允许静默空转**。

## 理由

1. 确定性：命令只在 tick 边界生效，序列化后重放必然一致（`CMD_OK` 门）。
2. 可审计：命令成为事件流的一部分，与引擎自身事件同轨。
3. 线程安全退化：HTTP 线程只做 `enqueue`（append 到 list），真正的状态变更只发生在 tick 线程。

## 后果

- 命令应用有最多 1 个 tick（默认 0.1 s 仿真时间）的延迟，玩家不可感知。
- `enqueue_command` 里的校验必须与 handler 内的校验一致，否则会出现"入队通过、应用炸掉"，
  把异常抛到 tick 线程里会直接杀死会话——所以校验前置到入队（fail fast，报给前端 400）。
- 批跑模式不经过命令队列（`_cmd_queue` 为空），因此 `STEP_OK` 不受影响。
