"""Delivers one message through an online SMS gateway (Orange SMS). Same `send(number, text) -> SendResult` seam as the dongle.

Same safety rule as the dongle: SENT only when the gateway confirmed. A definite refusal is FAILED. Anything unclear
(timeout, server error after the request left) is UNKNOWN and never retried, so a message can never go out twice.
Problems that happen BEFORE the message could have been accepted (no network, refused credentials, quota or policy
limit) return DONGLE_UNAVAILABLE: the message goes back to WAITING and the run pauses with the reason shown."""
from common.sms_encoding import MAX_PARTS_PER_MESSAGE, plan_sms
from common.sms_text import normalize_senegal_number
from gateways.orange_sms_client import (
    OrangeConnectionFailed,
    OrangeHttpError,
    OrangeResponseTimedOut,
)
from outbox import outbox_store
from outbox.message_sender import DONGLE_UNAVAILABLE, SendResult

HTTP_STATUSES_THAT_PAUSE_THE_RUN = (401, 403, 429)  # credentials, quota or rate policy: not about this one message


class OrangeSmsMessageSender:
    def __init__(self, orange_client):
        self.orange_client = orange_client

    def send(self, phone_number, message_text):
        normalized_number = normalize_senegal_number(phone_number)
        if normalized_number is None:
            return SendResult(outbox_store.FAILED, f"Numéro invalide : {phone_number}")
        if plan_sms(message_text).is_too_long:
            return SendResult(outbox_store.FAILED, f"Message trop long : plus de {MAX_PARTS_PER_MESSAGE} SMS")
        try:
            was_accepted = self.orange_client.send_text_message(normalized_number, message_text)
        except OrangeConnectionFailed as connection_error:
            return SendResult(DONGLE_UNAVAILABLE, f"Orange SMS injoignable (pas de connexion ?) : {connection_error}")
        except OrangeResponseTimedOut:
            return SendResult(outbox_store.UNKNOWN, "Orange SMS n'a pas répondu à temps : vérifiez auprès du destinataire")
        except OrangeHttpError as http_error:
            return self._result_for_http_error(http_error)
        if was_accepted:
            return SendResult(outbox_store.SENT)
        return SendResult(outbox_store.UNKNOWN, "Réponse inattendue d'Orange SMS : vérifiez auprès du destinataire")

    @staticmethod
    def _result_for_http_error(http_error):
        status = http_error.http_status
        if status == 401:
            return SendResult(DONGLE_UNAVAILABLE, f"Identifiants Orange SMS refusés ({http_error.detail})")
        if status in HTTP_STATUSES_THAT_PAUSE_THE_RUN:
            return SendResult(DONGLE_UNAVAILABLE, f"Orange SMS refuse l'envoi (crédit ou limite atteints ?) : {http_error.detail}")
        if status >= 500:
            return SendResult(outbox_store.UNKNOWN, f"Erreur du serveur Orange SMS ({status}) : vérifiez auprès du destinataire")
        return SendResult(outbox_store.FAILED, f"Orange SMS a refusé le message ({status}) : {http_error.detail}")
