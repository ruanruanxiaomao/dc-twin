# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — R1 集装单元层（G3，默认关闭）

Each SKU now has a handling unit: pallet or tote, footprint, gross weight, load rating,
units per load and an identification method. The layer is wired into the engine but
`enabled=false`, so the v3 golden is still byte-identical — that equality is the gate.

### Added

- **`sku_master.json` carries `unit_load`** for all 240 SKUs (180 pallets / 60 totes),
  derived deterministically from existing fields (no rng, no clock): the pallet standard
  is the lightest one that **fits the cell and carries the weight**, then units are
  derated for cell height. `unit_load_counts` summarises the mix.
- **Engine (config-gated)**: `UNITLOAD_HANDOFF`, `ID_FIRST_READ`, `ID_RESCAN` per receipt,
  and `UNITLOAD_OVERLOAD_REJECTED` when a unit exceeds its rating — the reject keeps the
  stock untouched and leaves an event instead of silently dropping the goods.
  First-read vs rescan is decided by integer arithmetic on `(sku, log seq)`, **not** by
  `rng`, so enabling the layer cannot shift the demand stream.
- **`scripts/check_compatibility.py`**: per-SKU load / envelope / identification check,
  in `dc run --scope inventory` (6 → 7 steps). Self-test injects overload, oversized
  envelope and a sub-threshold read rate; all three must fail.

### Fixed (found while wiring it)

- **Cell geometry was copied from the layout tool (1.1 × 1.1 m)** while the project's
  declared design pallet is EUR 1200 × 800. A 1.2 m pallet does not fit a 1.1 m opening,
  so the envelope check reported 220 violations. Two configs were each plausible and
  jointly absurd: the cell had been sized for an imaginary 1100 × 1100 pallet.
  Cells are now derived from the design pallet (`0.9 × 1.3 m`) in both `capacity_spec`
  and `rack_spec`, with `level:"assumption"` recorded in `meta.sources`.
- **Carrier selection ignored geometry.** The first generator assigned pallets by
  `idx % 3`; 4 SKUs then exceeded their assigned pallet's rack rating and 220 violated
  the envelope. Selection now filters by "fits the cell (90° rotation allowed)" first,
  then by capacity, and derates units when nothing fits.
- **Envelope check assumed one orientation.** A pallet can be placed long-side into the
  depth or across the beam; judging only `l → width` marked every EUR pallet oversized.
- **Vertical clearance was double-counted**: the stack was capped at `clearH`, then the
  "protrusion" allowance was added, so tall pallets always failed by 1 mm.

### Verified

| Gate | Evidence |
|---|---|
| UNITLOAD_OK | 240 SKUs: 0 overload, 0 envelope violations, max load utilisation 99.3% |
| OVERLOAD_NEG | injected 1500 kg vs 1000 kg rating → rejected, stock unchanged, event emitted |
| ID_READ_OK | enabled run: first-read share ≥ 0.90 of ~4% configured miss rate; counters in `sim_state` |
| DEFAULT_OFF | `unit_load_spec.enabled=false` → event stream byte-identical to the v3 golden |
| DETERMINISM | same seed → identical `UNITLOAD_*` / `ID_*` stream |

## [Unreleased] — R0 资料驱动重构：口径护栏（不改行为）

Phase R0 of the material-driven refactor (plan v1.2 §11). Nothing is wired into the
engine yet: this round only puts the four new specs, their schemas, the analytic
capacity formulas and the five calibre corrections on disk — with negative controls
that prove the corrections are doing something.

### Added

- **Four specs + schemas**: `unit_load_spec` (pallets/totes/load ratings/identification),
  `rack_spec` (9 types keyed by **access mechanism**, not automation), `equipment_spec`
  (10 types + time model + AST→aisle-width rule), `capacity_spec` (cell geometry, demand,
  factors, alerts, cross-validation tolerance). Every number carries `source` and
  `level`; values the material does not give are marked `level:"gap"` instead of invented.
- **`scripts/capacity_model.py`** — pure analytic layer (no rng, no clock, writes
  nothing): cell geometry → unit → aisle → device cycles → capacity / load / warnings.
  Ships **both** the corrected and the material's original formulas (`FIX` / `LEGACY`),
  because a fix nobody can switch off is a fix nobody can test.
- **`scripts/check_capacity_spec.py`** — R0 gate: 4 schemas validate, specs are
  semantically consistent (drive-in must be single-end LIFO, push-back ≠ pallet
  gravity, every rack's equipment exists in the type library, the B1/B2/B3 switches
  are still the corrected values), and `capacity_model --self-test` passes.
  Wired into `ACCEPT_CHAIN` (46 → 47 steps).
- **`tests/unit/test_capacity_model.py`** — 25 tests: appendix C formulas by hand,
  the five negative controls, and the config semantics that R2/R3 will depend on.
- **ADR-REFACTOR-01..05** + `REFACTOR_PROTOCOL.md` (gate-before-code, default-off,
  generational refreeze, snapshot upgrade rules).

### Fixed (calibre, per §5.1)

- **B1** load rate divisor: `(in+out)/(2·C)`. The material divides single-command
  counts by composite capacity, over-stating load by ~2×.
- **B2** forklift travel: `D̄=(L_a/2)·d_f` already contains `d_f`, so `t=D̄/v` — the
  material multiplies again and gets `d_f²`, inconsistent with its own stacker branch.
- **B3** height check applies to **all** equipment types; the material only checks
  forklifts, so a stacker serving a 9 m rack raises no warning.
- **B4** the load fields are actually used: gross pallet weight ≤ min(rack rating,
  cell rating, equipment payload).
- **B5** `n_depth` unlocked (double-deep capacity can double, rear blocked by front);
  the material hard-codes it to 1.

### Verified

| Gate | Evidence |
|---|---|
| R0_SCHEMA_OK | 4/4 specs validate against their schemas |
| R0_SEMANTIC_OK | rack/equipment/unit/capacity specs agree with each other |
| R0_BUGFIX_NEG_OK | each of B1–B5 gives a **different** result under `LEGACY`, in the expected direction |
| STEP_BASELINE_OK | v3 golden still byte-identical (8 scenarios / 24 artefacts) |
| KPI_BASELINE_OK | 336 frozen values unchanged |
| pytest | 223 passed |

