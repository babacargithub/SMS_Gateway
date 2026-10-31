<#
Builds the Windows installer in one command:  packaging\windows\dist\SMS_Gateway_Setup_<version>.exe

  powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows_installer.ps1 [-Version 1.0.0]

Needs on the BUILD PC only (the recipient needs nothing):
  python -m pip install --user pyinstaller PySide6 openpyxl pyusb libusb-package
  winget install --id JRSoftware.InnoSetup -e --scope user
Steps: icon (.ico from the app's SVG) -> PyInstaller (one folder, no console) -> Inno Setup (French installer that also
installs the dongle driver, see install_dongle_driver.ps1). Keep this file pure ASCII.
#>
param([string]$Version = '1.0.0')

$ErrorActionPreference = 'Stop'
$packagingFolder = $PSScriptRoot
$repositoryRoot = (Resolve-Path (Join-Path $packagingFolder '..\..')).Path
$buildFolder = Join-Path $packagingFolder 'build'
$buildAssetsFolder = Join-Path $packagingFolder 'build_assets'
New-Item -ItemType Directory -Force -Path $buildFolder, $buildAssetsFolder | Out-Null

function Invoke-Step([string]$stepName, [scriptblock]$command) {
    Write-Host "`n=== $stepName" -ForegroundColor Cyan
    & $command
    if ($LASTEXITCODE -ne 0) { throw "$stepName failed (exit code $LASTEXITCODE)" }
}

$innoCompilerCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
)
$innoCompiler = $innoCompilerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $innoCompiler) { throw 'Inno Setup 6 not found: winget install --id JRSoftware.InnoSetup -e --scope user' }

Push-Location $repositoryRoot
try {
    Invoke-Step 'Application icon' { python (Join-Path $packagingFolder 'make_app_icon.py') (Join-Path $buildAssetsFolder 'sms_gateway.ico') }
    Invoke-Step 'PyInstaller' {
        python -m PyInstaller (Join-Path $packagingFolder 'sms_gateway.spec') --noconfirm --clean `
            --distpath (Join-Path $buildFolder 'pyinstaller_dist') --workpath (Join-Path $buildFolder 'pyinstaller_work')
    }
    Invoke-Step 'Inno Setup' { & $innoCompiler "/DAppVersion=$Version" (Join-Path $packagingFolder 'sms_gateway_setup.iss') }
} finally {
    Pop-Location
}
$installer = Join-Path $packagingFolder "dist\SMS_Gateway_Setup_$Version.exe"
Write-Host "`nInstaller ready: $installer ($([math]::Round((Get-Item $installer).Length / 1MB, 1)) MB)" -ForegroundColor Green
