from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from common.french_labels import NARROW_NO_BREAK_SPACE, format_number
from gateways.balance_reader_thread import start_balance_reading
from gateways.gateway_settings_dialog import OrangeSettingsDialog
from sim.ussd_dialer import UssdDialer

BALANCE_STYLE_SHEET = "font-size: 34px; font-weight: 600;"
CARD_STYLE_SHEET = "QFrame#simCard { border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 8px; }"
MUTED_STYLE_SHEET = "color: gray;"
ERROR_STYLE_SHEET = "color: #DC2626;"
OPEN_ENDED_OFFER_YEAR = 2090  # an offer expiring after this (Orange: 2100) has no real expiry date


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


class GatewayCard(QFrame):
    """One online SMS gateway (Orange SMS): remaining SMS units, or why they could not be read."""

    configure_requested = Signal()

    def __init__(self, gateway):
        super().__init__()
        self.setObjectName("simCard")
        self.setStyleSheet(CARD_STYLE_SHEET)
        self.gateway = gateway

        name_label = QLabel(f"{gateway.display_name} (passerelle SMS)")
        name_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.configure_button = QPushButton("Configurer…")
        self.configure_button.clicked.connect(self.configure_requested)
        self.balance_label = QLabel("Lecture du solde…")
        self.balance_label.setWordWrap(True)
        self.balance_label.setStyleSheet(MUTED_STYLE_SHEET)
        self.detail_label = QLabel()
        self.detail_label.setStyleSheet(MUTED_STYLE_SHEET)
        self.detail_label.setVisible(False)

        header_layout = QHBoxLayout()
        header_layout.addWidget(name_label)
        header_layout.addStretch()
        header_layout.addWidget(self.configure_button)
        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.addLayout(header_layout)
        card_layout.addWidget(self.balance_label)
        card_layout.addWidget(self.detail_label)

    def show_not_configured(self):
        self.balance_label.setText("Non configuré : cliquez sur « Configurer… » pour saisir les identifiants.")
        self.balance_label.setStyleSheet(MUTED_STYLE_SHEET)

    def show_balance(self, balance, error_message):
        if balance is None:
            self.balance_label.setText(error_message)
            self.balance_label.setStyleSheet(ERROR_STYLE_SHEET)
            return
        self.balance_label.setText(format_sms_units(balance.available_units))
        self.balance_label.setStyleSheet(BALANCE_STYLE_SHEET)
        if balance.nearest_expiration is not None and balance.nearest_expiration.year < OPEN_ENDED_OFFER_YEAR:
            self.detail_label.setText(f"Expire le {balance.nearest_expiration:%d/%m/%Y}")
            self.detail_label.setVisible(True)


def format_sms_units(available_units):
    return f"{format_number(available_units)}{NARROW_NO_BREAK_SPACE}SMS"


class SimPage(QWidget):
    balance_check_requested = Signal()
    ussd_request_submitted = Signal(str, object, bool)
    session_close_requested = Signal(object)

    def __init__(self, gateways=()):
        super().__init__()
        self.gateways = list(gateways)
        self.sim_check_pending = False
        self.gateway_checks_pending = 0
        self.check_button = QPushButton("Vérifier le solde")
        self.check_button.setToolTip(
            "Interroge l'opérateur de la SIM (code USSD, jusqu'à une minute) et les passerelles SMS (Orange SMS)"
        )
        self.check_button.clicked.connect(self.start_balance_check)
        self.status_label = QLabel("Cliquez sur « Vérifier le solde » pour interroger la SIM.")
        self.status_label.setStyleSheet(MUTED_STYLE_SHEET)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.addWidget(self.check_button)
        toolbar_layout.addWidget(self.status_label)
        toolbar_layout.addStretch()

        self.cards_layout = QVBoxLayout()
        self.gateway_cards_layout = QVBoxLayout()
        self.ussd_dialer = UssdDialer()
        self.ussd_dialer.ussd_request_submitted.connect(self.ussd_request_submitted)
        self.ussd_dialer.session_close_requested.connect(self.session_close_requested)
        page_layout = QVBoxLayout(self)
        page_layout.addLayout(toolbar_layout)
        page_layout.addLayout(self.cards_layout)
        page_layout.addLayout(self.gateway_cards_layout)
        page_layout.addWidget(self.ussd_dialer, 1)

    def start_balance_check(self):
        self.check_button.setEnabled(False)
        self.status_label.setText("Interrogation de l'opérateur en cours…")
        self.sim_check_pending = True
        self.show_gateway_balances()
        self.balance_check_requested.emit()

    def show_gateway_balances(self):
        self.clear_layout(self.gateway_cards_layout)
        for gateway in self.gateways:
            gateway_card = GatewayCard(gateway)
            gateway_card.configure_requested.connect(lambda card=gateway_card: self.configure_gateway(card))
            self.gateway_cards_layout.addWidget(gateway_card)
            self.start_gateway_balance_reading(gateway_card)

    def start_gateway_balance_reading(self, gateway_card):
        if not gateway_card.gateway.is_configured():
            gateway_card.show_not_configured()
            return
        self.gateway_checks_pending += 1
        gateway_card.balance_label.setText("Lecture du solde…")
        gateway_card.balance_label.setStyleSheet(MUTED_STYLE_SHEET)

        def show_result(balance, error_message):
            self.gateway_checks_pending -= 1
            try:
                gateway_card.show_balance(balance, error_message)
            except RuntimeError:
                pass  # the card was replaced by a newer check
            self.enable_check_button_when_everything_answered()

        start_balance_reading(gateway_card.gateway, show_result)

    def configure_gateway(self, gateway_card):
        gateway = gateway_card.gateway
        settings_dialog = OrangeSettingsDialog(self, gateway.credentials_store, gateway.credentials)
        settings_dialog.exec()
        if settings_dialog.saved:
            gateway.reload_credentials()
            self.start_gateway_balance_reading(gateway_card)

    def enable_check_button_when_everything_answered(self):
        if not self.sim_check_pending and self.gateway_checks_pending == 0:
            self.check_button.setEnabled(True)

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            old_widget = layout.takeAt(0).widget()
            if old_widget is not None:
                old_widget.deleteLater()

    def show_ussd_exchange_result(self, exchange_result):
        self.ussd_dialer.show_exchange_result(exchange_result)

    def show_balance_results(self, balance_results):
        self.ussd_dialer.abandon_session_after_other_ussd_use()
        self.clear_layout(self.cards_layout)
        self.sim_check_pending = False
        self.enable_check_button_when_everything_answered()
        if not balance_results:
            self.status_label.setText("Aucun dongle prêt : branchez un dongle et attendez qu'il soit « Prêt » dans Appareils.")
            return
        self.status_label.setText("")
        for balance_result in balance_results:
            self.cards_layout.addWidget(SimCard(balance_result))
