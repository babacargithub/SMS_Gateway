"""Reads a gateway's remaining balance off the UI thread (the HTTP call can take several seconds)."""
from PySide6.QtCore import QThread, Signal

_threads_still_running = set()  # keeps a thread alive if the widget that asked is closed before the answer comes


class BalanceReaderThread(QThread):
    balance_read = Signal(object, str)  # the gateway's balance (None on failure), French error message ("" on success)

    def __init__(self, gateway):
        super().__init__()
        self.gateway = gateway

    def run(self):
        try:
            balance = self.gateway.read_balance()
        except RuntimeError as balance_error:
            self.balance_read.emit(None, str(balance_error))
            return
        self.balance_read.emit(balance, "")


def start_balance_reading(gateway, on_balance_read):
    """Starts reading `gateway`'s balance; `on_balance_read(balance_or_None, error_message)` runs on the UI thread."""
    balance_thread = BalanceReaderThread(gateway)
    _threads_still_running.add(balance_thread)
    balance_thread.balance_read.connect(on_balance_read)
    balance_thread.finished.connect(lambda: _threads_still_running.discard(balance_thread))
    balance_thread.start()
