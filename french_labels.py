"""French wording and formatting shared by several screens."""

NARROW_NO_BREAK_SPACE = " "  # French thousands separator: 8 813


def format_number(number):
    return f"{number:,}".replace(",", NARROW_NO_BREAK_SPACE)


def choose_singular_or_plural(count, singular_text, plural_text):
    return singular_text if count <= 1 else plural_text


def format_contact_count(contact_count):
    return f"{format_number(contact_count)} contact" if contact_count <= 1 else f"{format_number(contact_count)} contacts"


def format_message_count(message_count):
    return f"{format_number(message_count)} message" if message_count <= 1 else f"{format_number(message_count)} messages"


def format_french_date(iso_date_text):
    """'2026-09-30' -> '30/09/2026'."""
    year, month, day = iso_date_text.split("-")
    return f"{day}/{month}/{year}"


def format_french_datetime(iso_datetime_text):
    """'2026-09-30 14:05:59' -> '30/09/2026 14:05'."""
    date_part, time_part = iso_datetime_text.split(" ")
    return f"{format_french_date(date_part)} {time_part[:5]}"


def format_custom_fields(custom_fields, separator="  ·  "):
    """The 'Détails' of a contact: every extra column of the imported file, e.g. 'Ville : Dakar  ·  Montant : 5000'.
    Inside an SMS use a plain ASCII separator: text mode turns characters like '·' into '?'."""
    return separator.join(f"{field_name} : {field_value}" for field_name, field_value in custom_fields.items())


SIM_STATUS_LABELS = {
    "READY": "Prête",
    "SIM PIN": "Code PIN requis",
    "SIM PUK": "Code PUK requis",
    "NOT INSERTED": "Absente",
}


def translate_sim_status(raw_sim_status):
    return SIM_STATUS_LABELS.get(raw_sim_status, raw_sim_status)


# Message states are stored in English codes (WAITING...) and shown in French.
MESSAGE_STATE_LABELS = {
    "WAITING": "En attente",
    "SENDING": "En cours d'envoi",
    "SENT": "Envoyé",
    "FAILED": "Échec",
    "UNKNOWN": "Non confirmé",
}
