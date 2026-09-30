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

from compose_page import ComposePage
from contact_store import ContactStore
from contacts_page import ContactsPage
from message_sender import SimulatedMessageSender
from outbox_page import OutboxPage
from outbox_store import OutboxStore
from devices_page import DevicesPage
from dongle_monitor import DongleMonitor
from sidebar import SIDEBAR_SECTION_NAMES, Sidebar


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
        self.outbox_store.recover_interrupted_sends()  # messages cut off mid-send last time become "Non confirmé"
        self.message_sender = SimulatedMessageSender()  # swap for the dongle-backed sender when real sending exists
        self.compose_page = ComposePage(self.contact_store, self.outbox_store)
        self.outbox_page = OutboxPage(self.outbox_store, self.message_sender)
        self.contacts_page = ContactsPage(self.contact_store)
        dedicated_pages = {
            "Appareils": self.devices_page,
            "Composer": self.compose_page,
            "Contacts": self.contacts_page,
            "Boîte d'envoi": self.outbox_page,
        }
        self.content_stack = QStackedWidget()
        for section_name in SIDEBAR_SECTION_NAMES:
            self.content_stack.addWidget(dedicated_pages.get(section_name) or build_placeholder_page(section_name))

        self.dongle_monitor = DongleMonitor()
        self.dongle_monitor.statuses_updated.connect(self.devices_page.show_statuses)
        self.devices_page.refresh_requested.connect(self.dongle_monitor.request_immediate_rescan)
        self.compose_page.messages_queued.connect(self.show_outbox)
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

    def show_outbox(self):
        self.sidebar_list.setCurrentRow(SIDEBAR_SECTION_NAMES.index("Boîte d'envoi"))

    def closeEvent(self, close_event):
        self.outbox_page.shutdown()
        self.dongle_monitor.stop()
        self.dongle_monitor.wait(5000)
        super().closeEvent(close_event)


def main():
    application = QApplication(sys.argv)
    QLocale.setDefault(QLocale(QLocale.French, QLocale.France))
    qt_french_translator = QTranslator()  # standard buttons: Oui / Non / Annuler...
    if qt_french_translator.load("qtbase_fr", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        application.installTranslator(qt_french_translator)
    main_window = MainWindow()
    main_window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
