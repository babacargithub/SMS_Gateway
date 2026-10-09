"""Lets the client (running in a background thread) ask for the login dialog, which must live on the UI thread."""
import threading

from PySide6.QtCore import QObject, QThread, Signal, Slot

from globe_transports.globe_login_dialog import GlobeLoginDialog


class LoginPrompter(QObject):
    prompt_requested = Signal()

    def __init__(self, parent_widget):
        super().__init__(parent_widget)
        self.parent_widget = parent_widget
        self.login_function = None   # set to the current client's `login`
        self.base_url = ""
        self.prompt_answered = threading.Event()
        self.login_succeeded = False
        self.prompt_requested.connect(self.show_login_dialog)

    def request_login(self):
        """Called by the client from any thread: blocks until the owner logged in (True) or cancelled (False)."""
        if QThread.currentThread() is self.thread():
            self.show_login_dialog()
            return self.login_succeeded
        self.prompt_answered.clear()
        self.prompt_requested.emit()   # queued onto the UI thread
        self.prompt_answered.wait()
        return self.login_succeeded

    @Slot()
    def show_login_dialog(self):
        try:
            login_dialog = GlobeLoginDialog(self.login_function, self.base_url, self.parent_widget)
            self.login_succeeded = login_dialog.exec() == GlobeLoginDialog.Accepted
        finally:
            self.prompt_answered.set()
