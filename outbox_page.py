from PySide6.QtCore import QAbstractTableModel, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

import outbox_store
from dialogs import ask_yes_no_defaulting_to_no
from french_labels import MESSAGE_STATE_LABELS, format_french_datetime, format_message_count, format_number
from sending_worker import SendingWorker
from table_helpers import POSITION_COLUMN_TITLE, POSITION_COLUMN_WIDTH_PIXELS
from icons import (
    CHECK_CIRCLE_ICON_SHAPES,
    CLOCK_ICON_SHAPES,
    HELP_CIRCLE_ICON_SHAPES,
    PAUSE_ICON_SHAPES,
    PLAY_ICON_SHAPES,
    REFRESH_ICON_SHAPES,
    STOP_ICON_SHAPES,
    X_CIRCLE_ICON_SHAPES,
    build_colored_icon,
)

# state -> (icon shapes, icon color)
STATE_ICON_STYLES = {
    outbox_store.WAITING: (CLOCK_ICON_SHAPES, "#F59E0B"),
    outbox_store.SENDING: (REFRESH_ICON_SHAPES, "#3B82F6"),
    outbox_store.SENT: (CHECK_CIRCLE_ICON_SHAPES, "#10B981"),
    outbox_store.FAILED: (X_CIRCLE_ICON_SHAPES, "#EF4444"),
    outbox_store.UNKNOWN: (HELP_CIRCLE_ICON_SHAPES, "#64748B"),
}
COLUMN_TITLES = [POSITION_COLUMN_TITLE, "État", "Destinataire", "Numéro", "Message", "Liste", "Créé le"]
POSITION_COLUMN, STATE_COLUMN, MESSAGE_COLUMN = 0, 1, 4
ALL_STATES_FILTER_LABEL = "Tous les états"
BADGE_ICON_SIZE_PIXELS = 14  # 25% smaller than before (18)
ICON_SIZE_PIXELS = 15        # 25% smaller than before (20)
FILTER_ICON_SIZE_PIXELS = 12  # 25% smaller than the default (16)
STATE_COLUMN_WIDTH_PIXELS = 56
CONTROL_ICON_SIZE_PIXELS = 16
SIMULATION_BANNER_STYLE = "background: rgba(245, 158, 11, 0.18); color: #b9770e; border-radius: 6px; padding: 6px 10px;"

# what the sending controls are doing
IDLE, RUNNING, PAUSED, STOPPING = "idle", "running", "paused", "stopping"


def build_state_icons():
    return {state: build_colored_icon(shapes, color) for state, (shapes, color) in STATE_ICON_STYLES.items()}


