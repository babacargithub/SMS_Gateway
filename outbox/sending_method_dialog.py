"""« Envoyer par… » : the SIM dongle, or one of the online SMS gateways (Orange SMS today).

For a gateway the remaining balance is read in the background while the dialog is open."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from common.french_labels import format_gateway_balance, format_message_count, format_sms_count
from gateways.balance_reader_thread import start_balance_reading
from gateways.gateway_settings_dialog import OrangeSettingsDialog

SIM_METHOD_KEY = "sim"
LAST_METHOD_SETTING = "sending_method"


class SendingMethodDialog(QDialog):
    """After exec(): `chosen_method_key` is SIM_METHOD_KEY or a gateway key (None if cancelled)."""

    def __init__(self, parent, gateways, messages_to_send_count, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Envoyer les messages")
        self.setMinimumWidth(460)
        self.gateways = {gateway.key: gateway for gateway in gateways}
        self.messages_to_send_count = messages_to_send_count
        self.settings = settings or QSettings("SMS Gateway", "SMS Gateway")
        self.chosen_method_key = None
        self.method_buttons = {}
        self.balance_labels = {}
        self.configure_buttons = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Comment envoyer {format_message_count(messages_to_send_count)} ?"))
        self.button_group = QButtonGroup(self)
        self._add_method_row(layout, SIM_METHOD_KEY, "SIM (dongle USB)", with_balance=False)
        for gateway in gateways:
            self._add_method_row(layout, gateway.key, gateway.display_name, with_balance=True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Continuer")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept_choice)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        last_method_key = self.settings.value(LAST_METHOD_SETTING, SIM_METHOD_KEY)
        self.method_buttons.get(last_method_key, self.method_buttons[SIM_METHOD_KEY]).setChecked(True)
        for gateway in gateways:
            self.refresh_gateway_row(gateway)

    def _add_method_row(self, layout, method_key, label_text, with_balance):
        radio_button = QRadioButton(label_text)
        self.button_group.addButton(radio_button)
        self.method_buttons[method_key] = radio_button
        row_layout = QHBoxLayout()
        row_layout.addWidget(radio_button)
        row_layout.addStretch()
        if with_balance:
            balance_label = QLabel()
            self.balance_labels[method_key] = balance_label
            configure_button = QPushButton("Configurer…")
            configure_button.clicked.connect(lambda _checked=False, key=method_key: self.configure_gateway(key))
            self.configure_buttons[method_key] = configure_button
            row_layout.addWidget(balance_label)
            row_layout.addWidget(configure_button)
        layout.addLayout(row_layout)

    def refresh_gateway_row(self, gateway):
        balance_label = self.balance_labels[gateway.key]
        if not gateway.is_configured():
            balance_label.setText("Non configuré")
            balance_label.setStyleSheet("color: #64748B;")
            return
        balance_label.setText("Solde : lecture…")
        balance_label.setStyleSheet("color: #64748B;")
        start_balance_reading(gateway, lambda balance, error_message, key=gateway.key: self.show_balance(key, balance, error_message))

    def show_balance(self, gateway_key, balance, error_message):
        try:
            balance_label = self.balance_labels[gateway_key]
            if balance is None:
                balance_label.setText(error_message)
                balance_label.setToolTip(error_message)
                balance_label.setStyleSheet("color: #EF4444;")
                return
            balance_text = format_gateway_balance(balance.available_units, balance.nearest_expiration)
            balance_label.setText(f"Solde : {balance_text}")
            balance_label.setStyleSheet("color: #10B981;")
            if balance.available_units < self.messages_to_send_count:
                balance_label.setText(f"Solde : {balance_text} — insuffisant pour {format_sms_count(self.messages_to_send_count)}")
                balance_label.setStyleSheet("color: #F59E0B;")
        except RuntimeError:
            pass  # the dialog was closed (its widgets deleted) before the answer arrived

    def configure_gateway(self, gateway_key):
        gateway = self.gateways[gateway_key]
        settings_dialog = OrangeSettingsDialog(self, gateway.credentials_store, gateway.credentials)
        settings_dialog.exec()
        if settings_dialog.saved:
            gateway.reload_credentials()
            self.refresh_gateway_row(gateway)

    def accept_choice(self):
        chosen_key = next(key for key, button in self.method_buttons.items() if button.isChecked())
        if chosen_key in self.gateways and not self.gateways[chosen_key].is_configured():
            self.configure_gateway(chosen_key)
            if not self.gateways[chosen_key].is_configured():
                return
        self.chosen_method_key = chosen_key
        self.settings.setValue(LAST_METHOD_SETTING, chosen_key)
        self.accept()
