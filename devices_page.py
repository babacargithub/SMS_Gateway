from PySide6.QtCore import Qt, Signal
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

from french_labels import translate_sim_status

COLUMN_TITLES = ["État", "IMEI", "Opérateur", "Signal", "SIM", "Réseau", "USB", "Détails"]


def format_signal(signal_dbm, signal_bars):
    if signal_dbm is None:
        return "Inconnu"
    filled_bars = "●" * signal_bars + "○" * (4 - signal_bars)
    return f"{filled_bars}  {signal_dbm} dBm"


class DevicesPage(QWidget):
    refresh_requested = Signal()

    def __init__(self):
        super().__init__()
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
        self.empty_state_label.setVisible(not dongle_statuses)
        self.devices_table.setVisible(bool(dongle_statuses))
        self.devices_table.setRowCount(len(dongle_statuses))
        for row_index, status in enumerate(dongle_statuses):
            cell_texts = [
                status.state,
                status.imei or "-",
                status.operator_name or "-",
                format_signal(status.signal_dbm, status.signal_bars),
                translate_sim_status(status.sim_status) if status.sim_status else "-",
                status.network_registration or "-",
                status.usb_location,
                status.error_message or "",
            ]
            for column_index, cell_text in enumerate(cell_texts):
                self.devices_table.setItem(row_index, column_index, QTableWidgetItem(cell_text))
