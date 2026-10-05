from PySide6.QtCore import QAbstractTableModel, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from common.dialogs import ask_yes_no_defaulting_to_no
from common.french_labels import format_french_datetime, format_message_count, format_number
from common.icons import CHECK_CIRCLE_ICON_SHAPES, REPLY_ICON_SHAPES, TRASH_ICON_SHAPES, build_colored_icon
from inbox.reply_dialog import ReplyDialog
from inbox.inbox_store import InboxStore
from common.sms_text import normalize_senegal_number
from common.table_helpers import POSITION_COLUMN_TITLE, POSITION_COLUMN_WIDTH_PIXELS

COLUMN_TITLES = [POSITION_COLUMN_TITLE, "Expéditeur", "Message", "", "Reçu le"]
POSITION_COLUMN, SENDER_COLUMN, MESSAGE_COLUMN, REPLY_COLUMN, RECEIVED_AT_COLUMN = range(5)
REPLY_COLUMN_WIDTH_PIXELS = 44
REPLY_ICON_SIZE_PIXELS = 16
FEEDBACK_DISPLAY_MILLISECONDS = 6000
CONTROL_ICON_SIZE_PIXELS = 16
PREVIEW_HEIGHT_PIXELS = 110


def reply_number_of(inbox_message):
    """The number a reply can be sent to ('+221' + 9 digits), or None for a sender such as « Expresso » or a short code."""
    return normalize_senegal_number(inbox_message.sender_number)


def format_sender(inbox_message):
    """'Awa Diop (+221771234567)' for a known contact, the bare number otherwise."""
    displayed_number = normalize_senegal_number(inbox_message.sender_number) or inbox_message.sender_number
    if inbox_message.sender_name:
        return f"{inbox_message.sender_name} ({displayed_number})"
    return displayed_number


class InboxTableModel(QAbstractTableModel):
    """Read-only model over the received messages; unread ones are drawn in bold."""

    def __init__(self):
        super().__init__()
        self.inbox_messages = []
        self.row_by_message_id = {}
        self.reply_icon = build_colored_icon(REPLY_ICON_SHAPES, "#3B82F6")

    def set_messages(self, inbox_messages):
        self.beginResetModel()
        self.inbox_messages = inbox_messages
        self.row_by_message_id = {message.message_id: row for row, message in enumerate(inbox_messages)}
        self.endResetModel()

    def message_at_row(self, row):
        return self.inbox_messages[row]

    def mark_row_as_read(self, row):
        self.inbox_messages[row].is_read = True
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMN_TITLES) - 1))

    def rowCount(self, parent=None):
        return len(self.inbox_messages)

    def columnCount(self, parent=None):
        return len(COLUMN_TITLES)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMN_TITLES[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        inbox_message = self.inbox_messages[index.row()]
        column = index.column()
        if role == Qt.DisplayRole:
            return (
                format_number(index.row() + 1),
                format_sender(inbox_message),
                inbox_message.message_text.replace("\n", " "),
                "",  # the reply button is drawn as an icon
                format_french_datetime(inbox_message.received_at),
            )[column]
        if column == REPLY_COLUMN:
            if role == Qt.DecorationRole and reply_number_of(inbox_message):
                return self.reply_icon
            if role == Qt.ToolTipRole:
                return "Répondre" if reply_number_of(inbox_message) else "Impossible de répondre à cet expéditeur"
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignCenter)
        if role == Qt.FontRole and not inbox_message.is_read:
            bold_font = QFont()
            bold_font.setBold(True)
            return bold_font
        if role == Qt.TextAlignmentRole and column == POSITION_COLUMN:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and column == MESSAGE_COLUMN:
            return inbox_message.message_text
        return None


