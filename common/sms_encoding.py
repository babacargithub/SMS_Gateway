"""Pure: how a text becomes one or several SMS (3GPP TS 23.038 / 23.040). No USB, no UI.

A text is sent in the GSM 7-bit alphabet when every character is in it (160 characters per SMS, 153 per part when the
message is split), otherwise as Unicode/UCS-2 (70 characters per SMS, 67 per part). Typical French trap: é è à ù are in
the 7-bit alphabet but ç ê â î ô û ë ï œ « » ’ and capital À È are not, so one of them makes the whole message Unicode.
A long message is split into parts glued back together by the phone (concatenated SMS, 8-bit reference). Each part is a
separate SMS for the operator and is billed as one."""
import random
import unicodedata
from dataclasses import dataclass

from common.gsm_charset import GSM_BASIC_CHARACTER_SET, GSM_ESCAPE_SEPTET, GSM_EXTENSION_CHARACTERS

ALPHABET_GSM7, ALPHABET_UCS2 = "gsm7", "ucs2"
SINGLE_SMS_CAPACITY = {ALPHABET_GSM7: 160, ALPHABET_UCS2: 70}
PART_CAPACITY = {ALPHABET_GSM7: 153, ALPHABET_UCS2: 67}      # 6 bytes of the SMS are taken by the concatenation header
MAX_PARTS_PER_MESSAGE = 10                                   # a cost guard: a longer message is refused, not truncated

SEPTET_BY_BASIC_CHARACTER = {
    character: septet for septet, character in enumerate(GSM_BASIC_CHARACTER_SET) if septet != GSM_ESCAPE_SEPTET
}
EXTENSION_SEPTET_BY_CHARACTER = {character: septet for septet, character in GSM_EXTENSION_CHARACTERS.items()}

# What a French text commonly contains that the 7-bit alphabet does not: replaced when the owner asks to stay in 7 bits.
SIMPLIFIED_CHARACTERS = {
    "’": "'", "‘": "'", "“": '"', "”": '"', "«": '"', "»": '"', "–": "-", "—": "-", "…": "...", "•": "-",
    "œ": "oe", "Œ": "OE", "ç": "c", "\t": " ", " ": " ", " ": " ", " ": " ",
}

CONCATENATION_HEADER_LENGTH_BYTES = 6                        # UDH length, element id 0, element length 3, reference, total, part
SUBMIT_MESSAGE_TYPE = 0x01
USER_DATA_HEADER_PRESENT = 0x40
INTERNATIONAL_NUMBER_TYPE = 0x91
DATA_CODING_GSM7, DATA_CODING_UCS2 = 0x00, 0x08


@dataclass(frozen=True)
class SmsPlan:
    alphabet: str
    parts: tuple          # the text of each SMS, in order (one entry when no splitting is needed)
    unit_count: int       # characters as the alphabet counts them (an extension character such as « € » counts for 2)

    @property
    def part_count(self):
        return len(self.parts)

    @property
    def is_unicode(self):
        return self.alphabet == ALPHABET_UCS2

    @property
    def is_too_long(self):
        return self.part_count > MAX_PARTS_PER_MESSAGE


def is_gsm7_character(character):
    return character in SEPTET_BY_BASIC_CHARACTER or character in EXTENSION_SEPTET_BY_CHARACTER


def characters_forcing_unicode(text):
    """The distinct characters of `text` that are not in the 7-bit alphabet, in order of appearance."""
    return list(dict.fromkeys(character for character in text if not is_gsm7_character(character)))


def simplify_for_gsm7(text):
    """Keep what the 7-bit alphabet has; replace the rest by a close equivalent (ê → e, ç → c, « → \"), else « ? »."""
    simplified_characters = []
    for character in text:
        if is_gsm7_character(character):
            simplified_characters.append(character)
        elif character in SIMPLIFIED_CHARACTERS:
            simplified_characters.append(SIMPLIFIED_CHARACTERS[character])
        else:
            base_characters = "".join(c for c in unicodedata.normalize("NFKD", character) if not unicodedata.combining(c))
            simplified_characters.append(base_characters if base_characters and all(is_gsm7_character(c) for c in base_characters) else "?")
    return "".join(simplified_characters)


def septets_of_character(character):
    if character in SEPTET_BY_BASIC_CHARACTER:
        return [SEPTET_BY_BASIC_CHARACTER[character]]
    return [GSM_ESCAPE_SEPTET, EXTENSION_SEPTET_BY_CHARACTER[character]]


def utf16_units_of_character(character):
    return len(character.encode("utf-16-be")) // 2


