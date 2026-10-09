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
  (narrow no-break space) and `30/09/2026`. Shared wording helpers live in `common/french_labels.py`.
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
  So only the switch needs privilege. **Also tested (debugged with root):** a LaunchDaemon cannot do it even as root — the kernel logs
  `(Sandbox) System Policy: deny iokit-open-service IOUSBHostInterface` for any process outside a login session; Full Disk
  Access on Python did not help; `launchctl asuser` from a daemon did not help; a device-level SET_FEATURE does not switch this
  model. **Root is not enough; the process must trace back to a login session** — it works as root from iTerm (macOS then does
  a TCC check attributed to iTerm) and is denied outright (no TCC check) from any launchd job, daemon or agent. **Current
  design (`mode_switcher/`):** the app itself, when its monitor sees `12d1:14fe`, runs
  `sudo -n <helper python> <helper> --switch-now` (`dongle.switch_dongles_to_modem_mode_through_helper`, retried every 20 s).
  ONE passwordless sudoers rule (`/etc/sudoers.d/sms-gateway-mode-switcher`), for a command with no arguments, in the
  root-owned `/usr/local/libexec/sms-gateway/` (own venv from `/usr/bin/python3`). Install with
  `sudo ./mode_switcher/install_switcher.sh` (also removes any old daemon/agent), remove with `uninstall_switcher.sh`.
  **Verified** by running the helper from the terminal session: dongle went 14fe → 151d. Consequence: the app must be
  launched from a terminal (like today); a Finder-launched packaged app would need its own privacy grant. Shows « Détecté » for
  25 s, then a French error (missing helper / last failure). `scripts/huawei_switch.py` remains as a manual fallback.
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
- Operator USSD codes must be configurable per SIM/operator, not hard-coded (`sim/mobile_operators.py` + `operators.json`). The gateway must work with Orange, Expresso and Free SIMs, not only this Expresso one.

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
    Reliable reassembly requires PDU mode (`AT+CMGF=0`, `AT+CMGL=4`) and the UDH concat header — **now what the app does** (see `inbox/sms_pdu.py`).
  - Parts can be missing; indexes are sparse; reading marks messages as read.
  - Strategy: read → store in SQLite → delete from SIM (`AT+CMGD=<i>`). SIM capacity is small.
  - New-message notification: `AT+CNMI=2,1,0,0,0` → `+CMTI: "SM",<index>` (used by the app: the monitor thread listens to the AT port between requests; a `+CMTI` seen anywhere, even swallowed as noise before a command or inside a reply, triggers a SIM read).
- USSD: `AT+CUSD=1,"<GSM 7-bit packed hex>",15`. This dongle expects **packed hex**, not plain text.
  - Examples: `*222#` = `2A994C3602`, `#111#` = `A3582C3602`.
  - `OK` comes immediately; the `+CUSD: <status>,"<hex>",15` reply arrives **asynchronously** seconds later.
  - Replies are GSM 7-bit packed hex → need a GSM 03.38 unpacker.
  - Status: 0 final, 1 menu awaiting reply, 2 ended by network, 4 not supported. Close with `AT+CUSD=2`.
  - Sessions time out after ~20–30 s. **Unresolved:** a menu reply sent as `"36"` (packed `6`) was
    rejected with "Entrée non valide" — could be the format or a session timeout. Needs testing.

## Code map

Layout: one folder per feature, imported from the project root (`from contacts.contact_store import ContactStore`;
`app.py` stays at the root and is started from there). Put new code in the folder of its feature; anything used by
several features goes in `common/`. Features may use `common/`; `outbox/` and `devices/` also use `sim/` and `inbox/`
parsers, but `common/` never imports a feature.
`common/` (shared UI + helpers, `app_paths.py`, `database.py`), `contacts/`, `globe_transports/`, `compose/`, `outbox/`, `history/`, `inbox/`,
`blacklist/`, `conversations/`, `devices/` (USB/AT layer + monitor thread), `sim/` (balance, USSD), `mode_switcher/` (root helper), `scripts/`, `legacy/`.

Desktop app (current):
- `app.py` — entry point and `MainWindow` (sidebar + stacked pages). Owns the `DongleMonitor` thread.
- `common/sidebar.py` — `Sidebar`: colored SVG icons, separators (custom delegate, none after the last item),
  order: Composer, Réception, Contacts, Boîte d'envoi, Historique, Appareils. Pages are matched **by name** in
  `app.py`, so renaming a sidebar entry means updating the key there too.
