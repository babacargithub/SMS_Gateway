# SMS Gateway — project brief

Internal **desktop app** for macOS, inspired by SMS Caster, that sends (and later receives) SMS
through Huawei USB dongles plugged into one Mac. Internal use only, not distributed.
You open the app, import contacts from Excel/CSV, compose, and send. There is **no server, no HTTP API
and no daemon** in the current design. External integrations (Laravel apps such as Bayal Services and
Global Transports, contact-fetching APIs) may come much later; keep the core UI-independent so they can be added.

## Decisions (already made)

- **Platform:** macOS only, one Mac (Apple Silicon M2). No Windows, no installers, no licensing concerns.
- **Language / UI:** Python + **PySide6 (Qt)**, a single process. Reuses the proven raw-USB code directly.
  Rejected: Vue served over HTTP, Swift rewrite, Electron/Tauri with a sidecar.
- **Architecture:** UI → services → dongle manager (one thread owning all USB) → device layer (pyusb/AT).
  The UI never touches USB; it talks to the monitor thread through Qt signals and a thread-safe queue.
- **Storage:** SQLite, single file, backed up regularly (not built yet).
- **Runs as:** the logged-in user, launched by hand. No launchd, no root for the app itself.
- **Contacts:** imported from Excel/CSV only (no external API for now).
- **UI language: French only.** Every label, message, tooltip, error shown to the user, and the Qt standard
  buttons (Oui/Non/Annuler, via `qtbase_fr.qm` loaded in `app.py`), must be in French. Code, comments and
  identifiers stay in English. Use French typography: « guillemets », space before `:` `?` `!`, `8 813`
  (narrow no-break space) and `30/09/2026`. Shared wording helpers live in `french_labels.py`.
- **Contacts are organised in named lists** (e.g. "Fidèles clients"). The list name is asked at import; a file can
  also be imported into an existing list (numbers already in that list are skipped). The same number may be in
  several lists (dedupe when a send targets several lists). Deleting a list deletes its contacts (ON DELETE CASCADE);
  every delete asks for confirmation with "Non" as the default button. List names are unique ignoring case and
  accents-case (compared with `casefold()` in Python, because SQLite `NOCASE` only handles A–Z).

## Hardware facts (verified by hand)

- Dongle: Huawei, USB vendor `0x12d1`.
  - Plugged in, it appears as a virtual CD-ROM: product `0x14fe`, volume `/Volumes/Internet Everywhere`
    (disk shows up as e.g. `disk8`). The bundled Huawei installer is Intel-only and useless — never rely on it.
  - Mode switch: eject the volume, then write this message to the bulk OUT endpoint of interface 0:
    `55534243123456780000000000000011062000000100000000000000000000`
    → re-enumerates about 10 s later as product `0x151d` (modem mode).
  - **The switch is not persistent**: every replug returns to `0x14fe`.
- In modem mode macOS creates **no** `/dev/cu.*` port (no driver). All communication is raw USB via
  pyusb + libusb (`/opt/homebrew/lib/libusb-1.0.dylib`).
- The AT port is **interface 0** (class `0xFF`, bulk IN/OUT). Found by probing vendor-class interfaces
  and keeping the one that answers `AT` with `OK`.
- **Root (tested):** the CD-ROM → modem **switch needs root** (`USBError: Access denied`, even after
  `diskutil eject`). **Modem mode works fine as a normal user** (IMEI, signal, SIM, network all read without sudo).
  So only the switch needs privilege — planned fix: a small privileged helper for that one step, so the app
  never runs as root. Until then, run `sudo ~/usbenv/bin/python huawei_switch.py` after each replug.
- `diskutil eject` can hang (it timed out at 15 s once) — always catch `subprocess.SubprocessError`.
- The dongle may emit unsolicited lines (`^RSSI`, `^HCSQ`, `^BOOT`…); flush before each command.
  `AT^CURC=0` disables them.
- Identity read from the dongle: IMEI via `AT+CGSN`, operator via `AT+COPS?` (returns `expresso`),
  SIM via `AT+CPIN?`, registration via `AT+CREG?` (1 home, 5 roaming).

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
  → `+CMGS: <ref>` then `OK`. Confirmed working end to end with the scripts. The same flow is now in the
  app (`DongleConnection.send_text_message`) but **not yet verified from the Compose screen**.
- `+CMS ERROR: 302` was caused by a malformed number. Numbers must be `+221` + 9 digits, no spaces.
- Error replies: `+CMS ERROR: n` ends in digits, so detect final replies with `"ERROR" in reply`, not `endswith("ERROR")`.
- Signal: `AT+CSQ` returned 11–14 (≈ −91 to −85 dBm) earlier and 4 bars (−67 dBm) later.
  dBm = −113 + 2 × value; 99 = unknown.
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

