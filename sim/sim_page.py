from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from sim.ussd_dialer import UssdDialer
from common.french_labels import NARROW_NO_BREAK_SPACE, format_number

BALANCE_STYLE_SHEET = "font-size: 34px; font-weight: 600;"
CARD_STYLE_SHEET = "QFrame#simCard { border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 8px; }"
MUTED_STYLE_SHEET = "color: gray;"
ERROR_STYLE_SHEET = "color: #DC2626;"


def format_balance_fcfa(balance_fcfa):
    return f"{format_number(balance_fcfa)}{NARROW_NO_BREAK_SPACE}FCFA"


class SimCard(QFrame):
    """One SIM: operator, balance and when it was read."""

    def __init__(self, balance_result):
        super().__init__()
        self.setObjectName("simCard")
        self.setStyleSheet(CARD_STYLE_SHEET)

        operator_label = QLabel(balance_result.operator_display_name or "Opérateur inconnu")
        operator_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        identity_label = QLabel(f"IMEI {balance_result.imei or '-'}  ·  USB {balance_result.usb_location}")
        identity_label.setStyleSheet(MUTED_STYLE_SHEET)

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.addWidget(operator_label)
        card_layout.addWidget(identity_label)

        if balance_result.balance_fcfa is not None:
            balance_label = QLabel(format_balance_fcfa(balance_result.balance_fcfa))
            balance_label.setStyleSheet(BALANCE_STYLE_SHEET)
            card_layout.addWidget(balance_label)
        else:
            error_label = QLabel(balance_result.error_message or "Solde indisponible")
            error_label.setStyleSheet(ERROR_STYLE_SHEET)
            error_label.setWordWrap(True)
            card_layout.addWidget(error_label)
            if balance_result.operator_reply_text:
                reply_label = QLabel(f"Réponse reçue : {balance_result.operator_reply_text}")
                reply_label.setStyleSheet(MUTED_STYLE_SHEET)
                reply_label.setWordWrap(True)
                reply_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                card_layout.addWidget(reply_label)

        if balance_result.used_ussd_code and not balance_result.is_ussd_code_verified:
            unverified_label = QLabel(
                f"Code {balance_result.used_ussd_code} non encore vérifié pour cet opérateur : "
                "corrigez-le dans operators.json s'il ne donne pas le solde."
            )
            unverified_label.setStyleSheet(MUTED_STYLE_SHEET)
            unverified_label.setWordWrap(True)
            card_layout.addWidget(unverified_label)

        checked_at_label = QLabel(f"Relevé le {balance_result.checked_at:%d/%m/%Y à %H:%M}")
        checked_at_label.setStyleSheet(MUTED_STYLE_SHEET)
        card_layout.addWidget(checked_at_label)


class SimPage(QWidget):
    balance_check_requested = Signal()
    ussd_request_submitted = Signal(str, object, bool)
    session_close_requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.check_button = QPushButton("Vérifier le solde")
        self.check_button.setToolTip("Interroge l'opérateur (code USSD) ; peut prendre jusqu'à une minute")
        self.check_button.clicked.connect(self.start_balance_check)
        self.status_label = QLabel("Cliquez sur « Vérifier le solde » pour interroger la SIM.")
        self.status_label.setStyleSheet(MUTED_STYLE_SHEET)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.addWidget(self.check_button)
        toolbar_layout.addWidget(self.status_label)
        toolbar_layout.addStretch()

        self.cards_layout = QVBoxLayout()
        self.ussd_dialer = UssdDialer()
        self.ussd_dialer.ussd_request_submitted.connect(self.ussd_request_submitted)
        self.ussd_dialer.session_close_requested.connect(self.session_close_requested)
        page_layout = QVBoxLayout(self)
        page_layout.addLayout(toolbar_layout)
        page_layout.addLayout(self.cards_layout)
        page_layout.addWidget(self.ussd_dialer, 1)

    def start_balance_check(self):
        self.check_button.setEnabled(False)
        self.status_label.setText("Interrogation de l'opérateur en cours…")
        self.balance_check_requested.emit()

    def show_ussd_exchange_result(self, exchange_result):
        self.ussd_dialer.show_exchange_result(exchange_result)

    def show_balance_results(self, balance_results):
        self.ussd_dialer.abandon_session_after_other_ussd_use()
        while self.cards_layout.count():
            old_card = self.cards_layout.takeAt(0).widget()
            if old_card is not None:
                old_card.deleteLater()
        self.check_button.setEnabled(True)
        if not balance_results:
            self.status_label.setText("Aucun dongle prêt : branchez un dongle et attendez qu'il soit « Prêt » dans Appareils.")
            return
        self.status_label.setText("")
        for balance_result in balance_results:
            self.cards_layout.addWidget(SimCard(balance_result))
