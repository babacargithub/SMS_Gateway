"""WhatsApp-style conversation with one correspondent: received messages on the left, sent ones on the right, each with
its date, and the composer for the next message below."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QMenu,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from blacklist.blacklist_store import BlacklistStore
from common.dialogs import ask_yes_no_defaulting_to_no
from common.french_labels import format_french_date, format_french_datetime, format_number, format_sms_count
from common.sms_encoding import MAX_PARTS_PER_MESSAGE, characters_forcing_unicode, plan_sms, simplify_for_gsm7
from common.sms_preferences import (
    SIMPLIFY_CHECKBOX_LABEL,
    SIMPLIFY_CHECKBOX_TOOLTIP,
    load_simplify_to_7_bit,
    save_simplify_to_7_bit,
)
from conversations.conversation_store import RECEIVED_DIRECTION, SENT_DIRECTION, ConversationStore
from outbox import outbox_store

GRAY_TEXT = "color: gray;"
RED_TEXT = "color: #c0392b;"
ORANGE_TEXT = "color: #b9770e;"
# explicit text colors so the bubbles stay readable in dark mode too
RECEIVED_BUBBLE_STYLE = "QFrame#bubble { background: #E5E7EB; border-radius: 10px; } QLabel { color: #111827; background: transparent; }"
SENT_BUBBLE_STYLE = "QFrame#bubble { background: #D9F2C4; border-radius: 10px; } QLabel { color: #111827; background: transparent; }"
FAILED_BUBBLE_STYLE = "QFrame#bubble { background: #FADBD8; border-radius: 10px; } QLabel { color: #111827; background: transparent; }"
BUBBLE_META_STYLE = "color: #6B7280; font-size: 11px;"
DAY_SEPARATOR_STYLE = "color: #6B7280; font-size: 11px; background: rgba(128, 128, 128, 0.15); border-radius: 8px; padding: 2px 10px;"
BUBBLE_MAX_WIDTH_PIXELS = 380
COMPOSER_HEIGHT_PIXELS = 80
REFRESH_INTERVAL_MILLISECONDS = 2000
STATE_LABELS = {
    outbox_store.WAITING: "En attente d'envoi",
    outbox_store.SENDING: "Envoi en cours…",
    outbox_store.FAILED: "Échec : sera renvoyé au prochain envoi",
    outbox_store.UNKNOWN: "Envoi non confirmé",
}


class ConversationDialog(QDialog):
    """Opened from the Réception reply icon. Sending queues the message with priority (`reply_submitted`) and the bubble
    appears at once, « En attente d'envoi » until the dongle has sent it. The thread refreshes by itself while open."""

    reply_submitted = Signal(str, str, str)  # number to answer, recipient name (may be empty), text
    blacklist_changed = Signal()

    def __init__(self, parent, conversation_store: ConversationStore, blacklist_store: BlacklistStore, number_to_answer, can_reply=True):
        super().__init__(parent)
        self.conversation_store = conversation_store
        self.blacklist_store = blacklist_store
        self.can_blacklist = can_reply  # only a real number can be blacklisted
        self.number_to_answer = number_to_answer
        self.setWindowTitle("Conversation")
        self.resize(560, 680)
        self.shown_entries_signature = None
        self.contact_name = ""

        self.title_label = QLabel("")
        self.title_label.setTextFormat(Qt.RichText)
        self.title_label.setStyleSheet("font-size: 15px;")
        self.settings_button = QPushButton("⚙ Paramètres")
        self.settings_menu = QMenu(self.settings_button)
        self.blacklist_action = self.settings_menu.addAction("")
        self.blacklist_action.triggered.connect(self.toggle_blacklist)
        self.settings_button.setMenu(self.settings_menu)
        self.settings_button.setEnabled(self.can_blacklist)
        self.blacklist_notice_label = QLabel("")
        self.blacklist_notice_label.setStyleSheet(RED_TEXT)
        self.blacklist_notice_label.setWordWrap(True)
        header_layout = QHBoxLayout()
        header_layout.addWidget(self.title_label, 1)
        header_layout.addWidget(self.settings_button)

        self.thread_layout = QVBoxLayout()
        self.thread_layout.setAlignment(Qt.AlignTop)
        thread_container = QWidget()
        thread_container.setLayout(self.thread_layout)
        self.thread_scroll_area = QScrollArea()
        self.thread_scroll_area.setWidgetResizable(True)
        self.thread_scroll_area.setWidget(thread_container)
        self.thread_scroll_area.setFrameShape(QFrame.NoFrame)

        self.reply_input = QPlainTextEdit()
        self.reply_input.setPlaceholderText("Votre message...")
        self.reply_input.setFixedHeight(COMPOSER_HEIGHT_PIXELS)
        self.simplify_checkbox = QCheckBox(SIMPLIFY_CHECKBOX_LABEL)
        self.simplify_checkbox.setToolTip(SIMPLIFY_CHECKBOX_TOOLTIP)
        self.simplify_checkbox.setChecked(load_simplify_to_7_bit())
        self.counter_label = QLabel("")
        self.notice_label = QLabel("")
        self.notice_label.setWordWrap(True)
        self.send_button = QPushButton("Envoyer")
        self.send_button.setDefault(True)
        self.close_button = QPushButton("Fermer")
        self.priority_label = QLabel("Le message part en priorité, dès que le message en cours d'envoi est terminé.")
        self.priority_label.setStyleSheet(GRAY_TEXT)
        self.priority_label.setWordWrap(True)

        footer_layout = QHBoxLayout()
        footer_layout.addWidget(self.counter_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.close_button)
        footer_layout.addWidget(self.send_button)

        dialog_layout = QVBoxLayout(self)
        dialog_layout.addLayout(header_layout)
        dialog_layout.addWidget(self.blacklist_notice_label)
        dialog_layout.addWidget(self.thread_scroll_area, 1)
        self.composer_widgets = [self.reply_input, self.simplify_checkbox, self.notice_label, self.priority_label]
        for composer_widget in self.composer_widgets:
            dialog_layout.addWidget(composer_widget)
        dialog_layout.addLayout(footer_layout)

        if not can_reply:  # an alphanumeric sender (« Expresso ») cannot receive an SMS: reading only
            for composer_widget in (*self.composer_widgets, self.counter_label, self.send_button):
                composer_widget.setVisible(False)

        self.reply_input.textChanged.connect(self.refresh_validation)
        self.simplify_checkbox.toggled.connect(save_simplify_to_7_bit)
        self.simplify_checkbox.toggled.connect(self.refresh_validation)
        self.send_button.clicked.connect(self.submit_reply)
        self.close_button.clicked.connect(self.accept)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.reload_thread)
        self.refresh_timer.start(REFRESH_INTERVAL_MILLISECONDS)

        self.refresh_blacklist_state()
        self.conversation_store.mark_received_messages_as_read(number_to_answer)
        self.reload_thread()
        self.refresh_validation()
        self.reply_input.setFocus()

    # ---- blacklist ------------------------------------------------------------------------------

    def refresh_blacklist_state(self):
        is_blacklisted = self.blacklist_store.is_blacklisted(self.number_to_answer)
        self.blacklist_action.setText("Retirer de la liste noire" if is_blacklisted else "Mettre sur liste noire")
        self.blacklist_notice_label.setText(
            "Numéro sur liste noire : les campagnes l'ignorent. Vous pouvez toujours lui écrire ici." if is_blacklisted else ""
        )
        self.blacklist_notice_label.setVisible(is_blacklisted)

    def toggle_blacklist(self):
        if self.blacklist_store.is_blacklisted(self.number_to_answer):
            self.blacklist_store.remove_number(self.number_to_answer)
        else:
            confirmed = ask_yes_no_defaulting_to_no(
                self,
                "Liste noire",
                f"Mettre {self.number_to_answer} sur liste noire ?\n\n"
                "Les campagnes ne lui enverront plus rien, et ses messages de campagne en attente seront retirés de la Boîte d'envoi.",
            )
            if not confirmed:
                return
            self.blacklist_store.add_number(self.number_to_answer, self.contact_name)
        self.refresh_blacklist_state()
        self.blacklist_changed.emit()

    # ---- thread ---------------------------------------------------------------------------------

    def reload_thread(self):
        conversation = self.conversation_store.load_conversation(self.number_to_answer)
        self.contact_name = conversation.contact_name
        displayed_name = f"<b>{conversation.contact_name}</b> · {conversation.conversation_key}" if conversation.contact_name \
            else f"<b>{conversation.conversation_key}</b>"
        self.title_label.setText(displayed_name)
        self.setWindowTitle(f"Conversation avec {conversation.contact_name or conversation.conversation_key}")
        entries_signature = [
            (entry.direction, entry.entry_id, entry.outbox_state, entry.message_text) for entry in conversation.entries
        ]
        if entries_signature == self.shown_entries_signature:
            return
        self.shown_entries_signature = entries_signature
        scrollbar = self.thread_scroll_area.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        self.clear_thread()
        previous_day = None
        for entry in conversation.entries:
            entry_day = entry.occurred_at.split(" ")[0]
            if entry_day != previous_day:
                self.thread_layout.addWidget(self.build_day_separator(entry_day))
                previous_day = entry_day
            self.thread_layout.addLayout(self.build_message_row(entry))
        if not conversation.entries:
            empty_label = QLabel("Aucun message échangé avec ce numéro.")
            empty_label.setAlignment(Qt.AlignCenter)
            empty_label.setStyleSheet(GRAY_TEXT)
            self.thread_layout.addWidget(empty_label)
        if was_at_bottom or scrollbar.maximum() == 0:
            QTimer.singleShot(0, self.scroll_to_latest_message)

    def clear_thread(self):
        while self.thread_layout.count():
            layout_item = self.thread_layout.takeAt(0)
            if layout_item.widget():
                layout_item.widget().deleteLater()
            elif layout_item.layout():
                self.delete_layout_widgets(layout_item.layout())

    def delete_layout_widgets(self, layout):
        while layout.count():
            layout_item = layout.takeAt(0)
            if layout_item.widget():
                layout_item.widget().deleteLater()

    def scroll_to_latest_message(self):
        scrollbar = self.thread_scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @staticmethod
    def build_day_separator(iso_day):
        day_label = QLabel(format_french_date(iso_day))
        day_label.setStyleSheet(DAY_SEPARATOR_STYLE)
        day_label.setAlignment(Qt.AlignCenter)
        day_container = QWidget()
        day_layout = QHBoxLayout(day_container)
        day_layout.setContentsMargins(0, 6, 0, 2)
        day_layout.addStretch()
        day_layout.addWidget(day_label)
        day_layout.addStretch()
        return day_container

    @staticmethod
    def build_message_row(entry):
        is_sent = entry.direction == SENT_DIRECTION
        has_failed = is_sent and entry.outbox_state in (outbox_store.FAILED, outbox_store.UNKNOWN)

        text_label = QLabel(entry.message_text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        meta_text = format_french_datetime(entry.occurred_at)
        if is_sent and entry.outbox_state in STATE_LABELS:
            meta_text += f" · {STATE_LABELS[entry.outbox_state]}"
        meta_label = QLabel(meta_text)
        meta_label.setStyleSheet(BUBBLE_META_STYLE)
        meta_label.setAlignment(Qt.AlignRight)
        if entry.error_detail:
            meta_label.setToolTip(entry.error_detail)

        bubble = QFrame()
        bubble.setObjectName("bubble")
        bubble.setStyleSheet(FAILED_BUBBLE_STYLE if has_failed else SENT_BUBBLE_STYLE if is_sent else RECEIVED_BUBBLE_STYLE)
        bubble.setMaximumWidth(BUBBLE_MAX_WIDTH_PIXELS)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(10, 6, 10, 6)
        bubble_layout.setSpacing(2)
        bubble_layout.addWidget(text_label)
        bubble_layout.addWidget(meta_label)

        row_layout = QHBoxLayout()
        if is_sent:
            row_layout.addStretch()
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch()
        return row_layout

    # ---- composer -------------------------------------------------------------------------------

    def reply_text(self):
        """What will be sent: the typed text, with special characters replaced when « Rester en 7 bits » is ticked."""
        typed_text = self.reply_input.toPlainText().strip()
        return simplify_for_gsm7(typed_text) if self.simplify_checkbox.isChecked() else typed_text

    def submit_reply(self):
        text_to_send = self.reply_text()
        if not text_to_send or plan_sms(text_to_send).is_too_long:
            return
        self.reply_submitted.emit(self.number_to_answer, self.contact_name, text_to_send)
        self.reply_input.clear()
        self.reload_thread()
        QTimer.singleShot(0, self.scroll_to_latest_message)
        self.reply_input.setFocus()

    def refresh_validation(self):
        typed_text = self.reply_text()
        sms_plan = plan_sms(typed_text)
        self.counter_label.setText(
            f"{format_number(len(typed_text))} caractères · {format_sms_count(sms_plan.part_count)}"
            + (" · Unicode" if sms_plan.is_unicode else "")
        )
        self.counter_label.setStyleSheet(RED_TEXT if sms_plan.is_too_long else GRAY_TEXT)
        if sms_plan.is_too_long:
            self.notice_label.setText(f"Trop long : {MAX_PARTS_PER_MESSAGE} SMS au maximum.")
            self.notice_label.setStyleSheet(RED_TEXT)
        elif sms_plan.is_unicode:
            self.notice_label.setText(
                "Unicode à cause de : " + " ".join(f"« {character} »" for character in characters_forcing_unicode(typed_text)[:8])
                + ". Un SMS Unicode contient 70 caractères au lieu de 160."
            )
            self.notice_label.setStyleSheet(ORANGE_TEXT)
        elif sms_plan.part_count > 1:
            self.notice_label.setText("Message long : envoyé en plusieurs parties, chacune facturée comme un SMS.")
            self.notice_label.setStyleSheet(ORANGE_TEXT)
        self.notice_label.setVisible(sms_plan.is_too_long or sms_plan.is_unicode or sms_plan.part_count > 1)
        self.send_button.setEnabled(bool(typed_text) and not sms_plan.is_too_long)
