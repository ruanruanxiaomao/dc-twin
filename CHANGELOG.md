# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
