import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from common.message_template import EXTRA_VARIABLE_NAMES
from compose.campaign_builder import build_campaign_draft
from outbox import outbox_store
from outbox.outbox_store import BatchEditRejected, OutboxStore


def contact(name, phone_number, custom_fields=None):
    return SimpleNamespace(name=name, phone_number=phone_number, custom_fields=custom_fields or {})


class BatchEditingTest(unittest.TestCase):
    def setUp(self):
        self.database_path = Path(tempfile.mkdtemp()) / "test.db"
        self.store = OutboxStore(self.database_path)
        recipients = [
            ("Clients", contact("Babacar", "+221773300853", {"Ville": "Dakar"})),
            ("Clients", contact("Awa", "+221771111111", {"Ville": "Thiès"})),
            ("Clients", contact("", "+221772222222")),
        ]
        self.template = "Bonjour {nom}, tu es un client fantastique ({détails})"
        draft = build_campaign_draft(self.template, recipients)
        self.campaign_id = self.store.queue_campaign(self.template, ["Clients"], draft.messages)

    def message_texts(self):
        return {message.phone_number: message.message_text for message in self.store.list_messages()}

    def finish(self, phone_number, final_state):
        message = next(m for m in self.store.list_messages() if m.phone_number == phone_number)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("UPDATE messages SET state = ? WHERE id = ?", (final_state, message.message_id))

    def test_each_message_keeps_all_its_variable_values(self):
        with sqlite3.connect(self.database_path) as connection:
            stored = json.loads(connection.execute(
                "SELECT variable_values FROM messages WHERE phone_number = '+221773300853'").fetchone()[0])
        extra_columns_left_empty = {name: "" for name in EXTRA_VARIABLE_NAMES}
        self.assertEqual(stored, {"nom": "Babacar", "telephone": "+221773300853", "details": "Ville : Dakar", **extra_columns_left_empty})

    def test_batch_keeps_its_template_and_used_variables(self):
        batch = self.store.get_batch(self.campaign_id)
        self.assertEqual(batch.message_template, self.template)
        self.assertEqual(batch.variables_used, ("details", "nom"))
        self.assertEqual(batch.state_counts, {outbox_store.WAITING: 3})
        self.assertEqual(batch.editable_count, 3)

    def test_title_can_be_added_in_front_of_the_name(self):
        self.store.update_batch_template(self.campaign_id, "Bonjour M. {nom}", False)
        self.assertEqual(self.message_texts()["+221773300853"], "Bonjour M. Babacar")
        self.assertEqual(self.store.get_batch(self.campaign_id).variables_used, ("nom",))

    def test_name_can_be_dropped_entirely(self):
        self.store.update_batch_template(self.campaign_id, "Bonjour.", False)
        self.assertEqual(set(self.message_texts().values()), {"Bonjour."})
        self.assertEqual(self.store.get_batch(self.campaign_id).variables_used, ())

    def test_a_variable_that_was_not_used_at_launch_can_be_added_later(self):
        self.store.update_batch_template(self.campaign_id, "Appelez le {téléphone}", False)
        self.assertEqual(self.message_texts()["+221771111111"], "Appelez le +221771111111")

    def test_only_unsent_messages_change(self):
        self.finish("+221773300853", outbox_store.SENT)
        self.finish("+221771111111", outbox_store.UNKNOWN)
        result = self.store.update_batch_template(self.campaign_id, "Typo corrigée {nom}", False)
        texts = self.message_texts()
        self.assertEqual(result.updated_count, 1)
        self.assertTrue(texts["+221773300853"].startswith("Bonjour Babacar"))   # already sent: history is never rewritten
        self.assertTrue(texts["+221771111111"].startswith("Bonjour Awa"))       # unknown outcome: left alone
        self.assertEqual(texts["+221772222222"], "Typo corrigée")               # waiting: updated (empty name leaves no hole)

    def test_failed_messages_are_corrected_too_because_the_next_run_resends_them(self):
        self.finish("+221771111111", outbox_store.FAILED)
        self.store.update_batch_template(self.campaign_id, "Salut {nom}", False)
        self.assertEqual(self.message_texts()["+221771111111"], "Salut Awa")

    def test_rejected_edits_change_nothing(self):
        before = self.message_texts()
        for bad_template, expected_word in (("", "vide"), ("Bonjour {prenom}", "inconnue"), ("{nom}", "vides"), ("x" * 3000, "dépasseraient")):
            with self.assertRaises(BatchEditRejected) as raised:
                self.store.update_batch_template(self.campaign_id, bad_template, False)
            self.assertIn(expected_word, str(raised.exception))
        self.assertEqual(self.message_texts(), before)
        self.assertEqual(self.store.get_batch(self.campaign_id).message_template, self.template)

    def test_nothing_to_edit_once_everything_is_sent(self):
        for phone in ("+221773300853", "+221771111111", "+221772222222"):
            self.finish(phone, outbox_store.SENT)
        with self.assertRaises(BatchEditRejected):
            self.store.update_batch_template(self.campaign_id, "Nouveau", False)

    def test_simplify_option_is_applied_when_rebuilding(self):
        self.store.update_batch_template(self.campaign_id, "Français pour {nom}", True)
        self.assertEqual(self.message_texts()["+221773300853"], "Francais pour Babacar")
        self.assertTrue(self.store.get_batch(self.campaign_id).simplify_to_7_bit)

    def test_messages_queued_before_variables_were_kept_are_left_alone(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("UPDATE messages SET variable_values = '{}' WHERE phone_number = '+221771111111'")
        result = self.store.update_batch_template(self.campaign_id, "Nouveau {nom}", False)
        self.assertEqual(result.legacy_count, 1)
        self.assertTrue(self.message_texts()["+221771111111"].startswith("Bonjour Awa"))
        self.assertEqual(self.message_texts()["+221773300853"], "Nouveau Babacar")

    def test_deleting_a_batch_removes_only_what_was_not_sent(self):
        self.finish("+221773300853", outbox_store.SENT)
        self.finish("+221771111111", outbox_store.UNKNOWN)
        self.assertEqual(self.store.delete_batch_pending_messages(self.campaign_id), 1)
        remaining = self.message_texts()
        self.assertEqual(set(remaining), {"+221773300853", "+221771111111"})

    def test_replies_are_not_batches(self):
        self.store.queue_reply("+221770000000", "", "Merci")
        self.assertEqual([batch.campaign_id for batch in self.store.list_batches()], [self.campaign_id])

    def test_sent_and_archived_batch_leaves_the_list_but_keeps_its_history(self):
        for phone in ("+221773300853", "+221771111111", "+221772222222"):
            self.finish(phone, outbox_store.SENT)
        self.store.archive_sent_messages()
        self.assertEqual(self.store.list_batches(), [])
        self.assertEqual(len(self.store.list_sent_messages()), 3)

    def test_old_database_is_upgraded(self):
        old_path = Path(tempfile.mkdtemp()) / "old.db"
        with sqlite3.connect(old_path) as connection:
            connection.executescript("""
                CREATE TABLE campaigns (id INTEGER PRIMARY KEY AUTOINCREMENT, message_template TEXT NOT NULL, list_names TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                    list_name TEXT NOT NULL DEFAULT '', recipient_name TEXT NOT NULL DEFAULT '', phone_number TEXT NOT NULL,
                    message_text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'WAITING', error_detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO campaigns (message_template, list_names) VALUES ('Bonjour {nom}', 'Anciens');
                INSERT INTO campaigns (message_template, list_names) VALUES ('Merci', 'Réponse');
                INSERT INTO messages (campaign_id, phone_number, message_text) VALUES (1, '+221771111111', 'Bonjour Awa');
                INSERT INTO messages (campaign_id, phone_number, message_text) VALUES (2, '+221772222222', 'Merci');
            """)
        upgraded_store = OutboxStore(old_path)
        batches = upgraded_store.list_batches()
        self.assertEqual([(b.campaign_id, b.variables_used) for b in batches], [(1, ("nom",))])
        result = upgraded_store.update_batch_template(1, "Salut {nom}", False)
        self.assertEqual((result.updated_count, result.legacy_count), (0, 1))  # the old message cannot be rebuilt


if __name__ == "__main__":
    unittest.main()
