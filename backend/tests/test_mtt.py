"""Tests for GGPoker tournament (MTT) hand histories.

Tournament exports differ from cash tables in three ways that used to make
the parser reject them outright:

  * the header carries the event name where cash tables carry the blinds, and
    the blinds move into a level tag: `Level13(350/700(85))`;
  * amounts drop the dollar sign (`collected 13,906 from pot`) because chips
    are not currency;
  * amounts carry thousands separators.

Both shapes share the same action lines, so the invariant still holds:
`sum(won) == -total_fees`.
"""

from decimal import Decimal

from app.api.import_hands import _compute_financials
from app.parsers.ggpoker import parse_hand_history

# Verbatim export: 6-max hyper, level 13 (350/700 with an 85 ante), hero
# shoves and loses the flip.
MTT_HAND = """\
Poker Hand #TM6224769827: Tournament #300704318, Daily Hyper $1 Hold'em No Limit - Level13(350/700(85)) - 2026/07/26 21:28:22
Table '107' 6-max Seat #5 is the button
Seat 1: f9bfeb2e (5,266 in chips)
Seat 2: Hero (6,258 in chips)
Seat 3: 3289c06f (7,690 in chips)
Seat 4: 8d445358 (9,865 in chips)
Seat 5: d2c2d0d0 (13,965 in chips)
Seat 6: c970caf8 (25,176 in chips)
8d445358: posts the ante 85
c970caf8: posts the ante 85
Hero: posts the ante 85
3289c06f: posts the ante 85
f9bfeb2e: posts the ante 85
d2c2d0d0: posts the ante 85
c970caf8: posts small blind 350
f9bfeb2e: posts big blind 700
*** HOLE CARDS ***
Dealt to f9bfeb2e 
Dealt to Hero [Ac Ts]
Dealt to 3289c06f 
Dealt to 8d445358 
Dealt to d2c2d0d0 
Dealt to c970caf8 
Hero: raises 5,473 to 6,173 and is all-in
3289c06f: raises 1,432 to 7,605 and is all-in
8d445358: folds
d2c2d0d0: folds
c970caf8: folds
f9bfeb2e: folds
Uncalled bet (1,432) returned to 3289c06f
3289c06f: shows [As 9s]
Hero: shows [Ac Ts]
*** FLOP *** [9c Jc 9h]
*** TURN *** [9c Jc 9h] [7s]
*** RIVER *** [9c Jc 9h 7s] [Qh]
*** SHOWDOWN ***
3289c06f collected 13,906 from pot
*** SUMMARY ***
Total pot 13,906 | Rake 0 | Jackpot 0 | Bingo 0 | Fortune 0 | Tax 0
Board [9c Jc 9h 7s Qh]
Seat 1: f9bfeb2e (big blind) folded before Flop
Seat 2: Hero showed [Ac Ts] and lost with a pair of Nines
Seat 3: 3289c06f showed [As 9s] and won (13,906) with three of a kind, Nines
Seat 4: 8d445358 folded before Flop
Seat 5: d2c2d0d0 (button) folded before Flop
Seat 6: c970caf8 (small blind) folded before Flop
"""


class TestMttHand:
    def test_header_blinds_come_from_the_level_tag(self):
        p = parse_hand_history(MTT_HAND)
        assert p.hand_id == "TM6224769827"
        assert p.sb_amount == Decimal("350")
        assert p.bb_amount == Decimal("700")
        assert p.stakes == "MTT 350/700"

    def test_header_is_recognised_as_a_tournament(self):
        p = parse_hand_history(MTT_HAND)
        assert p.game_mode == "MTT"
        assert p.table_size == 6
        assert p.button_seat == 5

    def test_thousands_separators_are_stripped(self):
        p = parse_hand_history(MTT_HAND)
        assert p.collected["3289c06f"] == Decimal("13906")
        assert p.uncalled_returns["3289c06f"] == Decimal("1432")
        assert p.total_rake == Decimal("0")

    def test_positions_follow_the_button(self):
        p = parse_hand_history(MTT_HAND)
        pos = {s["username"]: s["position"] for s in p.seats}
        assert pos["d2c2d0d0"] == "BTN"
        assert pos["c970caf8"] == "SB"
        assert pos["f9bfeb2e"] == "BB"
        assert pos["Hero"] == "UTG"

    def test_balance_invariant_holds(self):
        p = parse_hand_history(MTT_HAND)
        invested, _, share = _compute_financials(p)
        total = sum(
            p.collected.get(u, Decimal("0"))
            + p.uncalled_returns.get(u, Decimal("0"))
            - invested[u]
            - share.get(u, Decimal("0"))
            for u in invested
        )
        assert total == -p.total_fees

    def test_hero_loses_exactly_their_stack(self):
        """Hero shoved 6,258 (ante included) and collected nothing."""
        p = parse_hand_history(MTT_HAND)
        invested, _, share = _compute_financials(p)
        net = (
            p.collected.get("Hero", Decimal("0"))
            + p.uncalled_returns.get("Hero", Decimal("0"))
            - invested["Hero"]
            - share.get("Hero", Decimal("0"))
        )
        assert invested["Hero"] == Decimal("6258")
        assert net == Decimal("-6258")
