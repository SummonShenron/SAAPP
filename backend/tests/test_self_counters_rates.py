import unittest

from backend.utils import self_counters


class TestTurnEndAndChoiceRates(unittest.TestCase):
    def test_turn_end_and_last_step_rates_share_all_turns_that_ended(self):
        rates = self_counters._rates({
            "turn_end_answered": 6,
            "turn_end_ran_out_progress": 2,
            "turn_end_ran_out_lost": 1,
            "turn_end_looped": 1,
            "last_step_progressing": 2,
            "last_step_stalled": 1,
        })
        self.assertAlmostEqual(rates["turn_ran_out_progress"], 0.2)
        self.assertAlmostEqual(rates["turn_ran_out_lost"], 0.1)
        self.assertAlmostEqual(rates["last_step_progressing"], 0.2)
        self.assertAlmostEqual(rates["last_step_stalled"], 0.1)

    def test_no_turn_ends_gives_no_rate_rather_than_zero(self):
        rates = self_counters._rates({"turns_total": 5})
        self.assertIsNone(rates["turn_ran_out_lost"])
        self.assertIsNone(rates["last_step_stalled"])

    def test_choice_picked_is_share_of_asks_and_asked_is_share_of_turns(self):
        rates = self_counters._rates({"turns_total": 10, "choice_asked": 4, "choice_picked": 1})
        self.assertAlmostEqual(rates["choice_asked"], 0.4)
        self.assertAlmostEqual(rates["choice_picked"], 0.25)


if __name__ == "__main__":
    unittest.main()
