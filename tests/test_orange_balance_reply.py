import unittest

from sim.mobile_operators import KNOWN_OPERATORS, parse_balance_with_patterns

ORANGE_BALANCE_REPLY = (
    "Credit global: 162881F,compteur recharge 17050F avec une validite illimitee."
    "Tapez 0 pour le detail:date de validite et montant de chaque compteur.Quitter:1"
)


class OrangeBalanceReplyTest(unittest.TestCase):
    def setUp(self):
        self.orange_operator = next(operator for operator in KNOWN_OPERATORS if operator.key == "orange")

    def test_reads_the_global_credit_not_the_recharge_counter(self):
        balance = parse_balance_with_patterns(ORANGE_BALANCE_REPLY, self.orange_operator.balance_reply_patterns)
        self.assertEqual(balance, 162881)

    def test_still_reads_the_older_credit_sentence(self):
        balance = parse_balance_with_patterns(
            "Vous avez 200 Fcfa de credit", self.orange_operator.balance_reply_patterns
        )
        self.assertEqual(balance, 200)

    def test_orange_sms_costs_ten_francs(self):
        self.assertEqual(self.orange_operator.sms_cost_francs, 10)


if __name__ == "__main__":
    unittest.main()
