"""The Globe Transports token, kept in the macOS Keychain (same place and same `security` tool as the Orange credentials).
Only the token, its expiry and the server it belongs to are stored: never the password. Nothing is written to disk in clear text."""
import json
import subprocess

from globe_transports.globe_transports_client import GlobeToken, parse_expiry

KEYCHAIN_SERVICE_NAME = "SMS Gateway"
GLOBE_KEYCHAIN_ACCOUNT_NAME = "globe_transports_token"
KEYCHAIN_COMMAND_TIMEOUT_SECONDS = 10


class KeychainTokenStore:
    def load(self):
        """The saved token, or None when nothing was saved (or the Keychain cannot be read)."""
        try:
            completed_command = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE_NAME, "-a", GLOBE_KEYCHAIN_ACCOUNT_NAME, "-w"],
                capture_output=True, text=True, timeout=KEYCHAIN_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.SubprocessError:
            return None
        if completed_command.returncode != 0:
            return None
        try:
            saved_fields = json.loads(completed_command.stdout.strip())
            return GlobeToken(saved_fields["token"], parse_expiry(saved_fields["expires_at"]), saved_fields["base_url"])
        except (ValueError, TypeError, KeyError):
            return None

    def save(self, token):
        """Raises RuntimeError with a French message if the Keychain refuses."""
        saved_fields = {"token": token.token, "expires_at": token.expires_at.isoformat(), "base_url": token.base_url}
        try:
            completed_command = subprocess.run(
                ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE_NAME, "-a", GLOBE_KEYCHAIN_ACCOUNT_NAME,
                 "-w", json.dumps(saved_fields)],
                capture_output=True, text=True, timeout=KEYCHAIN_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.SubprocessError as keychain_error:
            raise RuntimeError("Trousseau d'accès injoignable : la connexion n'a pas pu être enregistrée.") from keychain_error
        if completed_command.returncode != 0:
            raise RuntimeError("Le trousseau d'accès a refusé l'enregistrement de la connexion.")

    def clear(self):
        try:
            subprocess.run(
                ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE_NAME, "-a", GLOBE_KEYCHAIN_ACCOUNT_NAME],
                capture_output=True, text=True, timeout=KEYCHAIN_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.SubprocessError:
            pass   # nothing to delete, or the Keychain is unreachable: the next login overwrites it anyway
