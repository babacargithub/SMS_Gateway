"""Runs one backend request off the UI thread and hands the answer back on it."""
import logging

from PySide6.QtCore import QThread, Signal

from globe_transports.globe_transports_client import GlobeTransportsError

_threads_still_running = set()  # keeps a thread alive if the dialog that asked is closed before the answer comes


class BackgroundRequestThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)   # French message

    def __init__(self, request_function):
        super().__init__()
        self.request_function = request_function

    def run(self):
        try:
            result = self.request_function()
        except GlobeTransportsError as request_error:
            self.failed.emit(str(request_error))
        except Exception:  # a bug must not leave the dialog waiting forever
            logging.exception("Unexpected error while calling Globe Transports")
            self.failed.emit("Erreur inattendue pendant la communication avec Globe Transports.")
        else:
            self.succeeded.emit(result)


def run_in_background(request_function, on_success, on_failure):
    """`on_success(result)` or `on_failure(french_message)` runs on the UI thread."""
    request_thread = BackgroundRequestThread(request_function)
    _threads_still_running.add(request_thread)
    request_thread.succeeded.connect(on_success)
    request_thread.failed.connect(on_failure)
    request_thread.finished.connect(lambda: _threads_still_running.discard(request_thread))
    request_thread.start()
