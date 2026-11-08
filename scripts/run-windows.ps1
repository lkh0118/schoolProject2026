param([string]$DataDir = '')
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        throw 'Run scripts\setup-windows.ps1 first.'
    }
    if ($DataDir) {
        & .venv\Scripts\python.exe -m delta_review --data-dir $DataDir
    } else {
        & .venv\Scripts\python.exe -m delta_review
    }
    if ($LASTEXITCODE -ne 0) { throw "Delta Review exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