def split_into_parts(text, unit_count_of_character, capacity):
    """Cut `text` into chunks of at most `capacity` units without ever cutting a character in two."""
    parts, current_part, current_units = [], [], 0
    for character in text:
        character_units = unit_count_of_character(character)
        if current_units + character_units > capacity:
            parts.append("".join(current_part))
            current_part, current_units = [], 0
        current_part.append(character)
        current_units += character_units
    parts.append("".join(current_part))
    return parts


def plan_sms(text):
    """Choose the alphabet and cut the text into the SMS parts it needs."""
    if all(is_gsm7_character(character) for character in text):
        alphabet, units_of = ALPHABET_GSM7, lambda character: len(septets_of_character(character))
    else:
        alphabet, units_of = ALPHABET_UCS2, utf16_units_of_character
    unit_count = sum(units_of(character) for character in text)
    if unit_count <= SINGLE_SMS_CAPACITY[alphabet]:
        return SmsPlan(alphabet, (text,), unit_count)
    return SmsPlan(alphabet, tuple(split_into_parts(text, units_of, PART_CAPACITY[alphabet])), unit_count)


def is_plain_single_sms(plan):
    """One 7-bit SMS of printable ASCII only: the case the text-mode send (verified on the dongle) handles unchanged."""
    return plan.alphabet == ALPHABET_GSM7 and plan.part_count == 1 and all(
        32 <= ord(character) < 127 and character not in "@$_[\\]^{|}~" for character in plan.parts[0]
    )


def encode_destination_address(phone_number):
    digits = phone_number.lstrip("+")
    if not digits.isdigit():
        raise ValueError(f"Numéro invalide : {phone_number}")
    padded_digits = digits + "F" if len(digits) % 2 else digits
    swapped_pairs = "".join(padded_digits[index + 1] + padded_digits[index] for index in range(0, len(padded_digits), 2))
    return f"{len(digits):02X}{INTERNATIONAL_NUMBER_TYPE:02X}{swapped_pairs}"


def pack_septets(septets, leading_bit_count=0):
    """The 7-bit values packed into bytes, starting `leading_bit_count` bits in (room for a header's fill bits)."""
    packed_bits = 0
    for septet_index, septet in enumerate(septets):
        packed_bits |= septet << (leading_bit_count + 7 * septet_index)
    total_bit_count = leading_bit_count + 7 * len(septets)
    return packed_bits.to_bytes((total_bit_count + 7) // 8, "little")


def build_user_data(part_text, alphabet, concatenation_header):
    """(user data length as the SMS counts it, user data bytes). The header, if any, comes first."""
    if alphabet == ALPHABET_GSM7:
        septets = [septet for character in part_text for septet in septets_of_character(character)]
        if not concatenation_header:
            return len(septets), pack_septets(septets)
        header_bit_count = len(concatenation_header) * 8
        header_septet_count = (header_bit_count + 6) // 7                  # the header is padded up to a septet boundary
        text_bytes = pack_septets(septets, leading_bit_count=header_septet_count * 7)
        header_value = int.from_bytes(concatenation_header, "little")
        user_data = (int.from_bytes(text_bytes, "little") | header_value).to_bytes(len(text_bytes), "little")
        return header_septet_count + len(septets), user_data
    text_bytes = part_text.encode("utf-16-be")
    return len(concatenation_header) + len(text_bytes), concatenation_header + text_bytes


def build_submit_pdus(destination_number, plan, concatenation_reference=None):
    """One (length for AT+CMGS, PDU hex) per part, ready to send in PDU mode with the SMSC left to the SIM's default.
    The length counts the TPDU only (everything after the leading `00` that means « default SMSC »)."""
    reference = random.randrange(256) if concatenation_reference is None else concatenation_reference
    is_split = plan.part_count > 1
    data_coding_scheme = DATA_CODING_GSM7 if plan.alphabet == ALPHABET_GSM7 else DATA_CODING_UCS2
    destination_address = encode_destination_address(destination_number)
    pdus = []
    for part_number, part_text in enumerate(plan.parts, start=1):
        header = bytes([0x05, 0x00, 0x03, reference, plan.part_count, part_number]) if is_split else b""
        user_data_length, user_data = build_user_data(part_text, plan.alphabet, header)
        first_octet = SUBMIT_MESSAGE_TYPE | (USER_DATA_HEADER_PRESENT if is_split else 0)
        tpdu_hex = (
            f"{first_octet:02X}00{destination_address}00{data_coding_scheme:02X}{user_data_length:02X}{user_data.hex().upper()}"
        )
        pdus.append((len(tpdu_hex) // 2, "00" + tpdu_hex))
    return pdus
