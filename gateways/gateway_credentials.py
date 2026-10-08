"""Orange SMS credentials, kept in the macOS Keychain (one generic-password item holding a small JSON blob).

The `security` command line tool is used so no extra dependency is needed. Nothing is written to disk in clear text."""
import json
import subprocess
from dataclasses import asdict, dataclass

KEYCHAIN_SERVICE_NAME = "SMS Gateway"
ORANGE_KEYCHAIN_ACCOUNT_NAME = "orange_sms_credentials"
KEYCHAIN_COMMAND_TIMEOUT_SECONDS = 10
KEYCHAIN_ITEM_NOT_FOUND_EXIT_CODE = 44


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
        """The saved credentials, or None when nothing was saved (or the Keychain cannot be read)."""
        try:
            completed_command = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE_NAME, "-a", ORANGE_KEYCHAIN_ACCOUNT_NAME, "-w"],
                capture_output=True, text=True, timeout=KEYCHAIN_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.SubprocessError:
            return None
        if completed_command.returncode != 0:
            return None
        try:
            saved_fields = json.loads(completed_command.stdout.strip())
            return OrangeSmsCredentials(**saved_fields)
        except (ValueError, TypeError):
            return None

    def save(self, credentials):
        """Raises RuntimeError with a French message if the Keychain refuses."""
        try:
            completed_command = subprocess.run(
                ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE_NAME, "-a", ORANGE_KEYCHAIN_ACCOUNT_NAME,
                 "-w", json.dumps(asdict(credentials))],
                capture_output=True, text=True, timeout=KEYCHAIN_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.SubprocessError as keychain_error:
            raise RuntimeError(f"Trousseau d'accès injoignable : {keychain_error}") from keychain_error
        if completed_command.returncode != 0:
            raise RuntimeError(f"Le trousseau d'accès a refusé l'enregistrement : {completed_command.stderr.strip()}")
