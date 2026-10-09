"""« Se connecter à Globe Transports » : asks for the username and password, logs in, and forgets the password."""
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from globe_transports.background_request import run_in_background

RED_TEXT = "color: #c0392b;"
GRAY_TEXT = "color: gray;"


class GlobeLoginDialog(QDialog):
    """Accepted only once `login_function(username, password)` succeeded (it stores the token itself)."""

    def __init__(self, login_function, base_url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Se connecter à Globe Transports")
        self.setMinimumWidth(420)
        self.login_function = login_function
        self.is_busy = False
        self.is_closed = False

        self.server_label = QLabel(f"Serveur : {base_url}")
        self.server_label.setStyleSheet(GRAY_TEXT)
        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.login_button = self.button_box.button(QDialogButtonBox.Ok)
        self.login_button.setText("Se connecter")
        self.button_box.button(QDialogButtonBox.Cancel).setText("Annuler")
        self.button_box.accepted.connect(self.start_login)
        self.button_box.rejected.connect(self.reject)

        form_layout = QFormLayout()
        form_layout.addRow("Nom d'utilisateur :", self.username_input)
        form_layout.addRow("Mot de passe :", self.password_input)
        note_label = QLabel("Le mot de passe n'est jamais enregistré : seule la connexion (valable 90 jours) l'est, dans le trousseau d'accès de macOS.")
        note_label.setWordWrap(True)
        note_label.setStyleSheet(GRAY_TEXT)
        dialog_layout = QVBoxLayout(self)
        dialog_layout.addWidget(self.server_label)
        dialog_layout.addLayout(form_layout)
        dialog_layout.addWidget(self.message_label)
        dialog_layout.addWidget(note_label)
        dialog_layout.addWidget(self.button_box)
        self.username_input.textChanged.connect(self.refresh_login_button)
        self.password_input.textChanged.connect(self.refresh_login_button)
        self.refresh_login_button()

    def refresh_login_button(self):
        self.login_button.setEnabled(bool(self.username_input.text().strip() and self.password_input.text()) and not self.is_busy)

    def set_busy(self, is_busy):
        self.is_busy = is_busy
        self.username_input.setEnabled(not is_busy)
        self.password_input.setEnabled(not is_busy)
        self.refresh_login_button()

    def start_login(self):
        if self.is_busy or not self.login_button.isEnabled():
            return
        username, password = self.username_input.text(), self.password_input.text()
        self.message_label.setStyleSheet(GRAY_TEXT)
        self.message_label.setText("Connexion en cours…")
        self.set_busy(True)
        run_in_background(
            lambda: self.login_function(username, password),
            lambda _result: self.on_login_succeeded(),
            self.on_login_failed,
        )

    def on_login_succeeded(self):
        if self.is_closed:
            return
        self.password_input.clear()
        self.accept()

    def on_login_failed(self, message):
        if self.is_closed:
            return
        self.set_busy(False)
        self.password_input.clear()
        self.password_input.setFocus()
        self.message_label.setStyleSheet(RED_TEXT)
        self.message_label.setText(message)

    def done(self, result_code):
        self.is_closed = True
        self.password_input.clear()   # never leave the password sitting in the widget
        super().done(result_code)
