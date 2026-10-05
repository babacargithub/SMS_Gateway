"""Pure: a phone number typed by hand in the Composer, turned into the same kind of recipient row as a contact list gives."""
from dataclasses import dataclass, field

from common.sms_text import normalize_senegal_number

TYPED_NUMBER_LIST_NAME = "Numéro saisi"   # shown in the « Liste » column of the Boîte d'envoi


@dataclass
class TypedRecipient:
    """Looks like a StoredContact for the campaign builder: name, phone_number and extra fields (none here)."""
    name: str
    phone_number: str
    custom_fields: dict = field(default_factory=dict)


def build_typed_recipient_rows(raw_number_text, name_text=""):
    """([(list name, recipient)], error message). Nothing typed is not an error: it just means no recipient yet."""
    if not raw_number_text.strip():
        return [], ""
    normalized_number = normalize_senegal_number(raw_number_text.strip())
    if normalized_number is None:
        return [], f"Numéro invalide : « {raw_number_text.strip()} ». Format attendu : 77 123 45 67 ou +221 77 123 45 67."
    return [(TYPED_NUMBER_LIST_NAME, TypedRecipient(name_text.strip(), normalized_number))], ""
