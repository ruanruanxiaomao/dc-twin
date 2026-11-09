# Contributing

Thanks for taking a look. This is a course-project-derived research repository:
the priority is **reproducibility and honest limitations**, not feature volume.

## Before you start

```bash
pip install -e ".[dev]"
dc doctor
pytest -m "not blender"
dc validate --tier data
```

## Ground rules

1. **Do not hard-code geometry.** Layout comes only from
   `config/layout_spec.json`; scenes come from `data/layout.json`. New knobs
   belong in config, with a schema entry and a test.
2. **Do not weaken a validator to make a check pass.** Fix the data or the
   algorithm. Negative tests must stay able to fail (`tests/run_all_negative.py`).
3. **Every new public behaviour needs a test** and a CHANGELOG entry.
4. **Keep the data chain reproducible.** The seed lives in two files and must
   stay consistent; `dc doctor` checks it.
5. **Label your KPI source.** Analytic what-if numbers and measured main-chain
   numbers must never be presented as the same thing.
6. **No large binaries in git.** Generated `.blend` / `.glb` / `.mp4` / PNG
   belong in `out/` (ignored) or a GitHub Release; README images go through
   `scripts/build_docs_assets.py`.
7. **ASCII-only for `.ps1` files** (PowerShell 5.1 GBK decoding pitfall).

## Adding a scenario

Copy `config/scenarios/baseline.json`, set `overrides`, `seed` and
`assertions`, then run `python scripts/scenario_run.py --scenario <file>`.
Assertions are deliberately allowed to fail — fix the model, not the threshold.

## Adding a KPI

Add it to `scripts/kpi.py` with `definition`, `formula`, `unit`, `source` and
a provenance tag (`analytic` / `measured` / `assumption`). Add a unit test.

## Commit / PR

- Keep one logical change per commit.
- Paste `dc doctor --json` output when reporting environment problems.
- CI runs `ruff check` on **new code** (`dc_twin/`, `run.py`, the new scripts
  and pytest tests), `pytest -m "not blender"` and `dc validate --tier data`
  on Ubuntu + Windows, Python 3.11 + 3.13. The legacy `scripts/*.py` and
  `console/server.py` predate the lint gate and are not yet lint-clean;
  do not add new violations there. Blender jobs run one preview frame only.
