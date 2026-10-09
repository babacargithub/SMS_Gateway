from functools import partial

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from compose.campaign_builder import CampaignDraft, build_campaign_draft, sample_extra_column_values
from common.french_labels import choose_singular_or_plural, format_contact_count, format_message_count, format_number, format_sms_count
from compose.sms_cost_estimate import describe_estimated_costs
from compose.typed_recipient import TYPED_NUMBER_LIST_NAME, build_typed_recipient_rows
from common.message_template import BASE_VARIABLE_LABELS, POSSIBLE_VARIABLES_DESCRIPTION, VARIABLE_LABELS, describe_variable, find_unknown_variables
from common.sms_preferences import (
    SIMPLIFY_CHECKBOX_LABEL,
    SIMPLIFY_CHECKBOX_TOOLTIP,
    load_simplify_to_7_bit,
    save_simplify_to_7_bit,
)
from common.sms_encoding import MAX_PARTS_PER_MESSAGE, SINGLE_SMS_CAPACITY, ALPHABET_GSM7, ALPHABET_UCS2

GRAY_TEXT = "color: gray;"
RED_TEXT = "color: #c0392b;"
GREEN_TEXT = "color: #1e8449;"
ORANGE_TEXT = "color: #b9770e;"
SECTION_TITLE_STYLE = "font-weight: 600;"
PREVIEW_STYLE = "background: rgba(128, 128, 128, 0.12); border-radius: 6px; padding: 8px;"
RECOMPUTE_DELAY_MILLISECONDS = 150  # wait for a pause in typing before rebuilding every message
DRAFT_SETTINGS_GROUP = "compose_draft"  # message, recipient choice, typed number and ticked lists, kept between launches


