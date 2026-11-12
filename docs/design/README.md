# Design & audit inputs

The project was shaped by three external review documents produced during the
course project (two strict audits and one parameterisation design report).
They are **not distributed with this repository**: they were inputs to the
work, not deliverables, and their conclusions are already incorporated in
[the full project report](../PROJECT_REPORT.md) (sections 12–16) and in
[the open-source plan](../OPEN_SOURCE_PLAN.md).

| Document | What it changed | Where the outcome lives |
|---|---|---|
| Audit round 1 (engineering / geometry, 8×P0) | Layout collisions, physical time model, independent validators, negative tests, compliance files | `docs/PROJECT_REPORT.md` §12 |
| Audit round 2 (operations / route-map semantics) | Priority scheduling, coverage KPIs, time–space diagram, honest "conditional pass" labels | `docs/PROJECT_REPORT.md` §14 |
| Design plan (four-layer config, 10 steps, A22–A40) | `layout_spec.json`, `spec_layout.py`, 8 scenarios, 25 KPIs, fleet/reservation replay | `docs/PROJECT_REPORT.md` §16 |
| Open-source plan (this release) | Repo hygiene, cross-platform CLI, hero README, CI, output contracts, static demo | `docs/OPEN_SOURCE_PLAN.md`, `README.md`, `CHANGELOG.md` |

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
