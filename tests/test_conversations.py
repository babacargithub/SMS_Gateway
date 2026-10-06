import sqlite3
import tempfile
import unittest
from pathlib import Path

from conversations.conversation_store import RECEIVED_DIRECTION, SENT_DIRECTION, ConversationStore
from inbox.inbox_store import InboxStore
from inbox.sms_pdu import SmsPart
from outbox import outbox_store
from outbox.outbox_store import OutboxStore


class FakeDraftMessage:
    def __init__(self, phone_number, recipient_name=""):
        self.list_name, self.recipient_name, self.phone_number, self.message_text = "L", recipient_name, phone_number, "Campagne"


class FakeReceivedMessage:
    def __init__(self, sender_number, message_text, received_at):
        self.sender_number, self.message_text, self.received_at = sender_number, message_text, received_at


class ConversationsTest(unittest.TestCase):
    def setUp(self):
        self.database_path = Path(tempfile.mkdtemp()) / "test.db"
        self.outbox = OutboxStore(self.database_path)
        self.inbox = InboxStore(self.database_path)
        self.conversations = ConversationStore(self.database_path)

    def test_every_spelling_of_a_number_lands_in_one_conversation(self):
        self.outbox.queue_campaign("Campagne", ["L"], [FakeDraftMessage("+221771234567", "Awa")])
        self.inbox.store_received_messages([FakeReceivedMessage("221771234567", "Merci", "2000-01-01 10:00:00")])
        self.outbox.queue_reply("+221771234567", "Awa", "De rien")
        conversation = self.conversations.load_conversation("77 123 45 67")
        self.assertEqual(  # the received message is dated long ago, the two sent ones are dated now
            [(entry.direction, entry.message_text) for entry in conversation.entries],
            [(RECEIVED_DIRECTION, "Merci"), (SENT_DIRECTION, "Campagne"), (SENT_DIRECTION, "De rien")],
        )
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 1)

    def test_different_numbers_stay_apart_and_alphanumeric_senders_have_their_own_conversation(self):
        self.outbox.queue_campaign("C", ["L"], [FakeDraftMessage("+221771111111"), FakeDraftMessage("+221772222222")])
        self.inbox.store_received_messages([FakeReceivedMessage("Expresso", "Promo", "2026-10-05 10:00:00")])
        self.assertEqual(len(self.conversations.load_conversation("+221771111111").entries), 1)
        self.assertEqual(self.conversations.load_conversation("Expresso").entries[0].message_text, "Promo")

    def test_one_time_grouping_of_messages_stored_before_conversations(self):
        legacy_path = Path(tempfile.mkdtemp()) / "legacy.db"
        legacy_outbox = OutboxStore(legacy_path)
        legacy_inbox = InboxStore(legacy_path)
        legacy_outbox.queue_campaign("C", ["L"], [FakeDraftMessage("+221771234567", "Awa"), FakeDraftMessage("+221772222222")])
        legacy_inbox.store_received_messages([FakeReceivedMessage("+221771234567", "Salut", "2026-10-05 10:00:00")])
        with sqlite3.connect(legacy_path) as connection:  # make it look like a database from before the feature
            connection.execute("UPDATE messages SET conversation_id = NULL")
            connection.execute("UPDATE received_messages SET conversation_id = NULL")
            connection.execute("DELETE FROM conversations")
        legacy_conversations = ConversationStore(legacy_path)
        self.assertTrue((legacy_path.parent / "legacy.db.before-conversations.bak").exists())
        self.assertEqual(len(legacy_conversations.load_conversation("+221771234567").entries), 2)
        self.assertEqual(legacy_conversations.load_conversation("+221771234567").contact_name, "Awa")
        self.assertEqual(legacy_conversations.group_existing_messages_into_conversations(), 0)  # nothing left: idempotent

    def test_sent_message_is_dated_when_delivered_and_opening_marks_received_as_read(self):
        message_id = self.outbox.queue_reply("+221771234567", "", "Bonjour")
        self.outbox.finish_message(message_id, outbox_store.SENT)
        self.inbox.store_received_messages([FakeReceivedMessage("+221771234567", "Salut", "2026-10-05 10:00:00")])
        self.assertEqual(self.inbox.count_unread(), 1)
        self.conversations.mark_received_messages_as_read("+221771234567")
        self.assertEqual(self.inbox.count_unread(), 0)
        sent_entry = next(entry for entry in self.conversations.load_conversation("+221771234567").entries if entry.direction == SENT_DIRECTION)
        self.assertEqual(sent_entry.outbox_state, outbox_store.SENT)


if __name__ == "__main__":
    unittest.main()
