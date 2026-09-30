"""Background thread that owns every USB dongle and publishes status snapshots to the UI."""
import logging
import queue
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field

import usb.core
from PySide6.QtCore import QThread, Signal

import dongle
from dongle import DongleConnection, DongleStatus

SCAN_INTERVAL_MS = 3000
HEALTH_CHECK_INTERVAL_SECONDS = 30
MISSED_REPLIES_BEFORE_RESET = 3
SWITCH_WAIT_SECONDS = 20
RETRY_AFTER_ERROR_SECONDS = 10


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

    def __init__(self, inbox_store=None):
        super().__init__()
        self.inbox_store = inbox_store
        self.open_connections = {}   # usb_location -> DongleConnection
        self.current_statuses = {}   # usb_location -> DongleStatus
        self.last_health_check_at = {}
        self.missed_replies = {}
        self.retry_not_before = {}
        self.switching_started_at = {}
        self.wake_up_requested = threading.Event()
        self.pending_messages = queue.Queue()
        self.force_full_refresh = False

    def request_immediate_rescan(self):
        """Called from the UI thread: rescan now, ignoring health-check and retry timers."""
        self.force_full_refresh = True
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
            self._send_pending_messages()
            try:
                self._scan_once(usb_backend)
                self._drain_sim_inboxes()
            except Exception:  # a bad scan must never kill the monitor for good
                logging.exception("Dongle scan failed; retrying on next cycle")
            self.wake_up_requested.wait(SCAN_INTERVAL_MS / 1000)
            self.wake_up_requested.clear()
        self._refuse_pending_messages()
        for connection in self.open_connections.values():
            connection.close()

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

    def _refuse_pending_messages(self):
        """The monitor is closing: nothing more will be sent, and waiting callers must not hang."""
        while not self.pending_messages.empty():
            self.pending_messages.get().finish(OUTCOME_NO_DONGLE, "L'application se ferme")

    def _drain_sim_inboxes(self):
        """Move every message from each ready SIM into the database, then free the SIM slots.
        A message is deleted from the SIM only after the database has saved it."""
        if self.inbox_store is None:
            return
        for usb_location, connection in list(self.open_connections.items()):
            status = self.current_statuses.get(usb_location)
            if status is None or status.state != dongle.STATE_READY:
                continue
            try:
                received_messages = connection.read_stored_text_messages()
                if not received_messages:
                    continue
                new_message_count = self.inbox_store.store_received_messages(received_messages)
                if new_message_count:
                    self.inbox_messages_received.emit(new_message_count)
                for received_message in received_messages:
                    if not connection.delete_stored_message(received_message.sim_index):
                        logging.warning("SIM slot %s could not be freed", received_message.sim_index)
            except (usb.core.USBError, RuntimeError, sqlite3.Error):
                logging.exception("Reading the SIM inbox failed; retrying on next cycle")

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
        if time.time() < self.retry_not_before.get(usb_location, 0):
            return
        self.current_statuses[usb_location] = DongleStatus(usb_location, dongle.STATE_DETECTED)
        try:
            dongle.switch_to_modem_mode(cdrom_device)
            self.switching_started_at[usb_location] = time.time()
        except (usb.core.USBError, RuntimeError, OSError, subprocess.SubprocessError) as switch_error:
            self.current_statuses[usb_location] = DongleStatus(
                usb_location, dongle.STATE_ERROR, error_message=f"Échec du changement de mode : {switch_error}"
            )
        self.retry_not_before[usb_location] = time.time() + RETRY_AFTER_ERROR_SECONDS
