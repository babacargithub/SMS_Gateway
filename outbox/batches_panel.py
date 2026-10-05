"""The list of batches (campaigns) shown at the top of the Boîte d'envoi, each with a « Gérer » button."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from common.french_labels import format_french_datetime, format_number
from outbox import outbox_store

COLUMN_TITLES = ["Créé le", "Listes", "Message", "Messages", "En attente", "Envoyés", "Échecs / non confirmés", ""]
CREATED_COLUMN, LISTS_COLUMN, MESSAGE_COLUMN, TOTAL_COLUMN, WAITING_COLUMN, SENT_COLUMN, PROBLEMS_COLUMN, MANAGE_COLUMN = range(8)
VISIBLE_ROWS_BEFORE_SCROLLING = 3
ROW_HEIGHT_PIXELS = 30
HEADER_HEIGHT_PIXELS = 26
MESSAGE_PREVIEW_MAX_CHARACTERS = 90


class BatchesPanel(QWidget):
    manage_requested = Signal(int)   # campaign id

    def __init__(self):
        super().__init__()
        self.title_label = QLabel("Lots d'envoi")
        self.title_label.setStyleSheet("font-weight: 600;")
        self.batches_table = QTableWidget(0, len(COLUMN_TITLES))
        self.batches_table.setHorizontalHeaderLabels(COLUMN_TITLES)
        self.batches_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.batches_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.batches_table.verticalHeader().setVisible(False)
        self.batches_table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT_PIXELS)
        self.batches_table.horizontalHeader().setFixedHeight(HEADER_HEIGHT_PIXELS)
        header = self.batches_table.horizontalHeader()
        header.setSectionResizeMode(MESSAGE_COLUMN, QHeaderView.Stretch)
        for column in (CREATED_COLUMN, LISTS_COLUMN, TOTAL_COLUMN, WAITING_COLUMN, SENT_COLUMN, PROBLEMS_COLUMN, MANAGE_COLUMN):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)

        panel_layout = QVBoxLayout(self)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(self.title_label)
        panel_layout.addWidget(self.batches_table)
        self.setVisible(False)

    def show_batches(self, batch_summaries):
        self.setVisible(bool(batch_summaries))
        self.batches_table.setRowCount(len(batch_summaries))
        visible_row_count = min(len(batch_summaries), VISIBLE_ROWS_BEFORE_SCROLLING)
        self.batches_table.setFixedHeight(HEADER_HEIGHT_PIXELS + visible_row_count * ROW_HEIGHT_PIXELS + 4)
        for row, batch in enumerate(batch_summaries):
            counts = batch.state_counts
            template_preview = batch.message_template.replace("\n", " ")
            if len(template_preview) > MESSAGE_PREVIEW_MAX_CHARACTERS:
                template_preview = template_preview[:MESSAGE_PREVIEW_MAX_CHARACTERS].rstrip() + "…"
            edited_mark = " (modifié)" if batch.edited_at else ""
            cell_texts = {
                CREATED_COLUMN: format_french_datetime(batch.created_at),
                LISTS_COLUMN: batch.list_names,
                MESSAGE_COLUMN: template_preview + edited_mark,
                TOTAL_COLUMN: format_number(batch.total_count),
                WAITING_COLUMN: format_number(counts.get(outbox_store.WAITING, 0) + counts.get(outbox_store.SENDING, 0)),
                SENT_COLUMN: format_number(counts.get(outbox_store.SENT, 0)),
                PROBLEMS_COLUMN: format_number(counts.get(outbox_store.FAILED, 0) + counts.get(outbox_store.UNKNOWN, 0)),
            }
            for column, cell_text in cell_texts.items():
                table_item = QTableWidgetItem(cell_text)
                if column in (TOTAL_COLUMN, WAITING_COLUMN, SENT_COLUMN, PROBLEMS_COLUMN):
                    table_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == MESSAGE_COLUMN:
                    table_item.setToolTip(batch.message_template)
                self.batches_table.setItem(row, column, table_item)
            manage_button = QPushButton("Gérer")
            manage_button.setToolTip("Corriger le message, ou supprimer les messages de ce lot qui ne sont pas encore partis")
            manage_button.clicked.connect(lambda _checked=False, campaign_id=batch.campaign_id: self.manage_requested.emit(campaign_id))
            self.batches_table.setCellWidget(row, MANAGE_COLUMN, manage_button)
