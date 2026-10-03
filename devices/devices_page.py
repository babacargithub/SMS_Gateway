from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from common.french_labels import translate_sim_status
from devices.device_sounds import play_device_connected_sound, play_device_disconnected_sound

COLUMN_TITLES = ["État", "IMEI", "Opérateur", "Signal", "SIM", "Réseau", "USB", "Détails"]
SIGNAL_COLUMN_INDEX = COLUMN_TITLES.index("Signal")


SIGNAL_BAR_COUNT = 4
SIGNAL_BAR_WIDTH = 6
SIGNAL_BAR_GAP = 3
SIGNAL_BAR_MIN_HEIGHT = 6
SIGNAL_BAR_HEIGHT_STEP = 4
SIGNAL_EMPTY_BAR_COLOR = QColor(200, 200, 200)


def color_for_signal_bars(signal_bars):
    if signal_bars >= 3:
        return QColor(46, 160, 67)
    if signal_bars == 2:
        return QColor(230, 150, 20)
    return QColor(210, 60, 50)


class SignalBarsWidget(QWidget):
    """Phone-style ascending bars followed by the dBm value."""

    def __init__(self, signal_dbm, signal_bars):
        super().__init__()
        self.signal_bars = 0 if signal_dbm is None else signal_bars
        self.signal_label_text = "Inconnu" if signal_dbm is None else f"{signal_dbm} dBm"
        self.setToolTip(
            "Signal inconnu"
            if signal_dbm is None
            else f"{self.signal_bars} barre(s) sur {SIGNAL_BAR_COUNT} ({signal_dbm} dBm)"
        )

    def sizeHint(self):
        bars_width = SIGNAL_BAR_COUNT * (SIGNAL_BAR_WIDTH + SIGNAL_BAR_GAP)
        text_width = self.fontMetrics().horizontalAdvance(self.signal_label_text)
        return QSize(bars_width + text_width + 12, 24)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        baseline_y = (self.height() + self._tallest_bar_height()) / 2
        for bar_index in range(SIGNAL_BAR_COUNT):
            bar_height = SIGNAL_BAR_MIN_HEIGHT + bar_index * SIGNAL_BAR_HEIGHT_STEP
            is_filled = bar_index < self.signal_bars
            painter.setBrush(
                color_for_signal_bars(self.signal_bars) if is_filled else SIGNAL_EMPTY_BAR_COLOR
            )
            bar_left = 4 + bar_index * (SIGNAL_BAR_WIDTH + SIGNAL_BAR_GAP)
            painter.drawRoundedRect(
                QRectF(bar_left, baseline_y - bar_height, SIGNAL_BAR_WIDTH, bar_height), 1.5, 1.5
            )
        painter.setPen(self.palette().text().color())
        text_left = 4 + SIGNAL_BAR_COUNT * (SIGNAL_BAR_WIDTH + SIGNAL_BAR_GAP) + 4
        painter.drawText(
            QRectF(text_left, 0, self.width() - text_left, self.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            self.signal_label_text,
        )

    @staticmethod
    def _tallest_bar_height():
        return SIGNAL_BAR_MIN_HEIGHT + (SIGNAL_BAR_COUNT - 1) * SIGNAL_BAR_HEIGHT_STEP


class DevicesPage(QWidget):
    refresh_requested = Signal()

    def __init__(self):
        super().__init__()
        self.known_usb_locations = set()
        self.refresh_button = QPushButton("Actualiser")
        self.refresh_button.setToolTip("Rescanner l'USB et relire chaque dongle maintenant")
        self.refresh_button.clicked.connect(self.refresh_requested)
        toolbar_layout = QHBoxLayout()
        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addStretch()

        self.empty_state_label = QLabel("Aucun dongle détecté. Branchez un dongle Huawei.")
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        self.empty_state_label.setStyleSheet("font-size: 16px; color: gray;")

        self.devices_table = QTableWidget(0, len(COLUMN_TITLES))
        self.devices_table.setHorizontalHeaderLabels(COLUMN_TITLES)
        self.devices_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.devices_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.devices_table.verticalHeader().setVisible(False)
        self.devices_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.devices_table.horizontalHeader().setStretchLastSection(True)

        page_layout = QVBoxLayout(self)
        page_layout.addLayout(toolbar_layout)
        page_layout.addWidget(self.devices_table)
        page_layout.addWidget(self.empty_state_label)

    def show_statuses(self, dongle_statuses):
        current_usb_locations = {status.usb_location for status in dongle_statuses}
        if current_usb_locations - self.known_usb_locations:
            play_device_connected_sound()
        if self.known_usb_locations - current_usb_locations:
            play_device_disconnected_sound()
        self.known_usb_locations = current_usb_locations
        self.empty_state_label.setVisible(not dongle_statuses)
        self.devices_table.setVisible(bool(dongle_statuses))
        self.devices_table.setRowCount(len(dongle_statuses))
        for row_index, status in enumerate(dongle_statuses):
            cell_texts = [
                status.state,
                status.imei or "-",
                status.operator_name or "-",
                "",
                translate_sim_status(status.sim_status) if status.sim_status else "-",
                status.network_registration or "-",
                status.usb_location,
                status.error_message or "",
            ]
            for column_index, cell_text in enumerate(cell_texts):
                self.devices_table.setItem(row_index, column_index, QTableWidgetItem(cell_text))
            self.devices_table.setCellWidget(
                row_index,
                SIGNAL_COLUMN_INDEX,
                SignalBarsWidget(status.signal_dbm, status.signal_bars),
            )
