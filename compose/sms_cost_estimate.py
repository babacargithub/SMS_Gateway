"""Pure: estimated cost of a campaign for the SIMs that are plugged in."""
from dataclasses import dataclass

from common.french_labels import format_message_count, format_number


@dataclass(frozen=True)
class SimSmsPrice:
    operator_display_name: str
    sms_cost_francs: int


def describe_estimated_costs(message_count, sim_prices):
    """One French line per distinct SIM operator with a known price; empty when nothing can be estimated."""
    if message_count <= 0:
        return []
    return [
        f"Coût estimé avec {price.operator_display_name} : {format_message_count(message_count)} × "
        f"{format_number(price.sms_cost_francs)} F = {format_number(message_count * price.sms_cost_francs)} FCFA"
        for price in sim_prices
    ]
