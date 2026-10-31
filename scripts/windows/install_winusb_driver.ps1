<#
Binds Microsoft's built-in WinUSB driver to the AT interfaces of every plugged-in Huawei dongle in modem mode
(USB 12D1:151D), so the app (pyusb + libusb) can talk to them. Windows has no driver for these interfaces
(Device Manager shows "HUAWEI Mobile" with code 28).

  - Interface 0 (MI_00): the AT port the app sends commands on. The ONLY one bound by default.
  - Interface 3 (MI_03): the second AT port where the dongle announces new SMS (+CMTI). Do NOT bind it while the
    Huawei filter "ew_usbenumfilter" (old Huawei driver package, upper filter of the composite device) is installed:
    that filter renames the interfaces "USB\VID_12D1&SUBCLASS_12&PROT_FF\..." (no "MI_xx"), libusb then cannot tell
    two WinUSB interfaces apart and every transfer on interface 0 fails with I/O error 87 (tested 2026-10-31).
    Without interface 3 the app reads the SIM on its 20 s timer instead.

What it does, per interface: picks C:\Windows\INF\winusb.inf > "WinUsb Device" (exactly what Device Manager does with
"Choose from a list"), then adds the DeviceInterfaceGUIDs registry value libusb needs (without it: "Entity not found"),
then restarts that one device. No download, no certificate, no test mode. Asks for administrator rights (UAC).

The binding belongs to the USB port: plugging the dongle into another port may need this script again.
Undo: Device Manager > the "HUAWEI Mobile" device > Uninstall, then unplug/replug.

Usage (from any PowerShell):  powershell -ExecutionPolicy Bypass -File scripts\windows\install_winusb_driver.ps1
                              ... -Interfaces "0,3"    (also the notification port; see the warning above)
#>
param(
    [string]$Interfaces = '0',   # text, not int[]: "powershell -File" (used for the elevated copy) cannot pass an array
    [string]$LogPath = (Join-Path $env:TEMP 'sms_gateway_winusb_install.log'),
    [switch]$AlreadyElevated
)
$InterfaceNumbers = @($Interfaces -split '[,; ]+' | Where-Object { $_ -ne '' } | ForEach-Object { [int]$_ })