## Code map

Desktop app (current):
- `app.py` — entry point and `MainWindow` (sidebar + stacked pages). Owns the `DongleMonitor` thread.
- `sidebar.py` — `Sidebar`: colored SVG icons, separators (custom delegate, none after the last item),
  order: Composer, Réception, Contacts, Boîte d'envoi, Historique, Appareils. Pages are matched **by name** in
  `app.py`, so renaming a sidebar entry means updating the key there too.
- `icons.py` — shared SVG → colored `QIcon` helper (`build_colored_icon`) and icon shapes (edit, trash).
- `french_labels.py` — French wording helpers (contact count with plural/thousands, dates, SIM status).
- `contact_import.py` — pure: read `.xlsx`/`.csv` (`;` separator, latin-1 fallback), guess phone/name columns,
  validate + dedupe rows into an `ImportPlan`; other columns are kept as custom fields (future `{variables}`).
- `contact_store.py` — SQLite (`~/Library/Application Support/SMS Gateway/sms_gateway.db`): `contact_lists` +
  `contacts(list_id, UNIQUE(list_id, phone_number))`. Short-lived connection per call. Auto-migrates the older
  list-less schema into one list, writing a `.before-lists.bak` copy first.
- `contacts_page.py` — opens on the lists (name, count, date, rename ✎ / delete 🗑 icon buttons); clicking a list
  shows its contacts (search, delete selection). `import_contacts_dialog.py` — column mapping, validation report,
  destination list (new name or existing list).
- `compose_page.py` — **campaign composer**: tick one or more contact lists, write a message with variables, live
  preview/validation, **Lancer** queues one personalised message per contact (state `WAITING`) and jumps to the Outbox.
  It does **not** send anything itself. The old single-number direct send was removed from this screen.
- `message_template.py` — pure: `{variables}`; supported for now: `{nom}`, `{téléphone}`, `{détails}` (case and accents
  ignored). `{détails}` = all extra imported columns as `Ville : Dakar, Montant : 5000` (ASCII `, ` separator, because
  text mode turns `·` into `?`). Unknown variables block Lancer. Extra columns 3–10 as variables are planned.
- `campaign_builder.py` — pure: template + recipients → `CampaignDraft` (a number in several lists is kept once,
  counts of empty values, messages over 160 characters or empty). Blocks Lancer on unknown variables / too long / empty.
- `outbox_store.py` — SQLite `campaigns` + `messages` (recipient, number and final text are **copied**, so deleting a
  contact or list never changes the queue). States: `WAITING`, `SENDING`, `SENT`, `FAILED`, `UNKNOWN`.
- `outbox_page.py` — Boîte d'envoi: **Envoyer / Pause (Reprendre) / Arrêter** on top, progress bar, "simulation" banner,
  per-state counters, table (N°, state icon only with tooltip, recipient, number, message, list, date), state filter,
  "Vider les messages en attente" (confirm, default Non). Rows update in place while sending; reloads when shown.
- `message_sender.py` — the seam for delivery: `send(number, text) -> SendResult`. **`SimulatedMessageSender` is the only
  implementation today** (random success/failure, nothing is sent; `is_simulation = True` shows the banner). Real
  sending = a dongle-backed sender with the same interface; the worker and screens stay unchanged.
- `sending_worker.py` — `SendingWorker(QThread)`: claims the oldest `WAITING` message (→ `SENDING`), sends, records
  SENT/FAILED/UNKNOWN. Pause/stop never cut a message in half (the in-flight one finishes first); stop leaves the rest
  `WAITING`. Messages cut off by a crash (`SENDING` at startup) become `UNKNOWN` and are never resent automatically.
- `devices_page.py` — table of dongles (state, IMEI, operator, signal bars + dBm, SIM, network, USB) and Refresh.
- `dongle.py` — **device layer, no UI**: USB scan/switch, `DongleConnection` (AT port, timeouts, flush),
  `send_text_message`, live status reading, `SendRejected` / `SendOutcomeUnknown`.
- `dongle_monitor.py` — `DongleMonitor(QThread)`: scans USB every 3 s, auto-switches CD-ROM dongles,
  health check every 30 s (3 misses → reconnect), Refresh (`request_immediate_rescan`), and a send entry point
  (`submit_text_message` → `sms_send_finished`) that **nothing calls yet** — the future dongle-backed sender will use it.
  The scan loop catches and logs every exception so the thread can never die silently.
- `sms_text.py` — pure helpers: `normalize_senegal_number`, `sanitize_for_text_mode` (ASCII only).
- Réception and Historique are empty placeholders for now.

Scripts (still useful, run outside the app):
- `huawei_switch.py` — one-shot CD-ROM → modem switch (needs sudo).
- `huawei_at.py` — interactive AT shell, plus `send <number> <text>`.

