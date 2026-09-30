import sys

from PySide6.QtCore import Qt
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
        self.compose_page = ComposePage()
        dedicated_pages = {"Devices": self.devices_page, "Compose": self.compose_page}
        self.content_stack = QStackedWidget()
        for section_name in SIDEBAR_SECTION_NAMES:
            self.content_stack.addWidget(dedicated_pages.get(section_name) or build_placeholder_page(section_name))

        self.dongle_monitor = DongleMonitor()
        self.dongle_monitor.statuses_updated.connect(self.devices_page.show_statuses)
        self.devices_page.refresh_requested.connect(self.dongle_monitor.request_immediate_rescan)
        self.compose_page.send_requested.connect(self.dongle_monitor.submit_text_message)
        self.dongle_monitor.sms_send_finished.connect(self.compose_page.show_send_result)
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

    def closeEvent(self, close_event):
        self.dongle_monitor.stop()
        self.dongle_monitor.wait(5000)
        super().closeEvent(close_event)


def main():
    application = QApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
