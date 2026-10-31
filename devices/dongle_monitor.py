"""Background thread that owns every USB dongle and publishes status snapshots to the UI."""
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import usb.core
from PySide6.QtCore import QThread, Signal

from common.platform_support import IS_WINDOWS
from devices import dongle
from devices.dongle import DongleConnection, DongleStatus
from inbox.sms_assembler import assemble_received_messages
from sim.mobile_operators import identify_operator, load_operators, parse_balance_with_patterns
from sim.sim_balance import SimBalanceResult, is_transient_failure_reply
from sim.ussd_session import REQUEST_KIND_CLOSE_SESSION, UssdExchangeResult, UssdRequest

SCAN_INTERVAL_MS = 3000
# The dongle announces each new SMS (AT+CNMI), so the inbox is read the moment one arrives. The timer below is only a safety net
# for a lost announcement; without announcement support the inbox is simply read every few seconds, as it used to be.
INBOX_SAFETY_CHECK_INTERVAL_SECONDS = 20
INBOX_POLL_INTERVAL_SECONDS = 3
LISTEN_SLICE_SECONDS = 0.25  # how long the thread listens to one dongle before looking at the request queues again
HEALTH_CHECK_INTERVAL_SECONDS = 30
MISSED_REPLIES_BEFORE_RESET = 3
SWITCH_WAIT_SECONDS = 20
RETRY_AFTER_ERROR_SECONDS = 10
MODE_SWITCHER_GRACE_SECONDS = 25  # the helper switches within ~3 s, then the dongle needs ~10 s to come back
RETRY_SWITCH_AFTER_SECONDS = 20
BALANCE_CHECK_ATTEMPTS = 3          # the operator sometimes answers "Operation ratee!" once
BALANCE_CHECK_RETRY_PAUSE_SECONDS = 4


OUTCOME_SENT = "sent"
OUTCOME_FAILED = "failed"            # the dongle or network definitely refused it
OUTCOME_UNKNOWN = "unknown"          # no confirmation either way: it may have gone out
OUTCOME_NO_DONGLE = "no_dongle"      # never handed to a dongle: certainly not sent, safe to try again later


@dataclass
class SendRequest:
    """One message waiting for the monitor thread (the only owner of the USB dongles) to send it.
    The caller blocks on `finished` and then reads `outcome` / `detail`."""
    destination_number: str
    message_text: str
    outcome: str = ""
    detail: str = ""
    finished: threading.Event = field(default_factory=threading.Event)
    _state_lock: threading.Lock = field(default_factory=threading.Lock)
    _started: bool = False
    _cancelled: bool = False

    def try_start(self):
        """Monitor side: False if the caller gave up before the send began (then nothing must be sent)."""
        with self._state_lock:
            if self._cancelled:
                return False
            self._started = True
            return True

    def cancel_if_not_started(self):
        """Caller side: True if the send had not begun, so the message is certainly not sent."""
        with self._state_lock:
            if self._started:
                return False
            self._cancelled = True
            return True

    def finish(self, outcome, detail=""):
        self.outcome, self.detail = outcome, detail
        self.finished.set()


