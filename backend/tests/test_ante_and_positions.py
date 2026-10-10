"""Tests for GG ante tables (9-max cash) and the 6/8/9-max position labels.

The `ante_9max.txt` fixture is a verbatim GG hand-history export: 9-max
$0.02/$0.05 with a $0.01 ante, header written as `($0.02/$0.05($0.01))` and
each blind line reading `posts the ante`.
"""

from decimal import Decimal

import pytest

from app.parsers.common import POSITIONS_BY_COUNT, ALL_POSITIONS
from app.parsers.ggpoker import parse_hand_history, split_hands


ANTE_FIXTURE = "ante_9max.txt"


def _ante_hands() -> list[str]:
    text = open(
        __import__("pathlib").Path(__file__).parent / "fixtures" / "ggpoker" / ANTE_FIXTURE,
        encoding="utf-8",
    ).read()
    return split_hands(text)


class TestAnteTable:
    """A table with an ante must parse, not raise."""

    def test_header_with_ante_parses(self):
        hands = _ante_hands()
        assert len(hands) == 8
        for h in hands:
            parsed = parse_hand_history(h)  # used to raise ValueError

    def test_stakes_not_halved_blind(self):
        """Regression: sb_amount was hardcoded to bb/2.

        On an ante table GG runs $0.02/$0.05, so bb/2 = $0.25 — wrong, and it
        also corrupted the derived stakes string.
        """
        parsed = parse_hand_history(_ante_hands()[0])
        assert parsed.bb_amount == Decimal("0.05")
        assert parsed.sb_amount == Decimal("0.02")
        assert parsed.stakes == "$0.02/$0.05"

    def test_ante_lines_are_captured(self):
        """`posts the ante` (with the article) must be recognized."""
        parsed = parse_hand_history(_ante_hands()[0])
        antes = [a for a in parsed.actions_by_street["preflop"] if a["action"] == "ante"]
        assert len(antes) == 9
        assert all(a["amount"] == Decimal("0.01") for a in antes)

    def test_ante_not_treated_as_voluntary_action(self):
        """An ante is forced, so it must not make anyone look VPIP."""
        from app.stat_flags import compute_stat_flags

        parsed = parse_hand_history(_ante_hands()[0])
        flags = compute_stat_flags(parsed)
        # Hero is seat 5 in this hand and folds preflop; only ante + fold.
        hero_preflop = [
            a for a in parsed.actions_by_street["preflop"]
            if a["username"] == "Hero"
        ]
        assert [a["action"] for a in hero_preflop] == ["ante", "fold"]
        assert flags["Hero"]["vpip"] is False
        assert flags["Hero"]["pfr"] is False

    def test_pot_balances(self):
        """Sum of net results must equal -total_fees for every hand.

        This is the invariant that catches an ante being dropped or
        double-counted anywhere in the invest/collect pipeline.
        """
        from app.api.import_hands import _compute_financials

        for h in _ante_hands():
            parsed = parse_hand_history(h)
            invested, _, _ = _compute_financials(parsed)
            total = Decimal("0")
            for uname, inv in invested.items():
                gross = parsed.collected.get(uname, Decimal("0"))
                back = parsed.uncalled_returns.get(uname, Decimal("0"))
                total += gross + back - inv
            assert total == -parsed.total_fees, (
                f"pot does not balance: {total} != {-parsed.total_fees}"
            )

    def test_rake_excludes_jackpot(self):
        """Rake and Jackpot are siblings, not components.

        Regression: total_rake used to sum Rake|Jackpot|Bingo|Fortune|Tax, and
        the caller then deducted per_player_jackpot on top — charging the
        jackpot twice.
        """
        hands = [parse_hand_history(h) for h in _ante_hands()]
        # This fixture has at least one hand with a non-zero jackpot.
        assert any(p.total_jackpot > 0 for p in hands), "fixture lost its jackpot"

        for p in hands:
            # total_rake is pure Rake, so it never exceeds the combined fees.
            assert p.total_rake <= p.total_fees
            # And the two columns together reconstruct the total fee exactly.
            assert p.total_rake + p.total_jackpot <= p.total_fees

    def test_fee_split_does_not_double_count(self):
        """End-to-end: rake + jackpot charged per winner == the real fee."""
        from app.api.import_hands import _compute_financials

        for h in _ante_hands():
            parsed = parse_hand_history(h)
            num_winners = max(len(parsed.collected), 1)
            charged = float(parsed.total_rake) / num_winners + float(
                parsed.total_jackpot
            ) / num_winners
            expected = float(parsed.total_fees) / num_winners
            # float division, so compare with a tolerance
            assert charged == pytest.approx(expected, abs=1e-9)

    def test_ante_is_counted_as_invested(self):
        """Each ante must land in player_invested, or folding would look free."""
        from app.api.import_hands import _compute_financials

        parsed = parse_hand_history(_ante_hands()[0])
        invested, _, _ = _compute_financials(parsed)
        # Hero is seat 5, not a blind: invested == ante only.
        assert invested["Hero"] == Decimal("0.01")

    def test_blinds_identified_on_ante_table(self):
        parsed = parse_hand_history(_ante_hands()[0])
        assert parsed.sb_player is not None
        assert parsed.bb_player is not None
        assert parsed.sb_player != parsed.bb_player


class TestPositionLabels:
    """The 6/8/9-max naming convention."""

    def test_6max_labels(self):
        assert POSITIONS_BY_COUNT[6] == ["BTN", "SB", "BB", "UTG", "HJ", "CO"]

    def test_8max_labels(self):
        assert POSITIONS_BY_COUNT[8] == [
            "BTN", "SB", "BB", "UTG", "UTG1", "LJ", "HJ", "CO",
        ]

    def test_9max_labels(self):
        assert POSITIONS_BY_COUNT[9] == [
            "BTN", "SB", "BB", "UTG", "UTG1", "UTG2", "LJ", "HJ", "CO",
        ]

    @pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8, 9])
    def test_label_count_matches_seat_count(self, n):
        assert len(POSITIONS_BY_COUNT[n]) == n

    @pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8, 9])
    def test_no_legacy_labels(self, n):
        """EP/MP are gone — they were ambiguous across table sizes."""
        for label in POSITIONS_BY_COUNT[n]:
            assert label not in ("EP", "MP")

    @pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8, 9])
    def test_labels_known_to_all_positions(self, n):
        """Every emitted label must be aggregatable / orderable."""
        for label in POSITIONS_BY_COUNT[n]:
            assert label in ALL_POSITIONS, f"{label} missing from ALL_POSITIONS"

    def test_all_positions_unique_and_ordered(self):
        assert len(ALL_POSITIONS) == len(set(ALL_POSITIONS))
        # Blinds are the most out-of-position, BTN the most in-position.
        assert ALL_POSITIONS.index("SB") < ALL_POSITIONS.index("BB")
        assert ALL_POSITIONS.index("BB") < ALL_POSITIONS.index("CO")
        assert ALL_POSITIONS.index("CO") < ALL_POSITIONS.index("BTN")

    def test_9max_ante_hand_assigns_expected_positions(self):
        """End-to-end: the real 9-max fixture must use the new labels."""
        parsed = parse_hand_history(_ante_hands()[0])
        by_seat = {s["seat"]: s["position"] for s in parsed.seats}
        # Button is seat 2, so clockwise: 2=BTN 3=SB 4=BB 5=UTG ...
        assert parsed.button_seat == 2
        assert by_seat[2] == "BTN"
        assert by_seat[3] == "SB"
        assert by_seat[4] == "BB"
        assert by_seat[5] == "UTG"
        assert by_seat[1] == "CO"
