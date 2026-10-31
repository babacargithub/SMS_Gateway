"""Orange SMS credentials, kept by the operating system's secret store (macOS Keychain or Windows Credential Manager,
see `common/secret_store.py`) as one item holding a small JSON blob. Nothing is written to disk in clear text."""
import json
from dataclasses import asdict, dataclass

from common.secret_store import read_secret, write_secret

ORANGE_KEYCHAIN_ACCOUNT_NAME = "orange_sms_credentials"


@dataclass
class OrangeSmsCredentials:
    client_id: str
    client_secret: str
    sender_number: str  # e.g. +221200600, the number the Orange SMS offer is attached to
    sender_name: str    # shown to the recipient instead of a number, e.g. GolobTrans

    def is_complete(self):
        return all(value.strip() for value in (self.client_id, self.client_secret, self.sender_number))


class OrangeCredentialsStore:
    def load(self):
        """The saved credentials, or None when nothing was saved (or the secret store cannot be read)."""
        saved_text = read_secret(ORANGE_KEYCHAIN_ACCOUNT_NAME)
        if not saved_text:
            return None
        try:
            return OrangeSmsCredentials(**json.loads(saved_text))
        except (ValueError, TypeError):
            return None

    def save(self, credentials):
        """Raises RuntimeError with a French message if the secret store refuses."""
        write_secret(ORANGE_KEYCHAIN_ACCOUNT_NAME, json.dumps(asdict(credentials)))
