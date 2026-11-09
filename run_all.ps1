param([string]$Scenario = 'config/scenarios/baseline.json', [string]$Scope = 'full')
# NOTE: keep this file ASCII-only. PowerShell 5.1 reads a BOM-less UTF-8 .ps1
# as ANSI/GBK; Chinese comments can swallow the FOLLOWING line.
# This is now only a Windows wrapper around the cross-platform run.py driver.
$ErrorActionPreference = 'Continue'

$Root = $PSScriptRoot
if (-not $Root) { $Root = (Get-Location).Path }
$req = Join-Path $Root 'requirements.txt'
if (-not (Test-Path -LiteralPath $req)) {
    $alt = Join-Path $Root 'dc_project'
    if (Test-Path -LiteralPath (Join-Path $alt 'requirements.txt')) { $Root = $alt }
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'requirements.txt'))) {
    throw "Cannot locate dc_project root (requirements.txt not found)"
}
Set-Location $Root

$Py = $null
$venvWin = Join-Path $Root '.venv\Scripts\python.exe'
$venvPosix = Join-Path $Root '.venv\bin\python'
if (Test-Path -LiteralPath $venvWin) { $Py = $venvWin }
elseif (Test-Path -LiteralPath $venvPosix) { $Py = $venvPosix }
if (-not $Py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source }
}
if (-not $Py) {
    $cmd = Get-Command python3 -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source }
}
if (-not $Py) { throw "Python 3.10+ not found. Install Python or create .venv." }
Write-Host ">> python = $Py"

& $Py 'run.py' '--scope' $Scope '--scenario' $Scenario
$rc = $LASTEXITCODE
if ($rc -ne 0) { throw "FAILED: run.py --scope $Scope (exit $rc)" }
Write-Host "RUN_ALL_OK scope=$Scope"