class InboxPage(QWidget):
    """Réception: every SMS received by the dongles (already moved off the SIM into the database)."""

    unread_count_changed = Signal(int)
    reply_submitted = Signal(str, str, str)  # number to answer, recipient name (may be empty), reply text

    def __init__(self, inbox_store: InboxStore):
        super().__init__()
        self.inbox_store = inbox_store

        self.summary_label = QLabel("")
        self.mark_all_read_button = QPushButton(
            build_colored_icon(CHECK_CIRCLE_ICON_SHAPES, "#10B981"), " Tout marquer comme lu"
        )
        self.delete_button = QPushButton(build_colored_icon(TRASH_ICON_SHAPES, "#EF4444"), " Supprimer la sélection")
        for control_button in (self.mark_all_read_button, self.delete_button):
            control_button.setIconSize(QSize(CONTROL_ICON_SIZE_PIXELS, CONTROL_ICON_SIZE_PIXELS))
        toolbar_layout = QHBoxLayout()
        self.feedback_label = QLabel("")
        self.feedback_label.setStyleSheet("color: #1e8449;")
        toolbar_layout.addWidget(self.summary_label)
        toolbar_layout.addWidget(self.feedback_label)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.mark_all_read_button)
        toolbar_layout.addWidget(self.delete_button)

        self.table_model = InboxTableModel()
        self.messages_table = QTableView()
        self.messages_table.setModel(self.table_model)
        self.messages_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.messages_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.messages_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.messages_table.verticalHeader().setVisible(False)
        self.messages_table.setWordWrap(False)
        header = self.messages_table.horizontalHeader()
        header.setSectionResizeMode(POSITION_COLUMN, QHeaderView.Fixed)
        header.setSectionResizeMode(SENDER_COLUMN, QHeaderView.Interactive)
        header.setSectionResizeMode(MESSAGE_COLUMN, QHeaderView.Stretch)
        header.setSectionResizeMode(REPLY_COLUMN, QHeaderView.Fixed)
        header.setSectionResizeMode(RECEIVED_AT_COLUMN, QHeaderView.ResizeToContents)
        self.messages_table.setIconSize(QSize(REPLY_ICON_SIZE_PIXELS, REPLY_ICON_SIZE_PIXELS))
        self.messages_table.setColumnWidth(REPLY_COLUMN, REPLY_COLUMN_WIDTH_PIXELS)
        self.messages_table.setMouseTracking(True)
        self.messages_table.setColumnWidth(POSITION_COLUMN, POSITION_COLUMN_WIDTH_PIXELS)
        self.messages_table.setColumnWidth(SENDER_COLUMN, 220)

        self.message_preview = QPlainTextEdit()
        self.message_preview.setReadOnly(True)
        self.message_preview.setFixedHeight(PREVIEW_HEIGHT_PIXELS)
        self.message_preview.setPlaceholderText("Sélectionnez un message pour le lire.")

        self.empty_label = QLabel("Aucun message reçu.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("font-size: 16px; color: gray;")

        page_layout = QVBoxLayout(self)
        page_layout.addLayout(toolbar_layout)
        page_layout.addWidget(self.messages_table)
        page_layout.addWidget(self.empty_label)
        page_layout.addWidget(self.message_preview)

        self.messages_table.clicked.connect(self.handle_cell_click)
        self.messages_table.entered.connect(self.show_hand_cursor_over_reply_icon)
        self.messages_table.selectionModel().selectionChanged.connect(self.show_selected_message)
        self.mark_all_read_button.clicked.connect(self.mark_everything_as_read)
        self.delete_button.clicked.connect(self.delete_selected_messages)
        self.reload()

    def showEvent(self, show_event):
        super().showEvent(show_event)
        self.reload()

    def reload(self):
        """Also called when the dongle thread stores new messages."""
        self.table_model.set_messages(self.inbox_store.list_messages())
        has_rows = self.table_model.rowCount() > 0
        self.messages_table.setVisible(has_rows)
        self.empty_label.setVisible(not has_rows)
        self.message_preview.clear()
        self.refresh_summary()

    def refresh_summary(self):
        unread_count = self.inbox_store.count_unread()
        total_count = self.table_model.rowCount()
        unread_text = f"{format_number(unread_count)} non lu{'s' if unread_count > 1 else ''}"
        self.summary_label.setText(f"{format_message_count(total_count)}  ·  {unread_text}")
        self.mark_all_read_button.setEnabled(unread_count > 0)
        self.delete_button.setEnabled(total_count > 0)
        self.unread_count_changed.emit(unread_count)

    def show_hand_cursor_over_reply_icon(self, hovered_index):
        can_reply = hovered_index.column() == REPLY_COLUMN and reply_number_of(self.table_model.message_at_row(hovered_index.row()))
        self.messages_table.viewport().setCursor(Qt.PointingHandCursor if can_reply else Qt.ArrowCursor)

    def handle_cell_click(self, clicked_index):
        if clicked_index.column() == REPLY_COLUMN:
            self.reply_to_message(self.table_model.message_at_row(clicked_index.row()))

    def reply_to_message(self, inbox_message):
        number_to_answer = reply_number_of(inbox_message)
        if number_to_answer is None:
            return  # an alphanumeric sender (« Expresso ») or short code cannot receive an SMS
        reply_dialog = ReplyDialog(self, format_sender(inbox_message), inbox_message.message_text, inbox_message.received_at)
        if reply_dialog.exec() != ReplyDialog.Accepted:
            return
        self.reply_submitted.emit(number_to_answer, inbox_message.sender_name, reply_dialog.reply_text())
        self.feedback_label.setText(f"Réponse à {inbox_message.sender_name or number_to_answer} mise en file prioritaire.")
        QTimer.singleShot(FEEDBACK_DISPLAY_MILLISECONDS, lambda: self.feedback_label.setText(""))

    def selected_rows(self):
        return sorted({index.row() for index in self.messages_table.selectionModel().selectedRows()})

    def show_selected_message(self):
        """Reading a message (selecting it) marks it as read."""
        rows = self.selected_rows()
        if not rows:
            self.message_preview.clear()
            return
        last_selected_message = self.table_model.message_at_row(rows[-1])
        self.message_preview.setPlainText(
            f"De : {format_sender(last_selected_message)}\n"
            f"Le : {format_french_datetime(last_selected_message.received_at)}\n\n"
            f"{last_selected_message.message_text}"
        )
        unread_rows = [row for row in rows if not self.table_model.message_at_row(row).is_read]
        if unread_rows:
            self.inbox_store.mark_as_read([self.table_model.message_at_row(row).message_id for row in unread_rows])
            for row in unread_rows:
                self.table_model.mark_row_as_read(row)
            self.refresh_summary()

    def mark_everything_as_read(self):
        self.inbox_store.mark_all_as_read()
        for row in range(self.table_model.rowCount()):
            if not self.table_model.message_at_row(row).is_read:
                self.table_model.mark_row_as_read(row)
        self.refresh_summary()

    def delete_selected_messages(self):
        rows = self.selected_rows()
        if not rows:
            return
        confirmed = ask_yes_no_defaulting_to_no(
            self,
            "Supprimer les messages",
            f"Supprimer {format_message_count(len(rows))} ?\n\nCette action est irréversible.",
        )
        if confirmed:
            self.inbox_store.delete_messages([self.table_model.message_at_row(row).message_id for row in rows])
            self.reload()
