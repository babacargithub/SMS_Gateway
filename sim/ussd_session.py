"""Pure: the messages exchanged between the interactive USSD dialer (UI thread) and the dongle monitor thread."""
import re
from dataclasses import dataclass
from typing import Optional

REQUEST_KIND_SEND = "send"
REQUEST_KIND_CLOSE_SESSION = "close_session"

# What can be typed: a code (*222#, #111#) or a menu choice (1, 2...). Only characters whose GSM code equals their ASCII code
# survive the dongle's 7-bit encoding, and digits, * # + are all a menu or a code ever needs.
ALLOWED_USSD_INPUT_PATTERN = re.compile(r"[0-9*#+]+")


def is_valid_ussd_input(typed_text):
    return ALLOWED_USSD_INPUT_PATTERN.fullmatch(typed_text) is not None


@dataclass
class UssdRequest:
    kind: str
    request_text: str = ""
    usb_location: Optional[str] = None   # None: the first ready dongle. An answer must go back to the dongle holding the session.
    starts_new_session: bool = False     # True: close whatever session is open first, so a new code is not taken as a menu answer


@dataclass
class UssdExchangeResult:
    usb_location: Optional[str]
    sent_text: str
    reply_text: str = ""
    is_session_open: bool = False        # a menu is waiting for the next answer
    error_message: Optional[str] = None
    imei: Optional[str] = None
