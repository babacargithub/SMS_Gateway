"""« Gérer » window of one batch: correct its message (rebuilt for every message not sent yet) or delete what is still waiting."""
from functools import partial

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from common.dialogs import ask_yes_no_defaulting_to_no
from common.french_labels import MESSAGE_STATE_LABELS, format_french_datetime, format_message_count, format_number, format_sms_count
from common.icons import TRASH_ICON_SHAPES, build_colored_icon
from common.message_template import BASE_VARIABLE_LABELS, EXTRA_COLUMN_NAMES, VARIABLE_LABELS
from common.sms_preferences import SIMPLIFY_CHECKBOX_LABEL, SIMPLIFY_CHECKBOX_TOOLTIP
from outbox import outbox_store
from outbox.batch_editing import plan_batch_edit
from outbox.outbox_store import BatchEditRejected

GRAY_TEXT = "color: gray;"
RED_TEXT = "color: #c0392b;"
GREEN_TEXT = "color: #1e8449;"
ORANGE_TEXT = "color: #b9770e;"
PREVIEW_STYLE = "background: rgba(128, 128, 128, 0.12); border-radius: 6px; padding: 8px;"
RECOMPUTE_DELAY_MILLISECONDS = 150
EDITOR_MIN_HEIGHT_PIXELS = 110
CONTROL_ICON_SIZE_PIXELS = 16