- `common/icons.py` — shared SVG → colored `QIcon` helper (`build_colored_icon`) and icon shapes (edit, trash).
- `common/french_labels.py` — French wording helpers (contact count with plural/thousands, dates, SIM status).
- `contacts/contact_import.py` — pure: read `.xlsx`/`.csv` (`;` separator, latin-1 fallback), guess phone/name columns,
  validate + dedupe rows into an `ImportPlan`; other columns are kept as custom fields (future `{variables}`).
- `contacts/contact_store.py` — SQLite (`~/Library/Application Support/SMS Gateway/sms_gateway.db`): `contact_lists` +
  `contacts(list_id, UNIQUE(list_id, phone_number))`. Short-lived connection per call. Auto-migrates the older
  list-less schema into one list, writing a `.before-lists.bak` copy first.
- **Import from Globe Transports (backend):** « Importer depuis Globe Transports... » next to the file import on the Contacts page. `globe_transports/globe_transports_client.py` (stdlib urllib, injectable transport, 10 s timeout, every failure = `GlobeTransportsError` with a French message): `GET /api/departs_for_sms?period=upcoming|past[&limit]` fills `globe_selection_dialog.py` (tree with checkboxes, « À venir / Passés », tri-state: a fully checked depart is asked as `entity=depart`, otherwise its checked buses as `entity=bus`; both = two calls merged on `booking_id`), then `GET /api/contacts_for_sms?entity=…&entity_ids[]=…&filter=paid|unpaid|all` (« Payés / Non payés / Tous »). Server address = QSettings `globe_transports/base_url` (default `http://127.0.0.1:8000`, editable in the dialog, `/api` added by the client). **Authentication:** `POST /api/sms_gateway/token` {username, password} → token valid 90 days; both GET routes send `Authorization: Bearer`. The token (+ expiry + server URL) lives in the macOS Keychain (`globe_transports/globe_token_store.py`, account `globe_transports_token`), **never the password**. The client (Qt-free) drops a token with < 3 days left or refused (401), asks `login_requester` for a new login (`login_prompter.py` shows `globe_login_dialog.py` on the UI thread while the request thread waits) and retries the failed call ONCE; login errors are French (401 « Identifiants invalides. », 403 backend message, 422, 429 « Trop de tentatives, réessayez dans une minute »). « Se déconnecter de Globe Transports » is in the selection dialog. Never log the token or password. Requests run in `background_request.py` threads. `contacts/globe_contact_mapping.py` (pure) turns bookings into `ParsedContact`s: phone (int or null) normalised, no/invalid number counted « sans numéro valide », same customer on several bookings kept once (« doublons »); `point_dep, destination, agent_number, depart_name, bus_name, seat_number, formatted_schedule` (« Heure de rendez-vous », "HH:MM") and `arret_bus` (« Lieu de rendez-vous ») are stored in `column_values` (no schema change) and as readable columns in `custom_fields` (null/absent = left out, empty in templates), and are template variables `{point_dep}` … `{arret_bus}`; their French descriptions (`NAMED_VARIABLE_DESCRIPTIONS` in `common/message_template.py`) are the button tooltips, the example-values lines and the column headers. The list choice is the shared `contacts/list_destination_picker.py` (also used by the file import), saving goes through `ContactsPage.save_contacts_in_chosen_list`; summary « 42 contacts importés, 5 doublons ignorés, 1 sans numéro valide ». Tests (mocked HTTP only): `tests/test_globe_transports.py`. **Not yet tried against the real backend.**
- `contacts/contacts_page.py` — opens on the lists (name, count, date, rename ✎ / delete 🗑 icon buttons); clicking a list
  shows its contacts (search, delete selection). `contacts/import_contacts_dialog.py` — column mapping, validation report,
  destination list (new name or existing list).
- `compose/compose_page.py` — **campaign composer**: choose recipients with two radio buttons — « Listes de contacts » (tick one or more) or « Un seul numéro » (typed number + optional name, via `compose/typed_recipient.py`; queued as a one-message campaign with list name « Numéro saisi ») — write a message with variables, live
  preview/validation (encoding, SMS count, cost per part), **Lancer** queues one personalised message per contact (state `WAITING`) and jumps to the Outbox.
  It does **not** send anything itself (both modes queue to the Outbox, which asks for confirmation before really sending). **The draft is kept between launches** (QSettings group `compose_draft`: message, recipient mode, typed number/name, ticked list ids; saved on every change and when the window closes, emptied once Lancer queues it). Tests that build a `ComposePage` must redirect `ComposePage.draft_settings` and the 7-bit preference functions (see `tests/test_compose_recipients_and_draft.py`) so they never write the owner's real preferences.
