import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

import compose.compose_page as compose_page_module
from compose.compose_page import ComposePage
from compose.typed_recipient import TYPED_NUMBER_LIST_NAME, build_typed_recipient_rows

application = QApplication.instance() or QApplication([])


class FakeContactStore:
    def list_contact_lists(self):
        return [SimpleNamespace(list_id=1, name="Fidèles clients", contact_count=2),
                SimpleNamespace(list_id=2, name="Prospects", contact_count=1)]

    def list_contacts_for_lists(self, list_ids):
        contacts_by_list = {
            1: [("Fidèles clients", SimpleNamespace(name="Awa", phone_number="+221771111111", custom_fields={})),
                ("Fidèles clients", SimpleNamespace(name="Moussa", phone_number="+221772222222", custom_fields={}))],
            2: [("Prospects", SimpleNamespace(name="Fatou", phone_number="+221773333333", custom_fields={}))],
        }
        return [row for list_id in list_ids for row in contacts_by_list[list_id]]


class FakeOutboxStore:
    def __init__(self):
        self.queued_campaigns = []

    def queue_campaign(self, template_text, list_names, messages, simplify_to_7_bit=False):
        self.queued_campaigns.append((template_text, list_names, messages))


class TypedRecipientTest(unittest.TestCase):
    def test_nothing_typed_is_not_an_error(self):
        self.assertEqual(build_typed_recipient_rows("  ", "Awa"), ([], ""))

    def test_number_is_normalized_and_named(self):
        rows, error = build_typed_recipient_rows("77 123 45 67", " Awa ")
        self.assertEqual(error, "")
        self.assertEqual(rows[0][0], TYPED_NUMBER_LIST_NAME)
        self.assertEqual((rows[0][1].name, rows[0][1].phone_number), ("Awa", "+221771234567"))

    def test_invalid_number_gives_a_french_error(self):
        rows, error = build_typed_recipient_rows("12345")
        self.assertEqual(rows, [])
        self.assertIn("Numéro invalide", error)


class ComposePageTest(unittest.TestCase):
    def setUp(self):
        settings_path = str(Path(tempfile.mkdtemp()) / "settings.ini")
        self.original_draft_settings = ComposePage.draft_settings

        def isolated_draft_settings():
            isolated = QSettings(settings_path, QSettings.IniFormat)
            isolated.beginGroup("compose_draft")
            return isolated

        ComposePage.draft_settings = staticmethod(isolated_draft_settings)
        self.original_load, self.original_save = compose_page_module.load_simplify_to_7_bit, compose_page_module.save_simplify_to_7_bit
        compose_page_module.load_simplify_to_7_bit = lambda: False
        compose_page_module.save_simplify_to_7_bit = lambda is_enabled: None  # never touch the real app preferences
        self.outbox_store = FakeOutboxStore()

    def tearDown(self):
        ComposePage.draft_settings = self.original_draft_settings
        compose_page_module.load_simplify_to_7_bit, compose_page_module.save_simplify_to_7_bit = self.original_load, self.original_save

    def make_page(self):
        return ComposePage(FakeContactStore(), self.outbox_store)

    def test_lists_mode_still_works(self):
        page = self.make_page()
        page.lists_widget.item(0).setCheckState(Qt.Checked)
        page.message_input.setPlainText("Bonjour {nom}")
        page.refresh_composition()
        self.assertEqual(len(page.current_draft.messages), 2)
        self.assertTrue(page.launch_button.isEnabled())
        page.launch()
        self.assertEqual(self.outbox_store.queued_campaigns[0][1], ["Fidèles clients"])

    def test_single_number_mode_queues_one_message_to_the_typed_number(self):
        page = self.make_page()
        page.single_number_radio.setChecked(True)
        page.number_input.setText("77 123 45 67")
        page.name_input.setText("Awa")
        page.message_input.setPlainText("Bonjour {nom}")
        page.refresh_composition()
        self.assertTrue(page.launch_button.isEnabled())
        self.assertIn("+221771234567", page.recipients_label.text())
        page.launch()
        template, list_names, messages = self.outbox_store.queued_campaigns[0]
        self.assertEqual(list_names, [TYPED_NUMBER_LIST_NAME])
        self.assertEqual([(m.phone_number, m.message_text) for m in messages], [("+221771234567", "Bonjour Awa")])
        self.assertEqual(page.number_input.text(), "")  # cleared like the lists are, so a double click cannot queue twice

    def test_invalid_or_missing_number_blocks_launch(self):
        page = self.make_page()
        page.single_number_radio.setChecked(True)
        page.message_input.setPlainText("Bonjour")
        page.refresh_composition()
        self.assertFalse(page.launch_button.isEnabled())
        page.number_input.setText("123")
        page.refresh_composition()
        self.assertFalse(page.launch_button.isEnabled())
        self.assertIn("Numéro invalide", page.recipients_label.text())

    def test_switching_modes_keeps_each_choice(self):
        page = self.make_page()
        page.lists_widget.item(1).setCheckState(Qt.Checked)
        page.single_number_radio.setChecked(True)
        page.number_input.setText("771234567")
        page.lists_radio.setChecked(True)
        self.assertEqual(len(page.recipient_rows), 1)             # the checked list again
        self.assertEqual(page.number_input.text(), "771234567")   # and the typed number is still there for later

    def test_draft_is_restored_after_reopening(self):
        first_page = self.make_page()
        first_page.lists_widget.item(1).setCheckState(Qt.Checked)
        first_page.message_input.setPlainText("Brouillon à finir")
        first_page.single_number_radio.setChecked(True)
        first_page.number_input.setText("771234567")
        first_page.name_input.setText("Awa")
        first_page.save_draft()

        reopened_page = self.make_page()
        self.assertEqual(reopened_page.message_input.toPlainText(), "Brouillon à finir")
        self.assertTrue(reopened_page.single_number_radio.isChecked())
        self.assertEqual((reopened_page.number_input.text(), reopened_page.name_input.text()), ("771234567", "Awa"))
        reopened_page.lists_radio.setChecked(True)
        self.assertEqual(reopened_page.checked_list_ids(), [2])

    def test_launched_message_is_no_longer_a_draft(self):
        page = self.make_page()
        page.lists_widget.item(0).setCheckState(Qt.Checked)
        page.message_input.setPlainText("Envoyé")
        page.launch()
        self.assertEqual(self.make_page().message_input.toPlainText(), "")

    def test_deleted_list_is_simply_not_ticked_on_restore(self):
        page = self.make_page()
        page.lists_widget.item(1).setCheckState(Qt.Checked)
        page.save_draft()
        ComposePage.draft_settings().setValue("checked_list_ids", ["99", "2"])
        self.assertEqual(self.make_page().checked_list_ids(), [2])


if __name__ == "__main__":
    unittest.main()
