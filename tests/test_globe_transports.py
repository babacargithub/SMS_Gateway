import json
import os
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from common.message_template import describe_variable
from compose.campaign_builder import build_campaign_draft
from contacts.contact_store import ContactStore
from contacts.globe_contact_mapping import build_globe_import_plan, describe_import_result, suggest_list_name
from globe_transports.globe_selection_dialog import GlobeSelectionDialog
from globe_transports.globe_transports_client import (
    DepartForSms,
    BusForSms,
    GlobeAuthenticationRequired,
    GlobeConnectionFailed,
    GlobeRequestTimedOut,
    GlobeTransportsClient,
    GlobeToken,
    GlobeTransportsError,
    InMemoryTokenStore,
    normalize_base_url,
)

application = QApplication.instance() or QApplication([])


class FakeTransport:
    """Answers from a script of (status, body) / exceptions and remembers every URL asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requested_urls = []
        self.requests = []

    def __call__(self, http_method, url, headers, body_bytes, timeout_seconds):
        self.requested_urls.append(url)
        self.requests.append({"method": http_method, "url": url, "headers": headers, "body": body_bytes})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def query_of(self, call_index):
        return urllib.parse.parse_qsl(urllib.parse.urlparse(self.requested_urls[call_index]).query)


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
DEFAULT_URL = "http://127.0.0.1:8000"
TOKEN_ANSWER = (200, json.dumps({"token": "secret-token-1", "expires_at": "2027-01-07T12:00:00+00:00"}))


def stored_token(token="stored-token", days_left=60, base_url=DEFAULT_URL):
    return GlobeToken(token, NOW + timedelta(days=days_left), base_url)


def client_with_token(transport, base_url=DEFAULT_URL, token_store=None, login_requester=None):
    """A client whose store already holds a long-lived token (so data calls need no login)."""
    token_store = token_store if token_store is not None else InMemoryTokenStore(stored_token(base_url=normalize_base_url(base_url)))
    return GlobeTransportsClient(base_url, transport, token_store=token_store, login_requester=login_requester, clock=lambda: NOW)


def booking(booking_id, phone, name="Awa Fatou NDIAYE", **overrides):
    values = {"booking_id": booking_id, "name": name, "phone": phone, "point_dep": "Dakar", "destination": "Touba",
              "agent_number": "771112233/782223344", "depart_name": "Dakar - Touba 08h", "bus_name": "Bus climatisé", "seat_number": 12}
    values.update(overrides)
    return values


class ClientTest(unittest.TestCase):
    def test_departs_are_parsed_and_the_request_is_built(self):
        transport = FakeTransport((200, json.dumps([{"id": 6741, "depart_name": "D1", "buses": [{"id": 5985, "name": "Bus climatisé"}]}])))
        departs = client_with_token(transport, "localhost:8000/").fetch_departs("past", limit=20)
        self.assertEqual(departs, [DepartForSms(6741, "D1", [BusForSms(5985, "Bus climatisé")])])
        self.assertTrue(transport.requested_urls[0].startswith("http://localhost:8000/api/departs_for_sms?"))
        self.assertEqual(transport.query_of(0), [("period", "past"), ("limit", "20")])

    def test_no_limit_is_sent_unless_asked(self):
        transport = FakeTransport((200, "[]"))
        self.assertEqual(client_with_token(transport).fetch_departs("upcoming"), [])
        self.assertEqual(transport.query_of(0), [("period", "upcoming")])

    def test_contacts_use_repeated_entity_ids_key(self):
        transport = FakeTransport((200, "[]"))
        client_with_token(transport).fetch_bookings("bus", [1, 2], "paid")
        self.assertEqual(transport.query_of(0), [("entity", "bus"), ("entity_ids[]", "1"), ("entity_ids[]", "2"), ("filter", "paid")])

    def test_departs_and_buses_make_two_calls_merged_without_the_same_booking_twice(self):
        transport = FakeTransport((200, json.dumps([booking(1, 770000001), booking(2, 770000002)])),
                                  (200, json.dumps([booking(2, 770000002), booking(3, 770000003)])))
        merged = client_with_token(transport).fetch_bookings_for_selection([10], [20], "all")
        self.assertEqual([item["booking_id"] for item in merged], [1, 2, 3])
        self.assertEqual(dict(transport.query_of(0))["entity"], "depart")
        self.assertEqual(dict(transport.query_of(1))["entity"], "bus")

    def test_only_one_call_when_only_buses_are_chosen(self):
        transport = FakeTransport((200, "[]"))
        self.assertEqual(client_with_token(transport).fetch_bookings_for_selection([], [5]), [])
        self.assertEqual(len(transport.requested_urls), 1)

    def test_validation_error_422_shows_the_backend_message(self):
        transport = FakeTransport((422, json.dumps({"message": "Le champ period est invalide."})))
        with self.assertRaises(GlobeTransportsError) as caught:
            client_with_token(transport).fetch_departs("nope")
        self.assertIn("422", str(caught.exception))
        self.assertIn("Le champ period est invalide.", str(caught.exception))

    def test_server_error_without_body(self):
        with self.assertRaises(GlobeTransportsError) as caught:
            client_with_token(FakeTransport((500, "<html>oops</html>"))).fetch_departs("past")
        self.assertIn("500", str(caught.exception))

    def test_timeout_and_unreachable_server_have_french_messages(self):
        with self.assertRaises(GlobeTransportsError) as caught:
            client_with_token(FakeTransport(GlobeRequestTimedOut("slow"))).fetch_departs("past")
        self.assertIn("ne répond pas", str(caught.exception))
        with self.assertRaises(GlobeTransportsError) as caught:
            client_with_token(FakeTransport(GlobeConnectionFailed("refused"))).fetch_departs("past")
        self.assertIn("Impossible de joindre", str(caught.exception))
        self.assertIn("127.0.0.1:8000", str(caught.exception))

    def test_malformed_json_and_wrong_shapes(self):
        for body in ("not json", '{"a": 1}', '[{"id": "x"}]', '[{"depart_name": "no id"}]'):
            with self.assertRaises(GlobeTransportsError, msg=body):
                client_with_token(FakeTransport((200, body))).fetch_departs("past")
        with self.assertRaises(GlobeTransportsError):
            client_with_token(FakeTransport((200, '["x"]'))).fetch_bookings("bus", [1])

    def test_base_url_normalisation(self):
        self.assertEqual(normalize_base_url(""), "http://127.0.0.1:8000")
        self.assertEqual(normalize_base_url(" example.com/api/ "), "http://example.com")
        self.assertEqual(normalize_base_url("https://globe.sn"), "https://globe.sn")


class LoginTest(unittest.TestCase):
    def login_client(self, *answers, store=None):
        transport = FakeTransport(*answers)
        return client_with_token(transport, token_store=store if store is not None else InMemoryTokenStore()), transport

    def test_login_success_stores_the_token_and_sends_credentials_as_json(self):
        store = InMemoryTokenStore()
        client, transport = self.login_client(TOKEN_ANSWER, store=store)
        client.login("  agent ", "p@ss word")
        request = transport.requests[0]
        self.assertEqual((request["method"], request["url"]), ("POST", f"{DEFAULT_URL}/api/sms_gateway/token"))
        self.assertEqual(json.loads(request["body"]), {"username": "agent", "password": "p@ss word"})
        self.assertEqual(request["headers"]["Accept"], "application/json")
        self.assertNotIn("Authorization", request["headers"])
        self.assertEqual(store.load().token, "secret-token-1")
        self.assertEqual(store.load().expires_at, datetime(2027, 1, 7, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(store.load().base_url, DEFAULT_URL)
        self.assertNotIn("p@ss word", repr(store.load()))   # the password is never kept

    def test_login_failures_have_french_messages_and_store_nothing(self):
        cases = [
            ((401, json.dumps({"message": "Identifiants invalides."})), "Identifiants invalides."),
            ((401, ""), "Identifiants invalides."),
            ((403, json.dumps({"message": "Vous ne pouvez pas envoyer de messages."})), "Vous ne pouvez pas envoyer de messages."),
            ((422, json.dumps({"message": "The password field is required."})), "Renseignez le nom d'utilisateur et le mot de passe."),
            ((429, ""), "Trop de tentatives, réessayez dans une minute"),
            ((500, "boom"), "erreur 500"),
            ((200, "not json"), "illisible"),
            ((200, json.dumps({"nothing": 1})), "inattendue"),
        ]
        for answer, expected_text in cases:
            store = InMemoryTokenStore()
            client, _ = self.login_client(answer, store=store)
            with self.assertRaises(GlobeTransportsError, msg=str(answer)) as caught:
                client.login("agent", "wrong")
            self.assertIn(expected_text, str(caught.exception))
            self.assertNotIn("wrong", str(caught.exception))
            self.assertIsNone(store.load())

    def test_logout_clears_the_token(self):
        store = InMemoryTokenStore(stored_token())
        client_with_token(FakeTransport(), token_store=store).logout()
        self.assertIsNone(store.load())


class TokenUseTest(unittest.TestCase):
    def test_token_is_attached_to_both_routes(self):
        transport = FakeTransport((200, "[]"), (200, "[]"))
        client = client_with_token(transport)
        client.fetch_departs("upcoming")
        client.fetch_bookings("bus", [1])
        for request in transport.requests:
            self.assertEqual(request["method"], "GET")
            self.assertEqual(request["headers"]["Authorization"], "Bearer stored-token")
            self.assertEqual(request["headers"]["Accept"], "application/json")

    def test_without_a_token_and_without_login_the_call_is_not_even_sent(self):
        transport = FakeTransport()
        with self.assertRaises(GlobeAuthenticationRequired):
            client_with_token(transport, token_store=InMemoryTokenStore()).fetch_departs("past")
        self.assertEqual(transport.requests, [])

    def test_missing_token_asks_for_login_then_continues(self):
        store = InMemoryTokenStore()
        transport = FakeTransport((200, "[]"))
        asked = []

        def login_requester():
            asked.append(True)
            store.save(stored_token("fresh"))
            return True
        client_with_token(transport, token_store=store, login_requester=login_requester).fetch_departs("past")
        self.assertEqual(len(asked), 1)
        self.assertEqual(transport.requests[0]["headers"]["Authorization"], "Bearer fresh")

    def test_cancelled_login_stops_the_call(self):
        transport = FakeTransport()
        with self.assertRaises(GlobeAuthenticationRequired):
            client_with_token(transport, token_store=InMemoryTokenStore(), login_requester=lambda: False).fetch_departs("past")
        self.assertEqual(transport.requests, [])

    def test_token_valid_for_more_than_three_days_is_reused_without_login(self):
        store = InMemoryTokenStore(stored_token(days_left=3, base_url=DEFAULT_URL))
        transport = FakeTransport((200, "[]"))
        client_with_token(transport, token_store=store, login_requester=lambda: self.fail("must not log in")).fetch_departs("past")
        self.assertEqual(len(transport.requests), 1)

    def test_token_expiring_in_under_three_days_is_dropped_and_login_asked(self):
        store = InMemoryTokenStore(GlobeToken("old", NOW + timedelta(days=2, hours=23), DEFAULT_URL))
        transport = FakeTransport((200, "[]"))

        def login_requester():
            self.assertIsNone(store.load())   # dropped before asking
            store.save(stored_token("renewed"))
            return True
        client_with_token(transport, token_store=store, login_requester=login_requester).fetch_departs("past")
        self.assertEqual(transport.requests[0]["headers"]["Authorization"], "Bearer renewed")

    def test_expired_token_and_token_of_another_server_are_not_used(self):
        for token in (GlobeToken("old", NOW - timedelta(days=1), DEFAULT_URL), stored_token(base_url="http://other.example")):
            transport = FakeTransport()
            with self.assertRaises(GlobeAuthenticationRequired):
                client_with_token(transport, token_store=InMemoryTokenStore(token)).fetch_departs("past")
            self.assertEqual(transport.requests, [])

    def test_a_401_drops_the_token_logs_in_again_and_retries_exactly_once(self):
        store = InMemoryTokenStore(stored_token("rejected"))
        transport = FakeTransport((401, json.dumps({"message": "Unauthenticated."})), (200, json.dumps([{"id": 1, "depart_name": "D", "buses": []}])))
        logins = []

        def login_requester():
            self.assertIsNone(store.load())
            logins.append(True)
            store.save(stored_token("new-token"))
            return True
        departs = client_with_token(transport, token_store=store, login_requester=login_requester).fetch_departs("upcoming")
        self.assertEqual([depart.depart_id for depart in departs], [1])
        self.assertEqual(len(logins), 1)
        self.assertEqual([request["headers"]["Authorization"] for request in transport.requests], ["Bearer rejected", "Bearer new-token"])

    def test_a_second_401_is_not_retried_again(self):
        store = InMemoryTokenStore(stored_token("rejected"))
        transport = FakeTransport((401, ""), (401, ""))

        def login_requester():
            store.save(stored_token("also-rejected"))
            return True
        with self.assertRaises(GlobeAuthenticationRequired):
            client_with_token(transport, token_store=store, login_requester=login_requester).fetch_departs("upcoming")
        self.assertEqual(len(transport.requests), 2)
        self.assertIsNone(store.load())

    def test_403_on_a_data_call_shows_the_permission_message_and_keeps_the_token(self):
        store = InMemoryTokenStore(stored_token())
        client = client_with_token(FakeTransport((403, json.dumps({"message": "Permission retirée."}))), token_store=store)
        with self.assertRaises(GlobeTransportsError) as caught:
            client.fetch_departs("past")
        self.assertEqual(str(caught.exception), "Permission retirée.")
        self.assertIsNotNone(store.load())

    def test_token_and_password_never_appear_in_error_messages(self):
        client = client_with_token(FakeTransport((500, "stored-token leaked?")))
        with self.assertRaises(GlobeTransportsError) as caught:
            client.fetch_departs("past")
        self.assertNotIn("stored-token", str(caught.exception))


class MappingTest(unittest.TestCase):
    def test_contacts_phone_normalisation_dedupe_and_counts(self):
        plan = build_globe_import_plan([
            booking(1, 770000000),                                   # integer phone
            booking(2, "77 000 00 00", name="Même client"),          # same customer, second booking
            booking(3, None),                                        # no number
            booking(4, 12345),                                       # not a Senegalese number
            booking(5, 781112233, name="Moussa", agent_number=None, seat_number=None),
        ])
        self.assertEqual([contact.phone_number for contact in plan.valid_contacts], ["+221770000000", "+221781112233"])
        self.assertEqual((plan.duplicate_count, plan.invalid_phone_count), (1, 2))
        first, second = plan.valid_contacts
        self.assertEqual(first.name, "Awa Fatou NDIAYE")
        self.assertEqual(first.column_values["seat_number"], "12")
        self.assertEqual(first.column_values["agent_number"], "771112233/782223344")
        self.assertEqual(first.custom_fields["Point de départ"], "Dakar")
        self.assertNotIn("agent_number", second.column_values)    # null values are left out
        self.assertEqual(suggest_list_name(plan), "Dakar - Touba 08h")

    def test_rendez_vous_fields_are_kept_when_sent(self):
        plan = build_globe_import_plan([booking(1, 770000000, formatted_schedule="07:45", arret_bus="Devant la pharmacie")])
        contact = plan.valid_contacts[0]
        self.assertEqual(contact.column_values["formatted_schedule"], "07:45")
        self.assertEqual(contact.column_values["arret_bus"], "Devant la pharmacie")
        self.assertEqual(contact.custom_fields["Heure de rendez-vous"], "07:45")
        self.assertEqual(contact.custom_fields["Lieu de rendez-vous"], "Devant la pharmacie")

    def test_rendez_vous_fields_absent_or_null_are_left_out_and_empty_in_templates(self):
        older_payload = booking(1, 770000000)                                                  # backend before the two fields
        null_payload = booking(2, 770000001, formatted_schedule=None, arret_bus=None)          # départ without schedule
        plan = build_globe_import_plan([older_payload, null_payload])
        for contact in plan.valid_contacts:
            self.assertNotIn("formatted_schedule", contact.column_values)
            self.assertNotIn("arret_bus", contact.column_values)
            self.assertNotIn("Heure de rendez-vous", contact.custom_fields)
        draft = build_campaign_draft("Rendez-vous à {formatted_schedule} à {arret_bus}.", [("L", contact) for contact in plan.valid_contacts])
        self.assertEqual(draft.messages[0].message_text, "Rendez-vous à  à .")
        self.assertEqual(draft.empty_value_counts, {"formatted_schedule": 2, "arret_bus": 2})

    def test_rendez_vous_template_end_to_end(self):
        plan = build_globe_import_plan([booking(1, 770000000, formatted_schedule="07:45", arret_bus="Devant la pharmacie")])
        with tempfile.TemporaryDirectory() as folder:
            store = ContactStore(Path(folder) / "db.sqlite")
            list_id, _, _ = store.import_into_new_list("L", plan.valid_contacts, "Globe Transports")
            stored_contact = store.list_contacts(list_id)[0]
        draft = build_campaign_draft("Rendez-vous à {formatted_schedule} à {arret_bus}", [("L", stored_contact)])
        self.assertEqual(draft.messages[0].message_text, "Rendez-vous à 07:45 à Devant la pharmacie")
        self.assertEqual(describe_variable("formatted_schedule"), "Heure de rendez-vous")
        self.assertEqual(describe_variable("arret_bus"), "Lieu de rendez-vous")

    def test_rendez_vous_fields_pass_through_the_client_untouched(self):
        payload = [booking(1, 770000000, formatted_schedule="07:45", arret_bus=None)]
        fetched = client_with_token(FakeTransport((200, json.dumps(payload)))).fetch_bookings("bus", [1])
        self.assertEqual((fetched[0]["formatted_schedule"], fetched[0]["arret_bus"]), ("07:45", None))

    def test_no_list_name_suggested_for_several_departs(self):
        plan = build_globe_import_plan([booking(1, 770000001), booking(2, 770000002, depart_name="Autre")])
        self.assertEqual(suggest_list_name(plan), "")

    def test_summary_wording(self):
        self.assertEqual(describe_import_result(42, 5, 1), "42 contacts importés, 5 doublons ignorés, 1 sans numéro valide")
        self.assertEqual(describe_import_result(1, 1, 0), "1 contact importé, 1 doublon ignoré")
        self.assertEqual(describe_import_result(0, 0, 0), "0 contact importé")
        self.assertEqual(describe_import_result(8813, 0, 0), "8\u202f813 contacts importés")

    def test_globe_values_are_stored_and_usable_in_templates(self):
        plan = build_globe_import_plan([booking(1, 770000000, seat_number=None)])
        with tempfile.TemporaryDirectory() as folder:
            store = ContactStore(Path(folder) / "db.sqlite")
            list_id, inserted, skipped = store.import_into_new_list("Dakar - Touba", plan.valid_contacts, "Globe Transports")
            self.assertEqual((inserted, skipped), (1, 0))
            stored_contact = store.list_contacts(list_id)[0]
            self.assertEqual(store.import_into_existing_list(list_id, plan.valid_contacts, "Globe Transports"), (0, 1))
        draft = build_campaign_draft("{nom} : {bus_name} de {point_dep} à {destination}, place {seat_number}.", [("L", stored_contact)])
        self.assertEqual(draft.messages[0].message_text, "Awa Fatou NDIAYE : Bus climatisé de Dakar à Touba, place .")
        self.assertEqual(draft.empty_value_counts, {"seat_number": 1})


class SelectionDialogTest(unittest.TestCase):
    def build_dialog(self):
        dialog = GlobeSelectionDialog(client_factory=lambda base_url, login_requester: SimpleNamespace(base_url=base_url), token_store=InMemoryTokenStore())
        dialog.populate_tree([
            DepartForSms(1, "D1", [BusForSms(11, "A"), BusForSms(12, "B")]),
            DepartForSms(2, "D2", [BusForSms(21, "C"), BusForSms(22, "D")]),
            DepartForSms(3, "D3", [BusForSms(31, "E")]),
        ])
        return dialog

    def test_nothing_checked_disables_import(self):
        dialog = self.build_dialog()
        self.assertEqual(dialog.selected_ids(), ([], []))
        self.assertFalse(dialog.import_button.isEnabled())

    def test_checking_a_depart_selects_all_its_buses_and_is_asked_as_a_depart(self):
        dialog = self.build_dialog()
        dialog.departs_tree.topLevelItem(0).setCheckState(0, Qt.Checked)
        self.assertEqual(dialog.selected_ids(), ([1], []))
        self.assertEqual(dialog.departs_tree.topLevelItem(0).child(1).checkState(0), Qt.Checked)
        self.assertTrue(dialog.import_button.isEnabled())

    def test_individual_buses_are_asked_as_buses_and_both_kinds_can_mix(self):
        dialog = self.build_dialog()
        dialog.departs_tree.topLevelItem(1).child(1).setCheckState(0, Qt.Checked)
        dialog.departs_tree.topLevelItem(0).setCheckState(0, Qt.Checked)
        self.assertEqual(dialog.selected_ids(), ([1], [22]))
        self.assertEqual(dialog.departs_tree.topLevelItem(1).checkState(0), Qt.PartiallyChecked)
        self.assertEqual(dialog.selection_label.text(), "Sélection : 1 départ + 1 bus")

    def test_checking_every_bus_of_a_depart_counts_as_the_whole_depart(self):
        dialog = self.build_dialog()
        dialog.departs_tree.topLevelItem(1).child(0).setCheckState(0, Qt.Checked)
        dialog.departs_tree.topLevelItem(1).child(1).setCheckState(0, Qt.Checked)
        self.assertEqual(dialog.selected_ids(), ([2], []))

    def test_logout_button_forgets_the_token_and_empties_the_tree(self):
        store = InMemoryTokenStore(stored_token())
        dialog = GlobeSelectionDialog(client_factory=lambda base_url, login_requester: SimpleNamespace(base_url=base_url), token_store=store)
        dialog.populate_tree([DepartForSms(1, "D1", [BusForSms(11, "A")])])
        dialog.logout_button.click()
        self.assertIsNone(store.load())
        self.assertEqual(dialog.departs_tree.topLevelItemCount(), 0)
        self.assertIn("déconnecté", dialog.status_label.text())

    def test_stale_answers_are_ignored(self):
        dialog = self.build_dialog()
        dialog.request_generation = 5
        dialog.on_departs_loaded(4, [])
        self.assertEqual(dialog.departs_tree.topLevelItemCount(), 3)


if __name__ == "__main__":
    unittest.main()