- `common/message_template.py` — pure: `{variables}`; supported for now: `{nom}`, `{téléphone}`, `{détails}` (case and accents
  ignored). `{détails}` = all extra imported columns as `Ville : Dakar, Montant : 5000` (ASCII `, ` separator, because
  text mode turns `·` into `?`). Unknown variables block Lancer. **`{col1}` … `{col10}`** = the extra columns the owner maps in the import dialog (10 combos « (aucune) » / any file column, defaulting to the remaining columns in order); stored per contact in `contacts.column_values` (JSON `{"col1": …}`, auto-added column), while `custom_fields` still keeps EVERY other column by header for `{détails}`. The Composer shows a `{colN}` button and a line of example values only for the columns the ticked lists have; every queued message stores all ten `colN` (empty ones as `""`), and a batch edit leaves messages lacking a used variable as they are (counted as « ne peuvent pas être reconstruits »). When adding to an existing list the mapping must match earlier imports (the dialog says so). Tests: `tests/test_extra_column_variables.py`.
- `compose/campaign_builder.py` — pure: template + recipients → `CampaignDraft` (a number in several lists is kept once,
  counts of empty values, messages over 160 characters or empty). Blocks Lancer on unknown variables / too long / empty.
- `outbox/outbox_store.py` — SQLite `campaigns` + `messages` (recipient, number and final text are **copied**, so deleting a
  contact or list never changes the queue). States: `WAITING`, `SENDING`, `SENT`, `FAILED`, `UNKNOWN`.
- **Batches (lots) and correcting waiting messages:** every campaign is a batch. `campaigns` keeps `message_template`, `variables_used` (JSON list), `simplify_to_7_bit`, `is_reply`, `edited_at`; every
  `messages` row keeps `variable_values` (JSON of ALL variables of that recipient: `nom`, `telephone`, `details` — not only the ones used, so a later template may use others). `common/message_template.py`
  (moved from `compose/`) has `render_final_message_text`, the one place that builds a final SMS text (Composer and batch editor both use it). `outbox/batch_editing.py` (pure `plan_batch_edit`) is used by the dialog preview AND by
  `OutboxStore.update_batch_template` (one `BEGIN IMMEDIATE` transaction, all or nothing): it re-renders every **WAITING or FAILED** message of the batch (FAILED ones are resent by the next run); SENT, SENDING and UNKNOWN never change.
  Rejected (`BatchEditRejected`, French reason): empty text, unknown variable, a message that would be empty or over 10 SMS. Messages queued before this feature have `variable_values='{}'` and cannot be rebuilt (reported, left as they are).
  `outbox/batches_panel.py` = table at the TOP of the Boîte d'envoi (batches that still have messages in the Outbox, replies excluded) with a « Gérer » button → `outbox/batch_manager_dialog.py` (edit the message with live preview, variable buttons,
  7-bit option, « Supprimer les messages en attente » = WAITING+FAILED of that batch, confirm default Non). Tests: `tests/test_batch_editing.py`.
- `outbox/outbox_page.py` — Boîte d'envoi: **Envoyer / Pause (Reprendre) / Arrêter** on top, progress bar,
  per-state counters, table (N°, state icon only with tooltip, recipient, number, message, list, date), state filter,
  "Vider les messages en attente" (confirm, default Non). **Multi-select (Cmd/Shift-click, « Tout sélectionner ») + « Supprimer la sélection » / Delete key**
  (confirm, default Non; a `SENDING` message is never deleted). **Envoyer asks for confirmation (default Non) before
  really sending.** Rows update in place while sending; reloads when shown.
