import unittest

from compose.sms_cost_estimate import SimSmsPrice, describe_estimated_costs
from sim.mobile_operators import KNOWN_OPERATORS


class SmsCostEstimateTest(unittest.TestCase):
    def test_orange_and_expresso_prices(self):
        prices = {operator.key: operator.sms_cost_francs for operator in KNOWN_OPERATORS}
        self.assertEqual(prices["orange"], 10)
        self.assertEqual(prices["expresso"], 40)

    def test_line_per_sim_with_french_formatting(self):
        lines = describe_estimated_costs(1234, [SimSmsPrice("Orange", 10), SimSmsPrice("Expresso", 40)])
        self.assertEqual(lines[0], "Coût estimé avec Orange : 1\u202f234 messages × 10 F = 12\u202f340 FCFA")
        self.assertIn("49\u202f360 FCFA", lines[1])

    def test_nothing_without_messages_or_prices(self):
        self.assertEqual(describe_estimated_costs(0, [SimSmsPrice("Orange", 10)]), [])
        self.assertEqual(describe_estimated_costs(5, []), [])


if __name__ == "__main__":
    unittest.main()
