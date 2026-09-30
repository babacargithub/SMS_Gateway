"""Pure: the mobile operators the gateway knows (Senegal), how to recognise one from what the dongle reports, and
which USSD code / reply format gives its balance.

Recognition prefers the numeric network code (MCC+MNC, e.g. "60802"), which never changes, over the name string
(which varies: "expresso", "Orange", "FREE"...). Codes and reply formats can be overridden without touching the code,
in `operators.json` next to the database, e.g. {"orange": {"balance_ussd_code": "#123#"}}."""
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Tuple

OPERATORS_OVERRIDE_FILE = Path.home() / "Library" / "Application Support" / "SMS Gateway" / "operators.json"

# "Vous avez 200 Fcfa de credit": the trusted sentence is the one that says "de crédit". The advert in the same reply
# also contains amounts (offers to buy), so a bare "any number followed by FCFA" is never accepted.
AMOUNT_TEXT = r"(\d[\d\s.,  ]*?)"
CURRENCY_TEXT = r"(?:f\s?cfa|xof|f)"
AMOUNT_THEN_CREDIT_PATTERN = rf"{AMOUNT_TEXT}\s*{CURRENCY_TEXT}\s+de\s+cr[eé]dit"
LABEL_THEN_AMOUNT_PATTERN = rf"(?:solde|cr[eé]dit(?:\s+principal)?)\s*(?:est\s*)?(?:de\s*)?[:=]?\s*{AMOUNT_TEXT}\s*{CURRENCY_TEXT}\b"


@dataclass(frozen=True)
class MobileOperator:
    key: str                                   # stable identifier, also the key in operators.json
    display_name: str
    network_codes: Tuple[str, ...]             # MCC+MNC
    name_aliases: Tuple[str, ...]              # casefolded substrings of the name the SIM reports
    balance_ussd_code: Optional[str]
    balance_reply_patterns: Tuple[str, ...]    # regexes; group 1 = the amount
    balance_code_verified: bool                # False until the owner confirmed it on a real SIM


KNOWN_OPERATORS = (
    MobileOperator(
        key="expresso",
        display_name="Expresso",
        network_codes=("60803",),
        name_aliases=("expresso",),
        balance_ussd_code="*222#",
        balance_reply_patterns=(AMOUNT_THEN_CREDIT_PATTERN,),
        balance_code_verified=True,
    ),
    # UNVERIFIED defaults: the code and the reply wording must be confirmed on a real SIM. If a check shows
    # « Réponse de l'opérateur non comprise », the Sim screen prints the raw reply so the pattern can be fixed.
    MobileOperator(
        key="orange",
        display_name="Orange",
        network_codes=("60801",),
        name_aliases=("orange", "sonatel"),
        balance_ussd_code="#123#",
        balance_reply_patterns=(AMOUNT_THEN_CREDIT_PATTERN, LABEL_THEN_AMOUNT_PATTERN),
        balance_code_verified=False,
    ),
    MobileOperator(
        key="free",
        display_name="Free",
        network_codes=("60802",),
        name_aliases=("free", "tigo", "saga"),
        balance_ussd_code="*222#",
        balance_reply_patterns=(AMOUNT_THEN_CREDIT_PATTERN, LABEL_THEN_AMOUNT_PATTERN),
        balance_code_verified=False,
    ),
)


def apply_overrides(operators, overrides):
    """`overrides` is {operator key: {"balance_ussd_code": ..., "balance_reply_patterns": [...]}}. Unknown keys and
    fields are ignored; a code override counts as verified because the owner chose it."""
    customised_operators = []
    for operator in operators:
        operator_overrides = overrides.get(operator.key)
        if not isinstance(operator_overrides, dict):
            customised_operators.append(operator)
            continue
        changes = {}
        ussd_code = operator_overrides.get("balance_ussd_code")
        if isinstance(ussd_code, str) and ussd_code.strip():
            changes["balance_ussd_code"] = ussd_code.strip()
            changes["balance_code_verified"] = True
        reply_patterns = operator_overrides.get("balance_reply_patterns")
        if isinstance(reply_patterns, list) and all(isinstance(pattern, str) for pattern in reply_patterns) and reply_patterns:
            changes["balance_reply_patterns"] = tuple(reply_patterns)
        customised_operators.append(replace(operator, **changes))
    return tuple(customised_operators)


def load_operators(overrides_file=OPERATORS_OVERRIDE_FILE):
    """Known operators with the owner's overrides applied. A missing or broken file means 'no overrides'."""
    try:
        overrides = json.loads(Path(overrides_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return KNOWN_OPERATORS
    return apply_overrides(KNOWN_OPERATORS, overrides) if isinstance(overrides, dict) else KNOWN_OPERATORS


def identify_operator(network_code, reported_name, operators):
    """The operator for this SIM, or None. The numeric network code wins over the name."""
    if network_code:
        for operator in operators:
            if network_code in operator.network_codes:
                return operator
    if reported_name:
        lowered_name = reported_name.casefold()
        for operator in operators:
            if any(alias in lowered_name for alias in operator.name_aliases):
                return operator
    return None


def parse_balance_with_patterns(operator_reply_text, reply_patterns):
    """Whole francs from the reply using the first pattern that matches, or None."""
    for reply_pattern in reply_patterns:
        try:
            balance_match = re.search(reply_pattern, operator_reply_text, re.IGNORECASE)
        except re.error:
            continue  # a broken pattern in operators.json must not break the screen
        if balance_match is None:
            continue
        amount_text = re.sub(r"[.,]\d{1,2}$", "", balance_match.group(1).strip())  # drop centimes
        amount_digits = re.sub(r"\D", "", amount_text)
        if amount_digits:
            return int(amount_digits)
    return None
