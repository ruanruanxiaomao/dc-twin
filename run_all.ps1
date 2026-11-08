$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
if (-not (Test-Path $Blender)) {
    $Blender = $env:BLENDER_EXE
}
if (-not $Blender -or -not (Test-Path $Blender)) {
    throw "Blender 5.2 not found. Set `$env:BLENDER_EXE to blender.exe path."
}

$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }

function Run($exe, [string[]]$Arguments) {
    Write-Host ">> $exe $($Arguments -join ' ')"
    & $exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "FAILED: $exe $($Arguments -join ' ') (exit $LASTEXITCODE)"
    }
}

# --- 数据层（纯标准库）---
Run $Py @('scripts/gen_data.py')
Run $Py @('scripts/layout_audit.py')          # 必须 0
Run $Py @('scripts/nav_grid.py')
Run $Py @('scripts/optimize_slotting.py')
Run $Py @('scripts/gen_tasks.py')
Run $Py @('scripts/plan_paths.py')
Run $Py @('scripts/gen_cargo.py')             # 订单货物状态机（闭环取货/交付）
Run $Py @('scripts/traffic_check.py')         # 必须 0，严格全程在场
Run $Py @('scripts/check_geometry.py')        # 必须 0
Run $Py @('scripts/plot_nav_plan.py')

# --- 场景层（Blender headless）---
Run $Blender @('-b', '--factory-startup', '-P', 'scripts/build_dc.py')
Run $Blender @('-b', '--factory-startup', '-P', 'scripts/animate.py')
Run $Blender @('-b', '--factory-startup', '-P', 'scripts/inspect_blend.py')
Run $Blender @('-b', '--factory-startup', '-P', 'scripts/export_glb.py')
Run $Blender @('-b', '--factory-startup', '-P', 'scripts/render.py')

# --- 产物解析与验收 ---
Run $Py @('scripts/annotate_heatmap.py')      # 给 05 图叠 5 档图例
Run $Py @('scripts/encode_video.py')
Run $Py @('scripts/parse_artifacts.py')    # 必须在 encode_video 之后，否则读到旧 MP4
Run $Py @('tests/test_validator_can_fail.py') # 7 个负测试，验证器必须能失败
Run $Py @('scripts/validate.py')              # 必须 0，任一 FAIL 会退出 1
Run $Py @('scripts/report_status.py')         # 从 JSON 生成 CURRENT_STATUS.md（单一真相源）

Write-Host ''
Write-Host '=== ARTIFACTS ==='
Get-ChildItem out -File | Select-Object Name, Length | Format-Table -AutoSize
Get-ChildItem out\renders -File | Select-Object Name, Length | Format-Table -AutoSize
