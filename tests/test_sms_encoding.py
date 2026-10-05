import unittest

from common.sms_encoding import (
    ALPHABET_GSM7, ALPHABET_UCS2, MAX_PARTS_PER_MESSAGE, build_submit_pdus, characters_forcing_unicode,
    encode_destination_address, is_plain_single_sms, plan_sms, simplify_for_gsm7,
)
from inbox.sms_pdu import decode_deliver_pdu

DELIVER_TIMESTAMP_HEX = "62015001000000"


def read_back_as_received_message(submit_pdu_hex):
    """Wrap the user data of a submit PDU into a received-message PDU and decode it with the inbox decoder."""
    pdu = bytes.fromhex(submit_pdu_hex)
    first_octet = pdu[1]
    address_length = pdu[3]
    address_end = 5 + (address_length + 1) // 2
    data_coding_scheme, user_data_length, user_data = pdu[address_end + 1], pdu[address_end + 2], pdu[address_end + 3:]
    deliver_first_octet = 0x04 | (first_octet & 0x40)
    deliver_hex = (
        "00" + f"{deliver_first_octet:02X}" + "0B916407281553F8" + "00" + f"{data_coding_scheme:02X}"
        + DELIVER_TIMESTAMP_HEX + f"{user_data_length:02X}" + user_data.hex()
    )
    return decode_deliver_pdu(1, deliver_hex)


class AlphabetChoiceTest(unittest.TestCase):
    def test_french_accents_that_the_7_bit_alphabet_has(self):
        plan = plan_sms("Bonjour, à bientôt é è ù")
        self.assertEqual(characters_forcing_unicode("é è à ù ö ñ"), [])
        self.assertEqual(plan.alphabet, ALPHABET_UCS2)  # « ô » is not in the alphabet
        self.assertEqual(plan_sms("Réservé à très bientôt".replace("ô", "o")).alphabet, ALPHABET_GSM7)

    def test_characters_that_force_unicode(self):
        self.assertEqual(characters_forcing_unicode("français, être, « ok » ’"), ["ç", "ê", "«", "»", "’"])

    def test_simplify_keeps_7_bit_letters_and_replaces_the_rest(self):
        simplified = simplify_for_gsm7("Très français, être « ok » ’ œuvre 😀")
        self.assertEqual(simplified, 'Très francais, etre " ok " \' oeuvre ?')
        self.assertEqual(plan_sms(simplified).alphabet, ALPHABET_GSM7)

    def test_plain_ascii_single_sms_keeps_the_text_mode_path(self):
        self.assertTrue(is_plain_single_sms(plan_sms("Bonjour Awa, rdv demain 10h.")))
        self.assertFalse(is_plain_single_sms(plan_sms("Bonjour é")))
        self.assertFalse(is_plain_single_sms(plan_sms("a" * 161)))
        self.assertFalse(is_plain_single_sms(plan_sms("mail@x.sn")))  # « @ » is the one ASCII character 7-bit and ASCII disagree on


class SplittingTest(unittest.TestCase):
    def test_limits_for_7_bit(self):
        self.assertEqual(plan_sms("a" * 160).part_count, 1)
        self.assertEqual(plan_sms("a" * 161).part_count, 2)
        self.assertEqual(plan_sms("a" * 306).part_count, 2)
        self.assertEqual(plan_sms("a" * 307).part_count, 3)

    def test_limits_for_unicode(self):
        self.assertEqual(plan_sms("ê" * 70).part_count, 1)
        self.assertEqual(plan_sms("ê" * 71).part_count, 2)
        self.assertEqual(plan_sms("ê" * 134).part_count, 2)
        self.assertEqual(plan_sms("ê" * 135).part_count, 3)

    def test_extension_character_counts_double_and_is_never_cut(self):
        plan = plan_sms("a" * 152 + "€" + "b" * 10)
        self.assertEqual(plan.parts[0], "a" * 152)  # the euro sign would need 2 septets: it starts the next part
        self.assertEqual("".join(plan.parts), "a" * 152 + "€" + "b" * 10)
        self.assertEqual(plan.unit_count, 152 + 2 + 10)

    def test_emoji_is_never_cut_between_its_two_halves(self):
        plan = plan_sms("a" * 66 + "😀" + "b" * 70)
        self.assertEqual("".join(plan.parts), "a" * 66 + "😀" + "b" * 70)
        for part in plan.parts:
            part.encode("utf-16-be").decode("utf-16-be")

    def test_too_long_is_flagged(self):
        self.assertTrue(plan_sms("a" * (153 * MAX_PARTS_PER_MESSAGE + 1)).is_too_long)
        self.assertFalse(plan_sms("a" * (153 * MAX_PARTS_PER_MESSAGE)).is_too_long)


class PduTest(unittest.TestCase):
    def test_known_7_bit_user_data(self):
        pdus = build_submit_pdus("+46708251358", plan_sms("hellohello"))
        length, pdu_hex = pdus[0]
        self.assertEqual(pdu_hex, "0001000B916407281553F80000" + "0A" + "E8329BFD4697D9EC37")
        self.assertEqual(length, (len(pdu_hex) - 2) // 2)

    def test_address_encoding(self):
        self.assertEqual(encode_destination_address("+221771234567"), "0C91227117325476")

    def test_round_trip_7_bit_single(self):
        text = "Réservé à Awa: 5 000 F €"
        part = read_back_as_received_message(build_submit_pdus("+221771234567", plan_sms(text))[0][1])
        self.assertEqual(part.text, text)
        self.assertIsNone(part.concatenation_reference)

    def test_round_trip_unicode_single(self):
        text = "Bonjour, ça va très bien ? « Oui » ’"
        part = read_back_as_received_message(build_submit_pdus("+221771234567", plan_sms(text))[0][1])
        self.assertEqual(part.text, text)

    def test_round_trip_long_7_bit_with_header(self):
        text = ("Voici un message très long. " * 12) + "à € fin"
        plan = plan_sms(text)
        self.assertEqual(plan.alphabet, ALPHABET_GSM7)
        self.assertGreater(plan.part_count, 1)
        parts = [read_back_as_received_message(pdu_hex) for _, pdu_hex in build_submit_pdus("+221771234567", plan, concatenation_reference=77)]
        self.assertEqual("".join(part.text for part in parts), text)
        self.assertEqual([(part.concatenation_reference, part.concatenation_total, part.concatenation_sequence) for part in parts],
                         [(77, plan.part_count, number) for number in range(1, plan.part_count + 1)])

    def test_round_trip_long_unicode_with_header(self):
        text = ("Être là, c’est déjà ça. " * 15) + "😀 fin"
        plan = plan_sms(text)
        self.assertEqual(plan.alphabet, ALPHABET_UCS2)
        parts = [read_back_as_received_message(pdu_hex) for _, pdu_hex in build_submit_pdus("+221771234567", plan, concatenation_reference=5)]
        self.assertEqual("".join(part.text for part in parts), text)
        self.assertTrue(all(part.concatenation_total == plan.part_count for part in parts))

    def test_every_part_fits_in_one_sms(self):
        for text in ("a" * 500, "ê" * 300):
            for length, pdu_hex in build_submit_pdus("+221771234567", plan_sms(text)):
                tpdu = bytes.fromhex(pdu_hex)[1:]
                user_data_octets = len(tpdu) - (2 + 8 + 1 + 1 + 1)  # type, reference, address(12 digits), pid, dcs, length
                self.assertLessEqual(user_data_octets, 140)
                self.assertEqual(length, len(tpdu))


if __name__ == "__main__":
    unittest.main()
