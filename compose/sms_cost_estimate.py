"""Pure: estimated cost of a campaign for the SIMs that are plugged in."""
from dataclasses import dataclass

from common.french_labels import format_message_count, format_number


@dataclass(frozen=True)
class SimSmsPrice:
    operator_display_name: str
    sms_cost_francs: int


def describe_estimated_costs(message_count, sim_prices, sms_count=None):
    """One French line per distinct SIM operator with a known price; empty when nothing can be estimated.
    `sms_count` is the number of SMS the operator bills (a long or Unicode message takes several); by default one each."""
    billed_sms_count = message_count if sms_count is None else sms_count
    if message_count <= 0 or billed_sms_count <= 0:
        return []
    if billed_sms_count == message_count:
        quantity_text = format_message_count(message_count)
    else:
        quantity_text = f"{format_message_count(message_count)} en {format_number(billed_sms_count)} SMS"
    return [
        f"Coût estimé avec {price.operator_display_name} : {quantity_text} × "
        f"{format_number(price.sms_cost_francs)} F = {format_number(billed_sms_count * price.sms_cost_francs)} FCFA"
        for price in sim_prices
    ]
