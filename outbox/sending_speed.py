"""Sending speed presets: the random pause between two messages. Pure, no UI."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SendingSpeed:
    speed_key: str
    french_label: str
    gap_seconds_range: tuple


# Prepaid SIMs that send too fast risk being blocked by the operator: the default stays the most cautious one.
SENDING_SPEEDS = (
    SendingSpeed("fast", "Rapide (≈ 1 s entre deux SMS)", (1.0, 1.5)),
    SendingSpeed("moderate", "Modérée (2 à 3 s)", (2.0, 3.0)),
    SendingSpeed("careful", "Prudente (5 à 10 s)", (5.0, 10.0)),
)
DEFAULT_SENDING_SPEED_KEY = "careful"


def find_sending_speed(speed_key):
    """The preset for a stored key; an unknown or missing key gives the cautious default."""
    for sending_speed in SENDING_SPEEDS:
        if sending_speed.speed_key == speed_key:
            return sending_speed
    return find_sending_speed(DEFAULT_SENDING_SPEED_KEY)
