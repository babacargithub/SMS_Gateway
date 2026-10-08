"""The online SMS gateways the Outbox can send through (besides the SIM dongle). Today: Orange SMS only.

A gateway object offers: `key`, `display_name`, `is_configured()`, `create_message_sender()`, `read_balance()` (raises
a French-message RuntimeError on failure) and `reload_credentials()`. Adding Expresso SMS Pro or Twilio later means
adding one class here and one entry in `build_supported_gateways`."""
from gateways.gateway_credentials import OrangeCredentialsStore
from gateways.orange_sms_client import (
    OrangeConnectionFailed,
    OrangeHttpError,
    OrangeResponseTimedOut,
    OrangeSmsClient,
)
from gateways.gateway_message_sender import OrangeSmsMessageSender


class OrangeSmsGateway:
    key = "orange_sms"
    display_name = "Orange SMS"

    def __init__(self, credentials_store=None, client_factory=OrangeSmsClient):
        self.credentials_store = credentials_store or OrangeCredentialsStore()
        self.client_factory = client_factory
        self.credentials = self.credentials_store.load()
        self.client = None

    def reload_credentials(self):
        self.credentials = self.credentials_store.load()
        self.client = None  # a new client, so the cached token of the old credentials is dropped

    def is_configured(self):
        return self.credentials is not None and self.credentials.is_complete()

    def _client(self):
        if self.client is None:
            self.client = self.client_factory(self.credentials)
        return self.client

    def create_message_sender(self):
        return OrangeSmsMessageSender(self._client())

    def read_balance(self):
        if not self.is_configured():
            raise RuntimeError("Orange SMS n'est pas configuré")
        try:
            return self._client().read_balance()
        except OrangeConnectionFailed as connection_error:
            raise RuntimeError(f"Orange SMS injoignable (pas de connexion ?) : {connection_error}") from connection_error
        except OrangeResponseTimedOut as timeout_error:
            raise RuntimeError("Orange SMS n'a pas répondu à temps") from timeout_error
        except OrangeHttpError as http_error:
            if http_error.http_status in (400, 401):
                raise RuntimeError(f"Identifiants Orange SMS refusés : {http_error.detail}") from http_error
            raise RuntimeError(f"Orange SMS a répondu une erreur ({http_error.http_status}) : {http_error.detail}") from http_error


def build_supported_gateways():
    return [OrangeSmsGateway()]