- **Online SMS gateways (`gateways/`, Orange SMS only for now):** **Envoyer** (new run, not Reprendre) opens `outbox/sending_method_dialog.py` « SIM (dongle USB) » or a gateway; the choice is remembered (QSettings `sending_method`), then the usual confirmation (default Non) names the method. A gateway shows its **balance** (read in a background thread; orange warning if lower than the number of messages). Only campaigns go through a gateway; **replies always use the SIM**. `gateways/orange_sms_client.py` (stdlib urllib, injectable transport, no network in tests): token `POST api.orange.com/oauth/v3/token` (cached, renewed on 401 once), send `POST /smsmessaging/v1/outbound/<tel:sender>/requests` (same body as GoTransV5's `SMSSender`), **balance `GET /sms/admin/v1/contracts`** (list of contracts; sum `availableUnits` of ACTIVE, not expired; verified live: 26 SMS, expiry 2100 = open-ended). `gateways/gateway_message_sender.py` maps results: 2xx with `outboundSMSMessageRequest` → SENT; no network / 401 / 403 / 429 → `DONGLE_UNAVAILABLE` (message back to WAITING, run pauses with the reason); timeout, 5xx or odd 2xx → UNKNOWN (never retried); other 4xx → FAILED. Credentials (client id/secret, sender number, sender name) live in the macOS Keychain (`gateways/gateway_credentials.py`, service « SMS Gateway »), entered through « Configurer… » (`gateways/gateway_settings_dialog.py`). **SIM screen:** « Vérifier le solde » also reads every gateway's balance (`gateways/balance_reader_thread.py`, off the UI thread) into a `GatewayCard` under the SIM cards (`26 SMS`, expiry only if before 2090, « Configurer… » button, « Non configuré » hint); the button re-enables when the SIM check AND the gateway checks have answered. The Outbox and the SIM page share the same gateway objects (one token cache). New gateway = one class in `gateways/supported_gateways.py`. **Real sending through Orange not yet verified** (owner tests with own number). Orange delivery receipts need a public callback URL: not supported. Tests: `tests/test_orange_sms_gateway.py`.
- `outbox/message_sender.py` — the seam for delivery: `send(number, text) -> SendResult`. `DongleMessageSender` validates the number,
  hands a `SendRequest` to the monitor thread (which owns the USB) and blocks for the outcome: `+CMGS` → SENT, definite
  refusal → FAILED, anything unclear → UNKNOWN (never retried automatically; only Envoyer/Reprendre requeues it), no dongle ready → `DONGLE_UNAVAILABLE` (message goes back to
  `WAITING`, worker pauses itself, page shows « En pause : … »). **Real sending is not yet verified with the dongle** —
  the owner must test it (Claude never sends real SMS).
- `outbox/sending_worker.py` — `SendingWorker(QThread)`: claims the oldest `WAITING` message (→ `SENDING`), sends, waits a random pause (speed chosen in the Outbox, `outbox/sending_speed.py`: Rapide 1–1.5 s / Modérée 2–3 s / Prudente 5–10 s, default Prudente, remembered via QSettings, applies live), records
  SENT/FAILED/UNKNOWN. Pause/stop never cut a message in half (the in-flight one finishes first); stop leaves the rest
  `WAITING`. Messages cut off by a crash (`SENDING` at startup) become `UNKNOWN` and are never resent automatically (only when the owner presses Envoyer/Reprendre).
- `devices/devices_page.py` — table of dongles (state, IMEI, operator, signal bars + dBm, SIM, network, USB) and Refresh.
- `devices/dongle.py` — **device layer, no UI**: USB scan, `DongleConnection` (AT port, timeouts, flush),
  `send_text_message`, live status reading, `SendRejected` / `SendOutcomeUnknown`.
- `devices/dongle_monitor.py` — `DongleMonitor(QThread)`: scans USB every 3 s, reports CD-ROM dongles (the app asks the root helper to switch them),
  health check every 30 s (3 misses → reconnect), Refresh (`request_immediate_rescan`), and a send entry point
  (`submit_text_message` → a `SendRequest` the caller waits on) used by `DongleMessageSender`; requests still queued when the monitor closes are refused.
  The scan loop catches and logs every exception so the thread can never die silently.
- `common/sms_text.py` — pure helper: `normalize_senegal_number`.
- `common/sms_encoding.py` — **accents + long messages** (pure): `plan_sms(text)` picks GSM 7-bit (160 chars/SMS, 153 per part) or Unicode/UCS-2 (70/67) and cuts the text into parts
  (never inside an extension char like « € » or an emoji); `build_submit_pdus` makes the SMS-SUBMIT PDUs (concatenation header, 8-bit reference); `simplify_for_gsm7` (ê→e, ç→c, « »→\") is the
  « Rester en 7 bits » checkbox in the Composer and the reply window (**off by default**: text goes out exactly as typed, like SMS Caster, which billed 1 SMS ≤160 and 2 SMS ≤306 with é è à ù; remembered in QSettings via `common/sms_preferences.py`). **Gotcha:** é è à ù ö ñ ü are 7-bit but ç ê â î ô û ë ï œ « » ’ and CAPITAL À È are not: one of them makes the
  whole message Unicode (70 chars). Max `MAX_PARTS_PER_MESSAGE = 10` SMS per message (cost guard; refused above). Every part is billed as one SMS.
  `DongleConnection.send_text_message`: a plain printable-ASCII single SMS keeps the **text-mode path verified on this dongle**; everything else goes in **PDU mode** (`AT+CMGF=0`, `AT+CMGS=<tpdu length>`,
  PDU hex + Ctrl+Z, one part after the other with a 1 s pause). If a LATER part fails after earlier ones went out the result is UNKNOWN (never FAILED, never retried automatically). **PDU sending is not yet verified with
  the real dongle/operators** (tests: `tests/test_sms_encoding.py` round-trips every PDU through the inbox decoder; `tests/test_long_and_accented_sending.py` scripts the AT dialogue).
- `inbox/sms_pdu.py` — pure: decodes one SMS-DELIVER PDU (GSM 7-bit / 8-bit / UCS2, numeric and alphanumeric senders like « Expresso », operator timestamp,
  concatenation header = « part n of N »). `inbox/received_sms_parser.py` — pure: `AT+CMGL=4` reply → `SmsPart`s (an undecodable entry is logged and left on the SIM).
  `inbox/sms_assembler.py` — pure: parts → complete messages. A long SMS is stored **only when every part is on the SIM** (parts stay there meanwhile);
  after 10 minutes an incomplete one is stored with a « […] » marker where the part is missing. Tests: `~/usbenv/bin/python -m unittest discover tests`.
  Messages stored before this change (text mode) may still be cut into fragments in the wrong order: they cannot be repaired, delete them.
- **Replies (Inbox → priority queue):** Réception has a reply icon column (blue arrow, only for senders that are real `+221` numbers, not « Expresso » or short codes). Click → the **conversation dialog** (see below) → `InboxPage.reply_submitted` → `OutboxPage.queue_reply` (wired in `app.py`). `OutboxStore.queue_reply` stores a message with `priority=1`
  (own one-message campaign, list « Réponse »); claims and the Outbox list order by `priority DESC, id`. `SendingWorker`: once the message in flight is done, every waiting reply goes out
  one after the other without the campaign pause (a reply cuts the pause short); `replies_only=True` workers (`OutboxPage.reply_worker`) deliver replies while the campaign is idle, paused or
  stopping, end when none is left, and on « no dongle » end and leave the reply first in line (`reply_delivery_postponed` → warning in `app.py`). Delivered replies reach the Historique like other SENT messages.
  Tests: `tests/test_reply_priority_queue.py`. **Not yet verified with the real dongle.**
- **Conversations:** one `conversations` row per correspondent (`conversation_key` = normalized `+221…` number, or the raw sender name such as « Expresso »); `messages` (sent, incl. batch messages and replies) and `received_messages` both carry `conversation_id`,
  set in the same transaction that saves the message (`common/conversation_table.py`: `find_or_create_conversation`). `conversations/conversation_store.py` reads a thread (sent + received, oldest first; a SENT message is dated `updated_at`,
  others `created_at`) and runs the **one-time grouping** of older messages when created in `app.py` (idempotent, writes `sms_gateway.db.before-conversations.bak` first). `conversations/conversation_dialog.py` = WhatsApp-style dialog
  (received left grey, sent right green, failed red, French date under each bubble, day separators, state label for unsent messages, composer below with the 7-bit option; refreshes every 2 s). The Réception reply icon opens it for every sender
  (read-only for « Expresso »/short codes); opening it marks that sender's messages read; sending emits `reply_submitted` → `OutboxPage.queue_reply` (priority queue). Tests: `tests/test_conversations.py`. **Not yet checked with the real dongle.**
- **Blacklist:** `blacklisted_numbers` table (normalized `+221…`), `blacklist/blacklist_store.py`. Campaigns skip these numbers at three levels: the Composer draft (`build_campaign_draft(blacklisted_numbers=…)`, counted in a notice; a typed blacklisted number blocks Lancer),
  `OutboxStore.queue_campaign` (never stores them) and `claim_next_waiting_message` (deletes pending campaign messages of a number blacklisted meanwhile). Manual replies (priority 1) are never blocked. Added from the conversation dialog's « Paramètres » menu
  (« Mettre sur liste noire », confirm default Non, or « Retirer de la liste noire »); `blacklist_changed` → `OutboxPage.drop_pending_messages_for_blacklisted_numbers`. Tests: `tests/test_blacklist.py`. Still to do: a page to list/remove blacklisted numbers, auto-blacklist on « STOP ».
- `inbox/inbox_store.py` — SQLite `received_messages` (unique on sender+time+text, `is_read`); sender name looked up from contacts.
- `inbox/inbox_page.py` — Réception: table (unread in bold), preview pane, selecting marks read, mark-all-read, delete (confirm). Sidebar shows `Réception (n)`.
- `DongleMonitor._drain_sim_inboxes` — when the dongle announces a new SMS (`+CMTI`, enabled with `AT+CNMI=2,1,0,0,0` at connect), at startup and on every new connection, plus a 20 s safety timer (3 s polling if the dongle refuses `AT+CNMI`): read SIM in PDU mode → assemble long messages → store in DB → only then `AT+CMGD` every slot of each stored message; emits `inbox_messages_received` → `inbox/notification_sound.py` (afplay Glass.aiff). **Not yet verified with the real dongle.** Long SMS are reassembled from their parts (PDU mode).
- `history/history_page.py` — Historique = the **sent** messages (`SENT`, same `messages` table, newest first, « Envoyé le » = `updated_at`), search,
  multi-select delete. The Outbox shows the current run: `SENT` messages stay there (success icon, `in_outbox=1`) to show progress and move to the Historique (`archive_sent_messages`) when the run ends, is stopped, the queue is cleared, or at app start. Envoyer/Reprendre first requeues `FAILED` **and `UNKNOWN` (« ? »)** messages (`requeue_failed_messages`; owner's explicit choice, an UNKNOWN one may be sent twice if it was delivered). While sending, the list auto-scrolls to the active message unless the user touched it in the last 10 s. SENDING icon = hourglass.

- `sim/sim_page.py` — **SIM** screen (last sidebar item): « Vérifier le solde » → one card per ready dongle with the balance (`1 250 FCFA`), or a French error.
  Flow: `DongleMonitor.request_balance_check` → `_read_balance` (USSD via `DongleConnection.run_ussd_code`, up to 3 tries on « Operation ratee! ») → signal `balance_check_finished`.
  Manual button only, never automatic. **Not yet verified with the real dongle.**
  Below the balance: `sim/ussd_dialer.py` — **interactive USSD dialer**. Type a code (`*222#`, `#111#`), read the reply in a transcript,
  answer a menu (the button becomes « Répondre »), « Fermer la session ». Only digits and `* # +` are accepted; every choice is typed by
  the person (the app never picks a menu option). Flow: `DongleMonitor.submit_ussd_request` → `DongleConnection.send_ussd_request`
  (`UssdReply.status` 1 = menu open) → `ussd_exchange_finished`; the answer goes back to the dongle holding the session; a new code closes
  the old session first; the balance check closes it too. Types in `sim/ussd_session.py`. **Menu answers still unverified** (see the
  « Unresolved » note in the USSD facts: an answer once got « Entrée non valide »).
- `sim/mobile_operators.py` — pure, **multi-operator** (Expresso, Orange, Free): `MobileOperator` profiles (network codes, name aliases, balance USSD code,
  reply regexes). The SIM is recognised by its numeric MCC+MNC (`devices/dongle.py` reads it with `AT+COPS=3,2` → `DongleStatus.operator_network_code`;
  Orange 60801, Free 60802, Expresso 60803), then by name alias. Owner overrides live in
  `~/Library/Application Support/SMS Gateway/operators.json`, e.g. `{"orange": {"balance_ussd_code": "#123#", "balance_reply_patterns": ["..."]}}`.
  **Verified: Expresso `*222#` and Orange `#123#`** (reply « Credit global: 162881F,compteur recharge… », parsed by `GLOBAL_CREDIT_PATTERN`). Free `*222#` is an unverified guess (flagged on the card); the card shows the raw reply when it can't be parsed.
  `sim/sim_balance.py` = result type + transient-failure rule; `sim/ussd_codec.py` = GSM 7-bit pack/unpack.

Scripts (still useful, run outside the app):
- `scripts/huawei_switch.py` — one-shot CD-ROM → modem switch (needs sudo).
- `scripts/huawei_at.py` — interactive AT shell, plus `send <number> <text>`.

Legacy (superseded by the desktop design, kept only as reference — mine it for queue/retry ideas, then delete):
- `legacy/gateway.py` — old FastAPI service with SQLite queue and worker. Never run on this Mac.
- `legacy/sn.globalsoft.smsgateway.plist` — old launchd daemon definition. Not used.

## Environment

- Python venv: `~/usbenv` (pyusb, PySide6; fastapi/uvicorn are only for the legacy gateway). Conda `(base)`
  is active in the shell — always call `~/usbenv/bin/python` explicitly.
- Run the app: `~/usbenv/bin/python app.py`. Restart it after code changes (it does not hot-reload).
- Homebrew libusb at `/opt/homebrew/lib`.
- Preview UI without a display: `QT_QPA_PLATFORM=offscreen`, `widget.grab().save(png)`, then view the PNG.
- zsh aborts a whole command when one glob matches nothing (`ls /dev/cu.* /dev/tty.usb*`).
- Only one process can own the dongle: close the app before using `scripts/huawei_at.py`.

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
  automatically — the user checks the recipient. Exception (owner's choice): pressing Envoyer/Reprendre requeues UNKNOWN (« ? ») messages together with FAILED ones. The Outbox must store this third state.
- Identify dongles by IMEI (`AT+CGSN`), not by USB location. The IMEI is read but the monitor still keys
  connections by USB location — switch to IMEI when multi-dongle support arrives.
- Structured logs with rotation. Not yet (only `logging.exception` in the scan loop).
- Mac must not sleep during a campaign: ✅ `common/keep_awake.py` holds an IOKit `PreventUserIdleSystemSleep` assertion for the whole `SendingWorker` run (also exempts the app from App Nap; screen lock is fine, closed lid / manual Sleep are not covered);
  `sudo pmset autorestart 1` is no longer needed because the app is not a daemon.
- Use direct USB ports or a powered hub (dongles draw up to 500 mA).
- Quitting mid-campaign must warn, then pause and persist the queue.

## Roadmap

1. ✅ App shell: sidebar with icons, Devices screen (state, IMEI, operator, signal, SIM, network, Refresh).
2. ✅ Compose → queue: lists + template + Lancer → messages `WAITING` in the Outbox (nothing is sent yet).
3. ✅ Privileged helper for the mode switch (sudo rule + root-owned helper in `mode_switcher/`, called by the app).
4. Queue worker: ✅ real sending through the dongle (5–10 s steady rate, confirmation before a run, pauses when no dongle) —
   **awaiting the owner's first real test**. Historique (sent messages) ✅. Received messages stay in Réception.
5. Contacts + import: ✅ named lists, Excel/CSV import, column mapping, `+221` normalization, duplicate/invalid
   report, rename/delete. Still to do: blacklist, edit a single contact, virtual table for 10k+ contacts.
6. Campaigns extras: pause/resume/stop per campaign, sleep prevention, more variables (columns 3–10), multipart SMS.
7. Devices extras: credit via USSD `*222#` (needs GSM 7-bit packed hex unpacker), test-SMS button.
8. Inbox: ✅ UCS2 decode, ✅ PDU-mode multipart reassembly (**not yet verified with the real dongle**). ✅ CNMI notifications (**not yet verified with the real dongle**). Still to do: auto-blacklist on "STOP".
9. Extras: UCS2 + multipart sending, delivery reports (`AT+CSMP`, `AT+CNMI`), scheduling, multi-dongle
   load balancing, low-credit alerts, per-SIM hourly/daily caps, optional HTTP API for the Laravel apps.

## Working rules

- Only send test SMS to the owner's own number (set via env `TEST_NUMBER`), never to real contacts.
  Claude must never send a real SMS on its own; ask the owner to test sends.
- Never automate choices in operator USSD menus that could purchase bundles.
- Keep a steady send rate; prepaid SIMs sending too fast risk being blocked by the operator.
- Use descriptive names for variables and functions, even when longer (owner's global rule).
- Keep USB/AT code out of UI files; UI talks to the monitor via signals only.
