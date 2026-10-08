import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from compose.campaign_builder import build_campaign_draft, sample_extra_column_values
from common.message_template import find_unknown_variables
from contacts.contact_import import build_import_plan, guess_extra_column_indexes
from contacts.contact_store import ContactStore
from outbox.batch_editing import EditableMessage, plan_batch_edit

HEADERS = ["Nom", "Téléphone", "Ville", "Genre", "Métier"]
ROWS = [["Awa", "77 111 11 11", "Dakar", "F", "Prof"], ["Moussa", "77 222 22 22", "Thiès", "", "Chauffeur"]]


class ExtraColumnVariablesTest(unittest.TestCase):
    def test_default_mapping_takes_remaining_columns_in_order(self):
        self.assertEqual(guess_extra_column_indexes(HEADERS, 1, 0), [2, 3, 4] + [None] * 7)

    def test_import_keeps_chosen_columns_as_col_values_and_every_column_as_details(self):
        plan = build_import_plan(HEADERS, ROWS, 1, 0, [4, None, 2])
        awa, moussa = plan.valid_contacts
        self.assertEqual(awa.column_values, {"col1": "Prof", "col3": "Dakar"})
        self.assertEqual(awa.custom_fields, {"Ville": "Dakar", "Genre": "F", "Métier": "Prof"})
        self.assertEqual(moussa.column_values, {"col1": "Chauffeur", "col3": "Thiès"})

    def test_ten_columns_are_stored_and_loaded(self):
        headers = ["Tel"] + [f"C{n}" for n in range(1, 13)]
        row = ["771111111"] + [f"v{n}" for n in range(1, 13)]
        plan = build_import_plan(headers, [row], 0, None, guess_extra_column_indexes(headers, 0, None))
        with tempfile.TemporaryDirectory() as folder:
            store = ContactStore(Path(folder) / "db.sqlite")
            list_id, _, _ = store.import_into_new_list("Test", plan.valid_contacts, "f.csv")
            stored = store.list_contacts(list_id)[0]
            self.assertEqual(stored.column_values, {f"col{n}": f"v{n}" for n in range(1, 11)})
            self.assertEqual(len(stored.custom_fields), 12)
            self.assertEqual(store.list_contacts_for_lists([list_id])[0][1].column_values, stored.column_values)

    def test_template_fills_col_variables_and_missing_values_become_empty(self):
        awa = SimpleNamespace(name="Awa", phone_number="+221771111111", custom_fields={}, column_values={"col1": "Dakar"})
        moussa = SimpleNamespace(name="Moussa", phone_number="+221772222222", custom_fields={}, column_values={})
        draft = build_campaign_draft("Bonjour {nom} de {col1}", [("L", awa), ("L", moussa)])
        self.assertEqual([m.message_text for m in draft.messages], ["Bonjour Awa de Dakar", "Bonjour Moussa de"])
        self.assertEqual(draft.empty_value_counts, {"col1": 1})
        self.assertEqual(find_unknown_variables("{col10} {col11}"), ["col11"])

    def test_samples_only_for_columns_with_data(self):
        rows = [("L", SimpleNamespace(column_values={"col2": "a"})), ("L", SimpleNamespace(column_values={"col2": "b", "col1": "x"}))]
        self.assertEqual(sample_extra_column_values(rows), {"col1": ["x"], "col2": ["a", "b"]})

    def test_old_batch_messages_without_col_values_are_not_rebuilt_with_a_raw_placeholder(self):
        old_message = EditableMessage(1, {"nom": "Awa", "telephone": "+221771111111", "details": ""})
        new_message = EditableMessage(2, {"nom": "Moussa", "telephone": "+221772222222", "details": "", "col1": "Dakar"})
        plan = plan_batch_edit("Salut {nom} {col1}", [old_message, new_message])
        self.assertEqual(plan.legacy_count, 1)
        self.assertEqual(plan.new_text_by_message_id, {2: "Salut Moussa Dakar"})
        self.assertEqual(plan_batch_edit("Salut {nom}", [old_message]).rebuilt_count, 1)


if __name__ == "__main__":
    unittest.main()