Legacy (superseded by the desktop design, kept only as reference — mine it for queue/retry ideas, then delete):
- `gateway.py` — old FastAPI service with SQLite queue and worker. Never run on this Mac.
- `sn.globalsoft.smsgateway.plist` — old launchd daemon definition. Not used.

## Environment

- Python venv: `~/usbenv` (pyusb, PySide6; fastapi/uvicorn are only for the legacy gateway). Conda `(base)`
  is active in the shell — always call `~/usbenv/bin/python` explicitly.
- Run the app: `~/usbenv/bin/python app.py`. Restart it after code changes (it does not hot-reload).
- Homebrew libusb at `/opt/homebrew/lib`.
- Preview UI without a display: `QT_QPA_PLATFORM=offscreen`, `widget.grab().save(png)`, then view the PNG.
- zsh aborts a whole command when one glob matches nothing (`ls /dev/cu.* /dev/tty.usb*`).
- Only one process can own the dongle: close the app before using `huawei_at.py`.

## Reliability requirements (dongle plugged in for days)

- Per-dongle state machine: Absent → Detected (CD-ROM) → Switching → Initializing → Ready → Busy
  → Error → Disconnected. Implemented: Detected, Switching, Initializing, Ready, Error. **Not yet:**
  Busy, Disconnected, and logging every transition.
- Rescan USB every few seconds (don't rely only on hotplug events). Done.
- Health check every 30 s (`AT+CSQ`, `AT+CPIN?`, `AT+CREG?`, `AT+COPS?`); 3 missed replies → reset and
  reconnect, even if the USB device still appears present. Done.
- Timeouts on every read/write. No blocking forever. Done for USB; a send waits up to 30 s.
- **Never duplicate a send.** A message is `sent` only on `+CMGS`; `not sent` only on a definite error;
  anything else (timeout, USB error after the text was written) is **unknown** and must never be retried
  automatically — the user checks the recipient. The Outbox must store this third state.
- Identify dongles by IMEI (`AT+CGSN`), not by USB location. The IMEI is read but the monitor still keys
  connections by USB location — switch to IMEI when multi-dongle support arrives.
- Structured logs with rotation. Not yet (only `logging.exception` in the scan loop).
- Mac must not sleep during a campaign: hold a power assertion (`caffeinate` / IOKit) while sending;
  `sudo pmset autorestart 1` is no longer needed because the app is not a daemon.
- Use direct USB ports or a powered hub (dongles draw up to 500 mA).
- Quitting mid-campaign must warn, then pause and persist the queue.

## Roadmap

1. ✅ App shell: sidebar with icons, Devices screen (state, IMEI, operator, signal, SIM, network, Refresh).
2. ✅ Compose → queue: lists + template + Lancer → messages `WAITING` in the Outbox (nothing is sent yet).
3. Privileged helper for the mode switch (so replug recovers without a manual `sudo`).
4. Queue worker: ✅ simulated (Envoyer/Pause/Arrêter, states SENDING → SENT/FAILED). **Next: real sending** — a dongle-backed
   `MessageSender` (steady 5–10 s rate, `+CMGS` → SENT, refusal → FAILED, no confirmation → UNKNOWN, dongle absent →
   pause), plus a confirmation before the first real run. Then Historique.
   (Outbox = the queue; Historique = archive of what was sent/received — confirm with the owner.)
5. Contacts + import: ✅ named lists, Excel/CSV import, column mapping, `+221` normalization, duplicate/invalid
   report, rename/delete. Still to do: blacklist, edit a single contact, virtual table for 10k+ contacts.
6. Campaigns extras: pause/resume/stop per campaign, sleep prevention, more variables (columns 3–10), multipart SMS.
7. Devices extras: credit via USSD `*222#` (needs GSM 7-bit packed hex unpacker), test-SMS button.
8. Inbox: CNMI notifications, UCS2 decode, PDU-mode multipart reassembly, auto-blacklist on "STOP".
9. Extras: UCS2 + multipart sending, delivery reports (`AT+CSMP`, `AT+CNMI`), scheduling, multi-dongle
   load balancing, low-credit alerts, per-SIM hourly/daily caps, optional HTTP API for the Laravel apps.

## Working rules

- Only send test SMS to the owner's own number (set via env `TEST_NUMBER`), never to real contacts.
  Claude must never send a real SMS on its own; ask the owner to test sends.
- Never automate choices in operator USSD menus that could purchase bundles.
- Keep a steady send rate; prepaid SIMs sending too fast risk being blocked by the operator.
- Use descriptive names for variables and functions, even when longer (owner's global rule).
- Keep USB/AT code out of UI files; UI talks to the monitor via signals only.
