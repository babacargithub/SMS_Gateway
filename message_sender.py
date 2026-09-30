"""What actually delivers one message. The queue worker only knows this interface, so the simulated sender
below can later be replaced by one that talks to the dongle without touching the worker or the screens."""
import random
import time
from dataclasses import dataclass

import outbox_store

SIMULATED_FAILURE_REASONS = (
    "Échec simulé : numéro refusé par le réseau",
    "Échec simulé : réseau indisponible",
    "Échec simulé : crédit insuffisant",
)


@dataclass
class SendResult:
    final_state: str      # outbox_store.SENT, FAILED or UNKNOWN
    error_detail: str = ""


class SimulatedMessageSender:
    """Pretends to send: waits a little, then succeeds or fails at random. Nothing leaves the computer."""

    is_simulation = True  # the Outbox shows a "simulation" banner for senders that set this

    def __init__(self, success_rate=0.8, minimum_seconds=0.15, maximum_seconds=0.5, random_source=None):
        self.success_rate = success_rate
        self.minimum_seconds = minimum_seconds
        self.maximum_seconds = maximum_seconds
        self.random_source = random_source or random.Random()

    def send(self, phone_number, message_text):
        time.sleep(self.random_source.uniform(self.minimum_seconds, self.maximum_seconds))
        if self.random_source.random() < self.success_rate:
            return SendResult(outbox_store.SENT)
        return SendResult(outbox_store.FAILED, self.random_source.choice(SIMULATED_FAILURE_REASONS))