**Deliberate deviation from the plan, recorded in ADR-REFACTOR-04:** §12.1's B1 example
says `in=out=C_a ⇒ ρ=0.5`, but C.7 defines `C_a` as *composite* capacity, under which
`in=out=C_a` is saturated (ρ=1.0). The project pins the composite definition and asserts
both points, so the guarded property (denominator ×2) is unchanged either way.

## [Unreleased] — P6b 批次 / 效期 / FEFO

P6a gave the engine a (SKU × location) ledger, but not **lots**: the same SKU's stock
was one number, so "先到期先出" could not be expressed and nothing could ever expire.
P6b adds lots, expiry, FEFO/FIFO and write-off — and a third golden generation,
because that necessarily changes the event stream again.

### Added

- **Data**: `inventory_initial.json` now carries `lots` (796 lots over 392 locations),
  `sku_master.json` carries `shelf_life_s`. Deterministic (integer arithmetic, no clock,
  no `hash()`), byte-identical across runs.
- **Engine**: lots per (SKU, location); `STOCK_DECREMENT` carries `lots: [{lot_id, qty,
  expiry_t}]` so "which lot went out" is auditable; `STOCK_RECEIPT` carries
  `lot_id`/`expiry_t`; `STOCK_EXPIRED` for write-off; `SET_LOT_POLICY` (FEFO ↔ FIFO).
- **KPI** (tick layer 56 → 62): `expiry_enabled`, `expired_units`, `expired_value`,
  `lots_opened`, `open_lots`, `fefo_violations`.
- **HUD**: 过期报废 card, deliberately **not** folded into variable cost.
- **Gates**: `check_fefo.py` (FEFO_OK — replays the lot ledger and requires consumption
  to be a prefix of the policy order; also "never ship expired"), `check_expiry.py`
  (EXPIRY_OK — no premature write-off, no missed write-off, state vs events),
  `dc run --scope inventory` 4 → 6 steps.

### Changed

- **Third golden generation**: `step_baseline_v3.json` / `kpi_baseline_v3.json`;
  `--check=v1|v2|v3`, `--freeze-v3`; default follows the newest that exists.
  v1 and v2 are never overwritten.
- **KPI baseline now covers inventory** (30 → 42 metrics, 336 frozen values). P6a's
  metrics were never in `kpi_baseline.METRICS`, which is why its refreeze said
  "KPI 变 0 项" — that was a gap in the gate, not a fact.
- **Snapshot 3.0**, `inventory.schema` 2.0: lots are part of the snapshot, a save
  whose lots disagree with the aggregate (`sum(lots) != stock`) is rejected, and a
  save with expiry enabled but no lots is rejected rather than silently degrading FEFO.
- `check_reorder` pressure window re-calibrated (scale 0.10 → 0.18): write-off shrinks
  available stock, so the old window degenerated to 9→9.

### Fixed (found while wiring it)

- **Expiry was invisible to the tick scheduler.** `step()` jumps idle ticks; a lot
  expiring between two jumps was skipped forever, so nothing ever expired. Expiry
  times now join the scheduled-event list and block the jump (same class of bug as
  the t=0 fault that was skipped).
- **FEFO and FIFO produced identical results**, because every later arrival also
  expired later. Data now contains date-code inversion (a later delivery with a
  shorter date code) — the real reason FEFO exists.
- **Reconciliation ignored write-off.** `_inv_reconcile` replayed only receipts and
  picks, so after the first expiry every (sku, location) looked "over-stocked".
- `check_inventory` conservation ignored `STOCK_EXPIRED` for the same reason; expired
  units are now an explicit term (期初 + 入 − 出 − 报废 = 期末).

### Verified

| Gate | Evidence |
|---|---|
| FEFO_OK | 27 picks replayed, 0 wrong-order, 0 expired shipped; injection of both is caught |
| EXPIRY_OK | 124 lots / 1657 units written off, 0 premature, 0 missed; ghost lot caught |
| STOCK_OK | 392 pairs, residual 0, 报废 1657 in the equation |
| SAVE_OK+ | snapshot 3.0 round-trip keeps lots; 2.0 and lot-less saves rejected; tampered qtys rejected |
| DETERMINISM+ | same seed → identical `STOCK_*` stream and closing lots |
| EXPIRY_OFF | with `expiry.enabled=false` the event stream is byte-identical to the v2 golden |
| REFREEZE_OK | v3 byte-identical, v2 differs by construction, A1–A40 40/40 |

**Not done**: level_06. Measured effect of FEFO vs FIFO is 3–5 units (≈0.2%) because
completions are capped at ~40/h by fleet capacity while 392 locations compete for those
picks; a level whose lever is indistinguishable is a fake choice. See ADR-GAME-10 §6.

## [Unreleased] — P6a 库存层（SKU / 安全库存 / 补货点）

The twin could move boxes but had no stock: `cargo_state.json` is order-level cargo
state, not an inventory ledger, so "缺货" could not happen, be asserted, or be played.
P6a adds a (SKU × location) ledger, safety stock, reorder points, stockout events and
holding cost — and, because that **must** change the event stream, a v1/v2 double
golden instead of pretending nothing moved.

### Added

- **`config/inventory_spec.json` + schema** — reorder / holding / stockout / shrink /
  pick_face / stock_policy. Behaviour in JSON, not Python.
- **`scripts/gen_inventory.py`** → `sku_master` / `inventory_slotting` / `inventory_initial`,
  derived from the existing `skus.json` + `slotting.json` (no second SKU master).
  Deterministic: fixed seed, sorted, no clock, no timestamp in output.
  All 392 SHELF locations now map to exactly one SKU (the 152 empty ones become
  A-class overflow), so the P6a-1 reverse lookup can never fall through.
- **Engine**: `STOCK_DECREMENT` / `STOCK_RECEIPT` / `STOCK_REPLENISH_ARRIVED` /
  `STOCKOUT` / `REORDER_TRIGGERED` / `PICKFACE_REFILL` / `HOLDING_ACCRUED`;
  stock settlement before `TASK_COMPLETED` (a stockout can no longer be counted as
  delivered); reorder → `REPLENISH` from the inbound dock; `SET_REORDER_POINT` command.
- **Snapshot 2.0**: inventory ledger is part of the snapshot and `restore` refuses a
  save whose stock disagrees with an event-stream replay.
- **Economy**: `Cost:Stockout_Penalty`, `Cost:Holding` (separate from per-order
  variable cost), and internal moves earn no revenue.
