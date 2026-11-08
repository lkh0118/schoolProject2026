param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw 'Install Python 3.12 x64 from python.org (include the py launcher), then reopen PowerShell.'
    }
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
        throw 'Install FFmpeg with ffprobe and add its bin directory to PATH. See README.md.'
    }
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 venv creation failed.' }
    }
    & .venv\Scripts\python.exe -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & .venv\Scripts\python.exe -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw 'Application installation failed.' }
    & .venv\Scripts\python.exe -m delta_review doctor
    if ($LASTEXITCODE -ne 0) { throw 'Environment check failed.' }
    if (-not $SkipTests) {
        & .venv\Scripts\python.exe -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed; keep the output for diagnosis.' }
    }
    Write-Host 'Setup complete. Launch with scripts\run-windows.ps1'
} finally {
    Pop-Location
}
