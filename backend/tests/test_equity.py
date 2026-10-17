"""All-in equity must be reproducible across rebuilds.

Preflop equity is Monte Carlo sampled. With an unseeded RNG, re-importing the
same hand gave a slightly different number every time, which made a
"Rebuild Stats" before/after comparison meaningless.
"""

from app.equity import calculate_headsup_equity


class TestEquityDeterminism:
    def test_preflop_is_reproducible_without_an_explicit_seed(self):
        args = (("Ah", "Kd"), ("Qs", "Ts"), [])
        first = calculate_headsup_equity(*args)
        second = calculate_headsup_equity(*args)
        assert first == second

    def test_different_matchups_do_not_share_a_stream(self):
        # A shared global seed would make these identical; they must not be.
        a = calculate_headsup_equity(("Ah", "Kd"), ("Qs", "Ts"), [])
        b = calculate_headsup_equity(("2c", "7d"), ("Qs", "Ts"), [])
        assert a != b

    def test_explicit_seed_overrides_the_matchup_hash(self):
        args = (("Ah", "Kd"), ("Qs", "Ts"), [])
        assert calculate_headsup_equity(*args, seed=1234) == calculate_headsup_equity(*args, seed=1234)

    def test_known_matchup_lands_in_the_right_range(self):
        # AA vs 72o is roughly 88% -- far enough from the 0.5 default that an
        # inverted or broken evaluator cannot sneak through.
        eq = calculate_headsup_equity(("Ah", "Ad"), ("7c", "2d"), [])
        assert 0.85 < eq < 0.92

    def test_river_is_exact(self):
        # River all-in is enumerated, not sampled: AA on this board beats 72o
        # outright, so the answer must be exactly 1.0.
        eq = calculate_headsup_equity(("Ah", "Ad"), ("7c", "2d"), ["Qs", "9d", "3c", "8h", "2s"])
        assert eq == 1.0

    def test_flop_is_enumerated_not_sampled(self):
        # ~990 runouts on a flop, all of them evaluated -> deterministic even
        # without a seed, and independent of num_samples.
        board = ["Qs", "9d", "3c"]
        a = calculate_headsup_equity(("Ah", "Ad"), ("7c", "2d"), board, num_samples=10)
        b = calculate_headsup_equity(("Ah", "Ad"), ("7c", "2d"), board, num_samples=9999)
        assert a == b
        assert 0.8 < a < 1.0
