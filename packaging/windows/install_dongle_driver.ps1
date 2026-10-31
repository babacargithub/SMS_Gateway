<#
Installs (or removes) the SMS Gateway USB driver package on THIS PC. Run as administrator (the installer does it).

Install:
  1. Removes any earlier SMS Gateway driver package and certificate (clean upgrade).
  2. Creates a code-signing certificate unique to this PC (non-exportable key, in LocalMachine\My).
  3. Builds the driver catalog (New-FileCatalog) next to sms_gateway_winusb.inf and signs it with that certificate.
  4. Trusts the certificate's PUBLIC part (LocalMachine\Root + TrustedPublisher), then DELETES the certificate and its
     private key: nothing else can ever be signed with it.
  5. pnputil /add-driver /install: Windows now binds WinUSB to the dongle automatically, on any USB port, no prompt.
Uninstall (-Uninstall): removes the driver package from the driver store and the certificate from both stores.

Everything is logged to %ProgramData%\SMS Gateway\driver_install.log. Exit code 0 = success.
Keep this file pure ASCII (Windows PowerShell 5.1 reads BOM-less files as ANSI).
#>
param(
    [string]$DriverFolder = (Join-Path $PSScriptRoot 'driver'),
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$InfFileName = 'sms_gateway_winusb.inf'
$CatalogFileName = 'sms_gateway_winusb.cat'
$CertificateSubjectPrefix = 'CN=SMS Gateway USB driver signer'
$PnpUtilSuccessCodes = @(0, 259, 3010)   # 259: no matching device plugged in right now; 3010: reboot suggested

$logFolder = Join-Path $env:ProgramData 'SMS Gateway'
New-Item -ItemType Directory -Force -Path $logFolder | Out-Null
$logPath = Join-Path $logFolder 'driver_install.log'
function Write-Log([string]$message) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Output $line
}

function Remove-SmsGatewayDriverPackages {
    $publishedPackages = Get-WindowsDriver -Online | Where-Object { $_.OriginalFileName -like "*\$InfFileName" }
    foreach ($package in $publishedPackages) {
        $output = pnputil /delete-driver $package.Driver /uninstall /force 2>&1 | Out-String
        Write-Log ("removed driver package {0}: exit {1} {2}" -f $package.Driver, $LASTEXITCODE, ($output.Trim() -split "`r?`n" | Select-Object -Last 1))
    }
}

function Remove-SmsGatewayCertificates {
    foreach ($storeName in 'Root', 'TrustedPublisher', 'My') {
        Get-ChildItem "Cert:\LocalMachine\$storeName" | Where-Object { $_.Subject -like "$CertificateSubjectPrefix*" } | ForEach-Object {
            Remove-Item -Path $_.PSPath -DeleteKey -ErrorAction SilentlyContinue
            if (Test-Path $_.PSPath) { Remove-Item -Path $_.PSPath }
            Write-Log "removed certificate $($_.Thumbprint) from LocalMachine\$storeName"
        }
    }
}

function Add-CertificateToStore($certificateFilePath, [string]$storeName) {
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($storeName, 'LocalMachine')
    $store.Open('ReadWrite')
    try {
        $store.Add((New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certificateFilePath)))
    } finally { $store.Close() }
}

try {
    Write-Log "=== $(if ($Uninstall) { 'uninstall' } else { 'install' }) (PowerShell $($PSVersionTable.PSVersion), $([Environment]::OSVersion.VersionString))"
    Remove-SmsGatewayDriverPackages
    Remove-SmsGatewayCertificates
    if ($Uninstall) { Write-Log 'uninstall done'; exit 0 }

    $infPath = Join-Path $DriverFolder $InfFileName
    $catalogPath = Join-Path $DriverFolder $CatalogFileName
    if (-not (Test-Path $infPath)) { throw "missing $infPath" }
    if (Test-Path $catalogPath) { Remove-Item $catalogPath -Force }

    $signingCertificate = New-SelfSignedCertificate -Type CodeSigningCert `
        -Subject "$CertificateSubjectPrefix ($env:COMPUTERNAME)" `
        -CertStoreLocation 'Cert:\LocalMachine\My' -KeyExportPolicy NonExportable `
        -HashAlgorithm SHA256 -NotAfter (Get-Date).AddYears(50)
    Write-Log "created certificate $($signingCertificate.Thumbprint)"

    try {
        # Trust first, so the signature below is checked against a trusted root and reports "Valid".
        $publicCertificatePath = Join-Path $env:TEMP "sms_gateway_driver_signer_$($signingCertificate.Thumbprint).cer"
        Export-Certificate -Cert $signingCertificate -FilePath $publicCertificatePath -Type CERT | Out-Null
        Add-CertificateToStore $publicCertificatePath 'Root'
        Add-CertificateToStore $publicCertificatePath 'TrustedPublisher'
        Remove-Item $publicCertificatePath -Force
        Write-Log 'public certificate trusted (Root + TrustedPublisher)'

        New-FileCatalog -Path $DriverFolder -CatalogFilePath $catalogPath -CatalogVersion 2.0 | Out-Null
        $signature = Set-AuthenticodeSignature -FilePath $catalogPath -Certificate $signingCertificate -HashAlgorithm SHA256
        Write-Log "catalog signed: $($signature.Status)"
        if ($signature.Status -ne 'Valid') { throw "catalog signature is $($signature.Status): $($signature.StatusMessage)" }
    } finally {
        Remove-Item -Path "Cert:\LocalMachine\My\$($signingCertificate.Thumbprint)" -DeleteKey -ErrorAction SilentlyContinue
        Write-Log 'signing certificate and private key deleted'
    }

    $pnpOutput = pnputil /add-driver $infPath /install 2>&1 | Out-String
    $pnpExitCode = $LASTEXITCODE
    Write-Log ("pnputil /add-driver exit {0}: {1}" -f $pnpExitCode, (($pnpOutput.Trim() -split "`r?`n") -join ' | '))
    if ($PnpUtilSuccessCodes -notcontains $pnpExitCode) { throw "pnputil failed with exit code $pnpExitCode" }
    Write-Log 'install done'
    exit 0
} catch {
    Write-Log "ERROR: $_"
    exit 1
}