class OutboxTableModel(QAbstractTableModel):
    """Read-only model over the queued messages (fast even with tens of thousands of rows)."""

    def __init__(self):
        super().__init__()
        self.queued_messages = []
        self.row_by_message_id = {}
        self.state_icons = build_state_icons()

    def set_messages(self, queued_messages):
        self.beginResetModel()
        self.queued_messages = queued_messages
        self.row_by_message_id = {message.message_id: row for row, message in enumerate(queued_messages)}
        self.endResetModel()

    def update_message_state(self, message_id, new_state, error_detail):
        """Refresh one row in place while sending (no full reload of thousands of rows)."""
        row = self.row_by_message_id.get(message_id)
        if row is None:
            return
        self.queued_messages[row].state = new_state
        self.queued_messages[row].error_detail = error_detail
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMN_TITLES) - 1))

    def rowCount(self, parent=None):
        return len(self.queued_messages)

    def columnCount(self, parent=None):
        return len(COLUMN_TITLES)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMN_TITLES[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        queued_message = self.queued_messages[index.row()]
        column = index.column()
        if role == Qt.DisplayRole:
            return (
                format_number(index.row() + 1),  # position in the displayed list
                "",  # the state is shown by its icon only
                queued_message.recipient_name or "—",
                queued_message.phone_number,
                queued_message.message_text.replace("\n", " "),
                queued_message.list_name,
                format_french_datetime(queued_message.created_at),
            )[column]
        if role == Qt.DecorationRole and column == STATE_COLUMN:
            return self.state_icons[queued_message.state]
        if role == Qt.TextAlignmentRole and column == POSITION_COLUMN:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.TextAlignmentRole and column == STATE_COLUMN:
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole:
            if column == STATE_COLUMN:
                state_label = MESSAGE_STATE_LABELS[queued_message.state]
                return f"{state_label} : {queued_message.error_detail}" if queued_message.error_detail else state_label
            if column == MESSAGE_COLUMN:
                return queued_message.message_text
        return None


class OutboxPage(QWidget):
    """The queue: every message of every launched campaign, with its state."""

    def __init__(self, outbox_store_instance, message_sender):
        super().__init__()
        self.outbox_store = outbox_store_instance
        self.message_sender = message_sender
        self.state_icons = build_state_icons()
        self.sending_worker = None
        self.run_state = IDLE
        self.waiting_count = 0

        self.send_button = self.build_control_button(PLAY_ICON_SHAPES, "#10B981", "Envoyer")
        self.pause_button = self.build_control_button(PAUSE_ICON_SHAPES, "#F59E0B", "Pause")
        self.stop_button = self.build_control_button(STOP_ICON_SHAPES, "#EF4444", "Arrêter")
        self.status_label = QLabel("")
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("%v / %m")
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setVisible(False)
        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self.send_button)
        controls_layout.addWidget(self.pause_button)
        controls_layout.addWidget(self.stop_button)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(self.status_label)
        controls_layout.addWidget(self.progress_bar, stretch=1)

        self.simulation_banner = QLabel("Mode simulation : aucun SMS n'est réellement envoyé. Les succès et les échecs sont aléatoires.")
        self.simulation_banner.setStyleSheet(SIMULATION_BANNER_STYLE)
        self.simulation_banner.setVisible(getattr(message_sender, "is_simulation", False))

        self.state_count_labels = {}
        summary_layout = QHBoxLayout()
        for state in outbox_store.ALL_STATES:
            icon_label = QLabel()
            icon_label.setPixmap(self.state_icons[state].pixmap(BADGE_ICON_SIZE_PIXELS, BADGE_ICON_SIZE_PIXELS))
            count_label = QLabel()
            self.state_count_labels[state] = count_label
            summary_layout.addWidget(icon_label)
            summary_layout.addWidget(count_label)
            summary_layout.addSpacing(14)
        summary_layout.addStretch()

        self.state_filter_combo = QComboBox()
        self.state_filter_combo.setIconSize(QSize(FILTER_ICON_SIZE_PIXELS, FILTER_ICON_SIZE_PIXELS))
        self.state_filter_combo.addItem(ALL_STATES_FILTER_LABEL, None)
        for state in outbox_store.ALL_STATES:
            self.state_filter_combo.addItem(self.state_icons[state], MESSAGE_STATE_LABELS[state], state)
        self.clear_waiting_button = QPushButton("Vider les messages en attente")
        toolbar_layout = QHBoxLayout()
        toolbar_layout.addWidget(self.state_filter_combo)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.clear_waiting_button)

        self.table_model = OutboxTableModel()
        self.messages_table = QTableView()
        self.messages_table.setModel(self.table_model)
        self.messages_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.messages_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.messages_table.verticalHeader().setVisible(False)
        self.messages_table.setWordWrap(False)
        header = self.messages_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(MESSAGE_COLUMN, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Interactive)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.messages_table.setIconSize(QSize(ICON_SIZE_PIXELS, ICON_SIZE_PIXELS))
        self.messages_table.setColumnWidth(POSITION_COLUMN, POSITION_COLUMN_WIDTH_PIXELS)
        self.messages_table.setColumnWidth(STATE_COLUMN, STATE_COLUMN_WIDTH_PIXELS)
        self.messages_table.setColumnWidth(2, 170)
        self.messages_table.setColumnWidth(5, 150)

        self.empty_label = QLabel("Aucun message. Composez un message puis cliquez sur « Lancer ».")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("font-size: 16px; color: gray;")

        page_layout = QVBoxLayout(self)
        page_layout.addLayout(controls_layout)
        page_layout.addWidget(self.simulation_banner)
        page_layout.addLayout(summary_layout)
        page_layout.addLayout(toolbar_layout)
        page_layout.addWidget(self.messages_table)
        page_layout.addWidget(self.empty_label)

        self.state_filter_combo.currentIndexChanged.connect(self.reload)
        self.clear_waiting_button.clicked.connect(self.clear_waiting_messages)
        self.send_button.clicked.connect(self.start_or_resume_sending)
        self.pause_button.clicked.connect(self.pause_sending)
        self.stop_button.clicked.connect(self.stop_sending)
        self.reload()

    def showEvent(self, show_event):
        super().showEvent(show_event)
        self.reload()

    def reload(self):
        state_counts = self.outbox_store.count_by_state()
        for state, count_label in self.state_count_labels.items():
            count_label.setText(f"{MESSAGE_STATE_LABELS[state]} : {format_number(state_counts[state])}")
        self.waiting_count = state_counts[outbox_store.WAITING]

        self.table_model.set_messages(self.outbox_store.list_messages(self.state_filter_combo.currentData()))
        has_rows = self.table_model.rowCount() > 0
        self.messages_table.setVisible(has_rows)
        self.empty_label.setVisible(not has_rows)
        self.apply_run_state()

    def clear_waiting_messages(self):
        waiting_count = self.outbox_store.count_by_state()[outbox_store.WAITING]
        confirmed = ask_yes_no_defaulting_to_no(
            self,
            "Vider les messages en attente",
            f"Supprimer les {format_message_count(waiting_count)} en attente ?\n\n"
            "Ils ne seront pas envoyés. Cette action est irréversible.",
        )
        if confirmed:
            self.outbox_store.delete_waiting_messages()
            self.reload()

    # ---- sending controls --------------------------------------------------------------------

    @staticmethod
    def build_control_button(icon_shapes, icon_color, button_text):
        control_button = QPushButton(build_colored_icon(icon_shapes, icon_color), " " + button_text)
        control_button.setIconSize(QSize(CONTROL_ICON_SIZE_PIXELS, CONTROL_ICON_SIZE_PIXELS))
        return control_button

    def apply_run_state(self):
        """Enable only the controls that make sense right now."""
        is_idle = self.run_state == IDLE
        self.send_button.setEnabled(self.run_state == PAUSED or (is_idle and self.waiting_count > 0))
        self.send_button.setText(" Reprendre" if self.run_state == PAUSED else " Envoyer")
        self.pause_button.setEnabled(self.run_state == RUNNING)
        self.stop_button.setEnabled(self.run_state in (RUNNING, PAUSED))
        self.clear_waiting_button.setEnabled(is_idle and self.waiting_count > 0)
        if self.run_state != IDLE:
            self.status_label.setText({RUNNING: "Envoi en cours...", PAUSED: "En pause", STOPPING: "Arrêt en cours..."}[self.run_state])

    def start_or_resume_sending(self):
        if self.run_state == PAUSED:
            self.sending_worker.resume()
            self.run_state = RUNNING
        elif self.run_state == IDLE and self.waiting_count > 0:
            self.sending_worker = SendingWorker(self.outbox_store, self.message_sender)
            self.sending_worker.message_state_changed.connect(self.table_model.update_message_state)
            self.sending_worker.progress_changed.connect(self.show_progress)
            self.sending_worker.run_finished.connect(self.finish_run)
            self.progress_bar.setRange(0, self.waiting_count)
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)
            self.run_state = RUNNING
            self.sending_worker.start()
        self.apply_run_state()

    def pause_sending(self):
        if self.run_state == RUNNING:
            self.sending_worker.pause()  # the message being sent finishes first
            self.run_state = PAUSED
            self.apply_run_state()

    def stop_sending(self):
        if self.run_state in (RUNNING, PAUSED):
            self.sending_worker.stop()  # ends after the message being sent; the rest stays waiting
            self.run_state = STOPPING
            self.apply_run_state()

    def show_progress(self, handled_count, total_count):
        self.progress_bar.setRange(0, max(total_count, 1))
        self.progress_bar.setValue(handled_count)
        self.refresh_state_counters()

    def refresh_state_counters(self):
        state_counts = self.outbox_store.count_by_state()
        for state, count_label in self.state_count_labels.items():
            count_label.setText(f"{MESSAGE_STATE_LABELS[state]} : {format_number(state_counts[state])}")
        self.waiting_count = state_counts[outbox_store.WAITING]

    def finish_run(self, finished_everything):
        self.sending_worker.wait()
        self.sending_worker = None
        self.run_state = IDLE
        self.reload()
        self.status_label.setText("Terminé" if finished_everything else "Arrêté")

    def shutdown(self):
        """Called when the application closes: stop cleanly after the message being sent."""
        if self.sending_worker is not None:
            self.sending_worker.stop()
            self.sending_worker.wait(5000)
