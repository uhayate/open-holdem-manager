"""Tests for Cash Drop accounting.

A Cash Drop is money GG injects into the pot. Nobody invests it, but the
winner's `collected` includes their share of it — so without an explicit
correction the drop is booked as profit and inflates bb/100.

The invariant that pins this down: for every hand,
`sum(won) == -total_fees`. A Cash Drop hand must satisfy it too.
"""

from decimal import Decimal

import pytest

from app.api.import_hands import _compute_financials
from app.parsers.ggpoker import parse_hand_history


# Verbatim GG export: `Cash Drop to Pot : total $0.2` before the blinds, with
# hero shoving 10x the BB and getting an uncalled bet back.
CASH_DROP_HAND = """\
Poker Hand #RC4853847351: Hold'em No Limit ($0.01/$0.02) - 2026/10/05 16:37:40
Table 'RushAndCash2292472' 6-max Seat #1 is the button
Seat 1: 586c57a3 ($2.56 in chips)
Seat 2: Hero ($2.21 in chips)
Seat 3: f23bf3f5 ($1.02 in chips)
Seat 4: b2ef4a3d ($2.4 in chips)
Seat 5: c4cda8b1 ($0.7 in chips)
Seat 6: f1960e2a ($1.94 in chips)
Cash Drop to Pot : total $0.2
Hero: posts small blind $0.01
f23bf3f5: posts big blind $0.02
*** HOLE CARDS ***
Dealt to 586c57a3
Dealt to Hero [6c Ac]
Dealt to f23bf3f5
Dealt to b2ef4a3d
Dealt to c4cda8b1
Dealt to f1960e2a
b2ef4a3d: raises $0.02 to $0.04
c4cda8b1: calls $0.04
f1960e2a: folds
586c57a3: calls $0.04
Hero: raises $2.17 to $2.21 and is all-in
f23bf3f5: folds
b2ef4a3d: folds
c4cda8b1: calls $0.66 and is all-in
586c57a3: folds
Uncalled bet ($1.51) returned to Hero
Hero: shows [6c Ac]
c4cda8b1: shows [3d 9d]
*** FLOP *** [5s 2d 8s]
*** TURN *** [5s 2d 8s] [Jc]
*** RIVER *** [5s 2d 8s Jc] [Td]
*** SHOWDOWN ***
Hero collected $1.61 from pot
*** SUMMARY ***
Total pot $1.7 | Rake $0.06 | Jackpot $0.03 | Bingo $0 | Fortune $0 | Tax $0
Board [5s 2d 8s Jc Td]
Seat 1: 586c57a3 (button) folded before Flop
Seat 2: Hero (small blind) showed [6c Ac] and won ($1.61) with Ace high
Seat 3: f23bf3f5 (big blind) folded before Flop
Seat 4: b2ef4a3d folded before Flop
Seat 5: c4cda8b1 showed [3d 9d] and lost with Jack high
Seat 6: f1960e2a folded before Flop (didn't bet)
"""

# A real hand from the same site with NO Cash Drop — the control. It has to be
# a verbatim export: deleting the "Cash Drop" line out of a hand that had one
# does NOT produce a valid hand, because the pot and fee figures stop matching
# the action lines (and the balance invariant then fails for a fake reason).
NO_DROP_HAND = """\
Poker Hand #RC4844523357: Hold'em No Limit ($0.01/$0.02) - 2026/10/01 22:37:22
Table 'RushAndCash41547515' 6-max Seat #1 is the button
Seat 1: a22f6536 ($1.97 in chips)
Seat 2: 24eaf76d ($1.04 in chips)
Seat 3: Hero ($2 in chips)
Seat 4: 1918cfdf ($2 in chips)
Seat 5: 367968d0 ($2.07 in chips)
Seat 6: 4e5dba5b ($2.22 in chips)
24eaf76d: posts small blind $0.01
Hero: posts big blind $0.02
*** HOLE CARDS ***
Dealt to a22f6536
Dealt to 24eaf76d
Dealt to Hero [4c 6s]
Dealt to 1918cfdf
Dealt to 367968d0
Dealt to 4e5dba5b
1918cfdf: folds
367968d0: folds
4e5dba5b: raises $0.02 to $0.04
a22f6536: folds
24eaf76d: calls $0.03
Hero: folds
*** FLOP *** [Js 3c 2d]
24eaf76d: checks
4e5dba5b: bets $0.04
24eaf76d: raises $0.06 to $0.1
4e5dba5b: folds
Uncalled bet ($0.06) returned to 24eaf76d
*** SHOWDOWN ***
24eaf76d collected $0.18 from pot
*** SUMMARY ***
Total pot $0.18 | Rake $0 | Jackpot $0 | Bingo $0 | Fortune $0 | Tax $0
Board [Js 3c 2d]
Seat 1: a22f6536 (button) folded before Flop (didn't bet)
Seat 2: 24eaf76d (small blind) won ($0.18)
Seat 3: Hero (big blind) folded before Flop
Seat 4: 1918cfdf folded before Flop (didn't bet)
Seat 5: 367968d0 folded before Flop (didn't bet)
Seat 6: 4e5dba5b folded on the Flop
"""


def _net_total(parsed, invested, share) -> Decimal:
    return sum(
        parsed.collected.get(u, Decimal("0"))
        + parsed.uncalled_returns.get(u, Decimal("0"))
        - invested[u]
        - share.get(u, Decimal("0"))
        for u in invested
    )


class TestCashDrop:
    def test_drop_is_detected(self):
        p = parse_hand_history(CASH_DROP_HAND)
        assert p.cash_drop_received == Decimal("0.2")
        assert parse_hand_history(NO_DROP_HAND).cash_drop_received == 0

    def test_drop_does_not_create_money(self):
        """sum(won) must equal -fees even with a Cash Drop.

        Before the fix this hand produced +0.11 against -0.09 of fees: the
        $0.20 drop was booked as profit.
        """
        p = parse_hand_history(CASH_DROP_HAND)
        invested, _, share = _compute_financials(p)
        assert _net_total(p, invested, share) == -p.total_fees

    def test_no_drop_hand_is_unchanged(self):
        p = parse_hand_history(NO_DROP_HAND)
        invested, _, share = _compute_financials(p)
        # No drop means no share is withheld.
        assert not any(share.values())
        assert _net_total(p, invested, share) == -p.total_fees

    def test_drop_shares_sum_to_the_drop(self):
        """The withheld shares must add back up to exactly the drop amount."""
        p = parse_hand_history(CASH_DROP_HAND)
        invested, _, share = _compute_financials(p)
        assert float(sum(share.values())) == pytest.approx(
            float(p.cash_drop_received)
        )

    def test_drop_is_split_pro_rata_by_investment(self):
        """The biggest investor gives up the biggest slice of the drop."""
        p = parse_hand_history(CASH_DROP_HAND)
        invested, _, share = _compute_financials(p)
        # Hero shoved 2.21 of the 3.02 total, so Hero should give up the most.
        biggest = max(invested, key=lambda u: invested[u])
        assert biggest == "Hero"
        assert share["Hero"] == max(share.values())

    def test_folded_player_still_gets_no_free_money(self):
        """A player who folded preflop invested only the blind.

        Their share of the drop is proportional to that blind, so it can't
        flip a small loss into a win.
        """
        p = parse_hand_history(CASH_DROP_HAND)
        invested, _, share = _compute_financials(p)
        # f1960e2a folded without posting a blind at all.
        assert invested["f1960e2a"] == Decimal("0")
        assert share["f1960e2a"] == 0
