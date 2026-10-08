param(
    [string]$TaskName = "DocStructureGpuOcrService",
    [int]$Port = 8769
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$StartScript = Join-Path $PSScriptRoot "start_gpu_ocr_service.bat"

if (-not (Test-Path $StartScript)) {
    throw "Missing start script: $StartScript"
}

$Argument = "/c `"$StartScript`" -Port $Port"

$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument $Argument `
    -WorkingDirectory $ProjectRoot

$Triggers = @(
    New-ScheduledTaskTrigger -AtStartup,
    New-ScheduledTaskTrigger -AtLogOn
)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Start doc-structure-mcp GPU OCR service on port $Port."

Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "[GPU OCR] startup task registered: $TaskName"
Write-Host "[GPU OCR] command: cmd.exe $Argument"
Write-Host "[GPU OCR] check: curl http://127.0.0.1:$Port/health"