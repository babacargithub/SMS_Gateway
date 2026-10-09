from PySide6.QtCore import QAbstractTableModel, QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from common.dialogs import ask_yes_no_defaulting_to_no
from common.french_labels import format_french_datetime, format_message_count, format_number
from common.message_detail_panel import MessageDetailPanel
from common.icons import TRASH_ICON_SHAPES, build_colored_icon
from outbox.outbox_store import OutboxStore
from common.table_helpers import POSITION_COLUMN_TITLE, POSITION_COLUMN_WIDTH_PIXELS

COLUMN_TITLES = [POSITION_COLUMN_TITLE, "Destinataire", "Numéro", "Message", "Liste", "Envoyé le"]
POSITION_COLUMN, RECIPIENT_COLUMN, NUMBER_COLUMN, MESSAGE_COLUMN, LIST_COLUMN, SENT_AT_COLUMN = range(6)
CONTROL_ICON_SIZE_PIXELS = 16


class HistoryTableModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.sent_messages = []

    def set_messages(self, sent_messages):
        self.beginResetModel()
        self.sent_messages = sent_messages
        self.endResetModel()

    def rowCount(self, parent=None):
        return len(self.sent_messages)

    def columnCount(self, parent=None):
        return len(COLUMN_TITLES)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMN_TITLES[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        sent_message = self.sent_messages[index.row()]
        column = index.column()
        if role == Qt.DisplayRole:
            return (
                format_number(index.row() + 1),
                sent_message.recipient_name or "—",
                sent_message.phone_number,
                sent_message.message_text.replace("\n", " "),
                sent_message.list_name,
                format_french_datetime(sent_message.updated_at),
            )[column]
        if role == Qt.TextAlignmentRole and column == POSITION_COLUMN:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and column == MESSAGE_COLUMN:
            return sent_message.message_text
        return None


class HistoryPage(QWidget):
    """Historique: every message the dongle confirmed as sent (moved here from the Outbox)."""

    def __init__(self, outbox_store_instance: OutboxStore):
        super().__init__()
        self.outbox_store = outbox_store_instance

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Rechercher (destinataire, numéro, message)…")
        self.search_field.setClearButtonEnabled(True)
        self.summary_label = QLabel("")
        self.select_all_button = QPushButton("Tout sélectionner")
        self.delete_button = QPushButton(build_colored_icon(TRASH_ICON_SHAPES, "#EF4444"), " Supprimer la sélection")
        self.delete_button.setIconSize(QSize(CONTROL_ICON_SIZE_PIXELS, CONTROL_ICON_SIZE_PIXELS))
        toolbar_layout = QHBoxLayout()
        toolbar_layout.addWidget(self.search_field, stretch=1)
        toolbar_layout.addWidget(self.summary_label)
        toolbar_layout.addWidget(self.select_all_button)
        toolbar_layout.addWidget(self.delete_button)

        self.table_model = HistoryTableModel()
        self.messages_table = QTableView()
        self.messages_table.setModel(self.table_model)
        self.messages_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.messages_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.messages_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.messages_table.verticalHeader().setVisible(False)
        self.messages_table.setWordWrap(False)
        header = self.messages_table.horizontalHeader()
        header.setSectionResizeMode(POSITION_COLUMN, QHeaderView.Fixed)
        header.setSectionResizeMode(RECIPIENT_COLUMN, QHeaderView.Interactive)
        header.setSectionResizeMode(NUMBER_COLUMN, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(MESSAGE_COLUMN, QHeaderView.Stretch)
        header.setSectionResizeMode(LIST_COLUMN, QHeaderView.Interactive)
        header.setSectionResizeMode(SENT_AT_COLUMN, QHeaderView.ResizeToContents)
        self.messages_table.setColumnWidth(POSITION_COLUMN, POSITION_COLUMN_WIDTH_PIXELS)
        self.messages_table.setColumnWidth(RECIPIENT_COLUMN, 170)
        self.messages_table.setColumnWidth(LIST_COLUMN, 150)

        self.message_detail_panel = MessageDetailPanel()
        self.empty_label = QLabel("Aucun message envoyé pour l'instant.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("font-size: 16px; color: gray;")

        page_layout = QVBoxLayout(self)
        page_layout.addLayout(toolbar_layout)
        page_layout.addWidget(self.messages_table, stretch=1)
        page_layout.addWidget(self.empty_label)
        page_layout.addWidget(self.message_detail_panel)

        self.search_field.textChanged.connect(self.reload)
        self.select_all_button.clicked.connect(self.messages_table.selectAll)
        self.delete_button.clicked.connect(self.delete_selected_messages)
        self.messages_table.selectionModel().selectionChanged.connect(self.refresh_controls)
        QShortcut(QKeySequence(Qt.Key_Delete), self.messages_table, self.delete_selected_messages, context=Qt.WidgetShortcut)
        QShortcut(QKeySequence(Qt.Key_Backspace), self.messages_table, self.delete_selected_messages, context=Qt.WidgetShortcut)
        self.reload()

    def showEvent(self, show_event):
        super().showEvent(show_event)
        self.reload()

    def reload(self):
        search_text = self.search_field.text().strip().casefold()
        sent_messages = self.outbox_store.list_sent_messages()
        if search_text:
            sent_messages = [
                message for message in sent_messages
                if search_text in f"{message.recipient_name} {message.phone_number} {message.message_text}".casefold()
            ]
        self.table_model.set_messages(sent_messages)
        has_rows = self.table_model.rowCount() > 0
        self.messages_table.setVisible(has_rows)
        self.empty_label.setVisible(not has_rows)
        self.empty_label.setText(
            "Aucun résultat." if search_text and not has_rows else "Aucun message envoyé pour l'instant."
        )
        self.refresh_controls()

    def refresh_controls(self, *_):
        selected_count = len(self.messages_table.selectionModel().selectedRows())
        total_count = self.table_model.rowCount()
        summary_text = format_message_count(total_count)
        if selected_count:
            summary_text += f"  ·  {selected_count} sélectionné{'s' if selected_count > 1 else ''}"
        self.summary_label.setText(summary_text)
        self.delete_button.setEnabled(selected_count > 0)
        self.select_all_button.setEnabled(total_count > 0)
        selected_rows = sorted({index.row() for index in self.messages_table.selectionModel().selectedRows()})
        self.message_detail_panel.show_selected_messages([self.table_model.sent_messages[row] for row in selected_rows])

    def delete_selected_messages(self):
        selected_rows = sorted({index.row() for index in self.messages_table.selectionModel().selectedRows()})
        if not selected_rows:
            return
        confirmed = ask_yes_no_defaulting_to_no(
            self,
            "Supprimer de l'historique",
            f"Supprimer {format_message_count(len(selected_rows))} de l'historique ?\n\n"
            "Cette action est irréversible.",
        )
        if confirmed:
            self.outbox_store.delete_messages([self.table_model.sent_messages[row].message_id for row in selected_rows])
            self.reload()
