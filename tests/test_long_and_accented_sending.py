import unittest
from types import SimpleNamespace

from compose.campaign_builder import build_campaign_draft
from compose.sms_cost_estimate import SimSmsPrice, describe_estimated_costs
from devices import dongle
from devices.dongle import DongleConnection, SendOutcomeUnknown, SendRejected


class ScriptedDongle(DongleConnection):
    """A DongleConnection whose serial exchange is replaced by a script: no USB."""

    def __init__(self, replies_to_submit):
        super().__init__(usb_device=SimpleNamespace(bus=0, address=0))
        self.commands = []                         # every AT command line, in order
        self.bodies = []                           # every text/PDU body sent after a `>` prompt
        self.replies_to_submit = list(replies_to_submit)

    def send_at_command(self, command_text, wait_seconds=2.0):
        self.commands.append(command_text)
        return "OK"

    def _write_and_read(self, raw_text, wait_seconds, is_reply_complete):
        if raw_text.startswith("AT+CMGS"):
            self.commands.append(raw_text.strip())
            return ">"
        if raw_text == "\x1b":
            return "OK"
        self.bodies.append(raw_text)
        return self.replies_to_submit.pop(0)


def contact(name, phone_number, custom_fields=None):
    return SimpleNamespace(name=name, phone_number=phone_number, custom_fields=custom_fields or {})


class PduSendingTest(unittest.TestCase):
    def setUp(self):
        dongle.PAUSE_BETWEEN_PARTS_SECONDS = 0  # no real waiting in tests

    def test_plain_ascii_still_uses_the_verified_text_mode(self):
        scripted = ScriptedDongle(["+CMGS: 1\r\nOK"])
        scripted.send_text_message("+221771234567", "Bonjour Awa")
        self.assertEqual(scripted.commands, ["AT+CMGF=1", 'AT+CMGS="+221771234567"'])
        self.assertEqual(scripted.bodies, ["Bonjour Awa\x1a"])

    def test_accented_text_goes_out_in_pdu_mode(self):
        scripted = ScriptedDongle(["+CMGS: 1\r\nOK"])
        scripted.send_text_message("+221771234567", "Très français")
        self.assertEqual(scripted.commands[0], "AT+CMGF=0")
        self.assertRegex(scripted.commands[1], r"^AT\+CMGS=\d+$")
        self.assertTrue(scripted.bodies[0].endswith("\x1a"))
        self.assertTrue(scripted.bodies[0].startswith("00"))

    def test_long_text_is_sent_as_several_parts_each_confirmed(self):
        scripted = ScriptedDongle(["+CMGS: 1\r\nOK", "+CMGS: 2\r\nOK"])
        scripted.send_text_message("+221771234567", "a" * 200)
        self.assertEqual(len(scripted.bodies), 2)

    def test_first_part_refused_is_a_clean_failure(self):
        scripted = ScriptedDongle(["+CMS ERROR: 500"])
        with self.assertRaises(SendRejected):
            scripted.send_text_message("+221771234567", "a" * 200)
        self.assertEqual(len(scripted.bodies), 1)  # the second part was never attempted

    def test_later_part_failing_is_unknown_never_a_retryable_failure(self):
        scripted = ScriptedDongle(["+CMGS: 1\r\nOK", "+CMS ERROR: 500"])
        with self.assertRaises(SendOutcomeUnknown) as raised:
            scripted.send_text_message("+221771234567", "a" * 200)
        self.assertIn("vérifiez auprès du destinataire", str(raised.exception))

    def test_message_over_the_part_limit_is_refused_before_anything_is_sent(self):
        scripted = ScriptedDongle([])
        with self.assertRaises(SendRejected):
            scripted.send_text_message("+221771234567", "a" * 2000)
        self.assertEqual(scripted.commands, [])


class CampaignDraftEncodingTest(unittest.TestCase):
    def test_accents_are_kept_and_counted(self):
        draft = build_campaign_draft("Réservé à {nom}", [("L", contact("Awa", "+221771111111"))])
        self.assertEqual(draft.messages[0].message_text, "Réservé à Awa")
        self.assertEqual(draft.unicode_message_count, 0)
        self.assertEqual(draft.total_sms_count, 1)

    def test_a_cedilla_turns_the_message_into_unicode_and_is_reported(self):
        draft = build_campaign_draft("Français pour {nom}", [("L", contact("Awa", "+221771111111"))])
        self.assertEqual(draft.unicode_message_count, 1)
        self.assertEqual(draft.characters_forcing_unicode, ["ç"])
        self.assertTrue(draft.messages[0].is_unicode)

    def test_simplify_option_keeps_the_message_in_7_bits(self):
        draft = build_campaign_draft("Français pour {nom}", [("L", contact("Awa", "+221771111111"))], simplify_to_7_bit=True)
        self.assertEqual(draft.messages[0].message_text, "Francais pour Awa")
        self.assertEqual(draft.unicode_message_count, 0)

    def test_long_messages_are_counted_in_parts_and_billed_as_such(self):
        recipients = [("L", contact(f"C{i}", f"+22177000000{i}")) for i in range(3)]
        draft = build_campaign_draft("x" * 200, recipients)
        self.assertEqual(draft.total_sms_count, 6)
        self.assertEqual(draft.multipart_message_count, 3)
        self.assertEqual(draft.too_long_count, 0)

    def test_message_over_the_limit_is_flagged(self):
        draft = build_campaign_draft("x" * 2000, [("L", contact("Awa", "+221771111111"))])
        self.assertEqual(draft.too_long_count, 1)

    def test_cost_counts_every_part(self):
        lines = describe_estimated_costs(3, [SimSmsPrice("Orange", 10)], sms_count=6)
        self.assertEqual(lines, ["Coût estimé avec Orange : 3 messages en 6 SMS × 10 F = 60 FCFA"])


if __name__ == "__main__":
    unittest.main()
