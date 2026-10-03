"""Reception of SMS in PDU mode: decoding and reassembly of long messages. Run: ~/usbenv/bin/python -m unittest discover tests"""
import unittest
from datetime import datetime, timedelta, timezone

from common.gsm_charset import GSM_BASIC_CHARACTER_SET
from inbox.received_sms_parser import parse_pdu_list_reply
from inbox.sms_assembler import INCOMPLETE_MESSAGE_GRACE_PERIOD, MISSING_PART_MARKER, assemble_received_messages
from inbox.sms_pdu import decode_deliver_pdu

# A published SMS-DELIVER example: GSM 7-bit "hellohello", stamped 99-03-29 15:16:59 (the year is always read as 20xx).
PUBLISHED_EXAMPLE_PDU = "07917283010010F5040BC87238880900F10000993092516195800AE8329BFD4697D9EC37"

ADVERT_PARTS = [
    "Avec Expresso, c’est à ton rythme ! un prêt de forfait ou de crédit et paye plus tard. Avec Expre",
    "sso Wer bi sorina nak lepa DIAMP! Fais vite le *212# pour recevoir",
    " votre prêt.",
]


def swapped_digits(value_text):
    padded = value_text + ("F" if len(value_text) % 2 else "")
    return "".join(padded[position + 1] + padded[position] for position in range(0, len(padded), 2))


