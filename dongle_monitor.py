"""Background thread that owns every USB dongle and publishes status snapshots to the UI."""
import logging
import queue
import subprocess
import threading
import time

import usb.core
from PySide6.QtCore import QThread, Signal

import dongle
from dongle import DongleConnection, DongleStatus

SCAN_INTERVAL_MS = 3000
HEALTH_CHECK_INTERVAL_SECONDS = 30
MISSED_REPLIES_BEFORE_RESET = 3
SWITCH_WAIT_SECONDS = 20
RETRY_AFTER_ERROR_SECONDS = 10


class DongleMonitor(QThread):
    statuses_updated = Signal(list)
    sms_send_finished = Signal(str, str, str)  # destination number, outcome, detail

    OUTCOME_SENT = "sent"
    OUTCOME_FAILED = "failed"
    OUTCOME_UNKNOWN = "unknown"

    def __init__(self):
        super().__init__()
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
        """Called from the UI thread; the result arrives via sms_send_finished."""
        self.pending_messages.put((destination_number, message_text))
        self.wake_up_requested.set()

    def stop(self):
        self.requestInterruption()
        self.wake_up_requested.set()

    def run(self):
        usb_backend = dongle.load_usb_backend()
        if usb_backend is None:
            self.statuses_updated.emit(
                [DongleStatus("-", dongle.STATE_ERROR, error_message="libusb not found (brew install libusb)")]
            )
            return
        while not self.isInterruptionRequested():
            self._send_pending_messages()
            try:
                self._scan_once(usb_backend)
            except Exception:  # a bad scan must never kill the monitor for good
                logging.exception("Dongle scan failed; retrying on next cycle")
            self.wake_up_requested.wait(SCAN_INTERVAL_MS / 1000)
            self.wake_up_requested.clear()
        for connection in self.open_connections.values():
            connection.close()

    def _send_pending_messages(self):
        while not self.pending_messages.empty():
            destination_number, message_text = self.pending_messages.get()
            ready_locations = [
                location for location in self.open_connections
                if self.current_statuses[location].state == dongle.STATE_READY
            ]
            if not ready_locations:
                self.sms_send_finished.emit(destination_number, self.OUTCOME_FAILED, "No dongle is ready")
                continue
            connection = self.open_connections[ready_locations[0]]
            try:
                connection.send_text_message(destination_number, message_text)
                self.sms_send_finished.emit(destination_number, self.OUTCOME_SENT, "")
            except dongle.SendRejected as rejection:
                self.sms_send_finished.emit(destination_number, self.OUTCOME_FAILED, str(rejection))
            except dongle.SendOutcomeUnknown as uncertainty:
                self.sms_send_finished.emit(destination_number, self.OUTCOME_UNKNOWN, str(uncertainty))
            except (usb.core.USBError, RuntimeError) as send_error:
                self.sms_send_finished.emit(destination_number, self.OUTCOME_FAILED, str(send_error))

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
                usb_location, dongle.STATE_ERROR, error_message=f"Mode switch failed: {switch_error}"
            )
        self.retry_not_before[usb_location] = time.time() + RETRY_AFTER_ERROR_SECONDS
