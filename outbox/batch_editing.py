"""Pure: what editing the message of a batch does to the messages that have not gone out yet. No UI, no database.

Every queued message keeps the values of its variables ({nom}, {téléphone}, {détails}), so a corrected template is simply
rendered again for each message: « Bonjour {nom} » can become « Bonjour M. {nom} » or just « Bonjour. »."""
from dataclasses import dataclass, field

from common.message_template import VARIABLE_LABELS, find_unknown_variables, render_final_message_text
from common.sms_encoding import MAX_PARTS_PER_MESSAGE, plan_sms


@dataclass
class EditableMessage:
    message_id: int
    variable_values: dict   # empty for a message queued before variables were stored: it cannot be rebuilt


@dataclass
class BatchEditPlan:
    new_text_by_message_id: dict = field(default_factory=dict)
    rebuilt_count: int = 0
    legacy_count: int = 0          # messages queued before variables were kept: left as they are
    empty_message_count: int = 0
    too_long_count: int = 0
    total_sms_count: int = 0
    unicode_message_count: int = 0
    unknown_variables: list = field(default_factory=list)
    template_is_empty: bool = False

    @property
    def rejection_reason(self):
        """Why this edit cannot be saved (French, shown to the owner), or '' when it can."""
        if self.template_is_empty:
            return "Le message est vide."
        if self.unknown_variables:
            return ("Variable inconnue : " + ", ".join("{" + name + "}" for name in self.unknown_variables)
                    + ". Variables possibles : " + ", ".join(VARIABLE_LABELS.values()) + ".")
        if self.empty_message_count:
            return f"{self.empty_message_count} message(s) seraient vides avec ce texte."
        if self.too_long_count:
            return f"{self.too_long_count} message(s) dépasseraient {MAX_PARTS_PER_MESSAGE} SMS : raccourcissez le message."
        return ""


def plan_batch_edit(template_text, editable_messages, simplify_to_7_bit=False):
    batch_edit_plan = BatchEditPlan(
        template_is_empty=not template_text.strip(),
        unknown_variables=find_unknown_variables(template_text),
    )
    for editable_message in editable_messages:
        if not editable_message.variable_values:
            batch_edit_plan.legacy_count += 1
            continue
        new_message_text = render_final_message_text(template_text, editable_message.variable_values, simplify_to_7_bit)
        sms_plan = plan_sms(new_message_text)
        batch_edit_plan.new_text_by_message_id[editable_message.message_id] = new_message_text
        batch_edit_plan.rebuilt_count += 1
        batch_edit_plan.total_sms_count += sms_plan.part_count if new_message_text else 0
        batch_edit_plan.empty_message_count += 0 if new_message_text else 1
        batch_edit_plan.too_long_count += 1 if sms_plan.is_too_long else 0
        batch_edit_plan.unicode_message_count += 1 if sms_plan.is_unicode else 0
    return batch_edit_plan
