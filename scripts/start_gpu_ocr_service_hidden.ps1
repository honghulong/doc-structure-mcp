param(
    [int]$Port = 8769,
    [switch]$NoStopOld
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $ProjectRoot "logs"
$OutLog = Join-Path $LogDir "gpu_ocr_service.out.log"
$ErrLog = Join-Path $LogDir "gpu_ocr_service.err.log"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Service = Join-Path $ProjectRoot "src\gpu_ocr_service.py"
$ProjectRootText = [string]$ProjectRoot

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path $Python)) {
    throw "Missing virtualenv python: $Python"
}
if (-not (Test-Path $Service)) {
    throw "Missing service file: $Service"
}

if (-not $NoStopOld) {
    $oldProcesses = Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -like "*gpu_ocr_service.py*" -and
            $_.CommandLine -like "*$ProjectRootText*"
        }

    foreach ($oldProcess in $oldProcesses) {
        Write-Host "[GPU OCR] stopping old pid=$($oldProcess.ProcessId)"
        Stop-Process -Id $oldProcess.ProcessId -Force -ErrorAction Stop
    }
}

$env:GPU_OCR_PORT = "$Port"

$process = Start-Process `
    -FilePath $Python `
    -ArgumentList @($Service) `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "[GPU OCR] started pid=$($process.Id) port=$Port"
Write-Host "[GPU OCR] stdout=$OutLog"
Write-Host "[GPU OCR] stderr=$ErrLog"
