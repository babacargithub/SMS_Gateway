"""Pure: GSM 03.38 7-bit packing, as this dongle expects for AT+CUSD (`*222#` <-> `2A994C3602`)."""
from common.gsm_charset import GSM_BASIC_CHARACTER_SET, gsm_septets_to_text, unpack_septets


USSD_CARRIAGE_RETURN_PADDING = "\r"


def encode_ussd_request(ussd_code):
    """'*222#' -> '2A994C3602'. Only characters whose GSM code equals their ASCII code (digits, * and #) are supported."""
    septets = []
    for character in ussd_code:
        septet = ord(character)
        if septet > 0x7F or GSM_BASIC_CHARACTER_SET[septet] != character:
            raise ValueError(f"Caractère non pris en charge dans un code USSD : {character!r}")
        septets.append(septet)
    packed_bits, bit_count, packed_bytes = 0, 0, bytearray()
    for septet in septets:
        packed_bits |= septet << bit_count
        bit_count += 7
        while bit_count >= 8:
            packed_bytes.append(packed_bits & 0xFF)
            packed_bits >>= 8
            bit_count -= 8
    if bit_count:
        packed_bytes.append(packed_bits & 0xFF)
    return packed_bytes.hex().upper()


def decode_packed_gsm7(packed_hex):
    """GSM 7-bit packed hex -> text. Raises ValueError if the hex is malformed."""
    packed_bytes = bytes.fromhex(packed_hex)
    septets = unpack_septets(packed_bytes, len(packed_bytes) * 8 // 7)
    return gsm_septets_to_text(septets).rstrip(USSD_CARRIAGE_RETURN_PADDING)


def decode_ussd_reply(reply_hex, data_coding_scheme):
    """Text of a +CUSD reply. DCS 72 (0x48) is UCS2; anything else is GSM 7-bit packed."""
    if data_coding_scheme == 72:
        return bytes.fromhex(reply_hex).decode("utf-16-be", errors="replace")
    return decode_packed_gsm7(reply_hex)