class ComposePage(QWidget):
    messages_queued = Signal()

    def __init__(self, contact_store, outbox_store, blacklist_store=None):
        super().__init__()
        self.contact_store = contact_store
        self.blacklist_store = blacklist_store  # numbers campaigns skip (None: no blacklist)
        self.outbox_store = outbox_store
        self.recipient_rows = []          # [(list_name, StoredContact)] of the checked lists, or the one typed number
        self.typed_number_error = ""
        self.current_draft = CampaignDraft()
        self.extra_column_samples = {}    # {"col1": ["Dakar", …]} for the columns the chosen recipients have
        self.sim_prices = []              # SimSmsPrice of each ready dongle whose operator has a known price

        self.lists_radio = QRadioButton("Listes de contacts")
        self.single_number_radio = QRadioButton("Un seul numéro")
        self.lists_radio.setChecked(True)
        self.recipient_mode_group = QButtonGroup(self)
        self.recipient_mode_group.addButton(self.lists_radio)
        self.recipient_mode_group.addButton(self.single_number_radio)
        recipient_mode_layout = QHBoxLayout()
        recipient_mode_layout.addWidget(self.lists_radio)
        recipient_mode_layout.addWidget(self.single_number_radio)
        recipient_mode_layout.addStretch()

        self.lists_widget = QListWidget()
        self.lists_widget.setMaximumHeight(110)
        self.no_lists_label = QLabel("Aucune liste de contacts. Importez-en une dans « Contacts ».")
        self.no_lists_label.setStyleSheet(GRAY_TEXT)
        self.lists_panel = QWidget()
        lists_panel_layout = QVBoxLayout(self.lists_panel)
        lists_panel_layout.setContentsMargins(0, 0, 0, 0)
        lists_panel_layout.addWidget(QLabel("Cochez une ou plusieurs listes :"))
        lists_panel_layout.addWidget(self.lists_widget)
        lists_panel_layout.addWidget(self.no_lists_label)

        self.number_input = QLineEdit()
        self.number_input.setPlaceholderText("Numéro : 77 123 45 67 ou +221 77 123 45 67")
        self.number_input.setClearButtonEnabled(True)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Nom (facultatif, pour la variable {nom})")
        self.name_input.setClearButtonEnabled(True)
        self.number_panel = QWidget()
        number_panel_layout = QHBoxLayout(self.number_panel)
        number_panel_layout.setContentsMargins(0, 0, 0, 0)
        number_panel_layout.addWidget(self.number_input, stretch=2)
        number_panel_layout.addWidget(self.name_input, stretch=2)
        self.number_panel.setVisible(False)

        self.recipients_label = QLabel("")
        self.recipients_label.setWordWrap(True)

        self.variable_buttons_layout = QHBoxLayout()
        self.variable_buttons_layout.addWidget(QLabel("Insérer une variable :"))
        for variable_label in BASE_VARIABLE_LABELS.values():
            self.variable_buttons_layout.addWidget(self.build_variable_button(variable_label))
        self.extra_column_buttons_layout = QHBoxLayout()   # {col1}… only for the columns the chosen lists actually have
        self.extra_column_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.variable_buttons_layout.addLayout(self.extra_column_buttons_layout)
        self.variable_buttons_layout.addStretch()
        self.extra_column_preview_label = QLabel("")
        self.extra_column_preview_label.setStyleSheet(GRAY_TEXT)
        self.extra_column_preview_label.setWordWrap(True)
        self.extra_column_preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.message_input = QPlainTextEdit()
        self.message_input.setPlaceholderText("Saisissez votre message... Exemple : Bonjour {nom}, votre rendez-vous est confirmé.")
        self.simplify_checkbox = QCheckBox(SIMPLIFY_CHECKBOX_LABEL)
        self.simplify_checkbox.setToolTip(SIMPLIFY_CHECKBOX_TOOLTIP)
        self.simplify_checkbox.setChecked(load_simplify_to_7_bit())
        self.counter_label = QLabel("")
        self.problems_label = QLabel("")
        self.problems_label.setWordWrap(True)
        self.notices_label = QLabel("")
        self.notices_label.setWordWrap(True)
        self.notices_label.setStyleSheet(ORANGE_TEXT)
        self.estimated_cost_label = QLabel("")
        self.estimated_cost_label.setStyleSheet(GRAY_TEXT)
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet(PREVIEW_STYLE)
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.launch_button = QPushButton("Lancer")
        self.launch_button.setDefault(True)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)

        page_layout = QVBoxLayout(self)
        page_layout.addWidget(self.section_title("1. Destinataires"))
        page_layout.addLayout(recipient_mode_layout)
        page_layout.addWidget(self.lists_panel)
        page_layout.addWidget(self.number_panel)
        page_layout.addWidget(self.recipients_label)
        page_layout.addWidget(self.section_title("2. Message"))
        page_layout.addLayout(self.variable_buttons_layout)
        page_layout.addWidget(self.extra_column_preview_label)
        page_layout.addWidget(self.message_input, stretch=1)
        page_layout.addWidget(self.simplify_checkbox)
        page_layout.addWidget(self.counter_label)
        page_layout.addWidget(self.problems_label)
        page_layout.addWidget(self.notices_label)
        page_layout.addWidget(self.estimated_cost_label)
        page_layout.addWidget(QLabel("Aperçu du premier message :"))
        page_layout.addWidget(self.preview_label)
        page_layout.addWidget(self.launch_button)
        page_layout.addWidget(self.result_label)

        self.recompute_timer = QTimer(self)
        self.recompute_timer.setSingleShot(True)
        self.recompute_timer.setInterval(RECOMPUTE_DELAY_MILLISECONDS)
        self.recompute_timer.timeout.connect(self.refresh_composition)
        self.message_input.textChanged.connect(self.recompute_timer.start)
        self.message_input.textChanged.connect(self.save_draft)
        self.number_input.textChanged.connect(self.save_draft)
        self.name_input.textChanged.connect(self.save_draft)
        self.lists_widget.itemChanged.connect(self.save_draft)
        self.single_number_radio.toggled.connect(self.save_draft)
        self.simplify_checkbox.toggled.connect(save_simplify_to_7_bit)
        self.simplify_checkbox.toggled.connect(self.refresh_composition)
        self.lists_widget.itemChanged.connect(self.reload_recipients)
        self.single_number_radio.toggled.connect(self.change_recipient_mode)
        self.number_input.textChanged.connect(self.reload_recipients)
        self.name_input.textChanged.connect(self.reload_recipients)
        self.launch_button.clicked.connect(self.launch)

        self.reload_lists()
        self.restore_draft()

    def build_variable_button(self, variable_label):
        variable_button = QPushButton(variable_label)
        variable_button.setToolTip(describe_variable(variable_label.strip("{}")))
        variable_button.clicked.connect(partial(self.insert_variable, variable_label))
        return variable_button

    @staticmethod
    def section_title(title_text):
        title_label = QLabel(title_text)
        title_label.setStyleSheet(SECTION_TITLE_STYLE)
        return title_label

    def showEvent(self, show_event):
        super().showEvent(show_event)
        self.reload_lists()  # lists may have been imported, renamed or deleted meanwhile

    # ---- draft kept between launches -----------------------------------------------------------

    @staticmethod
    def draft_settings():
        draft_settings = QSettings("SMS Gateway", "SMS Gateway")
        draft_settings.beginGroup(DRAFT_SETTINGS_GROUP)
        return draft_settings

    def save_draft(self, *_):
        draft_settings = self.draft_settings()
        draft_settings.setValue("message", self.message_input.toPlainText())
        draft_settings.setValue("single_number_mode", self.is_single_number_mode())
        draft_settings.setValue("number", self.number_input.text())
        draft_settings.setValue("name", self.name_input.text())
        draft_settings.setValue("checked_list_ids", [str(list_id) for list_id in self.checked_list_ids()])
        draft_settings.sync()

    def restore_draft(self):
        """Put back what was being written when the app was closed. Lists deleted meanwhile are simply not ticked."""
        draft_settings = self.draft_settings()
        saved_list_ids = {str(list_id) for list_id in (draft_settings.value("checked_list_ids", []) or [])}
        widgets_to_restore = (self.message_input, self.number_input, self.name_input, self.lists_widget, self.single_number_radio, self.lists_radio)
        for widget in widgets_to_restore:
            widget.blockSignals(True)
        self.message_input.setPlainText(draft_settings.value("message", "", type=str))
        self.number_input.setText(draft_settings.value("number", "", type=str))
        self.name_input.setText(draft_settings.value("name", "", type=str))
        for row in range(self.lists_widget.count()):
            list_item = self.lists_widget.item(row)
            list_item.setCheckState(Qt.Checked if str(list_item.data(Qt.UserRole)) in saved_list_ids else Qt.Unchecked)
        is_single_number_mode = draft_settings.value("single_number_mode", False, type=bool)
        self.single_number_radio.setChecked(is_single_number_mode)
        self.lists_radio.setChecked(not is_single_number_mode)
        for widget in widgets_to_restore:
            widget.blockSignals(False)
        self.lists_panel.setVisible(not is_single_number_mode)
        self.number_panel.setVisible(is_single_number_mode)
        self.reload_recipients()

    # ---- recipients --------------------------------------------------------------------------

    def checked_list_ids(self):
        return [
            self.lists_widget.item(row).data(Qt.UserRole)
            for row in range(self.lists_widget.count())
            if self.lists_widget.item(row).checkState() == Qt.Checked
        ]

    def is_single_number_mode(self):
        return self.single_number_radio.isChecked()

    def change_recipient_mode(self):
        self.lists_panel.setVisible(not self.is_single_number_mode())
        self.number_panel.setVisible(self.is_single_number_mode())
        if self.is_single_number_mode():
            self.number_input.setFocus()
        self.reload_recipients()

    def checked_list_names(self):
        if self.is_single_number_mode():
            return [TYPED_NUMBER_LIST_NAME]
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
        if self.is_single_number_mode():
            self.recipient_rows, self.typed_number_error = build_typed_recipient_rows(
                self.number_input.text(), self.name_input.text()
            )
        else:
            self.typed_number_error = ""
            self.recipient_rows = self.contact_store.list_contacts_for_lists(self.checked_list_ids())
        self.refresh_extra_column_variables()
        self.refresh_composition()

    def refresh_extra_column_variables(self):
        """One button and one line of example values per {colN} that the chosen recipients really have."""
        new_samples = sample_extra_column_values(self.recipient_rows)
        if new_samples.keys() != self.extra_column_samples.keys():
            while self.extra_column_buttons_layout.count():
                self.extra_column_buttons_layout.takeAt(0).widget().deleteLater()
            for column_name in new_samples:
                self.extra_column_buttons_layout.addWidget(self.build_variable_button(VARIABLE_LABELS[column_name]))
        self.extra_column_samples = new_samples
        preview_lines = [
            f"{VARIABLE_LABELS[column_name]}" + (f" ({describe_variable(column_name)})" if describe_variable(column_name) else "") + " : " + "  ·  ".join(samples) + ("…" if len(self.recipient_rows) > len(samples) else "")
            for column_name, samples in new_samples.items()
        ]
        self.extra_column_preview_label.setText("Exemples de valeurs :\n" + "\n".join(preview_lines) if preview_lines else "")
        self.extra_column_preview_label.setVisible(bool(preview_lines))

    def set_sim_prices(self, sim_prices):
        """Called whenever the dongles change: the price per SMS of each ready SIM."""
        if sim_prices != self.sim_prices:
            self.sim_prices = sim_prices
            self.refresh_composition()

    def insert_variable(self, variable_label):
        self.message_input.insertPlainText(variable_label)
        self.message_input.setFocus()

    # ---- live composition --------------------------------------------------------------------

    def refresh_composition(self):
        template_text = self.message_input.toPlainText()
        self.current_draft = build_campaign_draft(
            template_text, self.recipient_rows, self.simplify_checkbox.isChecked(),
            self.blacklist_store.list_numbers() if self.blacklist_store else frozenset(),
        )
        draft = self.current_draft
        has_template = bool(template_text.strip())

        if self.is_single_number_mode():
            if self.typed_number_error:
                self.recipients_label.setText(self.typed_number_error)
                self.recipients_label.setStyleSheet(RED_TEXT)
            elif draft.blacklisted_count:
                self.recipients_label.setText("Ce numéro est sur la liste noire : aucun message ne lui sera envoyé.")
                self.recipients_label.setStyleSheet(RED_TEXT)
            elif draft.messages:
                typed_recipient = draft.messages[0]
                recipient_text = f"{typed_recipient.recipient_name} ({typed_recipient.phone_number})" if typed_recipient.recipient_name else typed_recipient.phone_number
                self.recipients_label.setText(f"Destinataire : {recipient_text}")
                self.recipients_label.setStyleSheet("")
            else:
                self.recipients_label.setText("Saisissez le numéro du destinataire.")
                self.recipients_label.setStyleSheet(GRAY_TEXT)
        elif not self.checked_list_ids():
            self.recipients_label.setText("Cochez au moins une liste pour choisir les destinataires.")
            self.recipients_label.setStyleSheet(GRAY_TEXT)
        else:
            duplicates_text = f" ({format_number(draft.duplicate_count)} {choose_singular_or_plural(draft.duplicate_count, 'doublon ignoré', 'doublons ignorés')})" if draft.duplicate_count else ""
            self.recipients_label.setText(f"Destinataires : {format_contact_count(len(draft.messages))}{duplicates_text}")
            self.recipients_label.setStyleSheet("")

        is_over_limit = draft.too_long_count > 0
        self.counter_label.setText(self.describe_length(draft))
        self.counter_label.setStyleSheet(RED_TEXT if is_over_limit else GRAY_TEXT)

        problems = []
        unknown_variables = find_unknown_variables(template_text)
        if unknown_variables:
            problems.append("Variable inconnue : " + ", ".join("{" + name + "}" for name in unknown_variables)
                            + ". Variables possibles : " + POSSIBLE_VARIABLES_DESCRIPTION + ".")
        if draft.too_long_count:
            problems.append(f"{format_message_count(draft.too_long_count)} {choose_singular_or_plural(draft.too_long_count, 'dépasse', 'dépassent')} {MAX_PARTS_PER_MESSAGE} SMS : raccourcissez le message.")
        if has_template and draft.empty_message_count:
            problems.append(f"{format_message_count(draft.empty_message_count)} {choose_singular_or_plural(draft.empty_message_count, 'serait vide', 'seraient vides')}.")
        self.problems_label.setText("\n".join(problems))
        self.problems_label.setStyleSheet(RED_TEXT)

        notices = []
        if draft.unicode_message_count:
            notices.append(
                f"{format_message_count(draft.unicode_message_count)} en Unicode à cause de : "
                + " ".join(f"« {character} »" for character in draft.characters_forcing_unicode)
                + f". Un SMS Unicode contient {SINGLE_SMS_CAPACITY[ALPHABET_UCS2]} caractères au lieu de {SINGLE_SMS_CAPACITY[ALPHABET_GSM7]}. "
                "Cochez « Rester en 7 bits » pour les remplacer."
            )
        if draft.multipart_message_count:
            notices.append(
                f"{format_message_count(draft.multipart_message_count)} trop long{'s' if draft.multipart_message_count > 1 else ''} pour un SMS : "
                "envoyé(s) en plusieurs parties que le téléphone recolle, chaque partie étant facturée comme un SMS."
            )
        if draft.blacklisted_count and not self.is_single_number_mode():
            notices.append(
                f"{format_contact_count(draft.blacklisted_count)} sur liste noire : "
                f"{choose_singular_or_plural(draft.blacklisted_count, 'ignoré', 'ignorés')}."
            )
        for variable_name, empty_count in draft.empty_value_counts.items():
            notices.append(f"{format_contact_count(empty_count)} sans valeur pour {VARIABLE_LABELS[variable_name]} : elle restera vide.")
        self.notices_label.setText("\n".join(notices))

        has_valid_messages = has_template and not problems
        cost_lines = describe_estimated_costs(len(draft.messages), self.sim_prices, draft.total_sms_count) if has_valid_messages else []
        self.estimated_cost_label.setText("\n".join(cost_lines))
        self.estimated_cost_label.setVisible(bool(cost_lines))

        if draft.messages and has_template:
            first_message = draft.messages[0]
            self.preview_label.setText(
                f"{first_message.recipient_name or first_message.phone_number} · {format_sms_count(first_message.sms_count)}"
                f"{' · Unicode' if first_message.is_unicode else ''} :\n{first_message.message_text}"
            )
        else:
            self.preview_label.setText("—")

        self.launch_button.setEnabled(bool(draft.messages) and has_template and not problems)

    @staticmethod
    def describe_length(draft):
        """« Message le plus long : 214 caractères · 2 SMS » plus the total to be billed."""
        if not draft.messages:
            return f"Message le plus long : {format_number(draft.longest_message_length)} caractères"
        longest_text = f"Message le plus long : {format_number(draft.longest_message_length)} caractères · {format_sms_count(draft.longest_sms_count)}"
        if draft.total_sms_count != len(draft.messages):
            return f"{longest_text}  —  total : {format_sms_count(draft.total_sms_count)} pour {format_message_count(len(draft.messages))}"
        return longest_text

    # ---- launch ------------------------------------------------------------------------------

    def launch(self):
        self.recompute_timer.stop()
        self.refresh_composition()  # never queue from a stale preview
        if not self.launch_button.isEnabled():
            return
        draft = self.current_draft
        self.outbox_store.queue_campaign(
            self.message_input.toPlainText(), self.checked_list_names(), draft.messages, self.simplify_checkbox.isChecked()
        )
        self.result_label.setStyleSheet(GREEN_TEXT)
        self.result_label.setText(f"{format_message_count(len(draft.messages))} mis en file d'attente (état : En attente).")
        self.message_input.clear()  # empties the form so a double click cannot queue the same campaign twice
        self.lists_widget.blockSignals(True)
        for row in range(self.lists_widget.count()):
            self.lists_widget.item(row).setCheckState(Qt.Unchecked)
        self.lists_widget.blockSignals(False)
        self.number_input.clear()
        self.name_input.clear()
        self.reload_recipients()
        self.save_draft()  # the queued message is no longer a draft
        self.messages_queued.emit()
