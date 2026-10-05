"""Small compose window to answer one received SMS."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from common.french_labels import format_french_datetime, format_number, format_sms_count
from common.sms_encoding import MAX_PARTS_PER_MESSAGE, characters_forcing_unicode, plan_sms, simplify_for_gsm7
from common.sms_preferences import (
    SIMPLIFY_CHECKBOX_LABEL,
    SIMPLIFY_CHECKBOX_TOOLTIP,
    load_simplify_to_7_bit,
    save_simplify_to_7_bit,
)

GRAY_TEXT = "color: gray;"
RED_TEXT = "color: #c0392b;"
ORANGE_TEXT = "color: #b9770e;"
QUOTE_STYLE = "background: rgba(128, 128, 128, 0.12); border-radius: 6px; padding: 8px;"
QUOTE_MAX_CHARACTERS = 300
REPLY_INPUT_HEIGHT_PIXELS = 110


class ReplyDialog(QDialog):
    def __init__(self, parent, recipient_label, original_message_text, original_received_at):
        super().__init__(parent)
        self.setWindowTitle("Répondre")
        self.setMinimumWidth(460)

        title_label = QLabel(f"Répondre à <b>{recipient_label}</b>")
        title_label.setTextFormat(Qt.RichText)
        shortened_original = original_message_text.strip()
        if len(shortened_original) > QUOTE_MAX_CHARACTERS:
            shortened_original = shortened_original[:QUOTE_MAX_CHARACTERS].rstrip() + "…"
        quote_label = QLabel(f"{format_french_datetime(original_received_at)}\n{shortened_original}")
        quote_label.setWordWrap(True)
        quote_label.setStyleSheet(QUOTE_STYLE)
        quote_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.reply_input = QPlainTextEdit()
        self.reply_input.setPlaceholderText("Votre réponse...")
        self.reply_input.setFixedHeight(REPLY_INPUT_HEIGHT_PIXELS)
        self.simplify_checkbox = QCheckBox(SIMPLIFY_CHECKBOX_LABEL)
        self.simplify_checkbox.setToolTip(SIMPLIFY_CHECKBOX_TOOLTIP)
        self.simplify_checkbox.setChecked(load_simplify_to_7_bit())
        self.counter_label = QLabel("")
        self.notice_label = QLabel("")
        self.notice_label.setStyleSheet(ORANGE_TEXT)
        self.notice_label.setWordWrap(True)
        self.priority_label = QLabel("La réponse part en priorité, dès que le message en cours d'envoi est terminé.")
        self.priority_label.setStyleSheet(GRAY_TEXT)
        self.priority_label.setWordWrap(True)

        self.buttons = QDialogButtonBox()
        self.send_button = self.buttons.addButton("Envoyer", QDialogButtonBox.AcceptRole)
        self.cancel_button = self.buttons.addButton("Annuler", QDialogButtonBox.RejectRole)
        self.send_button.setDefault(True)

        dialog_layout = QVBoxLayout(self)
        dialog_layout.addWidget(title_label)
        dialog_layout.addWidget(quote_label)
        dialog_layout.addWidget(self.reply_input)
        dialog_layout.addWidget(self.simplify_checkbox)
        dialog_layout.addWidget(self.counter_label)
        dialog_layout.addWidget(self.notice_label)
        dialog_layout.addWidget(self.priority_label)
        dialog_layout.addWidget(self.buttons)

        self.reply_input.textChanged.connect(self.refresh_validation)
        self.simplify_checkbox.toggled.connect(save_simplify_to_7_bit)
        self.simplify_checkbox.toggled.connect(self.refresh_validation)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.refresh_validation()
        self.reply_input.setFocus()

    def reply_text(self):
        """What will be sent: the typed text, with special characters replaced when « Rester en 7 bits » is ticked."""
        typed_text = self.reply_input.toPlainText().strip()
        return simplify_for_gsm7(typed_text) if self.simplify_checkbox.isChecked() else typed_text

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
