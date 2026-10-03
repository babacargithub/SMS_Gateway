import tempfile
import unittest
from pathlib import Path

from outbox import outbox_store
from outbox.outbox_store import OutboxStore


class FakeDraftMessage:
    def __init__(self, phone_number):
        self.list_name, self.recipient_name, self.phone_number, self.message_text = "L", "", phone_number, "Bonjour"


class OutboxRunFlowTest(unittest.TestCase):
    def setUp(self):
        self.store = OutboxStore(Path(tempfile.mkdtemp()) / "test.db")
        self.store.queue_campaign("Bonjour", ["L"], [FakeDraftMessage(f"+22177000000{digit}") for digit in range(4)])

    def finish_next(self, final_state):
        message = self.store.claim_next_waiting_message()
        self.store.finish_message(message.message_id, final_state, "")
        return message

    def test_sent_messages_stay_in_outbox_until_archived(self):
        self.finish_next(outbox_store.SENT)
        self.assertEqual(self.store.count_by_state()[outbox_store.SENT], 1)
        self.assertEqual(self.store.list_sent_messages(), [])
        self.store.archive_sent_messages()
        self.assertEqual(len(self.store.list_sent_messages()), 1)
        self.assertEqual(self.store.count_by_state()[outbox_store.SENT], 0)

    def test_failed_come_back_first_unknown_never(self):
        failed_message = self.finish_next(outbox_store.FAILED)
        unknown_message = self.finish_next(outbox_store.UNKNOWN)
        self.assertEqual(self.store.requeue_failed_messages(), 1)
        self.assertEqual(self.store.claim_next_waiting_message().message_id, failed_message.message_id)
        self.assertEqual(self.store.count_by_state()[outbox_store.UNKNOWN], 1)
        self.assertNotEqual(unknown_message.message_id, failed_message.message_id)


if __name__ == "__main__":
    unittest.main()
