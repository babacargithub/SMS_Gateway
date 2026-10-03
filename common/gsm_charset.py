"""Pure: the GSM 03.38 7-bit alphabet, shared by USSD replies and SMS in PDU mode."""

GSM_BASIC_CHARACTER_SET = (
    "@£$¥èéùìòÇ\nØø\rÅå"
    "Δ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ"
    " !\"#¤%&'()*+,-./"
    "0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_ESCAPE_SEPTET = 0x1B
GSM_EXTENSION_CHARACTERS = {
    0x0A: "\f", 0x14: "^", 0x28: "{", 0x29: "}", 0x2F: "\\",
    0x3C: "[", 0x3D: "~", 0x3E: "]", 0x40: "|", 0x65: "€",
}


def unpack_septets(packed_bytes, septet_count, skipped_bit_count=0):
    """The `septet_count` 7-bit values packed in `packed_bytes`, starting `skipped_bit_count` bits in
    (an SMS with a user-data header starts its text after the header, padded to a septet boundary).
    Raises ValueError if the bytes are too short."""
    if skipped_bit_count + septet_count * 7 > len(packed_bytes) * 8:
        raise ValueError("Texte 7 bits tronqué")
    packed_bits = int.from_bytes(packed_bytes, "little") >> skipped_bit_count
    return [(packed_bits >> (7 * septet_index)) & 0x7F for septet_index in range(septet_count)]


def gsm_septets_to_text(septets):
    decoded_characters, is_escaped = [], False
    for septet in septets:
        if is_escaped:
            decoded_characters.append(GSM_EXTENSION_CHARACTERS.get(septet, " "))
            is_escaped = False
        elif septet == GSM_ESCAPE_SEPTET:
            is_escaped = True
        else:
            decoded_characters.append(GSM_BASIC_CHARACTER_SET[septet])
    return "".join(decoded_characters)
