import sys

from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QTranslator
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from compose.compose_page import ComposePage
from compose.sms_cost_estimate import SimSmsPrice
from contacts.contact_store import ContactStore
from contacts.contacts_page import ContactsPage
from outbox.message_sender import DongleMessageSender
from outbox.outbox_page import OutboxPage
from outbox.outbox_store import OutboxStore
from devices.devices_page import DevicesPage
from history.history_page import HistoryPage
from inbox.inbox_page import InboxPage
from inbox.inbox_store import InboxStore
from inbox.notification_sound import play_new_message_sound
from devices.dongle_monitor import DongleMonitor
from devices.dongle import STATE_READY
from sim.mobile_operators import identify_operator, load_operators
from sim.sim_page import SimPage
from common.app_icon import build_application_icon
from common.sidebar import SIDEBAR_SECTION_NAMES, Sidebar


def build_placeholder_page(section_name):
    page = QWidget()
    page_layout = QVBoxLayout(page)
    section_label = QLabel(section_name)
    section_label.setAlignment(Qt.AlignCenter)
    section_label.setStyleSheet("font-size: 24px; color: gray;")
    page_layout.addWidget(section_label)
    return page


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SMS Gateway")
        self.resize(1000, 650)

        self.sidebar_list = Sidebar()

        self.devices_page = DevicesPage()
        self.contact_store = ContactStore()
        self.outbox_store = OutboxStore()
        self.outbox_store.archive_sent_messages()  # a run cut short by a crash: its sent messages go to the Historique
        self.outbox_store.recover_interrupted_sends()  # messages cut off mid-send last time become "Non confirmé"
        self.inbox_store = InboxStore()
        self.dongle_monitor = DongleMonitor(self.inbox_store)
        self.message_sender = DongleMessageSender(self.dongle_monitor)
        self.compose_page = ComposePage(self.contact_store, self.outbox_store)
        self.outbox_page = OutboxPage(self.outbox_store, self.message_sender)
        self.contacts_page = ContactsPage(self.contact_store)
        self.history_page = HistoryPage(self.outbox_store)
        self.inbox_page = InboxPage(self.inbox_store)
        self.sim_page = SimPage()
        dedicated_pages = {
            "Appareils": self.devices_page,
            "Composer": self.compose_page,
            "Réception": self.inbox_page,
            "Contacts": self.contacts_page,
            "Boîte d'envoi": self.outbox_page,
            "Historique": self.history_page,
            "SIM": self.sim_page,
        }
        self.content_stack = QStackedWidget()
        for section_name in SIDEBAR_SECTION_NAMES:
            self.content_stack.addWidget(dedicated_pages.get(section_name) or build_placeholder_page(section_name))

        self.dongle_monitor.statuses_updated.connect(self.devices_page.show_statuses)
        self.dongle_monitor.statuses_updated.connect(self.update_compose_sim_prices)
        self.devices_page.refresh_requested.connect(self.dongle_monitor.request_immediate_rescan)
        self.sim_page.balance_check_requested.connect(self.dongle_monitor.request_balance_check)
        self.dongle_monitor.balance_check_finished.connect(self.sim_page.show_balance_results)
        self.sim_page.ussd_request_submitted.connect(self.dongle_monitor.submit_ussd_request)
        self.sim_page.session_close_requested.connect(self.dongle_monitor.submit_ussd_session_close)
        self.dongle_monitor.ussd_exchange_finished.connect(self.sim_page.show_ussd_exchange_result)
        self.compose_page.messages_queued.connect(self.show_outbox)
        self.dongle_monitor.inbox_messages_received.connect(self.handle_new_inbox_messages)
        self.inbox_page.unread_count_changed.connect(self.sidebar_list.show_unread_inbox_count)
        self.sidebar_list.show_unread_inbox_count(self.inbox_store.count_unread())
        self.dongle_monitor.start()

        self.sidebar_list.currentRowChanged.connect(self.content_stack.setCurrentIndex)
        self.sidebar_list.setCurrentRow(0)

        central_widget = QWidget()
        central_layout = QHBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.sidebar_list)
        central_layout.addWidget(self.content_stack)
        self.setCentralWidget(central_widget)

    def update_compose_sim_prices(self, dongle_statuses):
        known_operators = load_operators()
        sim_prices = []
        for status in dongle_statuses:
            if status.state != STATE_READY:
                continue
            operator = identify_operator(status.operator_network_code, status.operator_name, known_operators)
            if operator is None or operator.sms_cost_francs is None:
                continue
            sim_price = SimSmsPrice(operator.display_name, operator.sms_cost_francs)
            if sim_price not in sim_prices:
                sim_prices.append(sim_price)
        self.compose_page.set_sim_prices(sim_prices)

    def handle_new_inbox_messages(self, new_message_count):
        play_new_message_sound()
        self.inbox_page.reload()

    def show_outbox(self):
        self.sidebar_list.setCurrentRow(SIDEBAR_SECTION_NAMES.index("Boîte d'envoi"))

    def closeEvent(self, close_event):
        self.outbox_page.shutdown()
        self.dongle_monitor.stop()
        self.dongle_monitor.wait(5000)
        super().closeEvent(close_event)


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("SMS Gateway")
    application.setWindowIcon(build_application_icon())  # also the Dock icon on macOS
    QLocale.setDefault(QLocale(QLocale.French, QLocale.France))
    qt_french_translator = QTranslator()  # standard buttons: Oui / Non / Annuler...
    if qt_french_translator.load("qtbase_fr", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        application.installTranslator(qt_french_translator)
    main_window = MainWindow()
    main_window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
