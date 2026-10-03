"""Pure helpers for phone numbers and message text. No USB, no UI."""
import re
import unicodedata

MAX_SINGLE_SMS_LENGTH = 160
SENEGAL_NUMBER_PATTERN = re.compile(r"^\+221\d{9}$")


def normalize_senegal_number(raw_number_text):
    """Return '+221' + 9 digits, or None if the input can't be read as one."""
    compact_number = re.sub(r"[\s.\-()]", "", raw_number_text)
    if compact_number.startswith("00"):
        compact_number = "+" + compact_number[2:]
    if re.fullmatch(r"\d{9}", compact_number):
        compact_number = "+221" + compact_number
    elif re.fullmatch(r"221\d{9}", compact_number):
        compact_number = "+" + compact_number
    return compact_number if SENEGAL_NUMBER_PATTERN.fullmatch(compact_number) else None


def sanitize_for_text_mode(message_text):
    """Text mode is plain ASCII here: strip accents, replace anything else with '?'.
    Returns (clean_text, whether_anything_was_changed)."""
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", message_text)
        if not unicodedata.combining(character)
    )
    clean_text = "".join(
        character if (32 <= ord(character) < 127 or character == "\n") else "?"
        for character in without_accents
    )
    return clean_text, clean_text != message_text
