"""Pure: the result of one balance check, plus the retry rule for transient operator failures.
Which USSD code and reply format apply to a SIM is decided by `mobile_operators.py`."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

TRANSIENT_FAILURE_MARKERS = ("ratee", "ratée")  # "Operation ratee!" -> ask again a few seconds later


@dataclass
class SimBalanceResult:
    usb_location: str
    imei: Optional[str]
    operator_display_name: Optional[str]
    checked_at: datetime
    balance_fcfa: Optional[int] = None
    error_message: Optional[str] = None
    operator_reply_text: str = ""  # kept only to diagnose a reply that could not be understood
    used_ussd_code: Optional[str] = None
    is_ussd_code_verified: bool = True


def is_transient_failure_reply(operator_reply_text):
    lowered_reply = operator_reply_text.casefold()
    return any(marker in lowered_reply for marker in TRANSIENT_FAILURE_MARKERS)
