# SMS Gateway — project brief

Internal SMS tool inspired by SMS Caster (sidebar: Compose, Contacts, Outbox, Devices, Inbox),
sending through Huawei USB dongles plugged into one Mac. Internal use only, not distributed.
Other internal apps (Bayal Services, Global Transports — both Laravel) will call its HTTP API.

## Decisions (already made)

- **Platform:** macOS only, one Mac (Apple Silicon M2). No Windows, no installers, no licensing concerns.
- **Device service:** Python (already proven on this Mac). Harden it rather than rewrite it.
- **UI:** Vue.js app served by the Python service at http://localhost:8000.
- **Storage:** SQLite, single file, backed up regularly.
- **Integration:** local HTTP API with an `X-API-Key` header, used by the UI and the Laravel apps.
- **Runs as:** launchd LaunchDaemon (root), started at boot, restarted on crash.

## Hardware facts (verified by hand)

- Dongle: Huawei, USB vendor `0x12d1`.
  - Plugged in, it appears as a virtual CD-ROM: product `0x14fe`, volume `/Volumes/Internet Everywhere`.
    The bundled Huawei installer is Intel-only and useless on this Mac — never rely on it.
  - Mode switch: eject the volume, then write this message to the bulk OUT endpoint of interface 0:
    `55534243123456780000000000000011062000000100000000000000000000`
    → re-enumerates about 10 s later as product `0x151d` (modem mode).
  - **The switch is not persistent**: every replug returns to `0x14fe`.
- In modem mode macOS creates **no** `/dev/cu.*` port (no driver). All communication is raw USB via
  pyusb + libusb (`/opt/homebrew/lib/libusb-1.0.dylib`).
- The AT port is **interface 0** (class `0xFF`, bulk IN/OUT). Found by probing vendor-class interfaces
  and keeping the one that answers `AT` with `OK`.
- Scripts currently run with `sudo`. Not yet tested whether root is actually required — worth checking.
- The dongle may emit unsolicited lines (`^RSSI`, `^HCSQ`, `^BOOT`…); flush before each command.
  `AT^CURC=0` disables them.

## SIM facts

- The SIM in the dongle is **Expresso** (Senegal), not Orange.
- Balance: USSD `*222#`. Reply mixes an advert with the balance, e.g.
  `…Vous avez 200 Fcfa de credit` → parse the amount with a pattern, never display raw text as the balance.
  The same code sometimes returns `Operation ratee!` (transient failure, status 0) → retry later.
- `#111#` opens the Expresso offers portal; options 1–5 are **purchases**. Never automate choices there.
- Operator USSD codes must be configurable per SIM/operator, not hard-coded.

## AT command behaviour observed on this dongle

- Echo is on by default → send `ATE0` once at connect.
- `AT+CMGF=1` (text mode) works. Send: `AT+CMGS="+221XXXXXXXXX"` → `>` prompt → text + `\x1a`
  → `+CMGS: <ref>` then `OK`. Confirmed working end to end.
- `+CMS ERROR: 302` was caused by a malformed number. Numbers must be `+221` + 9 digits, no spaces.
- Signal: `AT+CSQ` returned 11–14 (≈ −91 to −85 dBm). dBm = −113 + 2 × value; 99 = unknown.
- Reading SMS: `AT+CPMS="SM","SM","SM"` then `AT+CMGL="ALL"`.
  - Bodies with accents come back as **UCS2 hex** (decode as UTF-16BE).
  - Long SMS arrive as **separate entries** in text mode (same sender + timestamp, text cut mid-word).
    Reliable reassembly requires PDU mode (`AT+CMGF=0`, `AT+CMGL=4`) and the UDH concat header.
  - Parts can be missing; indexes are sparse; reading marks messages as read.
  - Strategy: read → store in SQLite → delete from SIM (`AT+CMGD=<i>`). SIM capacity is small.
  - New-message notification: `AT+CNMI=2,1,0,0,0` → `+CMTI: "SM",<index>`.
