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
