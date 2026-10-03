"""Turns a template + the recipients of one or more lists into the messages to queue. No UI, no database."""
from dataclasses import dataclass, field

from common.french_labels import format_custom_fields
from compose.message_template import find_used_variables, render_message
from common.sms_text import MAX_SINGLE_SMS_LENGTH, sanitize_for_text_mode


@dataclass
class DraftMessage:
    list_name: str
    recipient_name: str
    phone_number: str
    message_text: str


@dataclass
class CampaignDraft:
    messages: list = field(default_factory=list)
    duplicate_count: int = 0            # same number found in several selected lists: kept once
    empty_value_counts: dict = field(default_factory=dict)  # variable -> contacts where its value is empty
    too_long_count: int = 0             # messages over one SMS (160 characters)
    empty_message_count: int = 0
    longest_message_length: int = 0


def contact_variable_values(contact):
    return {
        "nom": contact.name,
        "telephone": contact.phone_number,
        "details": format_custom_fields(contact.custom_fields, separator=", "),
    }


def build_campaign_draft(template_text, contacts_with_list_names):
    """contacts_with_list_names: [(list_name, StoredContact)]. Each phone number is kept only once."""
    used_variables = find_used_variables(template_text)
    campaign_draft = CampaignDraft()
    phone_numbers_already_kept = set()
    for list_name, contact in contacts_with_list_names:
        if contact.phone_number in phone_numbers_already_kept:
            campaign_draft.duplicate_count += 1
            continue
        phone_numbers_already_kept.add(contact.phone_number)

        variable_values = contact_variable_values(contact)
        for variable_name in used_variables:
            if not variable_values[variable_name].strip():
                campaign_draft.empty_value_counts[variable_name] = campaign_draft.empty_value_counts.get(variable_name, 0) + 1

        message_text, _ = sanitize_for_text_mode(render_message(template_text, variable_values))
        message_text = message_text.strip()
        if not message_text:
            campaign_draft.empty_message_count += 1
        if len(message_text) > MAX_SINGLE_SMS_LENGTH:
            campaign_draft.too_long_count += 1
        campaign_draft.longest_message_length = max(campaign_draft.longest_message_length, len(message_text))
        campaign_draft.messages.append(DraftMessage(list_name, contact.name, contact.phone_number, message_text))
    return campaign_draft
