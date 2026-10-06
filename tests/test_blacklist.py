import tempfile
import unittest
from pathlib import Path

from blacklist.blacklist_store import BlacklistStore
from compose.campaign_builder import build_campaign_draft
from outbox import outbox_store
from outbox.outbox_store import OutboxStore


class FakeContact:
    def __init__(self, phone_number):
        self.name, self.phone_number, self.custom_fields = "Awa", phone_number, {}


class FakeDraftMessage:
    def __init__(self, phone_number):
        self.list_name, self.recipient_name, self.phone_number, self.message_text = "L", "", phone_number, "Campagne"


class BlacklistTest(unittest.TestCase):
    def setUp(self):
        database_path = Path(tempfile.mkdtemp()) / "test.db"
        self.outbox = OutboxStore(database_path)
        self.blacklist = BlacklistStore(database_path)

    def test_any_spelling_is_blacklisted_and_invalid_numbers_are_refused(self):
        self.assertTrue(self.blacklist.add_number("77 123 45 67"))
        self.assertTrue(self.blacklist.is_blacklisted("+221771234567"))
        self.assertFalse(self.blacklist.add_number("Expresso"))
        self.blacklist.remove_number("221771234567")
        self.assertFalse(self.blacklist.is_blacklisted("+221771234567"))

    def test_draft_skips_and_counts_blacklisted_numbers(self):
        draft = build_campaign_draft("Salut", [("L", FakeContact("+221771111111")), ("L", FakeContact("+221772222222"))],
                                     blacklisted_numbers={"+221771111111"})
        self.assertEqual([message.phone_number for message in draft.messages], ["+221772222222"])
        self.assertEqual(draft.blacklisted_count, 1)

    def test_queue_never_stores_a_blacklisted_number(self):
        self.blacklist.add_number("+221771111111")
        self.outbox.queue_campaign("C", ["L"], [FakeDraftMessage("+221771111111"), FakeDraftMessage("+221772222222")])
        self.assertEqual([message.phone_number for message in self.outbox.list_messages()], ["+221772222222"])

    def test_blacklisting_after_queueing_removes_pending_campaign_messages_but_not_replies(self):
        self.outbox.queue_campaign("C", ["L"], [FakeDraftMessage("+221771111111"), FakeDraftMessage("+221772222222")])
        self.outbox.queue_reply("+221771111111", "", "Réponse")
        self.blacklist.add_number("+221771111111")
        self.assertEqual(self.outbox.remove_pending_messages_for_blacklisted_numbers(), 1)
        self.assertEqual(sorted(message.message_text for message in self.outbox.list_messages()), ["Campagne", "Réponse"])

    def test_claim_skips_a_message_blacklisted_while_waiting(self):
        self.outbox.queue_campaign("C", ["L"], [FakeDraftMessage("+221771111111"), FakeDraftMessage("+221772222222")])
        self.blacklist.add_number("+221771111111")
        self.assertEqual(self.outbox.claim_next_waiting_message().phone_number, "+221772222222")
        self.assertIsNone(self.outbox.claim_next_waiting_message())
        self.assertEqual(self.outbox.count_by_state()[outbox_store.WAITING], 0)


if __name__ == "__main__":
    unittest.main()
