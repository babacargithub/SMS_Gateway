"""Pure parsing of the dongle's `AT+CMGL="ALL"` reply (text mode, with `AT+CSDH=1` so the data coding scheme is shown).
No USB, no UI."""
import csv
import re
from dataclasses import dataclass

LIST_ENTRY_PREFIX = "+CMGL:"
RECEIVED_STATUSES = ("REC UNREAD", "REC READ")  # anything else (stored drafts, sent copies) is left alone on the SIM
DATA_CODING_SCHEME_FIELD = 8
UCS2_CODING_MASK = 0x0C
UCS2_CODING_VALUE = 0x08


@dataclass
class ReceivedSms:
    sim_index: int
    sender_number: str
    message_text: str
    received_at: str  # 'YYYY-MM-DD HH:MM:SS', as stamped by the operator


def parse_operator_timestamp(operator_timestamp):
    """'26/09/30,14:05:59+00' -> '2026-09-30 14:05:59'. Unreadable input is returned unchanged."""
    timestamp_match = re.match(r"(\d{2})/(\d{2})/(\d{2}),(\d{2}:\d{2}:\d{2})", operator_timestamp)
    if timestamp_match is None:
        return operator_timestamp
    year, month, day, time_of_day = timestamp_match.groups()
    return f"20{year}-{month}-{day} {time_of_day}"


def decode_body(body_text, data_coding_scheme):
    """UCS2 bodies (accents, emoji) arrive as UTF-16BE hex; everything else is plain text."""
    is_ucs2 = data_coding_scheme & UCS2_CODING_MASK == UCS2_CODING_VALUE
    if not is_ucs2:
        return body_text
    try:
        return bytes.fromhex(body_text.strip()).decode("utf-16-be")
    except ValueError:
        return body_text


def parse_list_entry_header(header_text):
    """Fields: index, status, sender, alpha, timestamp, tosca, fo, pid, dcs, ... (quoted fields may contain commas)."""
    header_fields = next(csv.reader([header_text.removeprefix(LIST_ENTRY_PREFIX).strip()]))
    if len(header_fields) < 5:
        return None
    try:
        sim_index = int(header_fields[0])
    except ValueError:
        return None
    try:
        data_coding_scheme = int(header_fields[DATA_CODING_SCHEME_FIELD])
    except (IndexError, ValueError):
        data_coding_scheme = 0
    return sim_index, header_fields[1], header_fields[2], header_fields[4], data_coding_scheme


def parse_sms_list_reply(reply_text):
    received_messages = []
    current_header, current_body_lines = None, []

    def finish_current_entry():
        if current_header is None:
            return
        sim_index, status, sender_number, operator_timestamp, data_coding_scheme = current_header
        if status not in RECEIVED_STATUSES:
            return
        message_text = decode_body("\n".join(current_body_lines).strip("\r\n"), data_coding_scheme)
        received_messages.append(
            ReceivedSms(sim_index, sender_number, message_text, parse_operator_timestamp(operator_timestamp))
        )

    reply_lines = reply_text.replace("\r\n", "\n").rstrip().split("\n")
    if reply_lines[-1].strip() == "OK":  # the command's final status line, not part of the last message
        reply_lines.pop()
    for raw_line in reply_lines:
        if raw_line.startswith(LIST_ENTRY_PREFIX):
            finish_current_entry()
            current_header, current_body_lines = parse_list_entry_header(raw_line), []
        elif current_header is not None:
            current_body_lines.append(raw_line)
    finish_current_entry()
    return received_messages
