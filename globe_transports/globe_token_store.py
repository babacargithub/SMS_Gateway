"""The Globe Transports token, kept by the operating system's secret store (macOS Keychain or Windows Credential Manager,
same place as the Orange credentials, see `common/secret_store.py`).
Only the token, its expiry and the server it belongs to are stored: never the password. Nothing is written to disk in clear text."""
import json

from common.secret_store import SECRET_STORE_NAME_FOR_USER, delete_secret, read_secret, write_secret
from globe_transports.globe_transports_client import GlobeToken, parse_expiry

GLOBE_KEYCHAIN_ACCOUNT_NAME = "globe_transports_token"


class KeychainTokenStore:
    def load(self):
        """The saved token, or None when nothing was saved (or the secret store cannot be read)."""
        saved_text = read_secret(GLOBE_KEYCHAIN_ACCOUNT_NAME)
        if not saved_text:
            return None
        try:
            saved_fields = json.loads(saved_text)
            return GlobeToken(saved_fields["token"], parse_expiry(saved_fields["expires_at"]), saved_fields["base_url"])
        except (ValueError, TypeError, KeyError):
            return None

    def save(self, token):
        """Raises RuntimeError with a French message (never containing the token) if the secret store refuses."""
        saved_fields = {"token": token.token, "expires_at": token.expires_at.isoformat(), "base_url": token.base_url}
        try:
            write_secret(GLOBE_KEYCHAIN_ACCOUNT_NAME, json.dumps(saved_fields))
        except RuntimeError as store_error:
            raise RuntimeError(f"{SECRET_STORE_NAME_FOR_USER} : la connexion n'a pas pu être enregistrée.") from store_error

    def clear(self):
        delete_secret(GLOBE_KEYCHAIN_ACCOUNT_NAME)   # nothing to delete, or unreachable: the next login overwrites it anyway