class DongleMonitor(QThread):
    statuses_updated = Signal(list)
    inbox_messages_received = Signal(int)  # how many new messages were stored in the database
    balance_check_finished = Signal(list)  # one SimBalanceResult per ready dongle (empty when none is ready)
    ussd_exchange_finished = Signal(object)  # a UssdExchangeResult, for every interactive USSD request

    def __init__(self, inbox_store=None):
        super().__init__()
        self.inbox_store = inbox_store
        self.open_connections = {}   # usb_location -> DongleConnection
        self.current_statuses = {}   # usb_location -> DongleStatus
        self.last_health_check_at = {}
        self.missed_replies = {}
        self.retry_not_before = {}
        self.switching_started_at = {}
        self.cdrom_first_seen_at = {}   # usb_location -> when a dongle was first seen in CD-ROM mode
        self.next_switch_attempt_at = {}
        self.last_switch_error = {}
        self.wake_up_requested = threading.Event()
        self.pending_messages = queue.Queue()
        self.force_full_refresh = False
        self.balance_check_requested = threading.Event()
        self.pending_ussd_requests = queue.Queue()
        self.next_scan_at = 0.0
        self.next_inbox_check_at = 0.0
        self.inbox_check_requested = True  # read the SIM once at startup: messages may have arrived while the app was closed

    def request_balance_check(self):
        """Called from the UI thread: ask every ready SIM for its balance; the answer comes back as `balance_check_finished`."""
        self.balance_check_requested.set()
        self.wake_up_requested.set()

    def submit_ussd_request(self, request_text, usb_location, starts_new_session):
        """Called from the UI thread: send one USSD string; the operator's answer comes back as `ussd_exchange_finished`."""
        self.pending_ussd_requests.put(UssdRequest("send", request_text, usb_location, starts_new_session))
        self.wake_up_requested.set()

    def submit_ussd_session_close(self, usb_location):
        self.pending_ussd_requests.put(UssdRequest(REQUEST_KIND_CLOSE_SESSION, usb_location=usb_location))
        self.wake_up_requested.set()

    def request_immediate_rescan(self):
        """Called from the UI thread: rescan now, ignoring health-check and retry timers."""
        self.force_full_refresh = True
        self.next_scan_at = 0.0
        self.wake_up_requested.set()

    def submit_text_message(self, destination_number, message_text):
        """Called from any thread. Returns a SendRequest: wait on `.finished`, then read `.outcome` and `.detail`."""
        send_request = SendRequest(destination_number, message_text)
        self.pending_messages.put(send_request)
        self.wake_up_requested.set()
        return send_request

    def stop(self):
        self.requestInterruption()
        self.wake_up_requested.set()

    def run(self):
        usb_backend = dongle.load_usb_backend()
        if usb_backend is None:
            self.statuses_updated.emit(
                [DongleStatus("-", dongle.STATE_ERROR, error_message="libusb introuvable (brew install libusb)")]
            )
            return
        while not self.isInterruptionRequested():
            self.wake_up_requested.clear()  # cleared before the queues are read, so a request made meanwhile is never missed
            self._send_pending_messages()
            self._check_balances_if_requested()
            self._process_ussd_requests()
            if time.time() >= self.next_scan_at:
                try:
                    self._scan_once(usb_backend)
                except Exception:  # a bad scan must never kill the monitor for good
                    logging.exception("Dongle scan failed; retrying on next cycle")
                self.next_scan_at = time.time() + SCAN_INTERVAL_MS / 1000
            self._collect_new_message_notices()
            if self.inbox_check_requested or time.time() >= self.next_inbox_check_at:
                self.inbox_check_requested = False
                try:
                    inbox_read_failed = self._drain_sim_inboxes()
                except Exception:
                    logging.exception("Reading the SIM inbox failed; retrying shortly")
                    inbox_read_failed = True
                self.next_inbox_check_at = time.time() + (
                    INBOX_POLL_INTERVAL_SECONDS if inbox_read_failed else self._inbox_check_interval_seconds()
                )
            self._listen_until(min(self.next_scan_at, self.next_inbox_check_at))
        self._refuse_pending_messages()
        for connection in self.open_connections.values():
            connection.close()

    def _ready_connections(self):
        return [
            connection for usb_location, connection in list(self.open_connections.items())
            if self.current_statuses.get(usb_location) is not None
            and self.current_statuses[usb_location].state == dongle.STATE_READY
        ]

    def _inbox_check_interval_seconds(self):
        ready_connections = self._ready_connections()
        every_dongle_announces = bool(ready_connections) and all(c.notifications_enabled for c in ready_connections)
        return INBOX_SAFETY_CHECK_INTERVAL_SECONDS if every_dongle_announces else INBOX_POLL_INTERVAL_SECONDS

    def _collect_new_message_notices(self):
        """Announcements noticed while a command was running (e.g. during a send) also count."""
        for connection in self._ready_connections():
            if connection.take_new_message_notice():
                self.inbox_check_requested = True

    def _listen_until(self, deadline):
        """Wait for the next thing to do, listening to the dongles meanwhile: returns as soon as one announces a new SMS,
        a request is queued from the UI, or the deadline passes."""
        while not self.isInterruptionRequested() and not self.wake_up_requested.is_set():
            remaining_seconds = deadline - time.time()
            if remaining_seconds <= 0:
                return
            listening_connections = [c for c in self._ready_connections() if c.notifications_enabled]
            if not listening_connections:
                self.wake_up_requested.wait(remaining_seconds)
                return
            for connection in listening_connections:
                try:
                    if connection.listen_for_notifications(min(LISTEN_SLICE_SECONDS, remaining_seconds)):
                        self.inbox_check_requested = True
                        return
                except (usb.core.USBError, ValueError):
                    self.wake_up_requested.wait(LISTEN_SLICE_SECONDS)  # a broken port: the health check will deal with it

    def _send_pending_messages(self):
        while not self.pending_messages.empty():
            send_request = self.pending_messages.get()
            if not send_request.try_start():
                continue  # the caller gave up while it was queued
            ready_locations = [
                location for location in self.open_connections
                if self.current_statuses[location].state == dongle.STATE_READY
            ]
            if not ready_locations:
                send_request.finish(OUTCOME_NO_DONGLE, "Aucun dongle n'est prêt")
                continue
            connection = self.open_connections[ready_locations[0]]
            try:
                connection.send_text_message(send_request.destination_number, send_request.message_text)
                send_request.finish(OUTCOME_SENT)
            except dongle.SendRejected as rejection:
                send_request.finish(OUTCOME_FAILED, str(rejection))
            except dongle.SendOutcomeUnknown as uncertainty:
                send_request.finish(OUTCOME_UNKNOWN, str(uncertainty))
            except (usb.core.USBError, RuntimeError) as send_error:
                # failures after the text is written are turned into SendOutcomeUnknown by the device layer,
                # so an error escaping from it happened before the text left: definitely not sent
                send_request.finish(OUTCOME_FAILED, str(send_error))

    def _process_ussd_requests(self):
        while not self.pending_ussd_requests.empty():
            self.ussd_exchange_finished.emit(self._run_ussd_request(self.pending_ussd_requests.get()))

    def _run_ussd_request(self, ussd_request):
        exchange_result = UssdExchangeResult(ussd_request.usb_location, ussd_request.request_text)
        ready_locations = [
            location for location in self.open_connections
            if self.current_statuses[location].state == dongle.STATE_READY
        ]
        usb_location = ussd_request.usb_location or (ready_locations[0] if ready_locations else None)
        if usb_location not in ready_locations:
            exchange_result.error_message = "Aucun dongle prêt" if not ussd_request.usb_location else "Le dongle n'est plus prêt"
            return exchange_result
        exchange_result.usb_location = usb_location
        exchange_result.imei = self.current_statuses[usb_location].imei
        connection = self.open_connections[usb_location]
        try:
            if ussd_request.kind == REQUEST_KIND_CLOSE_SESSION:
                connection.close_ussd_session()
                return exchange_result
            if ussd_request.starts_new_session:
                connection.close_ussd_session()
            ussd_reply = connection.send_ussd_request(ussd_request.request_text)
            exchange_result.reply_text = ussd_reply.text
            exchange_result.is_session_open = ussd_reply.is_session_open
        except (usb.core.USBError, RuntimeError, ValueError) as ussd_error:
            exchange_result.error_message = str(ussd_error)
        return exchange_result

    def _check_balances_if_requested(self):
        if not self.balance_check_requested.is_set():
            return
        self.balance_check_requested.clear()
        balance_results = []
        for usb_location, connection in list(self.open_connections.items()):
            status = self.current_statuses.get(usb_location)
            if status is None or status.state != dongle.STATE_READY:
                continue
            balance_results.append(self._read_balance(connection, status))
        self.balance_check_finished.emit(balance_results)

    def _read_balance(self, connection, status):
        balance_result = SimBalanceResult(status.usb_location, status.imei, status.operator_name, datetime.now())
        operator = identify_operator(status.operator_network_code, status.operator_name, load_operators())
        if operator is None or not operator.balance_ussd_code:
            reported_operator = status.operator_name or status.operator_network_code
            operator_label = f"« {reported_operator} »" if reported_operator else "inconnu"
            balance_result.error_message = (
                f"Opérateur {operator_label} non reconnu : ajoutez son code USSD dans operators.json"
            )
            return balance_result
        balance_result.operator_display_name = operator.display_name
        balance_result.used_ussd_code = operator.balance_ussd_code
        balance_result.is_ussd_code_verified = operator.balance_code_verified
        for attempt_number in range(1, BALANCE_CHECK_ATTEMPTS + 1):
            try:
                balance_result.operator_reply_text = connection.run_ussd_code(operator.balance_ussd_code)
            except (usb.core.USBError, RuntimeError) as ussd_error:
                balance_result.error_message = str(ussd_error)
            else:
                balance_result.balance_fcfa = parse_balance_with_patterns(
                    balance_result.operator_reply_text, operator.balance_reply_patterns
                )
                balance_result.error_message = (
                    None if balance_result.balance_fcfa is not None else "Réponse de l'opérateur non comprise"
                )
                if balance_result.balance_fcfa is not None or not is_transient_failure_reply(balance_result.operator_reply_text):
                    return balance_result
            if attempt_number < BALANCE_CHECK_ATTEMPTS and not self.isInterruptionRequested():
                time.sleep(BALANCE_CHECK_RETRY_PAUSE_SECONDS)
        return balance_result

    def _refuse_pending_messages(self):
        """The monitor is closing: nothing more will be sent, and waiting callers must not hang."""
        while not self.pending_messages.empty():
            self.pending_messages.get().finish(OUTCOME_NO_DONGLE, "L'application se ferme")

    def _drain_sim_inboxes(self):
        """Move every message from each ready SIM into the database, then free the SIM slots.
        A message is deleted from the SIM only after the database has saved it. Returns True if reading failed for some dongle."""
        if self.inbox_store is None:
            return False
        a_read_failed = False
        for usb_location, connection in list(self.open_connections.items()):
            status = self.current_statuses.get(usb_location)
            if status is None or status.state != dongle.STATE_READY:
                continue
            try:
                received_messages = assemble_received_messages(connection.read_stored_sms_parts(), datetime.now(timezone.utc))
                if not received_messages:
                    continue
                new_message_count = self.inbox_store.store_received_messages(received_messages)
                if new_message_count:
                    self.inbox_messages_received.emit(new_message_count)
                for received_message in received_messages:
                    for sim_index in received_message.sim_indexes:
                        if not connection.delete_stored_message(sim_index):
                            logging.warning("SIM slot %s could not be freed", sim_index)
            except (usb.core.USBError, RuntimeError, sqlite3.Error):
                logging.exception("Reading the SIM inbox failed; retrying shortly")
                a_read_failed = True
        return a_read_failed

    def _scan_once(self, usb_backend):
        full_refresh = self.force_full_refresh
        self.force_full_refresh = False
        if full_refresh:
            self.retry_not_before.clear()
            for usb_location in self.last_health_check_at:
                self.last_health_check_at[usb_location] = 0
        modem_devices = {
            dongle.usb_location_of(device): device
            for device in dongle.find_huawei_devices(usb_backend, dongle.MODEM_PRODUCT_ID)
        }
        cdrom_devices = {
            dongle.usb_location_of(device): device
            for device in dongle.find_huawei_devices(usb_backend, dongle.CDROM_PRODUCT_ID)
        }
        if modem_devices:
            self.switching_started_at.clear()
        for usb_location in list(self.cdrom_first_seen_at):
            if usb_location not in cdrom_devices:
                del self.cdrom_first_seen_at[usb_location]
                self.switching_started_at[usb_location] = time.time()  # it left CD-ROM mode: the helper switched it

        self._forget_unplugged_dongles(modem_devices)
        for usb_location, modem_device in modem_devices.items():
            self._refresh_modem(usb_location, modem_device)
        for usb_location, cdrom_device in cdrom_devices.items():
            self._handle_cdrom(usb_location, cdrom_device)

        visible_statuses = [self.current_statuses[location] for location in modem_devices]
        visible_statuses += [
            DongleStatus(location, dongle.STATE_SWITCHING)
            for location, started_at in self.switching_started_at.items()
            if location not in cdrom_devices and time.time() - started_at < SWITCH_WAIT_SECONDS
        ]
        visible_statuses += [
            self.current_statuses[location]
            for location in cdrom_devices
            if location in self.current_statuses
        ]
        self.statuses_updated.emit(visible_statuses)

    def _forget_unplugged_dongles(self, modem_devices):
        for usb_location in list(self.open_connections):
            if usb_location not in modem_devices:
                self._drop_connection(usb_location)
        for usb_location in list(self.current_statuses):
            if usb_location not in modem_devices and usb_location not in self.switching_started_at:
                del self.current_statuses[usb_location]

    def _drop_connection(self, usb_location):
        connection = self.open_connections.pop(usb_location, None)
        if connection is not None:
            connection.close()
        self.last_health_check_at.pop(usb_location, None)
        self.missed_replies.pop(usb_location, None)

    def _refresh_modem(self, usb_location, modem_device):
        if usb_location not in self.open_connections:
            if time.time() < self.retry_not_before.get(usb_location, 0):
                return
            self.current_statuses[usb_location] = DongleStatus(usb_location, dongle.STATE_INITIALIZING)
            connection = DongleConnection(modem_device)
            try:
                connection.open()
                status = DongleStatus(usb_location, dongle.STATE_READY)
                status.imei = connection.read_identity()
                connection.read_live_status(status)
            except (usb.core.USBError, RuntimeError) as open_error:
                connection.close()
                self.current_statuses[usb_location] = DongleStatus(
                    usb_location, dongle.STATE_ERROR, error_message=str(open_error)
                )
                self.retry_not_before[usb_location] = time.time() + RETRY_AFTER_ERROR_SECONDS
                return
            self.open_connections[usb_location] = connection
            self.current_statuses[usb_location] = status
            self.last_health_check_at[usb_location] = time.time()
            self.inbox_check_requested = True  # messages may have arrived while this dongle was not connected
            return

        seconds_since_check = time.time() - self.last_health_check_at[usb_location]
        if seconds_since_check < HEALTH_CHECK_INTERVAL_SECONDS:
            return
        self.last_health_check_at[usb_location] = time.time()
        try:
            self.open_connections[usb_location].read_live_status(self.current_statuses[usb_location])
            self.missed_replies[usb_location] = 0
        except (usb.core.USBError, RuntimeError):
            self.missed_replies[usb_location] = self.missed_replies.get(usb_location, 0) + 1
            if self.missed_replies[usb_location] >= MISSED_REPLIES_BEFORE_RESET:
                self._drop_connection(usb_location)  # reconnects on the next scan

    def _handle_cdrom(self, usb_location, cdrom_device):
        """Ask the root helper (mode_switcher/) to switch the dongle, retrying every few seconds, and report progress."""
        first_seen_at = self.cdrom_first_seen_at.setdefault(usb_location, time.time())
        if IS_WINDOWS:
            # Verified on the Windows PC: the dongle switches itself to modem mode a few seconds after being plugged in
            # (no helper, no root). If it stays a CD-ROM, only a replug helps.
            seconds_since_first_seen = time.time() - first_seen_at
            self.current_statuses[usb_location] = (
                DongleStatus(usb_location, dongle.STATE_DETECTED)
                if seconds_since_first_seen < MODE_SWITCHER_GRACE_SECONDS
                else DongleStatus(
                    usb_location,
                    dongle.STATE_ERROR,
                    error_message="Le dongle est resté en mode CD-ROM : débranchez-le puis rebranchez-le",
                )
            )
            return
        if not dongle.is_mode_switch_helper_installed():
            self.current_statuses[usb_location] = DongleStatus(
                usb_location,
                dongle.STATE_ERROR,
                error_message="Assistant de changement de mode non installé (sudo ./mode_switcher/install_switcher.sh)",
            )
            return
        if time.time() >= self.next_switch_attempt_at.get(usb_location, 0):
            self.next_switch_attempt_at[usb_location] = time.time() + RETRY_SWITCH_AFTER_SECONDS
            try:
                dongle.switch_dongles_to_modem_mode_through_helper()
                self.last_switch_error.pop(usb_location, None)
            except RuntimeError as switch_error:
                self.last_switch_error[usb_location] = str(switch_error)
        if time.time() - first_seen_at < MODE_SWITCHER_GRACE_SECONDS:
            self.current_statuses[usb_location] = DongleStatus(usb_location, dongle.STATE_DETECTED)
        else:
            self.current_statuses[usb_location] = DongleStatus(
                usb_location,
                dongle.STATE_ERROR,
                error_message=self.last_switch_error.get(usb_location, "Le changement de mode n'a pas abouti"),
            )
