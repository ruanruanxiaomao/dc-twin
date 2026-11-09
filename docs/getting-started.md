# Getting started

## 1. Install

```bash
git clone <this-repo> dc_project    # or just open the folder you already have
cd dc_project
python -m venv .venv
# Windows: .venv\Scripts\activate     POSIX: source .venv/bin/activate
pip install -e .
dc doctor
```

Python 3.10+ works. `dc doctor` reports the interpreter, the three pinned
dependencies, the Blender installation (if any), write permissions, loopback
port availability, path length and whether the two seed fields agree.

## 2. Data layer (no Blender)

```bash
dc demo                 # ~10 s: 11 data steps + 8 scenarios, then starts the console
dc demo --no-serve      # same data, no web server
dc run --scope data     # data chain only
dc run --scope scenarios
```

Open `http://127.0.0.1:8765/`. If the "scenarios" panel is empty, run
`python scripts/scenario_run.py --all`: the console reads `runs/`, which is
intentionally git-ignored and regenerated locally.

## 3. Full chain (Blender 5.2 required)

```bash
dc run --scope full
```

Blender is discovered in this order: `BLENDER_EXE` → `blender` on `PATH` →
common install folders (`%PROGRAMFILES%\Blender Foundation\Blender*`,
`/Applications/Blender.app`, `/usr/bin/blender`, `/opt/blender/blender`).
If none is found, `dc run --scope full` exits with `DC-E1001`; the data scopes
still work. Windows wrapper: `.\run_all.ps1`; POSIX wrapper: `bash run.sh --scope full`.

Outputs: `out/distribution_center.blend`, `out/dc_preview.glb`,
`out/anim_dc.mp4`, `out/renders/01..10`, `out/*_report.json`,
`out/CURRENT_STATUS.md`.

## 4. Verify

```bash
dc validate --tier data --json     # CI subset, no Blender
dc validate --tier full           # A1-A40
dc validate --check               # manifests / stale report detection
pytest -m "not blender"
```

## Troubleshooting

| Symptom | Action |
|---|---|
| `DC-E1001 Blender not found` | Install Blender 5.2, set `BLENDER_EXE`, or use `--scope data` |
| `DC-E1002 dependency installation failed` | `pip install -r requirements.txt` manually; check the Python reported by `dc doctor` |
| `dc` not found after `pip install -e .` | The console script lives in the interpreter's `Scripts/` / `bin/`; use `python -m dc_twin ...` instead |
| PowerShell and Bash use different Pythons | `run.py` always drives children with `sys.executable`, so the chain is interpreter-consistent |
| `SCHEDULE_FAIL` | Inspect `runs/<scenario>/<ts>/assertions.json`; do **not** lower `min_sep_m` |
| Chinese text garbled in PowerShell 5.1 | Read JSON with Python; files are UTF-8. `run_all.ps1` is ASCII-only on purpose |
| Empty scenario panel | `python scripts/scenario_run.py --all` (see step 2) |
| Missing reports after a fresh clone | Generated files are not tracked; run `dc demo` first |

More error codes: [cli.md](cli.md#error-codes).
