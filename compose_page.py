from functools import partial

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from campaign_builder import CampaignDraft, build_campaign_draft
from french_labels import choose_singular_or_plural, format_contact_count, format_message_count, format_number
from message_template import VARIABLE_LABELS, find_unknown_variables
from sms_text import MAX_SINGLE_SMS_LENGTH, sanitize_for_text_mode

GRAY_TEXT = "color: gray;"
RED_TEXT = "color: #c0392b;"
GREEN_TEXT = "color: #1e8449;"
ORANGE_TEXT = "color: #b9770e;"
SECTION_TITLE_STYLE = "font-weight: 600;"
PREVIEW_STYLE = "background: rgba(128, 128, 128, 0.12); border-radius: 6px; padding: 8px;"
RECOMPUTE_DELAY_MILLISECONDS = 150  # wait for a pause in typing before rebuilding every message


class ComposePage(QWidget):
    messages_queued = Signal()

    def __init__(self, contact_store, outbox_store):
        super().__init__()
        self.contact_store = contact_store
        self.outbox_store = outbox_store
        self.recipient_rows = []          # [(list_name, StoredContact)] of the checked lists
        self.current_draft = CampaignDraft()

        self.lists_widget = QListWidget()
        self.lists_widget.setMaximumHeight(110)
        self.no_lists_label = QLabel("Aucune liste de contacts. Importez-en une dans « Contacts ».")
        self.no_lists_label.setStyleSheet(GRAY_TEXT)
        self.recipients_label = QLabel("")

        self.variable_buttons_layout = QHBoxLayout()
        self.variable_buttons_layout.addWidget(QLabel("Insérer une variable :"))
        for variable_label in VARIABLE_LABELS.values():
            variable_button = QPushButton(variable_label)
            variable_button.clicked.connect(partial(self.insert_variable, variable_label))
            self.variable_buttons_layout.addWidget(variable_button)
        self.variable_buttons_layout.addStretch()

        self.message_input = QPlainTextEdit()
        self.message_input.setPlaceholderText("Saisissez votre message... Exemple : Bonjour {nom}, votre rendez-vous est confirmé.")
        self.counter_label = QLabel("")
        self.problems_label = QLabel("")
        self.problems_label.setWordWrap(True)
        self.notices_label = QLabel("")
        self.notices_label.setWordWrap(True)
        self.notices_label.setStyleSheet(ORANGE_TEXT)
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet(PREVIEW_STYLE)
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.launch_button = QPushButton("Lancer")
        self.launch_button.setDefault(True)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)

        page_layout = QVBoxLayout(self)
        page_layout.addWidget(self.section_title("1. Listes de contacts (cochez une ou plusieurs listes)"))
        page_layout.addWidget(self.lists_widget)
        page_layout.addWidget(self.no_lists_label)
        page_layout.addWidget(self.recipients_label)
        page_layout.addWidget(self.section_title("2. Message"))
        page_layout.addLayout(self.variable_buttons_layout)
        page_layout.addWidget(self.message_input, stretch=1)
        page_layout.addWidget(self.counter_label)
        page_layout.addWidget(self.problems_label)
        page_layout.addWidget(self.notices_label)
        page_layout.addWidget(QLabel("Aperçu du premier message :"))
        page_layout.addWidget(self.preview_label)
        page_layout.addWidget(self.launch_button)
        page_layout.addWidget(self.result_label)

        self.recompute_timer = QTimer(self)
        self.recompute_timer.setSingleShot(True)
        self.recompute_timer.setInterval(RECOMPUTE_DELAY_MILLISECONDS)
        self.recompute_timer.timeout.connect(self.refresh_composition)
        self.message_input.textChanged.connect(self.recompute_timer.start)
        self.lists_widget.itemChanged.connect(self.reload_recipients)
        self.launch_button.clicked.connect(self.launch)

        self.reload_lists()

    @staticmethod
    def section_title(title_text):
        title_label = QLabel(title_text)
        title_label.setStyleSheet(SECTION_TITLE_STYLE)
        return title_label

    def showEvent(self, show_event):
        super().showEvent(show_event)
        self.reload_lists()  # lists may have been imported, renamed or deleted meanwhile

    # ---- recipients --------------------------------------------------------------------------

    def checked_list_ids(self):
        return [
            self.lists_widget.item(row).data(Qt.UserRole)
            for row in range(self.lists_widget.count())
            if self.lists_widget.item(row).checkState() == Qt.Checked
        ]

    def checked_list_names(self):
        return [
            self.lists_widget.item(row).data(Qt.UserRole + 1)
            for row in range(self.lists_widget.count())
            if self.lists_widget.item(row).checkState() == Qt.Checked
        ]

    def reload_lists(self):
        previously_checked_ids = set(self.checked_list_ids())
        contact_lists = self.contact_store.list_contact_lists()
        self.lists_widget.blockSignals(True)
        self.lists_widget.clear()
        for contact_list in contact_lists:
            list_item = QListWidgetItem(f"{contact_list.name}  ({format_contact_count(contact_list.contact_count)})")
            list_item.setData(Qt.UserRole, contact_list.list_id)
            list_item.setData(Qt.UserRole + 1, contact_list.name)
            list_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            list_item.setCheckState(Qt.Checked if contact_list.list_id in previously_checked_ids else Qt.Unchecked)
            self.lists_widget.addItem(list_item)
        self.lists_widget.blockSignals(False)
        self.lists_widget.setVisible(bool(contact_lists))
        self.no_lists_label.setVisible(not contact_lists)
        self.reload_recipients()

    def reload_recipients(self):
        self.recipient_rows = self.contact_store.list_contacts_for_lists(self.checked_list_ids())
        self.refresh_composition()

    def insert_variable(self, variable_label):
        self.message_input.insertPlainText(variable_label)
        self.message_input.setFocus()

    # ---- live composition --------------------------------------------------------------------

    def refresh_composition(self):
        template_text = self.message_input.toPlainText()
        self.current_draft = build_campaign_draft(template_text, self.recipient_rows)
        draft = self.current_draft
        has_template = bool(template_text.strip())

        if not self.checked_list_ids():
            self.recipients_label.setText("Cochez au moins une liste pour choisir les destinataires.")
            self.recipients_label.setStyleSheet(GRAY_TEXT)
        else:
            duplicates_text = f" ({format_number(draft.duplicate_count)} {choose_singular_or_plural(draft.duplicate_count, 'doublon ignoré', 'doublons ignorés')})" if draft.duplicate_count else ""
            self.recipients_label.setText(f"Destinataires : {format_contact_count(len(draft.messages))}{duplicates_text}")
            self.recipients_label.setStyleSheet("")

        is_over_limit = draft.longest_message_length > MAX_SINGLE_SMS_LENGTH
        self.counter_label.setText(f"Message le plus long : {draft.longest_message_length} / {MAX_SINGLE_SMS_LENGTH} caractères")
        self.counter_label.setStyleSheet(RED_TEXT if is_over_limit else GRAY_TEXT)

        problems = []
        unknown_variables = find_unknown_variables(template_text)
        if unknown_variables:
            problems.append("Variable inconnue : " + ", ".join("{" + name + "}" for name in unknown_variables)
                            + ". Variables possibles : " + ", ".join(VARIABLE_LABELS.values()) + ".")
        if draft.too_long_count:
            problems.append(f"{format_message_count(draft.too_long_count)} {choose_singular_or_plural(draft.too_long_count, 'dépasse', 'dépassent')} {MAX_SINGLE_SMS_LENGTH} caractères : raccourcissez le message.")
        if has_template and draft.empty_message_count:
            problems.append(f"{format_message_count(draft.empty_message_count)} {choose_singular_or_plural(draft.empty_message_count, 'serait vide', 'seraient vides')}.")
        self.problems_label.setText("\n".join(problems))
        self.problems_label.setStyleSheet(RED_TEXT)

        notices = []
        if sanitize_for_text_mode(template_text)[1]:
            notices.append("Les accents et caractères spéciaux seront simplifiés (é → e, autres → ?).")
        for variable_name, empty_count in draft.empty_value_counts.items():
            notices.append(f"{format_contact_count(empty_count)} sans valeur pour {VARIABLE_LABELS[variable_name]} : elle restera vide.")
        self.notices_label.setText("\n".join(notices))

        if draft.messages and has_template:
            first_message = draft.messages[0]
            self.preview_label.setText(f"{first_message.recipient_name or first_message.phone_number} :\n{first_message.message_text}")
        else:
            self.preview_label.setText("—")

        self.launch_button.setEnabled(bool(draft.messages) and has_template and not problems)

    # ---- launch ------------------------------------------------------------------------------

    def launch(self):
        self.recompute_timer.stop()
        self.refresh_composition()  # never queue from a stale preview
        if not self.launch_button.isEnabled():
            return
        draft = self.current_draft
        self.outbox_store.queue_campaign(self.message_input.toPlainText(), self.checked_list_names(), draft.messages)
        self.result_label.setStyleSheet(GREEN_TEXT)
        self.result_label.setText(f"{format_message_count(len(draft.messages))} mis en file d'attente (état : En attente).")
        self.message_input.clear()  # empties the form so a double click cannot queue the same campaign twice
        self.lists_widget.blockSignals(True)
        for row in range(self.lists_widget.count()):
            self.lists_widget.item(row).setCheckState(Qt.Unchecked)
        self.lists_widget.blockSignals(False)
        self.reload_recipients()
        self.messages_queued.emit()
