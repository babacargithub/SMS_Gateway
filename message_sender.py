"""What actually delivers one message. The queue worker only knows this interface: `send(number, text) -> SendResult`."""
from dataclasses import dataclass

import outbox_store
from dongle_monitor import (
    OUTCOME_FAILED,
    OUTCOME_NO_DONGLE,
    OUTCOME_SENT,
    OUTCOME_UNKNOWN,
    DongleMonitor,
)
from sms_text import normalize_senegal_number

DONGLE_UNAVAILABLE = "DONGLE_UNAVAILABLE"  # not a message state: the message was never handed to a dongle
SEND_REQUEST_START_TIMEOUT_SECONDS = 60     # the monitor may be busy scanning or initializing another dongle
SEND_REQUEST_RESULT_TIMEOUT_SECONDS = 60    # the device layer itself waits up to 30 s for the network


@dataclass
class SendResult:
    final_state: str      # outbox_store.SENT, FAILED or UNKNOWN, or DONGLE_UNAVAILABLE (message goes back to waiting)
    error_detail: str = ""


class DongleMessageSender:
    """Sends through the dongle owned by the monitor thread (this code runs in the sending worker's thread).

    SENT only when the dongle confirmed (+CMGS). FAILED only on a definite refusal. Anything unclear is UNKNOWN
    and is never retried automatically, so a message can never go out twice."""

    def __init__(self, dongle_monitor: DongleMonitor):
        self.dongle_monitor = dongle_monitor

    def send(self, phone_number, message_text):
        normalized_number = normalize_senegal_number(phone_number)
        if normalized_number is None:
            return SendResult(outbox_store.FAILED, f"Numéro invalide : {phone_number}")
        send_request = self.dongle_monitor.submit_text_message(normalized_number, message_text)
        if not send_request.finished.wait(SEND_REQUEST_START_TIMEOUT_SECONDS):
            if send_request.cancel_if_not_started():
                return SendResult(DONGLE_UNAVAILABLE, "Le gestionnaire de dongles ne répond pas")
            # the send began meanwhile: wait for its real outcome
            if not send_request.finished.wait(SEND_REQUEST_RESULT_TIMEOUT_SECONDS):
                return SendResult(outbox_store.UNKNOWN, "Aucune réponse du dongle : vérifiez auprès du destinataire")
        outcome_to_result = {
            OUTCOME_SENT: SendResult(outbox_store.SENT),
            OUTCOME_FAILED: SendResult(outbox_store.FAILED, send_request.detail),
            OUTCOME_UNKNOWN: SendResult(outbox_store.UNKNOWN, send_request.detail),
            OUTCOME_NO_DONGLE: SendResult(DONGLE_UNAVAILABLE, send_request.detail),
        }
        return outcome_to_result[send_request.outcome]
