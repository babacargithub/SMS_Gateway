import json
import unittest
from datetime import datetime, timezone

from gateways.gateway_credentials import OrangeSmsCredentials
from gateways.gateway_message_sender import OrangeSmsMessageSender
from gateways.orange_sms_client import (
    OrangeConnectionFailed,
    OrangeResponseTimedOut,
    OrangeSmsClient,
    parse_contracts_into_balance,
)
from outbox import outbox_store
from outbox.message_sender import DONGLE_UNAVAILABLE

CREDENTIALS = OrangeSmsCredentials("id", "secret", "+221200600", "GolobTrans")
TOKEN_REPLY = (200, json.dumps({"access_token": "tok", "expires_in": 3600}))
ACCEPTED_REPLY = (201, json.dumps({"outboundSMSMessageRequest": {"address": "tel:+221771234567"}}))
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


class ScriptedTransport:
    """Answers each call with the next scripted reply (a tuple, or an exception to raise)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, http_method, url, headers, body_bytes, timeout_seconds):
        self.calls.append((http_method, url, headers, body_bytes))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def build_sender(*replies):
    transport = ScriptedTransport(*replies)
    return OrangeSmsMessageSender(OrangeSmsClient(CREDENTIALS, transport)), transport


class OrangeSendingTest(unittest.TestCase):
    def test_accepted_message_is_sent_with_expected_request(self):
        sender, transport = build_sender(TOKEN_REPLY, ACCEPTED_REPLY)
        result = sender.send("77 123 45 67", "Bonjour")
        self.assertEqual(result.final_state, outbox_store.SENT)
        _, send_url, headers, body = transport.calls[1]
        self.assertTrue(send_url.endswith("/outbound/tel%3A%2B221200600/requests"))
        self.assertEqual(headers["Authorization"], "Bearer tok")
        sent_request = json.loads(body)["outboundSMSMessageRequest"]
        self.assertEqual(sent_request["address"], "tel:+221771234567")
        self.assertEqual(sent_request["senderName"], "GolobTrans")
        self.assertEqual(sent_request["outboundSMSTextMessage"]["message"], "Bonjour")

    def test_token_is_reused_between_messages(self):
        sender, transport = build_sender(TOKEN_REPLY, ACCEPTED_REPLY, ACCEPTED_REPLY)
        sender.send("771234567", "a")
        sender.send("771234567", "b")
        self.assertEqual(len(transport.calls), 3)

    def test_refused_token_is_renewed_once_then_sent(self):
        sender, _ = build_sender(TOKEN_REPLY, (401, "{}"), TOKEN_REPLY, ACCEPTED_REPLY)
        self.assertEqual(sender.send("771234567", "a").final_state, outbox_store.SENT)

    def test_invalid_number_fails_without_any_call(self):
        sender, transport = build_sender()
        self.assertEqual(sender.send("123", "a").final_state, outbox_store.FAILED)
        self.assertEqual(transport.calls, [])

    def test_no_network_pauses_the_run(self):
        sender, _ = build_sender(OrangeConnectionFailed("dns"))
        self.assertEqual(sender.send("771234567", "a").final_state, DONGLE_UNAVAILABLE)

    def test_quota_refusal_pauses_the_run(self):
        sender, _ = build_sender(TOKEN_REPLY, (403, json.dumps({"requestError": {"policyException": {"messageId": "POL1", "text": "no credit"}}})))
        result = sender.send("771234567", "a")
        self.assertEqual(result.final_state, DONGLE_UNAVAILABLE)
        self.assertIn("POL1 no credit", result.error_detail)

    def test_timeout_and_server_error_are_unknown_never_failed(self):
        sender, _ = build_sender(TOKEN_REPLY, OrangeResponseTimedOut("t"))
        self.assertEqual(sender.send("771234567", "a").final_state, outbox_store.UNKNOWN)
        sender, _ = build_sender(TOKEN_REPLY, (500, "oops"))
        self.assertEqual(sender.send("771234567", "a").final_state, outbox_store.UNKNOWN)

    def test_other_client_error_is_a_definite_failure(self):
        sender, _ = build_sender(TOKEN_REPLY, (400, json.dumps({"code": 1, "message": "bad"})))
        self.assertEqual(sender.send("771234567", "a").final_state, outbox_store.FAILED)

    def test_unexpected_success_body_is_unknown(self):
        sender, _ = build_sender(TOKEN_REPLY, (200, "{}"))
        self.assertEqual(sender.send("771234567", "a").final_state, outbox_store.UNKNOWN)


class OrangeBalanceTest(unittest.TestCase):
    REAL_SHAPE = [{"status": "ACTIVE", "availableUnits": 26, "expirationDate": "2100-01-01T00:00:00.000Z"}]

    def test_real_response_shape(self):
        balance = parse_contracts_into_balance(self.REAL_SHAPE, NOW)
        self.assertEqual(balance.available_units, 26)

    def test_expired_and_inactive_contracts_are_ignored(self):
        contracts = self.REAL_SHAPE + [
            {"status": "ACTIVE", "availableUnits": 500, "expirationDate": "2026-01-01T00:00:00.000Z"},
            {"status": "SUSPENDED", "availableUnits": 900, "expirationDate": "2100-01-01T00:00:00.000Z"},
        ]
        self.assertEqual(parse_contracts_into_balance(contracts, NOW).available_units, 26)

    def test_client_reads_balance_through_contracts_route(self):
        transport = ScriptedTransport(TOKEN_REPLY, (200, json.dumps(self.REAL_SHAPE)))
        balance = OrangeSmsClient(CREDENTIALS, transport).read_balance(NOW)
        self.assertEqual(balance.available_units, 26)
        self.assertTrue(transport.calls[1][1].endswith("/sms/admin/v1/contracts"))


if __name__ == "__main__":
    unittest.main()
