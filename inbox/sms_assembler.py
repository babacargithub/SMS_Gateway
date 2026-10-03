"""Pure: turn the message parts found on the SIM into complete messages.

A long SMS arrives as several parts. A message is only stored (and its SIM slots freed) once every part is here, so the
text is never cut. If a part never arrives, the message is stored anyway after a grace period, with a « […] » marker where
the missing part was, so the SIM cannot fill up with orphan parts."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

MISSING_PART_MARKER = " […] "
INCOMPLETE_MESSAGE_GRACE_PERIOD = timedelta(minutes=10)


@dataclass
class ReceivedSms:
    sim_indexes: list        # every SIM slot holding a part of it: all are freed once the message is stored
    sender_number: str
    message_text: str
    received_at: str         # 'YYYY-MM-DD HH:MM:SS', as stamped by the operator


def assemble_received_messages(message_parts, now_utc):
    """Complete messages, oldest first. Parts of a message still waiting for its other parts are simply not returned:
    they stay on the SIM and are looked at again on the next read."""
    assembled_messages = [
        ReceivedSms([part.sim_index], part.sender_number, part.text, part.received_at)
        for part in message_parts
        if not part.is_part_of_long_message
    ]
    parts_by_message = defaultdict(list)
    for part in message_parts:
        if part.is_part_of_long_message:
            parts_by_message[(part.sender_number, part.concatenation_reference, part.concatenation_total)].append(part)

    for (sender_number, _reference, total_parts), parts_of_message in parts_by_message.items():
        part_by_sequence, spare_copy_indexes = {}, []
        for part in sorted(parts_of_message, key=lambda candidate: candidate.concatenation_sequence):
            if part.concatenation_sequence in part_by_sequence:
                spare_copy_indexes.append(part.sim_index)   # the same part received twice: keep one, free both slots
            else:
                part_by_sequence[part.concatenation_sequence] = part
        is_complete = len(part_by_sequence) == total_parts
        newest_part_time = max(part.sent_at_utc for part in parts_of_message)
        if not is_complete and now_utc - newest_part_time < INCOMPLETE_MESSAGE_GRACE_PERIOD:
            continue
        message_text = "".join(
            part_by_sequence[sequence].text if sequence in part_by_sequence else MISSING_PART_MARKER
            for sequence in range(1, total_parts + 1)
        )
        assembled_messages.append(ReceivedSms(
            [part.sim_index for part in part_by_sequence.values()] + spare_copy_indexes,
            sender_number,
            message_text,
            min(part.received_at for part in parts_of_message),
        ))
    return sorted(assembled_messages, key=lambda message: message.received_at)
