"""Where the Globe Transports backend lives, kept between launches."""
from PySide6.QtCore import QSettings

from globe_transports.globe_transports_client import normalize_base_url

BASE_URL_SETTING_KEY = "globe_transports/base_url"


def load_base_url():
    return normalize_base_url(QSettings("SMS Gateway", "SMS Gateway").value(BASE_URL_SETTING_KEY, "", type=str))


def save_base_url(typed_base_url):
    QSettings("SMS Gateway", "SMS Gateway").setValue(BASE_URL_SETTING_KEY, normalize_base_url(typed_base_url))