def build_deliver_pdu(sender_digits, text, timestamp_text="26093014055900", concatenation=None, use_ucs2=True):
    """A test encoder: international sender, UCS2 text, optional 8-bit concatenation header (reference, total, sequence)."""
    header = b""
    if concatenation:
        header = bytes([5, 0x00, 3, *concatenation])
    if use_ucs2:
        user_data = header + text.encode("utf-16-be")
        user_data_length, data_coding_scheme = len(user_data), "08"
    else:
        septets = [GSM_BASIC_CHARACTER_SET.index(character) for character in text]
        header_septets = -(-len(header) * 8 // 7)
        packed_bits = int.from_bytes(header, "little") | sum(septet << (7 * (header_septets + index)) for index, septet in enumerate(septets))
        user_data = packed_bits.to_bytes(-(-(header_septets + len(septets)) * 7 // 8), "little")
        user_data_length, data_coding_scheme = header_septets + len(septets), "00"
    first_octet = "44" if concatenation else "04"
    return (
        "00" + first_octet + f"{len(sender_digits):02X}" + "91" + swapped_digits(sender_digits)
        + "00" + data_coding_scheme + swapped_digits(timestamp_text) + f"{user_data_length:02X}" + user_data.hex().upper()
    )


class DecodePduTests(unittest.TestCase):
    def test_published_example(self):
        part = decode_deliver_pdu(1, PUBLISHED_EXAMPLE_PDU)
        self.assertEqual(part.text, "hellohello")
        self.assertTrue(part.received_at.endswith("-03-29 15:16:59"))   # the operator's local time
        self.assertFalse(part.is_part_of_long_message)

    def test_ucs2_accents_and_international_sender(self):
        part = decode_deliver_pdu(4, build_deliver_pdu("221771234567", "Bonjour à tous, c’est prêt"))
        self.assertEqual((part.sender_number, part.text), ("+221771234567", "Bonjour à tous, c’est prêt"))
        self.assertEqual(part.received_at, "2026-09-30 14:05:59")

    def test_gsm7_part_with_header_uses_the_padded_septet_boundary(self):
        pdu = build_deliver_pdu("221771234567", "Fais vite le *212# pour recevoir", concatenation=(7, 2, 2), use_ucs2=False)
        part = decode_deliver_pdu(2, pdu)
        self.assertEqual(part.text, "Fais vite le *212# pour recevoir")
        self.assertEqual((part.concatenation_reference, part.concatenation_total, part.concatenation_sequence), (7, 2, 2))

    def test_status_reports_are_ignored(self):
        self.assertIsNone(decode_deliver_pdu(1, "0006" + "0A" * 20))

    def test_alphanumeric_sender(self):
        sender_septets = [GSM_BASIC_CHARACTER_SET.index(character) for character in "Expresso"]
        packed_sender = sum(septet << (7 * index) for index, septet in enumerate(sender_septets)).to_bytes(7, "little")
        body_septets = [GSM_BASIC_CHARACTER_SET.index(character) for character in "Salut"]
        packed_body = sum(septet << (7 * index) for index, septet in enumerate(body_septets)).to_bytes(5, "little")
        pdu = "0004" + "0E" + "D0" + packed_sender.hex() + "00" + "00" + swapped_digits("26093014055900") + "05" + packed_body.hex()
        part = decode_deliver_pdu(1, pdu.upper())
        self.assertEqual((part.sender_number, part.text), ("Expresso", "Salut"))


class ListReplyTests(unittest.TestCase):
    def test_reads_entries_and_skips_sent_copies_and_broken_pdus(self):
        good_pdu = build_deliver_pdu("221771234567", "Bonjour")
        reply = (
            f"+CMGL: 1,1,,30\r\n{good_pdu}\r\n"
            f"+CMGL: 2,3,,30\r\n{good_pdu}\r\n"          # status 3: a copy of a sent message, left alone
            "+CMGL: 3,0,,30\r\nZZZZ\r\n"                  # unreadable: skipped, never deleted
            "\r\nOK"
        )
        self.assertEqual([part.sim_index for part in parse_pdu_list_reply(reply)], [1])


class ReassemblyTests(unittest.TestCase):
    def setUp(self):
        self.sms_timestamp = "26093014055900"
        self.now_utc = datetime(2026, 9, 30, 14, 6, tzinfo=timezone.utc)

    def parts_in_arrival_order(self, arrival_order, skip_sequences=()):
        parts = []
        for sequence in arrival_order:
            if sequence in skip_sequences:
                continue
            pdu = build_deliver_pdu("221771234567", ADVERT_PARTS[sequence - 1], concatenation=(42, 3, sequence))
            parts.append(decode_deliver_pdu(sequence + 10, pdu))
        return parts

    def test_parts_arriving_out_of_order_become_one_message_in_the_right_order(self):
        messages = assemble_received_messages(self.parts_in_arrival_order([2, 3, 1]), self.now_utc)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_text, "".join(ADVERT_PARTS))
        self.assertEqual(sorted(messages[0].sim_indexes), [11, 12, 13])   # every slot gets freed once it is stored

    def test_incomplete_message_waits_for_its_missing_part(self):
        self.assertEqual(assemble_received_messages(self.parts_in_arrival_order([1, 3], skip_sequences=(2,)), self.now_utc), [])

    def test_incomplete_message_is_stored_with_a_marker_after_the_grace_period(self):
        later = self.now_utc + INCOMPLETE_MESSAGE_GRACE_PERIOD + timedelta(minutes=1)
        messages = assemble_received_messages(self.parts_in_arrival_order([1, 3], skip_sequences=(2,)), later)
        self.assertEqual(messages[0].message_text, ADVERT_PARTS[0] + MISSING_PART_MARKER + ADVERT_PARTS[2])
        self.assertEqual(sorted(messages[0].sim_indexes), [11, 13])

    def test_same_part_received_twice_is_kept_once_and_both_slots_freed(self):
        parts = self.parts_in_arrival_order([1, 2, 3])
        duplicate = decode_deliver_pdu(99, build_deliver_pdu("221771234567", ADVERT_PARTS[1], concatenation=(42, 3, 2)))
        messages = assemble_received_messages(parts + [duplicate], self.now_utc)
        self.assertEqual(messages[0].message_text, "".join(ADVERT_PARTS))
        self.assertIn(99, messages[0].sim_indexes)

    def test_short_messages_pass_through(self):
        part = decode_deliver_pdu(5, build_deliver_pdu("221771234567", "Salut"))
        messages = assemble_received_messages([part], self.now_utc)
        self.assertEqual([(m.message_text, m.sim_indexes) for m in messages], [("Salut", [5])])


if __name__ == "__main__":
    unittest.main()