class BatchManagerDialog(QDialog):
    def __init__(self, parent, outbox_store_instance, campaign_id):
        super().__init__(parent)
        self.outbox_store = outbox_store_instance
        self.campaign_id = campaign_id
        self.changes_made = False
        self.setWindowTitle("Gérer le lot")
        self.setMinimumWidth(620)

        self.title_label = QLabel("")
        self.title_label.setStyleSheet("font-weight: 600;")
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.variables_label = QLabel("")
        self.variables_label.setWordWrap(True)
        self.variables_label.setStyleSheet(GRAY_TEXT)
        self.explanation_label = QLabel(
            "Corrigez le message : il est reconstruit pour chaque message en attente (et en échec, renvoyé au prochain envoi) à partir "
            "de ses variables. Les messages déjà envoyés ou non confirmés ne changent pas."
        )
        self.explanation_label.setWordWrap(True)
        self.explanation_label.setStyleSheet(GRAY_TEXT)

        self.template_input = QPlainTextEdit()
        self.template_input.setMinimumHeight(EDITOR_MIN_HEIGHT_PIXELS)
        variable_buttons_layout = QHBoxLayout()
        variable_buttons_layout.addWidget(QLabel("Insérer une variable :"))
        for variable_label in self.variable_labels_of_this_batch():
            variable_button = QPushButton(variable_label)
            variable_button.clicked.connect(partial(self.insert_variable, variable_label))
            variable_buttons_layout.addWidget(variable_button)
        variable_buttons_layout.addStretch()
        self.simplify_checkbox = QCheckBox(SIMPLIFY_CHECKBOX_LABEL)
        self.simplify_checkbox.setToolTip(SIMPLIFY_CHECKBOX_TOOLTIP)

        self.problems_label = QLabel("")
        self.problems_label.setWordWrap(True)
        self.problems_label.setStyleSheet(RED_TEXT)
        self.notices_label = QLabel("")
        self.notices_label.setWordWrap(True)
        self.notices_label.setStyleSheet(ORANGE_TEXT)
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet(PREVIEW_STYLE)
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)

        self.save_button = QPushButton("Enregistrer les modifications")
        self.save_button.setDefault(True)
        self.delete_button = QPushButton(build_colored_icon(TRASH_ICON_SHAPES, "#EF4444"), " Supprimer les messages en attente")
        self.delete_button.setIconSize(QSize(CONTROL_ICON_SIZE_PIXELS, CONTROL_ICON_SIZE_PIXELS))
        self.close_button = QPushButton("Fermer")
        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.delete_button)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.close_button)
        buttons_layout.addWidget(self.save_button)

        dialog_layout = QVBoxLayout(self)
        dialog_layout.addWidget(self.title_label)
        dialog_layout.addWidget(self.summary_label)
        dialog_layout.addWidget(self.variables_label)
        dialog_layout.addWidget(self.explanation_label)
        dialog_layout.addWidget(QLabel("Message du lot :"))
        dialog_layout.addWidget(self.template_input)
        dialog_layout.addLayout(variable_buttons_layout)
        dialog_layout.addWidget(self.simplify_checkbox)
        dialog_layout.addWidget(self.problems_label)
        dialog_layout.addWidget(self.notices_label)
        dialog_layout.addWidget(QLabel("Aperçu du premier message modifié :"))
        dialog_layout.addWidget(self.preview_label)
        dialog_layout.addWidget(self.result_label)
        dialog_layout.addLayout(buttons_layout)

        self.recompute_timer = QTimer(self)
        self.recompute_timer.setSingleShot(True)
        self.recompute_timer.setInterval(RECOMPUTE_DELAY_MILLISECONDS)
        self.recompute_timer.timeout.connect(self.refresh_preview)
        self.template_input.textChanged.connect(self.recompute_timer.start)
        self.simplify_checkbox.toggled.connect(self.refresh_preview)
        self.save_button.clicked.connect(self.save_changes)
        self.delete_button.clicked.connect(self.delete_pending_messages)
        self.close_button.clicked.connect(self.accept)

        self.batch = None
        self.editable_messages = []
        self.load_batch(reset_editor=True)

    # ---- data ----------------------------------------------------------------------------------

    def load_batch(self, reset_editor):
        self.batch = self.outbox_store.get_batch(self.campaign_id)
        if self.batch is None:  # everything of it has left the Outbox meanwhile
            self.title_label.setText("Ce lot n'a plus de message dans la Boîte d'envoi.")
            for control in (self.template_input, self.simplify_checkbox, self.save_button, self.delete_button):
                control.setEnabled(False)
            return
        self.editable_messages = self.outbox_store.list_editable_messages(self.campaign_id)
        self.title_label.setText(f"Lot du {format_french_datetime(self.batch.created_at)}")
        counts = self.batch.state_counts
        state_parts = [
            f"{format_number(counts[state])} {MESSAGE_STATE_LABELS[state].lower()}"
            for state in (outbox_store.WAITING, outbox_store.SENDING, outbox_store.SENT, outbox_store.FAILED, outbox_store.UNKNOWN)
            if counts.get(state)
        ]
        self.summary_label.setText(
            f"Listes : {self.batch.list_names}  ·  {format_message_count(self.batch.total_count)} : " + ", ".join(state_parts)
        )
        used_labels = [VARIABLE_LABELS[name] for name in self.batch.variables_used if name in VARIABLE_LABELS]
        self.variables_label.setText(
            "Variables utilisées au lancement : " + (", ".join(used_labels) if used_labels else "aucune")
            + ("  ·  modifié le " + format_french_datetime(self.batch.edited_at) if self.batch.edited_at else "")
        )
        can_edit = bool(self.editable_messages)
        self.template_input.setEnabled(can_edit)
        self.simplify_checkbox.setEnabled(can_edit)
        self.delete_button.setEnabled(can_edit)
        if reset_editor:
            self.template_input.blockSignals(True)
            self.template_input.setPlainText(self.batch.message_template)
            self.template_input.blockSignals(False)
            self.simplify_checkbox.blockSignals(True)
            self.simplify_checkbox.setChecked(self.batch.simplify_to_7_bit)
            self.simplify_checkbox.blockSignals(False)
        self.refresh_preview()

    def variable_labels_of_this_batch(self):
        """{nom}, {téléphone}, {détails} plus the {colN} that at least one message of this batch has a value for."""
        columns_with_values = {
            variable_name
            for editable_message in self.outbox_store.list_editable_messages(self.campaign_id)
            for variable_name, variable_value in editable_message.variable_values.items()
            if variable_name in EXTRA_COLUMN_NAMES and variable_value
        }
        return list(BASE_VARIABLE_LABELS.values()) + [VARIABLE_LABELS[name] for name in EXTRA_COLUMN_NAMES if name in columns_with_values]

    def insert_variable(self, variable_label):
        self.template_input.insertPlainText(variable_label)
        self.template_input.setFocus()

    # ---- live check ------------------------------------------------------------------------------

    def refresh_preview(self):
        if self.batch is None:
            return
        self.recompute_timer.stop()
        if not self.editable_messages:
            self.problems_label.setText("")
            self.notices_label.setText("Aucun message de ce lot n'est en attente : il n'y a rien à modifier.")
            self.preview_label.setText("—")
            self.save_button.setEnabled(False)
            return
        new_template = self.template_input.toPlainText()
        batch_edit_plan = plan_batch_edit(new_template, self.editable_messages, self.simplify_checkbox.isChecked())
        rejection_reason = batch_edit_plan.rejection_reason
        has_changed = new_template != self.batch.message_template or self.simplify_checkbox.isChecked() != self.batch.simplify_to_7_bit
        self.problems_label.setText(rejection_reason)

        notices = []
        if not rejection_reason:
            notices.append(
                f"{format_message_count(batch_edit_plan.rebuilt_count)} {'seront reconstruits' if batch_edit_plan.rebuilt_count > 1 else 'sera reconstruit'}"
                f" ({format_sms_count(batch_edit_plan.total_sms_count)} au total)."
            )
        if batch_edit_plan.legacy_count:
            notices.append(
                f"{format_message_count(batch_edit_plan.legacy_count)} ne peu(ven)t pas être reconstruit(s) avec ce texte (créés avant l'enregistrement des variables, ou sans la valeur d'une variable utilisée) : "
                "ils partiront avec leur texte actuel."
            )
        if batch_edit_plan.unicode_message_count and not rejection_reason:
            notices.append(f"{format_message_count(batch_edit_plan.unicode_message_count)} en Unicode (70 caractères par SMS au lieu de 160).")
        self.notices_label.setText("\n".join(notices))

        first_message_id = next(iter(batch_edit_plan.new_text_by_message_id), None)
        if first_message_id is not None and not rejection_reason:
            first_values = next(message.variable_values for message in self.editable_messages if message.message_id == first_message_id)
            self.preview_label.setText(
                f"{first_values.get('nom') or first_values.get('telephone', '')} :\n{batch_edit_plan.new_text_by_message_id[first_message_id]}"
            )
        else:
            self.preview_label.setText("—")
        self.save_button.setEnabled(has_changed and not rejection_reason and batch_edit_plan.rebuilt_count > 0)

    # ---- actions ----------------------------------------------------------------------------------

    def save_changes(self):
        self.recompute_timer.stop()
        try:
            batch_edit_result = self.outbox_store.update_batch_template(
                self.campaign_id, self.template_input.toPlainText(), self.simplify_checkbox.isChecked()
            )
        except BatchEditRejected as rejection:
            self.result_label.setStyleSheet(RED_TEXT)
            self.result_label.setText(str(rejection))
            return
        self.changes_made = True
        self.result_label.setStyleSheet(GREEN_TEXT)
        self.result_label.setText(
            f"{format_message_count(batch_edit_result.updated_count)} modifié{'s' if batch_edit_result.updated_count > 1 else ''}."
            if batch_edit_result.updated_count else "Aucun message n'a changé de texte."
        )
        self.load_batch(reset_editor=False)

    def delete_pending_messages(self):
        deletable_count = len(self.editable_messages)
        if not deletable_count:
            return
        confirmed = ask_yes_no_defaulting_to_no(
            self,
            "Supprimer les messages en attente",
            f"Supprimer les {format_message_count(deletable_count)} de ce lot qui ne sont pas encore partis ?\n\n"
            "Les messages déjà envoyés restent dans l'Historique. Cette action est irréversible.",
        )
        if not confirmed:
            return
        removed_count = self.outbox_store.delete_batch_pending_messages(self.campaign_id)
        self.changes_made = True
        self.result_label.setStyleSheet(GREEN_TEXT)
        self.result_label.setText(f"{format_message_count(removed_count)} supprimé{'s' if removed_count > 1 else ''}.")
        self.load_batch(reset_editor=False)
