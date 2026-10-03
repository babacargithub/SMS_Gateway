from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from sim.ussd_session import is_valid_ussd_input

MUTED_STYLE_SHEET = "color: gray;"
ERROR_STYLE_SHEET = "color: #DC2626;"
TRANSCRIPT_MINIMUM_HEIGHT_PIXELS = 160


class UssdDialer(QGroupBox):
    """Type a USSD code, read the operator's reply, answer its menu. Every choice is made by the person typing:
    nothing here ever picks an option on its own (some operator menus sell bundles)."""

    ussd_request_submitted = Signal(str, object, bool)  # text, usb_location holding the session (or None), starts a new session
    session_close_requested = Signal(object)            # usb_location

    def __init__(self):
        super().__init__("Composeur USSD")
        self.session_usb_location = None
        self.is_session_open = False
        self.is_waiting_for_operator = False

        self.transcript_view = QPlainTextEdit()
        self.transcript_view.setReadOnly(True)
        self.transcript_view.setMinimumHeight(TRANSCRIPT_MINIMUM_HEIGHT_PIXELS)
        self.transcript_view.setPlaceholderText("Les échanges avec l'opérateur apparaissent ici.")

        self.input_field = QLineEdit()
        self.input_field.setMaxLength(30)
        self.input_field.returnPressed.connect(self.submit_typed_text)
        self.submit_button = QPushButton()
        self.submit_button.clicked.connect(self.submit_typed_text)
        self.close_session_button = QPushButton("Fermer la session")
        self.close_session_button.clicked.connect(self.close_session)
        self.clear_button = QPushButton("Effacer")
        self.clear_button.clicked.connect(self.transcript_view.clear)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        warning_label = QLabel("Attention : certains menus d'opérateur déclenchent des achats. Relisez chaque choix avant de répondre.")
        warning_label.setStyleSheet(MUTED_STYLE_SHEET)
        warning_label.setWordWrap(True)

        input_layout = QHBoxLayout()
        input_layout.addWidget(self.input_field, 1)
        input_layout.addWidget(self.submit_button)
        input_layout.addWidget(self.close_session_button)
        input_layout.addWidget(self.clear_button)

        dialer_layout = QVBoxLayout(self)
        dialer_layout.addWidget(self.transcript_view)
        dialer_layout.addLayout(input_layout)
        dialer_layout.addWidget(self.status_label)
        dialer_layout.addWidget(warning_label)
        self.refresh_controls()

    def refresh_controls(self):
        can_type = not self.is_waiting_for_operator
        self.input_field.setEnabled(can_type)
        self.submit_button.setEnabled(can_type)
        self.close_session_button.setEnabled(can_type and self.is_session_open)
        self.submit_button.setText("Répondre" if self.is_session_open else "Composer")
        self.input_field.setPlaceholderText(
            "Votre choix, ex. 1" if self.is_session_open else "Code USSD, ex. *222#"
        )

    def show_status(self, status_text, is_error=False):
        self.status_label.setStyleSheet(ERROR_STYLE_SHEET if is_error else MUTED_STYLE_SHEET)
        self.status_label.setText(status_text)

    def append_to_transcript(self, speaker_label, text):
        self.transcript_view.appendPlainText(f"{speaker_label} : {text}\n")

    def submit_typed_text(self):
        typed_text = self.input_field.text().strip()
        if self.is_waiting_for_operator or not typed_text:
            return
        if not is_valid_ussd_input(typed_text):
            self.show_status("Saisie invalide : seuls les chiffres et les signes * # + sont acceptés.", is_error=True)
            return
        self.append_to_transcript("Vous", typed_text)
        self.input_field.clear()
        self.is_waiting_for_operator = True
        self.refresh_controls()
        self.show_status("En attente de l'opérateur (jusqu'à 25 secondes)…")
        self.ussd_request_submitted.emit(typed_text, self.session_usb_location if self.is_session_open else None, not self.is_session_open)

    def close_session(self):
        if self.is_session_open:
            self.session_close_requested.emit(self.session_usb_location)
        self.mark_session_closed()
        self.show_status("Session fermée.")

    def mark_session_closed(self):
        self.is_session_open = False
        self.session_usb_location = None
        self.refresh_controls()

    def show_exchange_result(self, exchange_result):
        self.is_waiting_for_operator = False
        if exchange_result.error_message:
            self.append_to_transcript("Erreur", exchange_result.error_message)
            self.mark_session_closed()
            self.show_status(exchange_result.error_message, is_error=True)
            return
        if exchange_result.reply_text:
            self.append_to_transcript("Opérateur", exchange_result.reply_text)
        self.is_session_open = exchange_result.is_session_open
        self.session_usb_location = exchange_result.usb_location if exchange_result.is_session_open else None
        self.refresh_controls()
        dongle_label = f" (dongle {exchange_result.imei})" if exchange_result.imei else ""
        self.show_status(
            f"Session ouverte : tapez le numéro de votre choix{dongle_label}." if self.is_session_open else f"Session terminée{dongle_label}."
        )
        self.input_field.setFocus()

    def abandon_session_after_other_ussd_use(self):
        """The balance check closes any open session before it dials: keep the dialer honest about that."""
        if self.is_session_open:
            self.append_to_transcript("Info", "Session fermée par la vérification du solde.")
        self.mark_session_closed()