- **KPI** (tick layer): `stockout_count`, `fill_rate`, `holding_cost`,
  `inventory_value`, `reorder_triggers`, `inventory_enabled` (50 → 56).
- **Levels**: `lose_conditions.stockouts`, plus `level_05` where the reorder point is
  the actual lever (fill rate vs holding cost).
- **Gates**: `check_inventory.py` (STOCK_OK), `check_reorder.py` (REORDER_OK, both
  directions), `check_pickface.py` (PICKFACE_OK), `report_refreeze.py` (REFREEZE_OK),
  `dc run --scope inventory`.

### Changed

- **v1 golden kept, v2 added.** `step_baseline` / `kpi_baseline` take `--check=v1|v2`
  and `--freeze-v2`; the default follows v2 when it exists. `REFREEZE_OK` requires
  `--check=v1` to FAIL — if v1 ever turns green, the inventory events never reached
  the event stream and the gate must go red.
- `gen_inventory` joins `DATA_CHAIN` (13 → 14); cleanroom STEPS/COMPARE grow to
  10 steps / 11 files, so A40 reproduces the inventory data from scratch.

### Fixed (found while wiring the engine)

- **Replenishment was moving stock the wrong way.** Implemented as reserve → pick
  face first, which made `stockout` go *up* with the reorder point (5 → 6): orders
  pick from `pick_loc`, and only 24 of 392 locations are pick faces. Now it
  replenishes from the inbound dock to the depleted location (4 → 3).
- **Internal moves competed with customers.** At P2 they consumed fleet capacity and
  completions fell 90 → 37 as the reorder point rose. Internal orders are P3.
- **Stress experiments overwrote the byte-level baseline.** `check_reorder` wrote its
  scaled-stock runs into `runs/baseline/`, which is exactly where the golden picks the
  "latest run". Experiments now write `out/exp/`.
- **Cross-warehouse pick-face pairing.** An F1 SKU could get a pick face in W, which
  would need a cross-warehouse transfer. Pick faces are now same-warehouse, and the
  engine rejects cross-warehouse internal orders outright.
- Holding cost was initially folded into per-order variable cost, which made unit cost
  drift with stock level; it is now its own line.

### Verified

| Gate | Evidence |
|---|---|
| STOCK_OK | 392 (sku, location) pairs, residual < 1e-9; +1e-6 tamper caught |
| REORDER_OK | factor 1→3: stockout 4→3 **and** holding 0.255→0.274; disabled logic fails |
| STOCKOUT_NEG | zeroed stock → `STOCKOUT` + `ORDER_LOST`, no `TASK_COMPLETED` |
| PICKFACE_OK | 24 face locations, 0 unexplained increase; +5 without event caught |
| REFREEZE_OK | v2 byte-identical, v1 differs by construction, A1–A40 40/40 |
| SAVE_OK+ | snapshot 2.0 round-trip; old 1.0 saves rejected; tampered stock rejected |
| DETERMINISM+ | same seed → identical `STOCK_*` stream and closing stock |
| ECONOMY_OK+ | Δcash == Σcredit − Σdebit − fixed accrual, holding included as a posting |

## [Unreleased] — 模拟运营游戏层（P0–P5 第一轮）

The twin could only be watched: `run()` was a single `while` loop from t=0 to
`horizon`, money was settled after the fact, and the front end scrubbed a
pre-computed keyframe track. This round wraps the *same* deterministic engine
in a game layer without touching its arithmetic.

**Every existing gate still passes: A1–A40 40/40, 17 negative tests, KPI golden,
and the batch event stream is byte-identical (`step_baseline --check`).**

### Added

- **`scripts/step_baseline.py` + `tests/golden/step_baseline.json`.** Freezes
  `sim_events/sim_kpi/sim_state` per scenario and compares **byte for byte**
  (only `run_id` and `wall_clock_s` normalised, since those must change). This is
  the gate that proves `run()` → `step()` changed nothing; `--self-test` proves
  the gate can fail.
- **`Simulation.step()` / `_init_run()` / `_finalize()`.** The old `while`
  body moved verbatim, including the idle tick-jump. `run()` is now
  `init → while not step() → finalize`, and refuses to re-init a resumed sim.
- **Player command queue** (`enqueue_command` / `_apply_commands`, tick-boundary
  application). `SET_POLICY`, `SET_DEMAND`, `BUY_AGV`, `SELL_AGV`, `SET_PRICE`,
  `INJECT_FAULT` work; unknown types and bad payloads raise.
- **Snapshot primitives**: `Mulberry32.state()/from_state()`,
  `Robot.serialize()/deserialize()` (33 fields), `EventLog.snapshot()/restore()`
  which reopens **append** and refuses a log whose last seq disagrees.
- **`Simulation.snapshot()` / `restore()`** — full resumable state; restoring and
  continuing produces the same event bytes as running straight through.
- **`console/game.py`** — game session + Fix-Your-Timestep wall-clock loop
  (`speed ∈ {1,2,4,8}`, pause zeroes the accumulator), command/save/load APIs.
- **`/api/game/*`** — start / pause / speed / command / state / save / load /
  fast-forward / levels / stop. HUD state is ~kilobytes, not the whole event log.
- **`scripts/game_economy.py`** — live cash, postings shared with `ledger.py`
  (`(source_event_seq, account)` idempotency), fixed cost accrued **per sim hour**
  (so it is *not* double-posted at `RUN_SETTLED`), bankruptcy after a grace period.
- **`scripts/game_levels.py` + `config/levels/*.json` + level schema** — 4 levels
  calibrated against measured runs, objectives (not assertions), 1–3 stars by
  rule, unlocks + `saves/meta.json` meta-progress.
- **`scripts/game_save.py`** — atomic save slots, rejects corrupt/version-mismatched
  saves, restores by continuing the *same* event log.
- **Front end**: `console/static/game.js` + a 游戏 tab. Play/pause/speed now talk
  to the server; the 3D sampler reads live positions; replay stays intact.
