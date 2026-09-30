# Builds the app and its installer:
#   dist\TheThirdComing\TheThirdComing.exe   (the app, runs without Python)
#   dist\TheThirdComing-Setup.exe            (the installer to share; needs Inno Setup 6)
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Version 1.1.3]
#
# You normally don't need to run this: GitHub builds and publishes the installer on every push to main
# (.github/workflows/release.yml). Without -Version, a local build is numbered <packaging\VERSION>.0.
param([string]$Version = "")
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
if (-not $Version) { $Version = (Get-Content packaging\VERSION -Raw).Trim() + ".0" }
Write-Host "Building version $Version"
# Baked into the app so it knows which release it is (the self-updater compares it with GitHub's latest).
Set-Content -Path stickfigure\_version.py -Value "VERSION = `"$Version`"" -Encoding utf8

& .\.venv\Scripts\python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "tests failed; not building" }

& .\.venv\Scripts\python tools\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "icon render failed" }

& .\.venv\Scripts\pyinstaller packaging\stickfigure.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built app: $root\dist\TheThirdComing\TheThirdComing.exe"

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup 6 not found, so no installer was made. Install it with:  winget install JRSoftware.InnoSetup"
    exit 0
}
& $iscc "/DAppVersion=$Version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host "Built installer $($Version): $root\dist\TheThirdComing-Setup.exe"
