"""Pure: decode one received SMS (SMS-DELIVER PDU, 3GPP TS 23.040) as listed by the dongle in PDU mode (`AT+CMGF=0`).
Handles GSM 7-bit / 8-bit / UCS2 text, numeric and alphanumeric senders (« Expresso »), the operator timestamp and the
concatenation header that says « part 2 of 3 » of a long message."""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from common.gsm_charset import gsm_septets_to_text, unpack_septets

MESSAGE_TYPE_MASK = 0x03
MESSAGE_TYPE_DELIVER = 0x00          # a received message; other types (status reports, our own stored sends) are ignored
USER_DATA_HEADER_PRESENT = 0x40
ADDRESS_TYPE_NUMBER_KIND_MASK = 0x70
ADDRESS_KIND_INTERNATIONAL = 0x10
ADDRESS_KIND_ALPHANUMERIC = 0x50
CONCATENATION_8_BIT_REFERENCE = 0x00
CONCATENATION_16_BIT_REFERENCE = 0x08
BCD_SYMBOLS = {0xA: "*", 0xB: "#"}
ALPHABET_GSM7, ALPHABET_8BIT, ALPHABET_UCS2 = "gsm7", "8bit", "ucs2"


class PduDecodeError(Exception):
    """The PDU is malformed or uses a feature this decoder does not support."""


@dataclass
class SmsPart:
    """One received PDU. A short message is one part; a long one arrives as several, told apart by the concatenation fields."""
    sim_index: int
    sender_number: str
    text: str
    received_at: str                  # 'YYYY-MM-DD HH:MM:SS' in the operator's local time
    sent_at_utc: datetime
    concatenation_reference: Optional[int] = None
    concatenation_total: int = 1
    concatenation_sequence: int = 1

    @property
    def is_part_of_long_message(self):
        return self.concatenation_reference is not None and self.concatenation_total > 1


def decode_address(address_bytes, digit_count, address_type):
    if address_type & ADDRESS_TYPE_NUMBER_KIND_MASK == ADDRESS_KIND_ALPHANUMERIC:
        return gsm_septets_to_text(unpack_septets(address_bytes, digit_count * 4 // 7))
    digits = []
    for address_byte in address_bytes:
        for nibble in (address_byte & 0x0F, address_byte >> 4):  # each byte holds two digits, low nibble first
            if nibble <= 9:
                digits.append(str(nibble))
            elif nibble in BCD_SYMBOLS:
                digits.append(BCD_SYMBOLS[nibble])
    number = "".join(digits[:digit_count])
    is_international = address_type & ADDRESS_TYPE_NUMBER_KIND_MASK == ADDRESS_KIND_INTERNATIONAL
    return f"+{number}" if is_international else number


def decode_timestamp(timestamp_bytes):
    """(local 'YYYY-MM-DD HH:MM:SS', aware UTC datetime). The last byte is the offset from UTC in quarter-hours."""
    date_and_time_fields = [int(f"{byte & 0x0F}{byte >> 4}") for byte in timestamp_bytes[:6]]
    year, month, day, hour, minute, second = date_and_time_fields
    local_time = datetime(2000 + year, month, day, hour, minute, second)
    offset_byte = timestamp_bytes[6]
    offset_quarter_hours = (offset_byte & 0x07) * 10 + (offset_byte >> 4)
    if offset_byte & 0x08:
        offset_quarter_hours = -offset_quarter_hours
    utc_time = (local_time - timedelta(minutes=15 * offset_quarter_hours)).replace(tzinfo=timezone.utc)
    return local_time.strftime("%Y-%m-%d %H:%M:%S"), utc_time


def alphabet_of(data_coding_scheme):
    coding_group = data_coding_scheme & 0xF0
    if coding_group <= 0x30:                       # general data coding
        if data_coding_scheme & 0x20:
            raise PduDecodeError("Texte compressé non pris en charge")
        return {0: ALPHABET_GSM7, 1: ALPHABET_8BIT, 2: ALPHABET_UCS2}.get((data_coding_scheme >> 2) & 0x03, ALPHABET_GSM7)
    if coding_group == 0xE0:
        return ALPHABET_UCS2
    if coding_group == 0xF0:
        return ALPHABET_8BIT if data_coding_scheme & 0x04 else ALPHABET_GSM7
    return ALPHABET_GSM7


def find_concatenation(user_data_header):
    """(reference, total parts, this part's number) from the header, or None for a message that is not split."""
    position = 0
    while position + 2 <= len(user_data_header):
        element_id, element_length = user_data_header[position], user_data_header[position + 1]
        element_data = user_data_header[position + 2:position + 2 + element_length]
        if element_id == CONCATENATION_8_BIT_REFERENCE and element_length == 3:
            reference, total, sequence = element_data
        elif element_id == CONCATENATION_16_BIT_REFERENCE and element_length == 4:
            reference, total, sequence = (element_data[0] << 8) | element_data[1], element_data[2], element_data[3]
        else:
            position += 2 + element_length
            continue
        return (reference, total, sequence) if 1 <= sequence <= total else None
    return None


def decode_text(user_data, user_data_length, has_header, alphabet):
    """Returns (text, concatenation)."""
    header_length = user_data[0] + 1 if has_header else 0
    concatenation = find_concatenation(user_data[1:header_length]) if has_header else None
    if alphabet == ALPHABET_GSM7:
        header_septets = math.ceil(header_length * 8 / 7)   # the text starts on the next septet boundary
        septets = unpack_septets(user_data, user_data_length - header_septets, skipped_bit_count=header_septets * 7)
        return gsm_septets_to_text(septets), concatenation
    text_bytes = user_data[header_length:user_data_length]
    if alphabet == ALPHABET_UCS2:
        return text_bytes.decode("utf-16-be", errors="replace"), concatenation
    return text_bytes.decode("latin-1"), concatenation


def decode_deliver_pdu(sim_index, pdu_hex):
    """An SmsPart, or None when the PDU is not a received message. Raises PduDecodeError if it cannot be read."""
    try:
        pdu = bytes.fromhex(pdu_hex.strip())
        position = 1 + pdu[0]                                 # skip the SMSC address
        first_octet = pdu[position]
        if first_octet & MESSAGE_TYPE_MASK != MESSAGE_TYPE_DELIVER:
            return None
        address_digit_count, address_type = pdu[position + 1], pdu[position + 2]
        address_byte_count = (address_digit_count + 1) // 2
        address_end = position + 3 + address_byte_count
        sender_number = decode_address(pdu[position + 3:address_end], address_digit_count, address_type)
        data_coding_scheme = pdu[address_end + 1]             # the byte before it is the protocol identifier
        timestamp_bytes = pdu[address_end + 2:address_end + 9]
        if len(timestamp_bytes) < 7:
            raise PduDecodeError("Horodatage tronqué")
        received_at, sent_at_utc = decode_timestamp(timestamp_bytes)
        user_data_length = pdu[address_end + 9]
        user_data = pdu[address_end + 10:]
        message_text, concatenation = decode_text(
            user_data, user_data_length, bool(first_octet & USER_DATA_HEADER_PRESENT), alphabet_of(data_coding_scheme)
        )
    except (IndexError, ValueError) as decoding_error:
        raise PduDecodeError(f"PDU illisible : {decoding_error}")
    reference, total, sequence = concatenation if concatenation else (None, 1, 1)
    return SmsPart(sim_index, sender_number, message_text, received_at, sent_at_utc, reference, total, sequence)
