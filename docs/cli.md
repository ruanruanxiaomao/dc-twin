# CLI reference

Install with `pip install -e .`. Every command is also available as
`python -m dc_twin <cmd>` if the `dc` console script is not on `PATH`.

| Command | Purpose |
|---|---|
| `dc doctor [--json]` | 17 environment checks; `--json` for issue reports |
| `dc demo [--port N] [--no-serve]` | Data chain + 8 scenarios + local console. No Blender |
| `dc run --scope data\|scenarios\|full` | 14 / 24 / 47 steps respectively |
| `dc validate --tier data\|render\|full [--json] [--junit PATH] [--check]` | Acceptance subset, machine output, manifest drift |
| `dc serve [--port N] [--host H]` | Start the console without regenerating data |
| `dc export --target demo\|release [--out DIR]` | Static GitHub Pages demo or checksum-verified release bundle |
| `dc compare RUN_A RUN_B [--format md\|csv\|json] [--out PATH]` | KPI / assertion / config delta between two runs |

### `dc run` options

| Flag | Meaning |
|---|---|
| `--scope` | `data` (no Blender), `scenarios` (+ scenario/KPI/adapters), `full` (+ Blender + renders + acceptance) |
| `--scenario PATH` | Run one extra scenario, e.g. `config/scenarios/peak.json` |
| `--seed N` | Persistently set `site.seed` in `layout_spec.json` **and** `seed` in `config.json` |
| `--out DIR` | Copy `out/` into DIR after the run |
| `--dry-run` | Print the selected steps without writing anything |
| `--quiet` | Only step headers and the final summary |
| `--preview` | Sets `DC_RENDER_PREVIEW=1` for a low-resolution render |

The step list lives in `dc_twin/runner.py`. Steps are executed in order with
`sys.executable` (or the discovered Blender), with a structured JSONL log at
`runs/logs/<UTC-timestamp>.jsonl`.

### Error codes

| Code | Meaning |
|---|---|
| `DC-E1001` | Blender not found (only blocks `--scope full`) |
| `DC-E1002` | Pinned dependencies could not be installed |
| `DC-E2001` | A pipeline step returned non-zero (the step name is printed) |
| `DC-E2003` | `layout_spec.json` validation failed (see `out/spec_layout_report.json`) |
| `DC-E3001` | Report schema / manifest mismatch (`dc validate --check`) |
| `DC-E4001` | Refusing a non-loopback console host without `DC_CONSOLE_TOKEN` |

### Safety

`dc serve` binds `127.0.0.1` by default. Setting `--host 0.0.0.0` is rejected
unless `DC_CONSOLE_TOKEN` is set; the console is a local tool, not a multi-user
service.

### Windows notes

`run_all.ps1` is a thin wrapper: it locates Python (`.venv` → `python` →
`python3`) and calls `run.py`. Keep that file **ASCII-only** — PowerShell 5.1
decodes BOM-less UTF-8 `.ps1` as ANSI/GBK, and a Chinese comment can swallow
the following line.