- USSD: `AT+CUSD=1,"<GSM 7-bit packed hex>",15`. This dongle expects **packed hex**, not plain text.
  - Examples: `*222#` = `2A994C3602`, `#111#` = `A3582C3602`.
  - `OK` comes immediately; the `+CUSD: <status>,"<hex>",15` reply arrives **asynchronously** seconds later.
  - Replies are GSM 7-bit packed hex → need a GSM 03.38 unpacker.
  - Status: 0 final, 1 menu awaiting reply, 2 ended by network, 4 not supported. Close with `AT+CUSD=2`.
  - Sessions time out after ~20–30 s. **Unresolved:** a menu reply sent as `"36"` (packed `6`) was
    rejected with "Entrée non valide" — could be the format or a session timeout. Needs testing.

## Existing code in this folder

- `huawei_switch.py` — one-shot CD-ROM → modem mode switch.
- `huawei_at.py` — interactive AT shell over raw USB, plus `send <number> <text>`. Works.
- `gateway.py` — FastAPI service: SQLite queue, background worker (auto mode switch, reconnect,
  retries, delay between sends), endpoints `POST /sms`, `POST /sms/bulk`, `GET /sms`, `GET /sms/{id}`,
  `GET /status`. Text mode only; strips accents; max 160 chars.
  **Not yet tested on the Mac** — first task is to run it and fix what breaks.
- `sn.globalsoft.smsgateway.plist` — launchd daemon definition (API key placeholder to replace).

## Environment

- Python venv: `~/usbenv` (pyusb, fastapi, uvicorn). Conda `(base)` is active in the shell —
  always call `~/usbenv/bin/python` explicitly.
- Homebrew libusb at `/opt/homebrew/lib`.
- zsh aborts a whole command when one glob matches nothing (`ls /dev/cu.* /dev/tty.usb*`).
- Only one process can own the dongle: stop the gateway before using `huawei_at.py`.

## Reliability requirements (dongle plugged in for days)

- Per-dongle state machine: Absent → Detected (CD-ROM) → Switching → Initializing → Ready → Busy
  → Error → Disconnected. Log every transition.
- Rescan USB every few seconds (don't rely only on hotplug events).
- Health check every 30–60 s (`AT`, `AT+CSQ`, `AT+CREG?`); 2–3 missed replies → reset and reconnect,
  even if the USB device still appears present.
- Timeouts on every read/write. No blocking forever.
- A message stuck in `sending` after a disconnect is requeued only if no `+CMGS` was received (no duplicates).
- Identify dongles by IMEI (`AT+CGSN`), not by USB location.
- Structured logs with rotation.
- Mac must not sleep (`sudo pmset -c sleep 0`) and should restart after power loss (`sudo pmset autorestart 1`).
- Use direct USB ports or a powered hub (dongles draw up to 500 mA).

## Roadmap

1. Run and harden `gateway.py`: state machine, health checks, IMEI identity, logs, test without sudo.
2. Devices screen (Vue): state, operator, signal bars + dBm, SIM status, credit (USSD balance), test SMS.
3. Outbox + Compose: single and bulk send, CSV import, templates with `{variables}`, char/part counter.
4. Contacts: groups, custom fields, CSV/Excel import, `+221` normalization, blacklist.
5. Inbox: CNMI notifications, UCS2 decode, PDU-mode multipart reassembly, auto-blacklist on "STOP".
6. Extras: UCS2 + multipart sending, delivery reports (`AT+CSMP`, `AT+CNMI`), scheduling, campaigns
   with pause/resume, multi-dongle load balancing, low-credit alerts, per-SIM hourly/daily caps.

## Working rules

- Only send test SMS to the owner's own number (set via env `TEST_NUMBER`), never to real contacts.
- Never automate choices in operator USSD menus that could purchase bundles.
- Keep a steady send rate; prepaid SIMs sending too fast risk being blocked by the operator.
