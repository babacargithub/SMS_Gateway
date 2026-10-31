# Windows setup — working notes

Running log of everything done to make the app work on Windows (PC « DESKTOP-46BDQQI », Windows 11 Pro 26200,
Python 3.12.10). Dead ends are kept on purpose: they explain why the final setup looks the way it does.
**Goal:** once everything works, turn the « Final setup » section into an installer step that the packaged app runs
to prepare a new PC automatically.

## Final setup (what the automatic installer must do) — current state

| # | Step | How (today, by hand) | Admin? | Status |
|---|------|----------------------|--------|--------|
| 1 | Python packages: `pyusb`, `libusb-package` (bundles `libusb-1.0.dll` 1.0.30), `PySide6`, `openpyxl` | `python -m pip install --user pyusb libusb-package PySide6 openpyxl` | no | ✅ (packaged app: bundle them, nothing to install) |
| 2 | Plug the dongle in and wait for modem mode `12D1:151D` | automatic on this PC (see « Mode switch ») | — | ✅ |
| 3 | Bind Microsoft's WinUSB driver to interface 0 (`MI_00`) and add `DeviceInterfaceGUIDs` | `scripts/windows/install_winusb_driver.ps1` (asks UAC, may show the « pilote incompatible » warning → Oui) | **yes** | ✅ |
| 4 | Do NOT bind interface 3 (see « Interface 3 ») | — | — | ✅ |

Data folder on Windows: `%APPDATA%\SMS Gateway` (database, `operators.json`). Secrets (Orange credentials, Globe
Transports token): Windows Credential Manager, generic credentials `SMS Gateway/<account>`.

**Automated since 2026-10-31 (log entry 18):** the installer `SMS_Gateway_Setup_<version>.exe` does step 3 for every
USB port with a signed driver package (no prompt after install), and the app switches CD-ROM mode itself (step 2 on
PCs without Huawei software). Step 1 is bundled. Remaining unknown: the app-side mode switch (not yet verified).

## Hardware facts on Windows

- Dongle: Huawei **E3131A**, firmware 21.157.41.00.314, IMEI 865456011612676.
- SIM currently in it: **Orange** (`AT+COPS?` → « SN ALIZE », network code `60801`), not Expresso.
- Modem mode `12D1:151D` exposes 5 interfaces: `MI_00`–`MI_03` vendor class `FF/12/FF`, `MI_04` mass storage (SD card).
  Interface 0 endpoints: `0x81` (interrupt/bulk in), `0x82` bulk in, `0x01` bulk out.

## Log

### 2026-10-31

1. **COM-port idea (pyserial).** Wrote `scripts/windows_port_probe.py`. Result: **0 serial ports.** Device history
   showed Windows once bound a « HUAWEI Mobile Connect - USB Device (COM3) » to the CD-ROM-mode device (`14FE`),
   but nothing in modem mode.
2. **Mode switch.** Plugged in, the dongle went to modem mode `151D` **by itself** — no helper, no admin. (On the Mac
   this needed a root helper.) Probable cause: old Huawei drivers already installed on this PC (driver store has
   `ewser2k.inf`, `ewmdm2k.inf`, `ew_usbenumfilter`, … from 2010–2011). **Not yet checked on a clean PC.**
3. **No driver for the AT interfaces.** `MI_00`–`MI_03` = « HUAWEI Mobile », problem code **28** (no driver). The old
   Huawei INFs only match `Subclass_01`, these interfaces are `Subclass_12` → no COM port can appear.
