param(
    [string]$HostAlias = "doc-gpu",
    [string]$RemoteDir = "D:/projects/doc-structure-mcp"
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param(
        [string]$Name,
        [scriptblock]$Command
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$RemoteCmdDir = $RemoteDir.Replace("/", "\")
$Items = @(
    "src",
    "tests",
    "scripts",
    "config",
    "requirements.txt",
    "README.md"
)

Set-Location $ProjectRoot

Write-Host "[sync] target: ${HostAlias}:$RemoteDir"
Invoke-Checked "remote mkdir" {
    ssh $HostAlias "cmd /c if not exist `"$RemoteCmdDir`" mkdir `"$RemoteCmdDir`""
}

foreach ($item in $Items) {
    if (-not (Test-Path $item)) {
        throw "Missing sync item: $item"
    }
}

Invoke-Checked "scp" {
    scp -r @Items "${HostAlias}:$RemoteDir/"
}

Write-Host "[sync] remote contents:"
Invoke-Checked "remote dir" {
    ssh $HostAlias "cmd /c dir `"$RemoteCmdDir`""
}

Write-Host "[sync] done"
