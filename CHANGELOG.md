# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
