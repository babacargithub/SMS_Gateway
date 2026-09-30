"""French wording shared by several screens."""


NARROW_NO_BREAK_SPACE = "\u202f"  # French thousands separator: 8 813


def format_contact_count(contact_count):
    grouped_digits = f"{contact_count:,}".replace(",", NARROW_NO_BREAK_SPACE)
    return f"{grouped_digits} contact" if contact_count <= 1 else f"{grouped_digits} contacts"


def format_french_date(iso_date_text):
    """'2026-09-30' -> '30/09/2026'."""
    year, month, day = iso_date_text.split("-")
    return f"{day}/{month}/{year}"


SIM_STATUS_LABELS = {
    "READY": "Prête",
    "SIM PIN": "Code PIN requis",
    "SIM PUK": "Code PUK requis",
    "NOT INSERTED": "Absente",
}


def translate_sim_status(raw_sim_status):
    return SIM_STATUS_LABELS.get(raw_sim_status, raw_sim_status)
