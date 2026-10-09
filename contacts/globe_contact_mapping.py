"""Pure: turns the bookings fetched from Globe Transports into contacts, with the same rules as a file import. No UI, no database.

Each booking becomes a contact (name + normalised phone). The booking's other values are kept as extra per-contact values
usable in message templates ({point_dep}, {destination}, {agent_number}, {depart_name}, {bus_name}, {seat_number}, {formatted_schedule}, {arret_bus}) and
as readable columns in the contact list. A customer with several bookings is kept once (first booking wins)."""
from dataclasses import dataclass, field

from common.french_labels import choose_singular_or_plural, format_number
from common.message_template import NAMED_VARIABLE_DESCRIPTIONS
from common.sms_text import normalize_senegal_number
from contacts.contact_import import ParsedContact

# variable name -> header shown in the contact list (the same French wording as the template buttons)
EXTRA_FIELD_HEADERS = NAMED_VARIABLE_DESCRIPTIONS
GLOBE_SOURCE_NAME = "Globe Transports"


@dataclass
class GlobeImportPlan:
    valid_contacts: list = field(default_factory=list)
    duplicate_count: int = 0        # same customer (same normalised number) on several bookings
    invalid_phone_count: int = 0    # no number, or one that is not a valid Senegalese number
    depart_names: list = field(default_factory=list)   # distinct, in order of appearance (to suggest a list name)


def value_to_text(raw_value):
    """The backend sends numbers as integers and missing values as null."""
    if raw_value is None or isinstance(raw_value, bool):
        return ""
    if isinstance(raw_value, float) and raw_value.is_integer():
        return str(int(raw_value))
    return str(raw_value).strip()


def build_globe_import_plan(bookings):
    import_plan = GlobeImportPlan()
    phone_numbers_already_seen = set()
    for booking in bookings:
        normalized_phone_number = normalize_senegal_number(value_to_text(booking.get("phone")))
        if normalized_phone_number is None:
            import_plan.invalid_phone_count += 1
            continue
        if normalized_phone_number in phone_numbers_already_seen:
            import_plan.duplicate_count += 1
            continue
        phone_numbers_already_seen.add(normalized_phone_number)
        extra_values = {variable_name: value_to_text(booking.get(variable_name)) for variable_name in EXTRA_FIELD_HEADERS}
        depart_name = extra_values["depart_name"]
        if depart_name and depart_name not in import_plan.depart_names:
            import_plan.depart_names.append(depart_name)
        import_plan.valid_contacts.append(
            ParsedContact(
                name=value_to_text(booking.get("name")),
                phone_number=normalized_phone_number,
                custom_fields={EXTRA_FIELD_HEADERS[name]: value for name, value in extra_values.items() if value},
                column_values={name: value for name, value in extra_values.items() if value},
            )
        )
    return import_plan


def suggest_list_name(import_plan):
    """The depart's name when every contact comes from the same depart, else nothing (the owner types one)."""
    return import_plan.depart_names[0] if len(import_plan.depart_names) == 1 else ""


def describe_import_result(inserted_count, duplicate_count, invalid_phone_count):
    """« 42 contacts importés, 5 doublons ignorés, 1 sans numéro valide » (parts equal to zero are left out, except the first)."""
    summary_parts = [f"{format_number(inserted_count)} {choose_singular_or_plural(inserted_count, 'contact importé', 'contacts importés')}"]
    if duplicate_count:
        summary_parts.append(f"{format_number(duplicate_count)} {choose_singular_or_plural(duplicate_count, 'doublon ignoré', 'doublons ignorés')}")
    if invalid_phone_count:
        summary_parts.append(f"{format_number(invalid_phone_count)} sans numéro valide")
    return ", ".join(summary_parts)
