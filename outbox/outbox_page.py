from PySide6.QtCore import QAbstractTableModel, QElapsedTimer, QEvent, QModelIndex, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from outbox import outbox_store
from common.dialogs import ask_yes_no_defaulting_to_no
from common.french_labels import MESSAGE_STATE_LABELS, format_french_datetime, format_message_count, format_number
from outbox.batch_manager_dialog import BatchManagerDialog
from outbox.batches_panel import BatchesPanel
from outbox.sending_speed import DEFAULT_SENDING_SPEED_KEY, SENDING_SPEEDS, find_sending_speed
from outbox.sending_method_dialog import SIM_METHOD_KEY, SendingMethodDialog
from outbox.sending_worker import SendingWorker
from common.table_helpers import POSITION_COLUMN_TITLE, POSITION_COLUMN_WIDTH_PIXELS
from common.icons import (
    CHECK_CIRCLE_ICON_SHAPES,
    CLOCK_ICON_SHAPES,
    HOURGLASS_ICON_SHAPES,
    HELP_CIRCLE_ICON_SHAPES,
    PAUSE_ICON_SHAPES,
    PLAY_ICON_SHAPES,
    STOP_ICON_SHAPES,
    TRASH_ICON_SHAPES,
    X_CIRCLE_ICON_SHAPES,
    build_colored_icon,
)