$isAdministrator = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdministrator) {
    if ($AlreadyElevated) { Write-Error 'Administrator rights were refused.'; exit 1 }
    $argumentsForElevated = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
        '-Interfaces', ($InterfaceNumbers -join ','), '-LogPath', "`"$LogPath`"", '-AlreadyElevated')
    Start-Process powershell -Verb RunAs -Wait -ArgumentList $argumentsForElevated
    if (Test-Path $LogPath) { Get-Content $LogPath -Encoding UTF8 }
    exit 0
}

$WinUsbInterfaceGuid = '{8C3E9D4A-5B1F-4E62-9A7D-2F6B1C0E5A13}'
$logLines = New-Object System.Collections.Generic.List[string]
function Write-Log([string]$line) { $logLines.Add($line); Write-Output $line }

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class WinUsbBinder {
    [StructLayout(LayoutKind.Sequential)]
    public struct SP_DEVINFO_DATA { public int cbSize; public Guid ClassGuid; public int DevInst; public IntPtr Reserved; }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct SP_DEVINSTALL_PARAMS {
        public int cbSize; public int Flags; public int FlagsEx; public IntPtr hwndParent;
        public IntPtr InstallMsgHandler; public IntPtr InstallMsgHandlerContext; public IntPtr FileQueue;
        public UIntPtr ClassInstallReserved; public int Reserved;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string DriverPath;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct SP_DRVINFO_DATA {
        public int cbSize; public int DriverType; public UIntPtr Reserved;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string Description;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string MfgName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string ProviderName;
        public long DriverDate; public ulong DriverVersion;
    }

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr SetupDiGetClassDevs(IntPtr classGuid, string enumerator, IntPtr hwnd, int flags);
    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiEnumDeviceInfo(IntPtr set, int index, ref SP_DEVINFO_DATA data);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiGetDeviceRegistryProperty(IntPtr set, ref SP_DEVINFO_DATA data, int property, out int regType, byte[] buffer, int size, out int required);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiGetDeviceInstanceId(IntPtr set, ref SP_DEVINFO_DATA data, StringBuilder id, int size, out int required);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiGetDeviceInstallParams(IntPtr set, ref SP_DEVINFO_DATA data, ref SP_DEVINSTALL_PARAMS p);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiSetDeviceInstallParams(IntPtr set, ref SP_DEVINFO_DATA data, ref SP_DEVINSTALL_PARAMS p);
    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiBuildDriverInfoList(IntPtr set, ref SP_DEVINFO_DATA data, int driverType);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiEnumDriverInfo(IntPtr set, ref SP_DEVINFO_DATA data, int driverType, int index, ref SP_DRVINFO_DATA drv);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiSetSelectedDriver(IntPtr set, ref SP_DEVINFO_DATA data, ref SP_DRVINFO_DATA drv);
    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);
    [DllImport("newdev.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool DiInstallDevice(IntPtr hwnd, IntPtr set, ref SP_DEVINFO_DATA data, ref SP_DRVINFO_DATA drv, int flags, out bool needReboot);

    const int DIGCF_PRESENT = 0x2, DIGCF_ALLCLASSES = 0x4, SPDRP_HARDWAREID = 0x1, SPDRP_SERVICE = 0x4;
    const int SPDIT_CLASSDRIVER = 1, DI_ENUMSINGLEINF = 0x10000, DI_FLAGSEX_ALLOWEXCLUDEDDRVS = 0x800;

    static string Fail(string step) { return "FAILED " + step + ", Win32 error " + Marshal.GetLastWin32Error(); }

    static string ReadStringProperty(IntPtr set, ref SP_DEVINFO_DATA data, int property) {
        int regType, required; var buffer = new byte[4096];
        if (!SetupDiGetDeviceRegistryProperty(set, ref data, property, out regType, buffer, buffer.Length, out required)) return "";
        return Encoding.Unicode.GetString(buffer, 0, Math.Max(0, required));
    }

    // Returns one line per matching device: "<instance id>|<result>".
    public static string[] Bind(string hardwareIdFragment, string infPath) {
        var results = new System.Collections.Generic.List<string>();
        IntPtr set = SetupDiGetClassDevs(IntPtr.Zero, "USB", IntPtr.Zero, DIGCF_PRESENT | DIGCF_ALLCLASSES);
        if (set == new IntPtr(-1)) { results.Add("|" + Fail("SetupDiGetClassDevs")); return results.ToArray(); }
        try {
            for (int index = 0; ; index++) {
                var data = new SP_DEVINFO_DATA(); data.cbSize = Marshal.SizeOf(data);
                if (!SetupDiEnumDeviceInfo(set, index, ref data)) break;
                if (ReadStringProperty(set, ref data, SPDRP_HARDWAREID).IndexOf(hardwareIdFragment, StringComparison.OrdinalIgnoreCase) < 0) continue;
                int required; var instanceId = new StringBuilder(512);
                SetupDiGetDeviceInstanceId(set, ref data, instanceId, 512, out required);
                if (ReadStringProperty(set, ref data, SPDRP_SERVICE).TrimEnd('\0').Equals("WinUSB", StringComparison.OrdinalIgnoreCase)) {
                    results.Add(instanceId + "|ALREADY WinUSB"); continue;
                }
                results.Add(instanceId + "|" + InstallWinUsb(set, ref data, infPath));
            }
        } finally { SetupDiDestroyDeviceInfoList(set); }
        return results.ToArray();
    }

    static string InstallWinUsb(IntPtr set, ref SP_DEVINFO_DATA data, string infPath) {
        var installParams = new SP_DEVINSTALL_PARAMS(); installParams.cbSize = Marshal.SizeOf(installParams);
        if (!SetupDiGetDeviceInstallParams(set, ref data, ref installParams)) return Fail("SetupDiGetDeviceInstallParams");
        installParams.DriverPath = infPath;
        installParams.Flags |= DI_ENUMSINGLEINF;
        installParams.FlagsEx |= DI_FLAGSEX_ALLOWEXCLUDEDDRVS;
        if (!SetupDiSetDeviceInstallParams(set, ref data, ref installParams)) return Fail("SetupDiSetDeviceInstallParams");
        if (!SetupDiBuildDriverInfoList(set, ref data, SPDIT_CLASSDRIVER)) return Fail("SetupDiBuildDriverInfoList");
        for (int driverIndex = 0; ; driverIndex++) {
            var driver = new SP_DRVINFO_DATA(); driver.cbSize = Marshal.SizeOf(driver);
            if (!SetupDiEnumDriverInfo(set, ref data, SPDIT_CLASSDRIVER, driverIndex, ref driver)) return "FAILED WinUsb Device not found in " + infPath;
            if (driver.Description.IndexOf("WinUsb", StringComparison.OrdinalIgnoreCase) < 0) continue;
            if (!SetupDiSetSelectedDriver(set, ref data, ref driver)) return Fail("SetupDiSetSelectedDriver");
            bool needReboot;
            if (!DiInstallDevice(IntPtr.Zero, set, ref data, ref driver, 0, out needReboot)) return Fail("DiInstallDevice");
            return "INSTALLED WinUSB" + (needReboot ? " (reboot needed)" : "");
        }
    }
}
'@

try {
    Write-Log "WinUSB install for Huawei 12D1:151D - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Log "(Windows may ask to confirm an 'incompatible driver' warning: answer Oui.)"
    foreach ($interfaceNumber in $InterfaceNumbers) {
        $hardwareId = 'USB\VID_12D1&PID_151D&MI_{0:D2}' -f $interfaceNumber
        $results = [WinUsbBinder]::Bind($hardwareId, "$env:WINDIR\INF\winusb.inf")
        if (-not $results) { Write-Log "$hardwareId : no such device plugged in (is the dongle in modem mode?)"; continue }
        foreach ($result in $results) {
            $instanceId, $outcome = $result -split '\|', 2
            Write-Log "$hardwareId : $outcome  [$instanceId]"
            if ($outcome -notmatch '^(INSTALLED|ALREADY)') { continue }
            $parametersKey = "HKLM:\SYSTEM\CurrentControlSet\Enum\$instanceId\Device Parameters"
            $existingGuids = (Get-ItemProperty -Path $parametersKey -ErrorAction SilentlyContinue).DeviceInterfaceGUIDs
            if (-not $existingGuids) {
                New-ItemProperty -Path $parametersKey -Name DeviceInterfaceGUIDs -PropertyType MultiString -Value @($WinUsbInterfaceGuid) -Force | Out-Null
                Write-Log "  added DeviceInterfaceGUIDs"
            }
            if ($outcome -match '^INSTALLED' -or -not $existingGuids) {
                $restartOutput = pnputil /restart-device "$instanceId" 2>&1 | Out-String
                Write-Log ("  restart: " + ($restartOutput.Trim() -split "`r?`n" | Select-Object -Last 1))
            }
        }
    }
} catch {
    Write-Log "ERROR: $_"
} finally {
    $logLines | Set-Content -Path $LogPath -Encoding UTF8
}
