from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sms_text import MAX_SINGLE_SMS_LENGTH, normalize_senegal_number, sanitize_for_text_mode

GRAY_TEXT = "color: gray;"
RED_TEXT = "color: #c0392b;"
GREEN_TEXT = "color: #1e8449;"
ORANGE_TEXT = "color: #b9770e;"


class ComposePage(QWidget):
    send_requested = Signal(str, str)  # normalized destination number, clean message text

    def __init__(self):
        super().__init__()
        self.send_in_progress = False

        self.number_input = QLineEdit()
        self.number_input.setPlaceholderText("77 123 45 67  ou  +221771234567")
        self.number_hint_label = QLabel("")

        self.message_input = QPlainTextEdit()
        self.message_input.setPlaceholderText("Saisissez votre message...")
        self.message_counter_label = QLabel("")
        self.message_warning_label = QLabel("")
        self.message_warning_label.setStyleSheet(ORANGE_TEXT)

        self.send_button = QPushButton("Envoyer")
        self.send_button.setDefault(True)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)

        form_layout = QFormLayout()
        form_layout.addRow("À :", self.number_input)
        form_layout.addRow("", self.number_hint_label)
        form_layout.addRow("Message :", self.message_input)
        form_layout.addRow("", self.message_counter_label)
        form_layout.addRow("", self.message_warning_label)

        page_layout = QVBoxLayout(self)
        page_layout.addLayout(form_layout)
        page_layout.addWidget(self.send_button)
        page_layout.addWidget(self.result_label)
        page_layout.addStretch()

        self.number_input.textChanged.connect(self.refresh_validation)
        self.message_input.textChanged.connect(self.refresh_validation)
        self.send_button.clicked.connect(self.request_send)
        self.refresh_validation()

    def normalized_number(self):
        return normalize_senegal_number(self.number_input.text())

    def clean_message(self):
        return sanitize_for_text_mode(self.message_input.toPlainText())

    def refresh_validation(self):
        typed_number = self.number_input.text().strip()
        normalized_number = self.normalized_number()
        if not typed_number:
            self.number_hint_label.setText("")
        elif normalized_number:
            self.number_hint_label.setStyleSheet(GRAY_TEXT)
            self.number_hint_label.setText(f"Sera envoyé à {normalized_number}")
        else:
            self.number_hint_label.setStyleSheet(RED_TEXT)
            self.number_hint_label.setText("Numéro invalide : 9 chiffres attendus (ou +221 suivi de 9 chiffres)")

        clean_text, was_changed = self.clean_message()
        message_length = len(clean_text)
        is_too_long = message_length > MAX_SINGLE_SMS_LENGTH
        self.message_counter_label.setText(f"{message_length} / {MAX_SINGLE_SMS_LENGTH} caractères")
        self.message_counter_label.setStyleSheet(RED_TEXT if is_too_long else GRAY_TEXT)
        self.message_warning_label.setText(
            "Les accents et caractères spéciaux seront simplifiés (é → e, autres → ?)." if was_changed else ""
        )

        self.send_button.setEnabled(
            bool(normalized_number) and 0 < message_length <= MAX_SINGLE_SMS_LENGTH and not self.send_in_progress
        )

    def request_send(self):
        clean_text, _ = self.clean_message()
        self.send_in_progress = True
        self.refresh_validation()
        self.result_label.setStyleSheet(GRAY_TEXT)
        self.result_label.setText("Envoi en cours...")
        self.send_requested.emit(self.normalized_number(), clean_text)

    def show_send_result(self, destination_number, outcome, detail):
        self.send_in_progress = False
        if outcome == "sent":
            self.result_label.setStyleSheet(GREEN_TEXT)
            self.result_label.setText(f"Envoyé à {destination_number}.")
            self.message_input.clear()
        elif outcome == "unknown":
            self.result_label.setStyleSheet(ORANGE_TEXT)
            self.result_label.setText(
                f"Non confirmé pour {destination_number} : {detail}. Le message a peut-être été envoyé, "
                "vérifiez auprès du destinataire avant de renvoyer."
            )
        else:
            self.result_label.setStyleSheet(RED_TEXT)
            self.result_label.setText(f"Non envoyé à {destination_number} : {detail}")
        self.refresh_validation()
