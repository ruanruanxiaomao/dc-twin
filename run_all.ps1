param([string]$Scenario = 'config/scenarios/baseline.json')
# Native commands (Blender deprecation warnings, pip notices) write to stderr.
# With Stop, PowerShell 5.1 turns those into terminating NativeCommandError,
# so use Continue and let Run() fail explicitly on a non-zero exit code.
$ErrorActionPreference = 'Continue'

# NOTE: keep this file ASCII-only. PowerShell 5.1 reads a BOM-less UTF-8 .ps1
# as ANSI/GBK; Chinese comments can then swallow the FOLLOWING line.
# param() must be the first statement. Some hosts set $PSScriptRoot to the
# caller's directory, so verify the root via requirements.txt.
$Root = $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }
$req = $Root + '\requirements.txt'
if (-not (Test-Path -LiteralPath $req)) {
    $alt = $Root + '\dc_project'
    if (Test-Path -LiteralPath ($alt + '\requirements.txt')) { $Root = $alt }
}
$req = $Root + '\requirements.txt'
if (-not (Test-Path -LiteralPath $req)) {
    throw "Cannot locate dc_project root (requirements.txt not found)"
}
Set-Location $Root

$Blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
if (-not (Test-Path $Blender)) { $Blender = $env:BLENDER_EXE }
if (-not $Blender -or -not (Test-Path $Blender)) {
    throw "Blender 5.2 not found. Set `$env:BLENDER_EXE to blender.exe path."
}

# PowerShell and Bash may resolve 'python' to different interpreters.
# Prefer the project .venv, then the local 3.13 with deps, then PATH python.
$Py = 'python'
$localPy = 'C:\Users\OYC\.workbuddy\binaries\python\versions\3.13.12\python.exe'
if (Test-Path -LiteralPath $localPy) { $Py = $localPy }
$venvPy = $Root + '\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPy) { $Py = $venvPy }
Write-Host ">> python = $Py"

& $Py -c "import PIL, imageio_ffmpeg, jsonschema" 2>&1 | Out-Null
$depsOk = ($LASTEXITCODE -eq 0)
if (-not $depsOk) {
    Write-Host '>> installing pinned dependencies from requirements.txt'
    & $Py -m pip install --disable-pip-version-check -r requirements.txt 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'DEPENDENCY_INSTALL_FAILED' }
}

function Run($exe, [string[]]$Arguments) {
    Write-Host ">> $exe $($Arguments -join ' ')"
    & $exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "FAILED: $exe $($Arguments -join ' ') (exit $LASTEXITCODE)"
    }
}

# --- L1 layout: layout_spec.json is the only input ---
Run $Py @('scripts/spec_layout.py', '--spec', 'config/layout_spec.json', '--quiet')
Run $Py @('scripts/gen_data.py')
Run $Py @('scripts/layout_audit.py')
Run $Py @('scripts/nav_grid.py')

# --- derived data chain ---
Run $Py @('scripts/optimize_slotting.py')
Run $Py @('scripts/gen_tasks.py')
Run $Py @('scripts/plan_paths.py')
Run $Py @('scripts/gen_cargo.py')
Run $Py @('scripts/traffic_check.py')
Run $Py @('scripts/check_geometry.py')
Run $Py @('scripts/plot_nav_plan.py')

# --- scenarios / fleet / traffic / data adapters ---
Run $Py @('scripts/scenario_run.py', '--all')
if ($Scenario -ne 'config/scenarios/baseline.json') {
    Run $Py @('scripts/scenario_run.py', '--scenario', $Scenario)
}
Run $Py @('scripts/adapters/csv_orders.py', '--csv', 'config/orders_sample.csv', '--out', 'out/csv_events.jsonl')
Run $Py @('scripts/fleet_sim.py')
Run $Py @('scripts/reservation_check.py')

# --- Blender scene layer ---
Run $Blender @('-b', '--factory-startup', '-P', 'scripts/build_dc.py')
Run $Blender @('-b', '--factory-startup', '-P', 'scripts/animate.py')
Run $Blender @('-b', '--factory-startup', '-P', 'scripts/inspect_blend.py')
Run $Blender @('-b', '--factory-startup', '-P', 'scripts/export_glb.py')
Run $Blender @('-b', '--factory-startup', '-P', 'scripts/render.py')

# --- artifact parsing ---
Run $Py @('scripts/annotate_heatmap.py')
Run $Py @('scripts/encode_video.py')
Run $Py @('scripts/parse_artifacts.py')

# --- negative tests (17 cases) + acceptance ---
Run $Py @('tests/run_all_negative.py')
Run $Py @('scripts/validate.py')
Run $Py @('scripts/report_status.py')

Write-Host ''
Write-Host '=== ARTIFACTS ==='
Get-ChildItem out -File | Select-Object Name, Length | Format-Table -AutoSize
Get-ChildItem out\renders -File | Select-Object Name, Length | Format-Table -AutoSize
