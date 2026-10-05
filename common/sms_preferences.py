"""Owner preference shared by the Composer and the reply window: replace the few letters outside the 7-bit SMS alphabet.
Off by default: the text is sent exactly as typed (SMS Caster never changed it either)."""
from PySide6.QtCore import QSettings

SIMPLIFY_TO_7_BIT_SETTING_KEY = "simplify_to_7_bit"
SIMPLIFY_CHECKBOX_LABEL = "Rester en 7 bits : remplacer ç ê â î ô û « » ’ À È... (é è à ù ö ñ ü sont conservés)"
SIMPLIFY_CHECKBOX_TOOLTIP = (
    "Un seul caractère hors de l'alphabet SMS fait passer tout le message en Unicode : 70 caractères par SMS au lieu de 160, "
    "donc plus de SMS à payer. Coché, ces caractères sont remplacés par leur équivalent simple (ê → e, ç → c, « » → \")."
)


def load_simplify_to_7_bit():
    return QSettings("SMS Gateway", "SMS Gateway").value(SIMPLIFY_TO_7_BIT_SETTING_KEY, False, type=bool)


def save_simplify_to_7_bit(is_enabled):
    QSettings("SMS Gateway", "SMS Gateway").setValue(SIMPLIFY_TO_7_BIT_SETTING_KEY, is_enabled)
