"""Turns a template + the recipients of one or more lists into the messages to queue. No UI, no database."""
from dataclasses import dataclass, field

from common.french_labels import format_custom_fields
from common.message_template import EXTRA_COLUMN_NAMES, find_used_variables, render_final_message_text
from common.sms_encoding import plan_sms, characters_forcing_unicode


@dataclass
class DraftMessage:
    list_name: str
    recipient_name: str
    phone_number: str
    message_text: str
    sms_count: int = 1                  # how many SMS (parts) it takes, each billed by the operator
    is_unicode: bool = False
    variable_values: dict = field(default_factory=dict)  # every variable of this recipient, kept so the text can be rebuilt later


@dataclass
class CampaignDraft:
    messages: list = field(default_factory=list)
    blacklisted_count: int = 0          # recipients skipped because their number is on the blacklist
    duplicate_count: int = 0            # same number found in several selected lists: kept once
    empty_value_counts: dict = field(default_factory=dict)  # variable -> contacts where its value is empty
    too_long_count: int = 0             # messages over the maximum number of SMS parts
    empty_message_count: int = 0
    longest_message_length: int = 0     # in characters
    longest_sms_count: int = 0
    total_sms_count: int = 0            # what the operator will bill: parts of every message added up
    unicode_message_count: int = 0      # messages sent as Unicode (70 characters per SMS instead of 160)
    multipart_message_count: int = 0
    characters_forcing_unicode: list = field(default_factory=list)  # the first few, to tell the owner what to replace


def contact_variable_values(contact):
    column_values = getattr(contact, "column_values", None) or {}
    extra_column_values = {column_name: column_values.get(column_name, "") for column_name in EXTRA_COLUMN_NAMES}
    return {
        "nom": contact.name,
        "telephone": contact.phone_number,
        "details": format_custom_fields(contact.custom_fields, separator=", "),
        **extra_column_values,
    }


def sample_extra_column_values(contacts_with_list_names, samples_per_column=3):
    """For the preview in the Composer: {"col1": ["Dakar", "Thiès", …]} with a few distinct values, only for the
    columns that hold data in at least one contact (in column order)."""
    samples_by_column = {}
    for _list_name, contact in contacts_with_list_names:
        column_values = getattr(contact, "column_values", None) or {}
        for column_name in EXTRA_COLUMN_NAMES:
            column_value = column_values.get(column_name, "")
            if not column_value:
                continue
            samples = samples_by_column.setdefault(column_name, [])
            if column_value not in samples and len(samples) < samples_per_column:
                samples.append(column_value)
    return {column_name: samples_by_column[column_name] for column_name in EXTRA_COLUMN_NAMES if column_name in samples_by_column}


MAX_REPORTED_CHARACTERS_FORCING_UNICODE = 8


def build_campaign_draft(template_text, contacts_with_list_names, simplify_to_7_bit=False, blacklisted_numbers=frozenset()):
    """contacts_with_list_names: [(list_name, StoredContact)]. Each phone number is kept only once.
    `blacklisted_numbers` ('+221…') are skipped and counted.
    `simplify_to_7_bit` replaces characters outside the GSM alphabet (ê → e, ç → c) so no message turns into Unicode."""
    used_variables = find_used_variables(template_text)
    campaign_draft = CampaignDraft()
    phone_numbers_already_kept = set()
    for list_name, contact in contacts_with_list_names:
        if contact.phone_number in phone_numbers_already_kept:
            campaign_draft.duplicate_count += 1
            continue
        if contact.phone_number in blacklisted_numbers:
            campaign_draft.blacklisted_count += 1
            continue
        phone_numbers_already_kept.add(contact.phone_number)

        variable_values = contact_variable_values(contact)
        for variable_name in used_variables:
            if not variable_values[variable_name].strip():
                campaign_draft.empty_value_counts[variable_name] = campaign_draft.empty_value_counts.get(variable_name, 0) + 1

        message_text = render_final_message_text(template_text, variable_values, simplify_to_7_bit)
        sms_plan = plan_sms(message_text)
        if not message_text:
            campaign_draft.empty_message_count += 1
        if sms_plan.is_too_long:
            campaign_draft.too_long_count += 1
        if sms_plan.is_unicode:
            campaign_draft.unicode_message_count += 1
            for character in characters_forcing_unicode(message_text):
                if (character not in campaign_draft.characters_forcing_unicode
                        and len(campaign_draft.characters_forcing_unicode) < MAX_REPORTED_CHARACTERS_FORCING_UNICODE):
                    campaign_draft.characters_forcing_unicode.append(character)
        if sms_plan.part_count > 1:
            campaign_draft.multipart_message_count += 1
        campaign_draft.longest_message_length = max(campaign_draft.longest_message_length, len(message_text))
        campaign_draft.longest_sms_count = max(campaign_draft.longest_sms_count, sms_plan.part_count)
        campaign_draft.total_sms_count += sms_plan.part_count if message_text else 0
        campaign_draft.messages.append(
            DraftMessage(list_name, contact.name, contact.phone_number, message_text, sms_plan.part_count, sms_plan.is_unicode, variable_values)
        )
    return campaign_draft