4. **Decision:** use WinUSB + libusb (same raw-USB code as on the Mac) instead of hunting for an old Huawei driver.
5. `pip install --user pyusb libusb-package` → pyusb 1.3.1, libusb 1.0.30.
6. **WinUSB on `MI_00` without Zadig:** SetupAPI from PowerShell (C# via Add-Type): `DI_ENUMSINGLEINF` on
   `C:\Windows\INF\winusb.inf`, `SetupDiBuildDriverInfoList(SPDIT_CLASSDRIVER)`, pick « WinUsb Device »,
   `DiInstallDevice`. Exactly what Device Manager's « Choisir parmi une liste » does. Needs UAC; Windows shows a
   « pilote incompatible » warning that must be accepted (the script waited on it until the owner clicked Oui).
7. **libusb could not open the device: `USBError: Entity not found`.** Cause: a hand-bound WinUSB has no
   `DeviceInterfaceGUIDs` value, so no device interface is published and libusb cannot find it. Fix: add
   `HKLM\SYSTEM\CurrentControlSet\Enum\<instance id>\Device Parameters\DeviceInterfaceGUIDs` (REG_MULTI_SZ, any GUID —
   we use `{8C3E9D4A-5B1F-4E62-9A7D-2F6B1C0E5A13}`), then `pnputil /restart-device <instance id>`.
8. **AT works over USB:** `ATI`, `AT+CGSN`, `AT+CPIN?` READY, `AT+CSQ` 11, `AT+CREG?` 0,1, `AT+COPS?` « SN ALIZE ».
9. Note: `python -I` hides `--user` packages (`ModuleNotFoundError: libusb_package`). Run our own scripts without `-I`.
10. **Interface 3 (dead end).** Bound WinUSB to `MI_03` too (on the Mac it carries the `+CMTI` new-SMS announcements).
    Then **every transfer on interface 0 failed** (`USBError: Input/Output Error`, libusb debug: « failure to read
    interface number … using default value » and « I/O error 87 »). Cause: the Huawei upper filter
    **`ew_usbenumfilter`** (`oem53.inf`) on the composite device renames the interfaces
    `USB\VID_12D1&SUBCLASS_12&PROT_FF\…` instead of `…&MI_00\…`; libusb reads the interface number from that name, so
    with two WinUSB interfaces it mixes them up. libusb 1.0.30 is the newest, no fix there.
    **Reverted:** `pnputil /remove-device <MI_03 instance id>` + `pnputil /scan-devices` → back to no driver; interface 0
    works again. The driver script now binds only `MI_00` by default.
    Possible later fix: remove the `ew_usbenumfilter` filter (then the names get `MI_xx` and both could be bound) —
    not done, it may be what makes the automatic mode switch work.
11. **Script bug:** `powershell -File` cannot pass an `[int[]]` parameter to the elevated copy (`0,3` arrived as `3`).
    The script takes the interface list as text (`-Interfaces "0,3"`).
12. **App code ported** (branch `dev-windows-support`):
    - `common/platform_support.py` — `IS_WINDOWS` / `IS_MACOS`.
    - `devices/dongle.py` — libusb from `libusb_package` on Windows; claiming a driverless interface raises
      `NotImplementedError` on Windows (not `USBError`) → both claim sites catch `INTERFACE_NOT_CLAIMABLE_ERRORS`.
    - `devices/dongle_monitor.py` — on Windows no root helper: a dongle still in CD-ROM mode after the grace period
      shows « Le dongle est resté en mode CD-ROM : débranchez-le puis rebranchez-le ».
    - `common/app_paths.py` — `%APPDATA%\SMS Gateway`.
    - `common/keep_awake.py` — `keep_computer_awake`: `SetThreadExecutionState` on Windows, IOKit on macOS.
    - `common/sound_player.py` — `winsound` on Windows, `afplay` on macOS (device + new-SMS sounds use it).
    - `common/secret_store.py` — Windows Credential Manager / macOS Keychain; used by the Orange credentials and the
      Globe Transports token stores.
13. **App device layer on Windows:** `DongleConnection.open()` → « Prêt », IMEI, operator code 60801, signal −91 dBm,
    `AT+CNMI` accepted, no notification port (expected, see 10).
14. **Tests:** `QT_QPA_PLATFORM=offscreen python -m unittest discover tests` → 151 tests OK on Windows.
15. **App launched** (`python app.py` from the repo root): window « SMS Gateway » opens, French UI, no error output;
    database created at `%APPDATA%\SMS Gateway\sms_gateway.db`.
16. **Verified by the owner in the app:** Appareils page shows the dongle « Prêt »; SIM page « Vérifier le solde »
    works (Orange SIM); a real SMS sent to the owner's own number went through.
17. **Receiving verified by the owner:** an SMS sent to the dongle's SIM appeared in Réception (read by the timer, no
    interface 3). Still to check on Windows: long/accented SMS (PDU mode), Orange gateway credentials in the Credential
    Manager, Globe Transports login.
18. **Packaging for a non-technical recipient** (owner's choice: the installer sets the driver up; tests on this PC only).
    - **Driver package** `packaging/windows/driver/sms_gateway_winusb.inf`: WinUSB (inbox, `Include=winusb.inf`) for
      `USB\VID_12D1&PID_14FE` (CD-ROM mode) and `USB\VID_12D1&PID_151D&MI_00` (AT port), with `DeviceInterfaceGUIDs`
      in the INF. Matching hardware IDs → no « pilote incompatible » warning, works on every USB port.
    - **Signing on the target PC** (`packaging/windows/install_dongle_driver.ps1`, run elevated by the installer):
      `New-SelfSignedCertificate` (unique per PC, non-exportable) → public part into LocalMachine Root +
      TrustedPublisher → `New-FileCatalog` + `Set-AuthenticodeSignature` → private key deleted → `pnputil /add-driver
      /install`. **Tested here: Windows accepted the package** (`oem64.inf`); MI_00 moved to it and kept working.
      First run reported « UnknownError » because the catalog was signed before the certificate was trusted → order
      fixed (trust first, then sign, and the script now fails if the signature is not « Valid »).
      `-Uninstall` removes the package (`Get-WindowsDriver` → `pnputil /delete-driver /uninstall /force`) and the certs.
    - **Surprise:** on this PC our package also took the CD-ROM mode `14FE` (instead of Huawei's `ew_hwusbdev`,
      `oem55.inf`; change pending until replug/reboot). So the app must now do the switch itself here too, which lets
      us test the « clean PC » path on this PC.
    - **App-side mode switch on Windows:** `dongle.switch_cdrom_dongle_in_process` sends the Huawei message (same bytes
      as the Mac helper) through WinUSB, no admin; the monitor retries every 20 s, shows « Changement de mode », and
      an error after 25 s. **Not yet verified** (needs an unplug/replug with the new driver).
    - **Log file** `%APPDATA%\SMS Gateway\logs\sms_gateway.log` (rotating, uncaught exceptions included): the only
      trace in the packaged app, which has no console.
    - **Build** (`packaging/windows/build_windows_installer.ps1`): icon (.ico from the app's SVG) → PyInstaller one
      folder, no console (`sms_gateway.spec`) → Inno Setup 6 (`sms_gateway_setup.iss`, French, admin, runs the driver
      script after copying files, French error message with the log path if it fails, uninstaller removes the driver).
      Build tools on this PC: `pip install --user pyinstaller` (6.22.3), `winget install JRSoftware.InnoSetup --scope
      user`. Output: `packaging/windows/dist/SMS_Gateway_Setup_1.0.0.exe` (35 MB; app folder 117 MB).
      PyInstaller warning « No backend available » is harmless: `libusb-1.0.dll` is bundled (checked).
      The `.iss` must be UTF-8 **with BOM** (French accents).
    - Packaged exe started on this PC: window opens, log written. Dongle not yet checked in the packaged app.

