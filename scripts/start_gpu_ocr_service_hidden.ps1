param(
    [int]$Port = 8769
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $ProjectRoot "logs"
$OutLog = Join-Path $LogDir "gpu_ocr_service.out.log"
$ErrLog = Join-Path $LogDir "gpu_ocr_service.err.log"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Service = Join-Path $ProjectRoot "src\gpu_ocr_service.py"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path $Python)) {
    throw "Missing virtualenv python: $Python"
}
if (-not (Test-Path $Service)) {
    throw "Missing service file: $Service"
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