# state -> (icon shapes, icon color)
STATE_ICON_STYLES = {
    outbox_store.WAITING: (CLOCK_ICON_SHAPES, "#F59E0B"),
    outbox_store.SENDING: (HOURGLASS_ICON_SHAPES, "#3B82F6"),
    outbox_store.SENT: (CHECK_CIRCLE_ICON_SHAPES, "#10B981"),  # kept in the Outbox until the run ends; the Historique reuses it
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
AUTO_SCROLL_PAUSE_AFTER_USER_ACTION_MILLISECONDS = 10_000  # leave the list alone this long after the user touched it
SEND_DURATION_SECONDS = 2  # rough time the dongle itself needs per SMS, added to the pause for the time estimate

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
        self.shown_states = outbox_store.OUTBOX_STATES  # a row whose new state is not shown (state filter) disappears

    def set_messages(self, queued_messages):
        self.beginResetModel()
        self.queued_messages = queued_messages
        self.row_by_message_id = {message.message_id: row for row, message in enumerate(queued_messages)}
        self.endResetModel()

    def update_message_state(self, message_id, new_state, error_detail):
        """Refresh one row in place while sending (no full reload of thousands of rows).
        A delivered message stays (success icon) until the run ends; it leaves only if the state filter hides it."""
        row = self.row_by_message_id.get(message_id)
        if row is None:
            return
        if new_state not in self.shown_states:
            self.beginRemoveRows(QModelIndex(), row, row)
            del self.queued_messages[row]
            self.row_by_message_id = {message.message_id: position for position, message in enumerate(self.queued_messages)}
            self.endRemoveRows()
            return
        self.queued_messages[row].state = new_state
        self.queued_messages[row].error_detail = error_detail
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMN_TITLES) - 1))

    def row_of_message(self, message_id):
        return self.row_by_message_id.get(message_id)

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

    reply_delivery_postponed = Signal(str)  # why a reply could not be sent now (it stays first in the queue)

    def __init__(self, outbox_store_instance, message_sender, gateways=()):
        super().__init__()
        self.outbox_store = outbox_store_instance
        self.message_sender = message_sender  # the SIM dongle: also what delivers the replies (gateways only send campaigns)
        self.gateways = list(gateways)
        self.state_icons = build_state_icons()
        self.sending_worker = None
        self.reply_worker = None  # sends just the replies while the campaign is idle or paused
        self.run_state = IDLE
        self.waiting_count = 0
        self.failed_count = 0
        self.unknown_count = 0
        self.last_user_action_timer = QElapsedTimer()  # restarted by any manual scroll / click / key on the list
        self.paused_reason = ""  # why the worker paused itself (e.g. no dongle), shown instead of « En pause »

        self.batches_panel = BatchesPanel()
        self.batches_panel.manage_requested.connect(self.open_batch_manager)
        self.send_button = self.build_control_button(PLAY_ICON_SHAPES, "#10B981", "Envoyer")
        self.pause_button = self.build_control_button(PAUSE_ICON_SHAPES, "#F59E0B", "Pause")
        self.stop_button = self.build_control_button(STOP_ICON_SHAPES, "#EF4444", "Arrêter")
        self.status_label = QLabel("")
        self.speed_combo = QComboBox()
        self.speed_combo.setToolTip("Pause aléatoire entre deux SMS. Plus c'est rapide, plus le risque que l'opérateur bloque la SIM augmente.")
        for sending_speed in SENDING_SPEEDS:
            self.speed_combo.addItem(sending_speed.french_label, sending_speed.speed_key)
        stored_speed_key = QSettings("SMS Gateway", "SMS Gateway").value("sending_speed", DEFAULT_SENDING_SPEED_KEY)
        self.speed_combo.setCurrentIndex(max(0, self.speed_combo.findData(stored_speed_key)))
        self.speed_combo.currentIndexChanged.connect(self.change_sending_speed)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("%v / %m")
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setVisible(False)
        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self.send_button)
        controls_layout.addWidget(self.pause_button)
        controls_layout.addWidget(self.stop_button)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(QLabel("Vitesse :"))
        controls_layout.addWidget(self.speed_combo)
        controls_layout.addSpacing(10)
        controls_layout.addWidget(self.status_label)
        controls_layout.addWidget(self.progress_bar, stretch=1)

        self.state_count_labels = {}
        summary_layout = QHBoxLayout()
        for state in outbox_store.OUTBOX_STATES:
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
        for state in outbox_store.OUTBOX_STATES:
            self.state_filter_combo.addItem(self.state_icons[state], MESSAGE_STATE_LABELS[state], state)
        self.clear_waiting_button = QPushButton("Vider les messages en attente")
        self.select_all_button = QPushButton("Tout sélectionner")
        self.delete_selection_button = QPushButton(build_colored_icon(TRASH_ICON_SHAPES, "#EF4444"), " Supprimer la sélection")
        self.delete_selection_button.setIconSize(QSize(CONTROL_ICON_SIZE_PIXELS, CONTROL_ICON_SIZE_PIXELS))
        self.selection_label = QLabel("")
        toolbar_layout = QHBoxLayout()
        toolbar_layout.addWidget(self.state_filter_combo)
        toolbar_layout.addWidget(self.selection_label)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.select_all_button)
        toolbar_layout.addWidget(self.delete_selection_button)
        toolbar_layout.addWidget(self.clear_waiting_button)

        self.table_model = OutboxTableModel()
        self.messages_table = QTableView()
        self.messages_table.setModel(self.table_model)
        self.messages_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.messages_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.messages_table.setSelectionMode(QAbstractItemView.ExtendedSelection)  # click, Shift/Cmd-click, Cmd+A
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

        self.empty_label = QLabel("Aucun message à envoyer. Les messages envoyés sont dans l'Historique.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("font-size: 16px; color: gray;")

        page_layout = QVBoxLayout(self)
        page_layout.addWidget(self.batches_panel)
        page_layout.addLayout(controls_layout)
        page_layout.addLayout(summary_layout)
        page_layout.addLayout(toolbar_layout)
        page_layout.addWidget(self.messages_table)
        page_layout.addWidget(self.empty_label)

        self.state_filter_combo.currentIndexChanged.connect(self.reload)
        self.clear_waiting_button.clicked.connect(self.clear_waiting_messages)
        self.select_all_button.clicked.connect(self.messages_table.selectAll)
        self.delete_selection_button.clicked.connect(self.delete_selected_messages)
        self.messages_table.selectionModel().selectionChanged.connect(self.refresh_selection_controls)
        QShortcut(QKeySequence(Qt.Key_Delete), self.messages_table, self.delete_selected_messages, context=Qt.WidgetShortcut)
        QShortcut(QKeySequence(Qt.Key_Backspace), self.messages_table, self.delete_selected_messages, context=Qt.WidgetShortcut)
        for watched_widget in (self.messages_table, self.messages_table.viewport()):
            watched_widget.installEventFilter(self)
        self.messages_table.verticalScrollBar().actionTriggered.connect(self.note_user_action_on_list)  # arrows / drag, not our scrollTo
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
        self.failed_count = state_counts[outbox_store.FAILED]
        self.unknown_count = state_counts[outbox_store.UNKNOWN]
        self.batches_panel.show_batches(self.outbox_store.list_batches())

        self.table_model.shown_states = self.states_to_show()
        self.table_model.set_messages(self.outbox_store.list_messages(self.states_to_show()))
        has_rows = self.table_model.rowCount() > 0
        self.messages_table.setVisible(has_rows)
        self.empty_label.setVisible(not has_rows)
        self.apply_run_state()
        self.refresh_selection_controls()

    def states_to_show(self):
        chosen_state = self.state_filter_combo.currentData()
        return (chosen_state,) if chosen_state else outbox_store.OUTBOX_STATES

    def selected_messages(self):
        selected_rows = sorted({index.row() for index in self.messages_table.selectionModel().selectedRows()})
        return [self.table_model.queued_messages[row] for row in selected_rows]

    def refresh_selection_controls(self, *_):
        selected_count = len(self.messages_table.selectionModel().selectedRows())
        self.selection_label.setText(f"{format_message_count(selected_count)} sélectionné{'s' if selected_count > 1 else ''}" if selected_count else "")
        self.delete_selection_button.setEnabled(selected_count > 0)
        self.select_all_button.setEnabled(self.table_model.rowCount() > 0)

    def delete_selected_messages(self):
        selected = self.selected_messages()
        if not selected:
            return
        deletable = [message for message in selected if message.state != outbox_store.SENDING]
        if not deletable:
            QMessageBox.information(self, "Suppression impossible", "Un message en cours d'envoi ne peut pas être supprimé.")
            return
        waiting_count = sum(1 for message in deletable if message.state == outbox_store.WAITING)
        warning_lines = [f"Supprimer {format_message_count(len(deletable))} de la boîte d'envoi ?"]
        if waiting_count:
            warning_lines.append(f"{format_message_count(waiting_count)} en attente ne seront pas envoyés.")
        if len(deletable) < len(selected):
            warning_lines.append("Le message en cours d'envoi sera conservé.")
        warning_lines.append("Cette action est irréversible.")
        if not ask_yes_no_defaulting_to_no(self, "Supprimer les messages", "\n\n".join(warning_lines)):
            return
        self.outbox_store.delete_messages([message.message_id for message in deletable])
        self.reload()

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
            self.outbox_store.archive_sent_messages()  # the batch is cleared: its delivered messages go to the Historique
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
        self.send_button.setEnabled(self.run_state == PAUSED or (is_idle and self.waiting_count + self.failed_count + self.unknown_count > 0))
        self.send_button.setText(" Reprendre" if self.run_state == PAUSED else " Envoyer")
        self.pause_button.setEnabled(self.run_state == RUNNING)
        self.stop_button.setEnabled(self.run_state in (RUNNING, PAUSED))
        self.clear_waiting_button.setEnabled(is_idle and self.waiting_count > 0)
        if self.run_state != IDLE:
            paused_text = f"En pause : {self.paused_reason}" if self.paused_reason else "En pause"
            self.status_label.setText({RUNNING: "Envoi en cours...", PAUSED: paused_text, STOPPING: "Arrêt en cours..."}[self.run_state])

    def start_or_resume_sending(self):
        """Starting or resuming always retries the FAILED messages first (they were certainly not sent)."""
        if self.run_state == PAUSED:
            self.paused_reason = ""
            self.outbox_store.requeue_failed_messages()
            self.reload()
            self.sending_worker.resume()
            self.run_state = RUNNING
        elif self.run_state == IDLE and self.waiting_count + self.failed_count + self.unknown_count > 0:
            campaign_sender, sending_method_name = self.choose_campaign_sender()
            if campaign_sender is None or not self.confirm_real_sending(sending_method_name):
                return
            self.outbox_store.requeue_failed_messages()
            self.reload()
            self.sending_worker = SendingWorker(
                self.outbox_store, campaign_sender, self.selected_sending_speed().gap_seconds_range
            )
            self.sending_worker.message_state_changed.connect(self.show_message_state_change)
            self.sending_worker.progress_changed.connect(self.show_progress)
            self.sending_worker.dongle_unavailable.connect(self.pause_because_dongle_unavailable)
            self.sending_worker.run_finished.connect(self.finish_run)
            self.progress_bar.setRange(0, max(self.waiting_count, 1))
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)
            self.run_state = RUNNING
            self.sending_worker.start()
        self.apply_run_state()

    # ---- replies (priority queue) ---------------------------------------------------------------

    def queue_reply(self, phone_number, recipient_name, reply_text):
        """A reply typed in the Inbox: queued with priority and sent as soon as the message in flight is done."""
        self.outbox_store.queue_reply(phone_number, recipient_name, reply_text)
        self.reload()
        self.deliver_waiting_replies()

    def drop_pending_messages_for_blacklisted_numbers(self):
        """A number was just blacklisted (Réception): its campaign messages not sent yet leave the queue."""
        self.outbox_store.remove_pending_messages_for_blacklisted_numbers()
        self.reload()

    def deliver_waiting_replies(self):
        """Make sure something is going to send the waiting replies."""
        if self.outbox_store.count_waiting_replies() == 0:
            return
        if self.run_state == RUNNING:
            self.sending_worker.notify_reply_queued()  # the campaign worker takes it right after the current message
        elif self.run_state == STOPPING:
            return  # finish_run calls this again once the campaign worker has ended
        elif self.reply_worker is None:
            # campaign idle, or paused by the user / for lack of a dongle: replies do not wait for it
            self.reply_worker = SendingWorker(self.outbox_store, self.message_sender, replies_only=True)
            self.reply_worker.message_state_changed.connect(self.show_reply_state_change)
            self.reply_worker.dongle_unavailable.connect(self.postpone_reply_delivery)
            self.reply_worker.run_finished.connect(self.finish_reply_run)
            self.reply_worker.start()

    def show_reply_state_change(self, message_id, new_state, error_detail):
        self.show_message_state_change(message_id, new_state, error_detail)
        self.refresh_state_counters()  # a replies-only worker reports no progress, so the counters follow its messages

    def postpone_reply_delivery(self, reason):
        self.reply_delivery_postponed.emit(reason or "aucun dongle prêt")

    def finish_reply_run(self, finished_everything):
        self.reply_worker.wait()
        self.reply_worker = None
        if self.run_state == IDLE:
            self.outbox_store.archive_sent_messages()  # no campaign run to show them in: delivered replies go to the Historique
            self.reload()
        else:
            self.refresh_state_counters()
            self.apply_run_state()
        if finished_everything:
            self.deliver_waiting_replies()  # a reply may have been queued just as the worker was ending

    def show_message_state_change(self, message_id, new_state, error_detail):
        self.table_model.update_message_state(message_id, new_state, error_detail)
        if new_state == outbox_store.SENDING:
            self.scroll_to_message_unless_user_is_interacting(message_id)

    # ---- follow the active message ------------------------------------------------------------

    def note_user_action_on_list(self, *_):
        self.last_user_action_timer.start()

    def user_is_interacting_with_list(self):
        if self.messages_table.verticalScrollBar().isSliderDown():
            return True
        return self.last_user_action_timer.isValid() and self.last_user_action_timer.elapsed() < AUTO_SCROLL_PAUSE_AFTER_USER_ACTION_MILLISECONDS

    def scroll_to_message_unless_user_is_interacting(self, message_id):
        row = self.table_model.row_of_message(message_id)
        if row is None or self.user_is_interacting_with_list():
            return
        self.messages_table.scrollTo(self.table_model.index(row, 0), QAbstractItemView.PositionAtCenter)

    def eventFilter(self, watched_widget, event):
        """Any wheel, click or key on the list counts as the user being in charge of the scrolling."""
        if event.type() in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.KeyPress):
            self.note_user_action_on_list()
        return super().eventFilter(watched_widget, event)

    def selected_sending_speed(self):
        return find_sending_speed(self.speed_combo.currentData())

    def change_sending_speed(self):
        """Remembered for next launch, and applied at once to a run already in progress."""
        QSettings("SMS Gateway", "SMS Gateway").setValue("sending_speed", self.speed_combo.currentData())
        if self.sending_worker is not None and self.sending_worker.isRunning():
            self.sending_worker.gap_seconds_range = self.selected_sending_speed().gap_seconds_range

    def choose_campaign_sender(self):
        """Ask « SIM or gateway ? ». Returns (sender, French name of the method), or (None, None) if cancelled."""
        if not self.gateways:
            return self.message_sender, "la SIM"
        method_dialog = SendingMethodDialog(self, self.gateways, self.waiting_count + self.failed_count + self.unknown_count)
        if method_dialog.exec() != SendingMethodDialog.Accepted:
            return None, None
        if method_dialog.chosen_method_key == SIM_METHOD_KEY:
            return self.message_sender, "la SIM"
        chosen_gateway = next(gateway for gateway in self.gateways if gateway.key == method_dialog.chosen_method_key)
        return chosen_gateway.create_message_sender(), f"l'offre {chosen_gateway.display_name}"

    def confirm_real_sending(self, sending_method_name="la SIM"):
        average_gap_seconds = sum(self.selected_sending_speed().gap_seconds_range) / 2 + SEND_DURATION_SECONDS
        messages_to_send_count = self.waiting_count + self.failed_count + self.unknown_count
        estimated_minutes = max(1, round(messages_to_send_count * average_gap_seconds / 60))
        failed_note = (
            f"Dont {format_message_count(self.failed_count + self.unknown_count)} en échec ou au résultat incertain (?), "
            "qui seront renvoyés en premier.\n\n" if self.failed_count + self.unknown_count else ""
        )
        return ask_yes_no_defaulting_to_no(
            self,
            "Envoyer les messages",
            f"Envoyer réellement {format_message_count(messages_to_send_count)} par SMS ?\n\n"
            f"{failed_note}"
            f"Durée estimée : environ {format_number(estimated_minutes)} min. "
            f"Les SMS envoyés ne peuvent pas être rappelés et consomment le crédit de {sending_method_name}.",
        )

    def pause_because_dongle_unavailable(self, reason):
        """The worker paused itself: the message it was about to send is back in the queue, untouched."""
        self.paused_reason = reason or "aucun dongle prêt"
        self.run_state = PAUSED
        self.refresh_state_counters()
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
        self.failed_count = state_counts[outbox_store.FAILED]
        self.unknown_count = state_counts[outbox_store.UNKNOWN]
        self.batches_panel.show_batches(self.outbox_store.list_batches())

    def open_batch_manager(self, campaign_id):
        batch_manager = BatchManagerDialog(self, self.outbox_store, campaign_id)
        batch_manager.exec()
        if batch_manager.changes_made:
            self.reload()  # corrected texts / removed messages

    def finish_run(self, finished_everything):
        self.sending_worker.wait()
        self.sending_worker = None
        self.run_state = IDLE
        self.outbox_store.archive_sent_messages()  # the run is over: delivered messages move to the Historique
        self.reload()
        self.status_label.setText("Terminé" if finished_everything else "Arrêté")
        self.deliver_waiting_replies()

    def shutdown(self):
        """Called when the application closes: stop cleanly after the message being sent."""
        for worker in (self.sending_worker, self.reply_worker):
            if worker is not None:
                worker.stop()
                worker.wait(5000)
