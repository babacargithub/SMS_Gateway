"""Pure parsing of the dongle's `AT+CMGL=4` reply in PDU mode. No USB, no UI."""
import logging

from inbox.sms_pdu import PduDecodeError, decode_deliver_pdu

LIST_ENTRY_PREFIX = "+CMGL:"
RECEIVED_STATUSES = (0, 1)  # 0 received unread, 1 received read. Stored drafts and sent copies (2, 3) are left alone on the SIM.


def parse_list_entry_header(header_text):
    """'+CMGL: 3,1,,25' -> (3, 1). None when unreadable."""
    header_fields = header_text.removeprefix(LIST_ENTRY_PREFIX).split(",")
    try:
        return int(header_fields[0]), int(header_fields[1])
    except (IndexError, ValueError):
        return None


def parse_pdu_list_reply(reply_text):
    """Every received message part on the SIM, as SmsPart. An entry that cannot be decoded is logged and left on the SIM
    (never deleted, so nothing is lost silently)."""
    reply_lines = [line.strip() for line in reply_text.replace("\r\n", "\n").split("\n")]
    received_parts = []
    for line_position, line in enumerate(reply_lines):
        if not line.startswith(LIST_ENTRY_PREFIX):
            continue
        header = parse_list_entry_header(line)
        pdu_line = next((candidate for candidate in reply_lines[line_position + 1:line_position + 3] if candidate), "")
        if header is None or header[1] not in RECEIVED_STATUSES or not pdu_line or pdu_line.startswith(LIST_ENTRY_PREFIX):
            continue
        try:
            received_part = decode_deliver_pdu(header[0], pdu_line)
        except PduDecodeError as decoding_error:
            logging.warning("SIM slot %s could not be decoded, left on the SIM: %s", header[0], decoding_error)
            continue
        if received_part is not None:
            received_parts.append(received_part)
    return received_parts
