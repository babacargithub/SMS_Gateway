"""Background thread that sends the WAITING messages one at a time. It can be paused, resumed and stopped.

Pause and stop never cut a message in half: the one being sent finishes first (a real SMS in flight cannot be
taken back, and its outcome must be recorded), then the worker waits (pause) or ends (stop)."""
import threading

from PySide6.QtCore import QThread, Signal

import outbox_store

GAP_BETWEEN_MESSAGES_SECONDS = 0.1  # simulation only; real sending will use a steady, slower rate


class SendingWorker(QThread):
    message_state_changed = Signal(int, str, str)   # message id, new state, error detail
    progress_changed = Signal(int, int)             # messages handled in this run, total for this run
    run_finished = Signal(bool)                     # True if it ended because everything was handled, False if stopped

    def __init__(self, outbox_store_instance, message_sender, gap_seconds=GAP_BETWEEN_MESSAGES_SECONDS):
        super().__init__()
        self.outbox_store = outbox_store_instance
        self.message_sender = message_sender
        self.gap_seconds = gap_seconds
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

    def run(self):
        handled_count = 0
        self.progress_changed.emit(0, self.outbox_store.count_by_state()[outbox_store.WAITING])
        finished_everything = True
        while True:
            self.may_continue.wait()
            if self.stop_requested.is_set():
                finished_everything = False
                break
            message_to_send = self.outbox_store.claim_next_waiting_message()
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
            self.outbox_store.finish_message(message_to_send.message_id, final_state, error_detail)
            self.message_state_changed.emit(message_to_send.message_id, final_state, error_detail)

            handled_count += 1
            still_waiting = self.outbox_store.count_by_state()[outbox_store.WAITING]
            self.progress_changed.emit(handled_count, handled_count + still_waiting)
            if self.stop_requested.wait(self.gap_seconds):
                finished_everything = False
                break
        self.run_finished.emit(finished_everything)
