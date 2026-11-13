# Design & audit inputs

The project was shaped by three external review documents produced during the
course project (two strict audits and one parameterisation design report).
They are **not distributed with this repository**: they were inputs to the
work, not deliverables, and their conclusions are already incorporated in
[the full project report](../PROJECT_REPORT.md) (sections 12–16).

| Document | What it changed | Where the outcome lives |
|---|---|---|
| Audit round 1 (engineering / geometry, 8×P0) | Layout collisions, physical time model, independent validators, negative tests, compliance files | `docs/PROJECT_REPORT.md` §12 |
| Audit round 2 (operations / route-map semantics) | Priority scheduling, coverage KPIs, time–space diagram, honest "conditional pass" labels | `docs/PROJECT_REPORT.md` §14 |
| Design plan (four-layer config, 10 steps, A22–A40) | `layout_spec.json`, `spec_layout.py`, 8 scenarios, 25 KPIs, fleet/reservation replay | `docs/PROJECT_REPORT.md` §16 |
| Open-source plan (this release) | Repo hygiene, cross-platform CLI, hero README, CI, output contracts, static demo | `README.md`, `CHANGELOG.md` |

Historical numbers in the project report are **snapshots**. Current measured
values are generated from JSON into [`out/CURRENT_STATUS.md`](../../out/CURRENT_STATUS.md).

## Game-layer ADRs (2026-11-12)

`模拟运营游戏改造计划报告.md` §6 produced seven decisions; section §2.3 of the
execution plan added an eighth once the override whitelist turned out to
contradict the engine. They are recorded here because the *reasons* outlive the code:

| ADR | Decision | Still constrains |
|---|---|---|
| [GAME-01](ADR-GAME-01.md) | Wrap, do not rewrite | every future change: A1–A40 + byte-identical batch output |
| [GAME-02](ADR-GAME-02.md) | Server-authoritative clock, front end renders | no local time logic, no local money in `app.js` / `game.js` |
| [GAME-03](ADR-GAME-03.md) | Commands applied at tick boundaries | unknown command / bad payload must raise |
| [GAME-04](ADR-GAME-04.md) | One source of truth for the books | fixed cost accrued hourly, never also at `RUN_SETTLED` |
| [GAME-05](ADR-GAME-05.md) | Snapshot to resume, event log to audit | snapshots must be complete; `restore` refuses mismatches |
| [GAME-06](ADR-GAME-06.md) | GPL reference isolation | reference projects inform design only |
| [GAME-07](ADR-GAME-07.md) | Level = scenario + business objectives | objectives stay separate from `assertions` |
| [GAME-08](ADR-GAME-08.md) | Override whitelist derives from the engine | never keep a second copy of policy names |
| [GAME-09](ADR-GAME-09.md) | Inventory: procurement stays out of P&L; snapshot 2.0; one v2 refreeze | COGS needs its own ADR + retariff + refreeze; SKU is derived from location, never sampled |
| [GAME-10](ADR-GAME-10.md) | Lots/expiry/FEFO: demo-time shelf life, waste is not a P&L line, expiry times join the scheduler | Waste costing needs the COGS ADR; batch policy is a weak lever under ~40 picks/h, so no level_06 |

## 资料驱动重构（R0–R8，方案书 v1.2）

主方案见工作区根目录 `模拟运营游戏_资料驱动重构方案书_生产实际与用户自由度.md`（§0–§16 + 附录 A–G）。
施工协议见 [REFACTOR_PROTOCOL](REFACTOR_PROTOCOL.md)（先立门、默认关闭、分代重冻、快照升版）。

| ADR | Decision | Consequence |
|---|---|---|
| [REFACTOR-01](ADR-REFACTOR-01.md) | Racks typed by **access mechanism**, not automation; `n_depth` unlocked; effective ≠ nominal slots | 9 types first; several parameters are `typical`/`gap`, not measured |
| [REFACTOR-02](ADR-REFACTOR-02.md) | Equipment type library; AST drives aisle width (**default off**) | Turning it on recomputes the nav grid, so it stays off until a level enables it |
| [REFACTOR-03](ADR-REFACTOR-03.md) | Analytic capacity layer **does not replace** the DES; they cross-validate at 15% | Assumption-level factors can never be quoted as findings |
| [REFACTOR-04](ADR-REFACTOR-04.md) | The material's five calibre bugs are fixed **and** kept as `LEGACY` for negative controls | B1 capacity unit is pinned: `C_a` is composite cycles/h, so `in=out=C_a ⇒ ρ=1.0` |
| [REFACTOR-05](ADR-REFACTOR-05.md) | Every new mechanism is config-gated off by default; one behaviour change = one refreeze | Racks (v4) and equipment (v5) must refreeze separately, never together |
| [REFACTOR-06](ADR-REFACTOR-06.md) | Rack mechanics wired into the **pick** path only, behind an explicit `rack_type` | Putaway/aisle-open/shuttle times stay analytic; LIFO rack + FEFO is counted, not forbidden |
| [REFACTOR-07](ADR-REFACTOR-07.md) | Vehicles are first-class: per-robot `model_id`, AST per rack group, `BUY_EQUIPMENT` | No v4 refreeze — default fleet is single-model and the golden stayed byte-identical |
| [REFACTOR-08](ADR-REFACTOR-08.md) | Analytic capacity reads the real layout and the deployed fleet; cross-validates **cycle time**, not throughput | AST is density-neutral: buying VNA buys aisle width, not slots — that is R5, with its own refreeze |
| [REFACTOR-09](ADR-REFACTOR-09.md) | Density from a **depth budget** (rows = floor((budget+aisle)/(depth+aisle))), no solver; boundaries reuse L2/L3/L6/L8 | Reach truck 4 to 5 rows (+6.25% slots) at 0 violations; VNA 2.0 m aisle is infeasible for this AGV and is rejected, not tuned away |
