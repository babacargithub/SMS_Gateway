"""Background thread that sends the WAITING messages one at a time. It can be paused, resumed and stopped.

Pause and stop never cut a message in half: the one being sent finishes first (a real SMS in flight cannot be
taken back, and its outcome must be recorded), then the worker waits (pause) or ends (stop).

Replies (priority messages queued from the Inbox) are always taken before the next campaign message: once the message in
flight is done, every waiting reply goes out one after the other, without the pause meant for campaign messages. A
`replies_only` worker sends just the replies and ends: it serves replies while the campaign is idle or paused."""
import random
import threading
import time

from PySide6.QtCore import QThread, Signal

from common.keep_awake import keep_mac_awake
from outbox import outbox_store
from outbox.message_sender import DONGLE_UNAVAILABLE

from outbox.sending_speed import find_sending_speed, DEFAULT_SENDING_SPEED_KEY

GAP_BETWEEN_MESSAGES_SECONDS_RANGE = find_sending_speed(DEFAULT_SENDING_SPEED_KEY).gap_seconds_range
GAP_POLL_SECONDS = 0.2  # how often the pause between two messages checks for a stop request or a new reply


class SendingWorker(QThread):
    message_state_changed = Signal(int, str, str)   # message id, new state, error detail
    progress_changed = Signal(int, int)             # messages handled in this run, total for this run
    dongle_unavailable = Signal(str)                # reason; the worker has paused itself and the message is back to WAITING
    run_finished = Signal(bool)                     # True if it ended because everything was handled, False if stopped

    def __init__(self, outbox_store_instance, message_sender, gap_seconds_range=GAP_BETWEEN_MESSAGES_SECONDS_RANGE,
                 replies_only=False):
        super().__init__()
        self.replies_only = replies_only
        self.reply_queued = threading.Event()   # set by the UI when a reply was queued: cuts the current pause short
        self.outbox_store = outbox_store_instance
        self.message_sender = message_sender
        self.gap_seconds_range = gap_seconds_range  # may be replaced while running: read again after every message
        self.stop_requested = threading.Event()
        self.may_continue = threading.Event()   # cleared while paused
        self.may_continue.set()

    def pause(self):
        self.may_continue.clear()

    def resume(self):
        self.may_continue.set()

    def stop(self):
        self.stop_requested.set()
        self.may_continue.set()  # wake the worker if it is paused

    def notify_reply_queued(self):
        self.reply_queued.set()

    def _wait_between_messages(self):
        """The random pause between two campaign messages. True if a stop was requested meanwhile; a new reply ends the
        pause early (the loop then sends it)."""
        pause_ends_at = time.monotonic() + random.uniform(*self.gap_seconds_range)
        while True:
            if self.stop_requested.is_set():
                return True
            if self.reply_queued.is_set():
                return False
            remaining_seconds = pause_ends_at - time.monotonic()
            if remaining_seconds <= 0:
                return False
            self.stop_requested.wait(min(remaining_seconds, GAP_POLL_SECONDS))

    def run(self):
        with keep_mac_awake("SMS Gateway : envoi de messages en cours"):
            self._send_waiting_messages()

    def _send_waiting_messages(self):
        handled_count = 0
        if not self.replies_only:
            self.progress_changed.emit(0, self.outbox_store.count_by_state()[outbox_store.WAITING])
        finished_everything = True
        while True:
            self.may_continue.wait()
            if self.stop_requested.is_set():
                finished_everything = False
                break
            self.reply_queued.clear()  # the claim below takes any reply queued so far
            message_to_send = self.outbox_store.claim_next_waiting_message(replies_only=self.replies_only)
            if message_to_send is None:
                break
            self.message_state_changed.emit(message_to_send.message_id, outbox_store.SENDING, "")
            try:
                send_result = self.message_sender.send(message_to_send.phone_number, message_to_send.message_text)
            except Exception as sending_error:  # an unexpected crash mid-send: the outcome is unknown, never retry it blindly
                send_result = None
                final_state, error_detail = outbox_store.UNKNOWN, f"Erreur inattendue pendant l'envoi : {sending_error}"
            else:
                final_state, error_detail = send_result.final_state, send_result.error_detail
            if final_state == DONGLE_UNAVAILABLE:
                # certainly not sent: put it back first in line and wait for the user to resume
                self.outbox_store.return_message_to_waiting(message_to_send.message_id)
                self.message_state_changed.emit(message_to_send.message_id, outbox_store.WAITING, "")
                self.dongle_unavailable.emit(error_detail)
                if self.replies_only:
                    finished_everything = False  # nobody resumes a replies-only worker: it ends, the reply stays queued
                    break
                self.may_continue.clear()
                continue
            self.outbox_store.finish_message(message_to_send.message_id, final_state, error_detail)
            self.message_state_changed.emit(message_to_send.message_id, final_state, error_detail)

            handled_count += 1
            if self.replies_only:
                continue  # the next claim returns None once every reply is handled
            still_waiting = self.outbox_store.count_by_state()[outbox_store.WAITING]
            self.progress_changed.emit(handled_count, handled_count + still_waiting)
            if still_waiting == 0:
                break
            if self.outbox_store.count_waiting_replies() > 0:
                continue  # replies go out one after the other, before the next campaign message
            if self._wait_between_messages():
                finished_everything = False
                break
        self.run_finished.emit(finished_everything)
