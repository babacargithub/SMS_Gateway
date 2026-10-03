# How to run the app

SMS Gateway is a macOS desktop app (Python + PySide6) that sends and receives SMS through Huawei USB dongles.

## Every time (normal use)

1. Open **iTerm** (see "Why iTerm" below).
2. Start the app:

   ```
   cd ~/Documents/Projects/Tools/SMS_Gateway
   ~/usbenv/bin/python app.py
   ```

3. Plug in the dongle (or leave it plugged in). It appears on the **Appareils** page by itself within about
   10 to 15 seconds: it is first « Détecté » (virtual CD-ROM), then switched to modem mode and « Prêt ».
   You do not have to unplug it again or type any command.

Always call `~/usbenv/bin/python` explicitly: the Conda `(base)` environment is active in the shell and is not the right one.
Restart the app after any code change (it does not hot-reload). Only one program can use the dongle at a time, so close the
app before using `scripts/huawei_at.py`.

### Why iTerm

Switching the dongle from CD-ROM mode to modem mode needs root. macOS also only lets a process that belongs to your login
session open the dongle's storage interface: a background daemon or agent is denied even as root. So the app itself asks the
root helper to do the switch, and it must be started from your terminal session.

If the app is started some other way (Finder, a launchd job, a packaged `.app`), the dongle will stay in CD-ROM mode and
Appareils shows « Le changement de mode n'a pas abouti ».

## One-time setup (already done on this Mac)

You only need this on a new machine, or after a reinstall.

1. **libusb**: `brew install libusb` (the app looks for `/opt/homebrew/lib/libusb-1.0.dylib`).
2. **Python environment** `~/usbenv` with `pyusb`, `PySide6` and `openpyxl`.
3. **Mode-switch helper** (needs your admin password once). Read the header of the script first, it says exactly what it installs:

   ```
   cd ~/Documents/Projects/Tools/SMS_Gateway
   sudo ./mode_switcher/install_switcher.sh
   ```

   It installs:
   - `/usr/local/libexec/sms-gateway/`: a root-owned copy of the helper and its own Python environment, so nothing you can edit is
     ever run as root;
   - `/etc/sudoers.d/sms-gateway-mode-switcher`: ONE passwordless rule for your user, for the exact command
     `<helper python> <helper> --switch-now`. That command takes no arguments and only switches dongles that are in CD-ROM mode.

   Both survive reboots and replugs. Nothing runs in the background.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| « Assistant de changement de mode non installé » | Run `sudo ./mode_switcher/install_switcher.sh` again. This can happen after a macOS or Xcode Command Line Tools update that replaces `/usr/bin/python3` (the helper's environment is built from it). It is safe to rerun. |
| « Le changement de mode n'a pas abouti » | The app was probably not started from iTerm. Quit it and start it from iTerm. |
| The dongle stays « Détecté » / nothing happens | Click **Actualiser** on Appareils. Check the cable and port (use a direct port or a powered hub). |
| Try the switch by hand | `sudo -n /usr/local/libexec/sms-gateway/venv/bin/python /usr/local/libexec/sms-gateway/dongle_mode_switcher.py --switch-now` |
| Fallback if the helper is broken | `sudo ~/usbenv/bin/python scripts/huawei_switch.py` (after each replug), then click **Actualiser**. |
| `libusb introuvable` | `brew install libusb` |

## Uninstall the helper

```
sudo ./mode_switcher/uninstall_switcher.sh
```

It removes the sudo rule and the root-owned files (and any older daemon or agent from earlier versions).

## Good to know

- The virtual CD-ROM mode is **not persistent**: every replug returns to CD-ROM mode, and the app switches it again.
- The SIM in the dongle is Expresso (Senegal), a prepaid SIM: keep the sending pace steady (the Outbox already waits 5 to 10 seconds between messages).
- Only send test SMS to your own number.
- The database is `~/Library/Application Support/SMS Gateway/sms_gateway.db`. Back it up regularly.