- `scripts/dc_root.py` — one root resolver; frozen builds resolve to the exe dir.
- **`scripts/check_live.py` + `scripts/live_probe.mjs`** — a real browser click-path
  (Edge/Chrome over CDP, Node 22's built-in `WebSocket`, zero extra deps):
  start → HUD numbers → clock runs → pause holds → resume → 2× → save → load →
  buy until cash goes negative. This is `LIVE_OK`; wiring tests alone could not
  prove "the button actually does something".
- **`dc run --scope game`** — new chain: byte-level step baseline + game tests +
  browser click-path. Kept out of `full` so CI without a browser still passes.
- `tests/run_game_tests.py` — one entry point listing the game test files, so the
  chain does not drift when tests are added.

### Changed

- **`scenario_tools.validate_scenario`** now accepts `dispatch.policy`,
  `battery.charge_policy`, `dispatch.battery_reserve_pct` with value checks
  derived from the engine's own tables. Before this, the engine read those keys
  but the validator rejected them, so "policy = scenario" was not actually true.
- `config/economy_spec.json` introduces `tariff_multiplier = 5.0`: demo prices
  make every scenario loss-making at 45 orders/h (revenue ¥193 vs ¥751 of fixed
  cost), so levels would be unwinnable. The multiplier is labelled
  `prices_source=game_balance` in the HUD and the post-hoc ledger still uses the
  demo prices — the two numbers must never be mixed.
- `check_exe.py` now walks the game chain (start → tick → pause → command → save →
  load) inside the packaged exe, because `game_*` modules are imported lazily and
  PyInstaller can silently drop them.
- **`report_status.py` no longer hardcodes the pipeline length.** It printed
  "31 步链（data 11 · scenarios 16 · full 31）" while the runner was 45 — now both the
  step counts and the new game-layer row (levels / objectives / commands / slots /
  ADRs) are computed from `dc_twin.runner` and the files on disk.
- **`check_claims.py` now scans `out/CURRENT_STATUS.md`** — the "single source of
  truth" was not in the guard's target list, which is exactly how a wrong step
  count survived. Adds a "N 步链" rule and a data/scenarios/full triple check;
  verified by re-injecting the old text (guard reports 2 problems).
- **`build_exe.py` now produces the release zip itself** (`dist/DC-Twin-win64.zip`).
  It used to be packed by hand, so after the game layer landed the zip still had
  506 entries and **zero game files** — a release that quietly shipped the old
  feature set while the CHANGELOG said otherwise.

### Fixed

- **Snapshot taken with unflushed event log** (surfaced only in the packaged exe:
  few events, buffer not full → `load` reported "log seq=0 vs snapshot seq=4").
  `EventLog.snapshot()` now flushes first.
- **`run()` after `step()` silently restarted the sim** (t reset to 0, seq doubled).
  `run()` initialises only when the sim has not started; `restore()` marks it started.
- **Robot snapshots were missing `charger_id`** (the plan's field list omitted it):
  every restored robot would have queued for the same charger.
- **Frozen builds resolved `config/` inside the unpack temp dir**, so the exe said
  "no such scenario: baseline". All modules now share `dc_root.project_root()`.
- **Loading a save left the game frozen.** `/api/game/load` only started the tick
  thread when `paused=false`, so pressing "继续" after a load did nothing — the flag
  was cleared but nobody was reading it. The thread now always starts; `paused`
  is just a gate. (Found by the browser click-path, not by unit tests.)
- **`GameSession.start()` reset a loaded save to `t=0`** — it called `_init_run()`
  unconditionally. Same bug class as `run()`, fixed the same way: only initialise
  when the sim has not started. A regression test waits on the real thread.
- `game_save.save_session` crashed when the slot dir was outside the project root
  (`relative_to` ValueError); it now falls back to an absolute path.
- **`out/CURRENT_STATUS.md` went dirty after every regeneration** — the file carries a
  `生成时间` line but is committed, so any rerun produced a one-line diff. Noise, and
  worse: real calibre changes hid inside it. `report_status.write_if_changed()` now
  compares everything except that line and leaves the file untouched (not even mtime)
  when nothing else changed; regeneration is idempotent. Covered by
  `tests/unit/test_report_status.py`, which also pins the two mistakes my first
  attempt made (list-vs-splitlines trailing element; forgetting the trailing blank).

### Verified

| Gate | Evidence |
|---|---|
| `ENV_OK` | `dc doctor` PASS (17 checks); `.venv` on Python 3.14.6 |
| `BASELINE_OK` | 65 pytest, A1–A40 40/40, 17 negatives, KPI golden |
| `STEP_OK` | 8 scenarios × 3 artefacts byte-identical; gate proven fallible |
| `CMD_OK` | same seed + same command stream → identical event bytes |
| `GAME_OK` | live ticking, pause holds `t_s`, 120 concurrent commands all applied |
| `ECON_OK` | Δcash == Σcredit − Σdebit − fixed accrual (1e-6); bankruptcy fires |
| `LEVEL_OK` | win/lose/stars/unlock, plus failure cases |
| `SAVE_OK` | restore-and-continue == uninterrupted run (byte-identical) |
| `LIVE_OK` | browser click-path (14 steps): HUD, clock, pause, resume, 2×, save, load, bankruptcy |
| `EXE_OK` | packaged exe: all read APIs + `game/*` chain pass; release zip rebuilt from it |

## [0.13.0] - 2026-11-12

Trucks were scenery: two box shapes parked on the yard lane, never at a dock,
never leaving. The console also still looked like default controls.

### Added

- **Yard truck timetable.** `scripts/gen_yard.py` writes `data/yard_trucks.json`:
  4 docks x 2 waves, each truck arrives through a southern gate, drives the lane,
  backs into its bay, dwells 34 s, reverses out and disappears; the next one pulls
  in behind it. One document is read by three consumers - Blender keyframes, the
  three.js viewer, and anyone auditing `dock_served`.
- Blender: `animate.py` writes arrival/berth/departure keyframes (position + a
  constant heading while reversing) and `hide_render` outside the truck's window.
- Viewer: `viewer3d.js` samples the same timetable when you scrub, so the 3D twin
  and the rendered video agree frame for frame.
- Site: southern truck gate with barrier, a 24-bay dock apron with painted bay
  lines, and the site extended 10 m further south so a 10 m trailer fits.

### Changed

- **UI pass** (`style.css` v4.10 section): one accent colour (#2f6fd0, the same
  blue as the rack uprights), three-layer depth (topbar / canvas / panel) with 1px
  borders and near-invisible shadows instead of heavy dividers, one type scale
  (11/12/13/15) with tabular numbers, one radius family (12/10/7), tabs as a
  segmented control with a solid active pill, and a lighter timeline.
- Truck orientation was measured, not guessed: the asset's cab faces local -Y, so
  a docked truck is rotation 0 and the heading while driving is atan2(dx, -dz).

Verification: validate 40/40, pytest 65, ARTIFACTS PASS (2284 nodes),
942 frames re-rendered, truck keyframes 8, WEBAPP_OK, DRIFT_OK, CLAIMS_OK.

## [0.12.0] - 2026-11-12

The model was rebuilt against a reference render: the site was four concrete slabs
floating in green, the racks were painted by heat index (red/green/yellow confetti),
and the sorting area was two grey boxes. Two of those were bugs, not taste.

### Fixed

- **Dock rects are absolute layout coordinates, but the builder added each
  warehouse's origin a second time.** W (origin 0,0) looked fine; F1-F3's docks,
  bumpers and dock lines were drawn at y ~ 180 m, outside the site — the white
  plates floating beside the front warehouses in the old screenshots. Docks are
  now placed once, from the rect.
- Station platforms were 1 m tall boxes, so every sorting/packing line sat on a
  plinth like an exhibition stand. They are 8 cm pads with a painted border now.
- Rack beams were 5.5% of the rack height (0.33 m on a 6 m rack): four orange
  drawers stacked up. 2% reads as a beam.

### Added

- `scripts/site_detail.py`: asphalt yard + grass verges, perimeter fence with posts
  and rails, east gate with guard house and barrier, kerbs, lane edge lines and
  centre dashes, trailer bays, 24 trees and 6 lamp masts, and per-building
  building envelope — 2.2 m parapet walls, outward eave with a thin colour band,
  full-height west wall with ribs and a high window strip, dock canopies with
  posts, and a facade sign on every warehouse.
- `scripts/zone_detail.py`: conveyors became belts with rails, legs and parcels
  (was: one 1.1 m solid box); the sorting line is a conveyor with four sloped
  chutes, two sort stations with monitors, and operators; packing lines get
  benches, cartons and operators. Sloped chutes are explicit meshes — tilting a
  cube with `rotation_euler[1]` broke as soon as the run turned 90 degrees.
- Racks are blue uprights + orange beams (industry palette). Heat is still in the
  model, but as a small end-cap tag instead of a 0.9 m colour plate on top of
  every rack — that plate was the loudest thing in the interior render.
- 3D viewer: shadow map on (no shadow = racks look like stickers), hemisphere
  light 1.55 -> 0.85 with a stronger sun for contrast, lower iso angle, a
  `作业区` camera preset, and the framing bounds now include the site margin.

### Changed

- `CAM_overview` / `CAM_heatmap` / `CAM_route` ortho scales: `ortho_scale` is the
  vertical extent at 16:9, so 205 was showing 115 m across — the new 136 m site
  was cropped. `CAM_interior` now points at the sorting line; `CAM_dock` at the
  dock face from the yard.
- Baseline render re-shot (942 frames + 6 stills), objects 1.8k -> 2.3k.

Verification: validate 40/40, pytest 65, WEBAPP_OK, DRIFT_OK, CLAIMS_OK.

## [0.11.0] - 2026-11-12

The console was readable but not usable: two of its ten tabs were a rendering bug,
the P&L lived in a 400 px column with no chart, the 3D view was a wall of
overlapping labels, and the dispatch rule was a constant in the engine.

### Fixed

- The tab bar wrapped at 7+3 in a 400 px sidebar and 10+0 at 40vw, so the same
  screen looked broken depending on which tab you were on. It is now a fixed
  5 x 2 grid at every width.
- Scenario cards reused `.item`'s `10px 1fr auto` grid, so the title landed in
  the 10 px dot column and Chinese text rendered one character per line
  (screenshot evidence). They have their own `.scen-item` grid now.

### Added

- **Operations board.** The 运营 tab is a full-width workspace: 6 KPI cards,
  a waterfall (revenue -> variable -> fixed -> operating profit), a cost donut,
  a sensitivity tornado, a per-run profit comparison with a table, the full
  P&L statement with subtotals, a diagnosis block ("short X orders of
  breakeven, and a longer shift cannot close it"), and the ledger. Charts are
  hand-written SVG - the packaged exe has no network, so no chart library.
- **Pluggable policies.** `DISPATCH_RULES` (nearest_first / farthest_first /
  highest_soc / lowest_soc / least_completed) and `CHARGE_POLICIES`
  (opportunistic / threshold) replace the hard-coded picks in `_assign` and
  `_run_robot`. A scenario's `overrides` now carries `dispatch.policy`,
  `battery.charge_policy` and `dispatch.battery_reserve_pct`, so a policy is
  saved the same way a scenario is - no second config format. Unknown names
  raise instead of silently falling back (a silent fallback invalidates any
  A/B result).
- 3D label declutter: greedy screen-space culling over the 21 area labels, with
  a 全部 / 仅分区 / 关 switch. Full-scene view keeps 8-10 non-overlapping
  labels instead of 21 stacked ones.

### Measured

Same 1800 s baseline, only the dispatch rule changed:

| rule | completed | distance | charge |
|---|---|---|---|
| nearest_first | 38 | 3561 m | 3 |
| highest_soc | 27 | 3164 m | 3 |
| farthest_first | 23 | 3330 m | 3 |

nearest_first wins on throughput but is not the cheapest in metres - exactly the
trade-off a saved policy pair is supposed to expose.

### Verification

pytest 65 (adds tests/test_policies.py), validate 40/40, KPI tick 50 / analytic
25, WEBAPP_OK, DRIFT_OK, CLAIMS_OK.

## [0.10.0] - 2026-11-11

Docks finally have a duration and an occupancy. Before this, a dock only had one
behaviour — it could be blocked — and five doors were indistinguishable from one.

### Added

- Docks are loaded as time-window resources: an (un)load occupies a door for
  150 s outbound / 180 s inbound, taken from `process_spec.process_steps`
  (`STG_OUT→DOCK_OUT`, `DOCK_IN→STG_IN`) rather than copied into the engine.
  Parallel slots equal the layout's real door count (10 zones, 38 doors).
- No free door means the order waits in the dispatch queue instead of driving to
  the apron: `DOCK_RESERVED` / `DOCK_HANDOFF` / `DOCK_REFUSED`, plus 11 `dock_*`
  metrics (tick layer 39 → 50).
- `scripts/check_dock.py`: recomputes transfers, mean service time, utilization
  and handoffs from events, then runs a capacity contrast (1 vs 8 doors per zone,
  300 outbound orders/h) where queueing and waiting must appear and then clear.
- `tests/test_dock_queue.py` (8) and `tests/test_fault_preempt.py` (3).

### Fixed

- `--dock-slots` changed `slots` but not the door time-window array, so 1/2/8
  doors produced byte-identical queue and throughput numbers — the switch looked
  like it worked while doing nothing.
- `_door_points` spread targets along the apron wall; the apron sits behind a
  single-lane corridor, so five targets were worse than one (baseline 38 → 31).

### Changed

- S4 no longer demands a reassignment from every fault: a fault that lands on an
  idle robot interrupts nothing, which is a real observation at 12% fleet
  utilization. It still forbids completions inside a window and still requires a
  reassignment when the fault hit a moving robot. The scenario now targets
  `agent: "ANY_BUSY"` so it exercises failure handling instead of luck.
- `kpi_baseline.py` freezes 30 metrics per scenario (was 24), 8 × 30 = 240.
- `check_claims.py` gained a numeric rule: any "N 项 tick/measured" must equal the
  measured count, because the regex table missed 39 → 50 in two documents.

### Notes

Given the apron corridor, an AGV that waits the whole 150 s at the door blocks
the lane (measured: peak 59 → 35 completed). The model therefore reserves the
door at dispatch and keeps the AGV handoff short; a truck-entity model with
appointments is the next step, not part of this one.

## [0.9.0] - 2026-11-11

The functional areas got bodies. Until now 21 areas rendered as 3 floor slabs and
18 name tags, so a receiving zone and a returns zone looked identical.

### Added

- `build_area_props()`: five prop archetypes driven by area kind — pallet stacks
  (pallet + staggered cartons + straps), workbenches (top, legs, cartons), pick
  shelves (posts, 3 levels, totes), sorting chutes (bed, side plates, tote) and
  battery cabinets (body + indicator). 16 groups / 128 meshes.
- Physical areas spread their props along the work band instead of stacking them
  in the middle; logical areas search for the nearest free spot (12 directions x
  4 radii) that stays inside a warehouse and clear of every solid.

### Fixed

- Placement used to test only the four corners of a prop's rectangle. F1's pick
  shelf ended up straddling a rack row (corners outside, body inside). Switched
  to AABB intersection; a read-back check over the .blend now reports
  `PROP_OVERLAP 0` against racks, docks and stations.

### Changed

- Scene 1327 -> 1455 objects, GLB 806 -> 830 KB, stills and the 942-frame video
  re-baked; validate 40/40 unchanged (props do not enter the nav grid, locations
  or acceptance counts).

## [0.8.0] - 2026-11-11

Real orders can now drive the tick engine, and CI proves it every run.

### Added

- `sim_engine.py --events PATH`: consume an external order stream instead of
  synthesising Poisson arrivals. Arrival times come from the data, do not consume
  the RNG (service times keep their separate stream), and participate in the
  tick-jump computation so idle ticks never skip a scheduled order.
- `scripts/real_data_pilot.py`: one command runs CSV adapter -> external-order
  simulation -> ledger -> P&L -> comparison report against the synthetic
  baseline (`out/pilot/report.md`). `--freeze/--check` pin 14 deterministic
  metrics; two runs of the same CSV match exactly.
- CI step that runs the pilot after the KPI baseline.

### Changed

- `scripts/kpi_baseline.py` excludes the `pilot_csv` run directory from the
  8-scenario baseline: the pilot has its own frozen values.

### Notes

The sample CSV has 24 orders across W/F1/F2/F3; the result proves the chain runs
end to end and reproduces, not what a real operation would earn.

## [0.7.0] - 2026-11-11

Two foundation pieces: KPIs that cannot drift silently, and an environment you can
rebuild with one command. No refactors, no new simulation features.

### Added

- `scripts/kpi_baseline.py`: freezes 8 scenarios x 24 deterministic metrics
  (`wall_clock_s` excluded on purpose), compares counts exactly and floats at a
  1e-6 relative tolerance, and requires an explicit `--freeze` to move the
  baseline. `--self-test` proves both drift kinds are caught.
  A full 23-step scenario chain (108 s) reproduces all 192 values bit for bit.
- `requirements.lock`: exact versions that both the local Windows box and the CI
  matrix have been tested with.
- `scripts/bootstrap.py`: create `.venv`, install from the lock, install the
  project editable with `--no-deps`, then run doctor. `--check` verifies an
  existing environment without installing, and is now a CI step.
- CI: environment consistency check, and KPI baseline drift after the scenario chain.

### Changed

- `dc_twin/paths.py` no longer carries its own `VERSION = "0.1.0"`; it imports
  `__version__`, which is what doctor prints (it had been reporting 0.1.0 while
  the package was already 0.6.0).
- doctor now reports optional capabilities (`opt:pywebview`) and whether the
  running interpreter is inside the tested range (`python_tested`), instead of
  silently passing.

## [0.6.0] - 2026-11-11

Made the project's own claims checkable, and closed the console's write endpoints.

### Added

- `scripts/check_claims.py`: derives step counts, KPI counts, acceptance checks,
  version, licence and area counts from code and artefacts, then flags stale
  wording in current-state docs. `--self-test` proves all nine rules fire.
  Historical reports are excluded on purpose: they record what was true then.
- `console/security.py` + `tests/test_console_security.py` (8 tests): Host must be
  loopback (blocks DNS rebinding), POST with an Origin/Referer must be same-origin
  (blocks CSRF), and a configured `DC_CONSOLE_TOKEN` is required for every request.
  Binding a non-loopback address without a token now refuses to start.
- CI step and README section for both guards.

### Changed

- Version has a single source: `dc_twin.__version__` (0.6.0); `pyproject.toml` now
  declares `dynamic = ["version"]` instead of duplicating it.
- Corrected stale claims across README, docs/cli.md, COMPLETION_STATUS.md, the
  console, the submission page and the demo footer: 31 to 45 steps, A1-A21 to
  A1-A40, 27 to 39 tick KPIs, MIT to Apache-2.0, "no remote/tag" removed, and the
  "L1 time-space reservation" wording replaced with what the code does.
- out/sim_latest.json and out/ledger_index.json store repository-relative paths
  instead of absolute C:/Users/... paths.
- scripts/check_doc_drift.py no longer hard-codes a desktop path; DC_PLAN_COPY
  points at the master plan.

## [0.5.0] - 2026-11-11

Shipped as a **Windows application** and trimmed the workspace by ~1 GB.

### Added

- `scripts/build_exe.py`: PyInstaller onedir build (`dist/DC Twin/DC Twin.exe`).
  The window, the web UI and the Python runtime go inside; `out/`, `data/`,
  `runs/`, `scripts/`, `dc_twin/` stay beside the exe so artefacts remain visible
  and the pipeline can still be re-run.
- `scripts/check_exe.py`: external verification of a build — 19 read-only
  endpoints, a real 10-step data-chain rerun, scenario save/delete, a headless
  3D screenshot, and an assertion that the server's root is the exe directory.
- `scripts/clean.py`: workspace cleanup with a dry-run default (frames, old run
  snapshots, stale artefact bundles, caches).
- `console/server.py` frozen branch: `ROOT` becomes the exe directory, `STATIC`
  follows it, and `find_python()` looks for a real interpreter instead of
  handing scripts to the GUI executable.
- `dc_app.pyw` redirects stdout/stderr to a temp log when launched without a
  console and shows a message box on fatal errors.

### Changed

- Plan document raised to v4.1 (§31 covers packaging, verification and cleanup).
- Archived screenshots moved to `assets/`; temporary screenshots and build
  work directories removed.

## [0.4.0] - 2026-11-09

Turned the submission page into a **complete project showcase** with an interactive 3D twin.

### Added

- `submission/twin3d.js`: zero-dependency software 3D projection renderer —
  perspective/oblique camera, per-face Lambert shading, painter's-algorithm depth
  sorting, cached static geometry, orbit/zoom/preset/follow controls, AGV chassis
  + carried pallet + status beacons + glowing trails. Renders all 196 racks as
  real 3D boxes. Explicitly labelled as a browser visualisation, not a Blender viewport.
- Three-view switcher (3D / top-down plan / Blender render), dark+light themes with
  canvas colours read from CSS variables, and a second rotatable 3D model preview.
- Two new tabs: **Project Map** (6-stage pipeline, four-layer config, data contracts,
  searchable map of 57 real deliverables, CLI) and **Model & Renders** (MP4 player,
  Blender audit stats, downloadable `.blend` / `.glb` / `.mp4`, 10 render gallery).
- Evidence tab now shows A1–A40 / S1–S9 / F1–F7 check details, 27 KPI definitions
  and a real simulation event stream; Ops shows ledger accounts and sample entries.
- `build_submission.py` inlines all of the above from `out/*.json`, copies 10 WebP
  renders + 3 binary assets, guards against absolute user paths in the public snapshot.
- `tests/test_stage_g.py` extended to 10 contract tests, including regressions for
  global entity coordinates, dock rect corner semantics, keyframe count and privacy.

### Fixed

- 3D/2D renderers double-applied `warehouse.origin` to already-global entity
  coordinates (F1/F2/F3 racks and docks were projected outside the site).
- Dock `rect` was read as `[x,y,w,h]` but is `[x0,y0,x1,y1]`; F3 produced a giant
  off-canvas wedge. Both renderers now use corner semantics.
- `sampled_paths()` aliased the source list and inflated `total_keyframes` by 72;
  the count now matches `data/paths.jsonl` (5421) exactly.
- `--cv-sky0` / `--cv-sky-0` variable mismatch made the canvas background grey.
- Hero image replaced with the perspective interior render (`02_interior.webp`);
  keyframe counts on the page are now read from the snapshot instead of hard-coded.

### Changed

- `submission/提交说明.md`, `docs/COMPLETION_STATUS.md`, README and this changelog
  updated; published link recorded in the app's "作品信息" table as real data.

## [0.3.0] - 2026-11-09

G1/G2 completed and a **zero-backend activity submission web app** added.

### Added

- **G1 — visual 2D layout editor** (`submission/editor.js`): select/move, add
  zone / rack row / dock door / home, delete, undo, keyboard shortcuts, live
  validation with red outlines, background tracing with two-point calibration,
  and draft-only JSON export (never overwrites `config/layout_spec.json`).
  Interaction reference: `Stegocode/warehouse_layout_editor` (MIT verified).
- **G2 — DXF import** (`scripts/import_dxf.py`, `config/layer_mapping.json`):
  zero-dependency ASCII DXF reader for LWPOLYLINE / LINE / INSERT, layer mapping,
  two-point scale calibration, sample DXF generator, and a loud refusal when no
  rack/wall layer is recognised (falls back to background tracing).
  `ezdxf` (MIT) remains an optional enhancement, not a required dependency.
- **Submission app** (`submission/`): 7-tab static site — overview, live twin
  (21 AGVs driven by **real inlined keyframes** from `data/paths.jsonl`),
  scenario lab with draft export, P&L waterfall / breakeven / sensitivity,
  layout editor, evidence gallery, and a submission-material tab.
  No CDN, no backend, no network, no build step; opens from `file://`.
- `scripts/build_submission.py` regenerates `submission/data.js` from real
  `out/*.json` + `data/layout.json` and copies 8 WebP renders (416 KB).
- `submission/提交说明.md`: ready-to-paste abstract, the WorkBuddy conversation
  share-link steps, a privacy checklist (personal paths / secrets / screenshots),
  a 3-minute reviewer tour and reproduction commands.
- `tests/test_stage_g.py`: 6 contract tests (DXF import / calibration / unknown
  layers, snapshot fields, static asset references, no CDN).

### Published

- Static submission app is live at
  `https://dc-twin-studio.app.workbuddy.host/?sharecode=SRGROMS4ZA`.
  The link is recorded in `submission/提交说明.md` and `docs/COMPLETION_STATUS.md`.
  Manage it from 「设置—数据管理—应用」.

### Changed

- `docs/COMPLETION_STATUS.md`, README and this changelog: G1/G2 status updated
  from "degraded" to "implemented (2D / ASCII subset)"; 3D drag preview and
  complex/binary DXF remain declared limitations.

---

## [0.2.0] - 2026-11-09

Stage A/B/C of [`docs/COMPLETION_PLAN.md`](docs/COMPLETION_PLAN.md): from a
reproducible static demo to a customisable, simulated, cost-accounted twin.
Full status and degradation register: [`docs/COMPLETION_STATUS.md`](docs/COMPLETION_STATUS.md).

### Added

- **A — tick-level simulation** (`scripts/sim_engine.py`, `config/sim_spec.json`,
  schema): deterministic Mulberry32 PRNG, continuous arrivals, online dispatch,
  9-state FSM, four-rate battery, fault/reassignment, L1 reservation, append-only
  `sim_events.jsonl`, 27 measured KPIs. All 8 scenarios really execute.
- **S-series validator** (`scripts/validate_sim.py`): S1–S9 independent
  recomputation + 8 scenario assertions + 4 tamper negative tests.
- **B — event-sourced ledger & P&L** (`config/cost_spec.json` + schema,
  `scripts/ledger.py`, `scripts/pnl_report.py`, `scripts/validate_pnl.py`):
  postings with `source_event_seq`, idempotent re-posting, pnl.json/md/csv,
  breakeven, sensitivity, demo-price badge, F1–F7 + 4 negative tests.
  E1/B0 replay degradation path stays available.
- **B5/B6 — console "运营" panel** (`/api/pnl`, `/api/ledger`, `POST /api/cost-spec`)
  and P&L deltas in `dc compare` (`out/pnl_compare.md`).
- **C1 — user-defined scenarios** (`scripts/scenario_tools.py` + console "场景"
  panel): template / copy builtin / validate / 600 s dry-run / save / run with
  failing assertions / delete. Custom scenarios are isolated under
  `config/scenarios/custom/` and `runs/custom/`; `--include-custom` for batch runs.
- **C3 — WMS adapter framework** (`scripts/adapters/base.py`, `generic_rest.py`,
  `config/integrations.json`): idempotent pulls, exponential backoff x3,
  environment-only secrets, default dry-run ack, local mock contract test.
- **C5 — `fleet_spec` wired into the layout source** via
  `spec_layout.py --fleet-spec` with new **L13** over-capacity rule; the shipped
  12/3/3/3 deployment keeps `data/layout.json` byte-identical.
- **C6 — self-designed LIF-like import/export** (`scripts/lif.py`): 74 nodes /
  70 synthetic edges, round-trip self-test, draft-only import. GPL-3.0 clean-room
  isolation from `vda5050_lif_editor`.
- **GitHub reference audit** (`docs/GITHUB_REFERENCE_AUDIT.md`): 16 repos
  re-checked via API, 2 new clones, 5 licence/fact corrections,
  `THIRD_PARTY_NOTICES.md` synced (WareTwin engine derivation, MAPF-CBS and
  layout-editor entries, GPL isolation statement).
- `tests/test_stage_c.py`: 11 contract/regression tests; runner
  `SCENARIO_CHAIN` now registers sim → ledger → pnl → S/F validators.

### Fixed

- `idle_rate` was identically 0 (fleet-level boolean) → vehicle-level share.
- `ENERGY_TICK` logged cumulative instead of window deltas → window deltas +
  tail flush, so S2 energy conservation closes (<0.0002%).
- Event-driven tick jumps skipped fault start/end → scheduled faults now exact
  (1800 s downtime for 3 x 600 s).
- `scenario_run.py` / `validate.py` picked tick-run directories when scanning
  `runs/<scenario>/*` → analytic runs are filtered by the files they own.
- `robot_failure` produced zero reassignments → scenario-owned
  `fault_target_policy: preempt_busy` genuinely interrupts an in-flight task.

### Degraded / not implemented (declared, not hidden)

- **D3**: L3 CBS solver stays `experimental` (MAPF-CBS is not self-contained).
- **G1**: no drag-and-drop layout editor; form-based editing + background
  tracing only. **G2**: no automatic DXF import. **G3**: no verified live WMS.
- **G4**: mixed fleet models are rejected; single model only.
- LIF is *not* VDA 5050 certified; all prices are `demo`; collision claims remain
  `sampled verification + reservation check`.

---

## [0.1.0] - 2026-11-08

First reproducible open-source release. Goal: make a strong local engineering
project usable by a stranger who clones it.

### Added

- Cross-platform `dc` CLI (`doctor`, `demo`, `run --scope`, `validate --tier`,
  `serve`, `export`, `compare`) as a thin wrapper over the existing scripts.
- `run.py` / `run.sh` / ASCII-only `run_all.ps1`; Blender discovery via
  `BLENDER_EXE` → `PATH` → standard install locations; **no personal paths**.
- `pyproject.toml` (editable install, pinned deps, pytest/ruff config),
  `.gitattributes`, `.editorconfig`, regenerated `.gitignore`.
- English hero README with WebP screenshots (<110 KB each) and a 4 s GIF,
  Chinese README retained as `README.zh-CN.md`, Mermaid architecture diagram,
  explicit "What it is / What it is not".
- `docs/getting-started.md`, `docs/cli.md`, `docs/data-contracts.md`,
  `CONTRIBUTING.md`, `SECURITY.md`, `CITATION.cff`, historical project report.
- Report sidecar envelopes (`.meta.json`) with `schema_version`, UTC time,
  generator and input hashes; `out/manifest.json` with SHA256 of all artifacts;
  `dc validate --check` drift detection.
- pytest test layout (unit / contract / integration / acceptance) wrapping
  the 17 existing negative tests; A13 replaced by a behavioural test.
- GitHub Actions CI: data matrix (Ubuntu/Windows × 3.11/3.13) without Blender,
  plus a one-frame Blender smoke job.
- Static GitHub Pages demo (`dc export --target demo`) with no backend.
- `dc compare` for KPI deltas between two scenario runs; `dc export --target
  release` for a checksum-verified bundle.

### Fixed

- Removed the hard-coded `C:\Users\...\python.exe` from `run_all.ps1`.
- Removed dead `AUDIT_REPORT.md` / `DESIGN_PLAN.md` references from the README.
- Stopped tracking 48 generated artifacts and temp files (`.git` payload
  reduced for future commits; historical blobs remain until a history rewrite).
- Restored the working tree to the A1–A40 baseline before starting (26 deleted
  files, 17 modified files had drifted from `HEAD`).

### Known limitations

Scenario KPIs are analytic (M/M/c) what-if approximations; traffic is sampled
verification plus a reservation-table replay, not continuous-time CBS/WHCA\*;
orders and SKU velocities are synthetic; each AGV executes one order; the GLB
is static. See [README.md](README.md#what-it-is--what-it-is-not).
