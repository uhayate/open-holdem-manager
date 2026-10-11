"""Shared parser types and utilities.

Contains the ParsedHand dataclass and position-assignment logic used by the
site parsers.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime

_ZERO = Decimal("0")


@dataclass
class ParsedHand:
    """Output of parsing a single hand history. Contains all extracted data."""
    hand_id: str
    site_id: int
    played_at: datetime
    game_type: str
    game_mode: str  # "Fast Fold" or ""
    stakes: str
    sb_amount: Decimal
    bb_amount: Decimal
    table_name: str
    table_size: int
    button_seat: int
    seats: list[dict]  # [{seat, username, stack, position}]
    actions_by_street: dict[str, list[dict]]
    board_cards: dict[str, list[str]]
    hero_cards: dict[str, tuple[str, str]]
    uncalled_returns: dict[str, Decimal]
    collected: dict[str, Decimal]
    total_rake: Decimal
    total_jackpot: Decimal
    # Every fee GG lists on the summary line (Rake + Jackpot + Bingo +
    # Fortune + Tax). `total_rake` is pure Rake; this is what the pot/EV
    # math wants, and it is what `sum(net_won)` must equal as a negative.
    total_fees: Decimal
    went_to_showdown_players: set[str]
    in_showdown: bool
    sb_player: str | None
    bb_player: str | None
    raw_text: str
    cash_drop_received: Decimal = _ZERO
    extra_boards: list[dict[str, list[str]]] = field(default_factory=list)
    rit_boards: int = 1       # 1=normal, 2=RIT, 3=RIT3
    is_cashout: bool = False


# Position labels, ordered clockwise starting at BTN.
# Ante tables can seat fewer players than the table's max — the label set is
# chosen by how many players actually showed up, so a 6-max that dealt 5 hands
# still reads as a 5-handed board rather than leaving a hole.
#
# 6/8/9-handed labels are the agreed convention:
#   6-max  BTN SB BB UTG HJ CO
#   8-max  BTN SB BB UTG UTG1 LJ HJ CO
#   9-max  BTN SB BB UTG UTG1 UTG2 LJ HJ CO
# 5- and 7-handed sets are interpolated by trimming the deepest early position
# and are the most likely to need revisiting once real traffic shows up.
POSITIONS_BY_COUNT = {
    2: ["BTN", "BB"],
    3: ["BTN", "SB", "BB"],
    4: ["BTN", "SB", "BB", "CO"],
    5: ["BTN", "SB", "BB", "UTG", "CO"],
    6: ["BTN", "SB", "BB", "UTG", "HJ", "CO"],
    7: ["BTN", "SB", "BB", "UTG", "UTG1", "HJ", "CO"],
    8: ["BTN", "SB", "BB", "UTG", "UTG1", "LJ", "HJ", "CO"],
    9: ["BTN", "SB", "BB", "UTG", "UTG1", "UTG2", "LJ", "HJ", "CO"],
}

# Nominal 6-max label set — used as the fallback when the seat count is
# outside the 2..9 range the table above covers.
POSITIONS_6MAX = ["BTN", "SB", "BB", "UTG", "HJ", "CO"]

# Every label the parser can emit, ordered from the blinds up to the button.
# Positional-stats aggregation and IP/OOP ordering both key off this, so any
# label in POSITIONS_BY_COUNT has to appear here too.
ALL_POSITIONS = [
    "SB", "BB", "UTG", "UTG1", "UTG2", "LJ", "HJ", "CO", "BTN",
]

# Order to render position columns and tables in: earliest to act first, blinds
# last (UTG, UTG1, ..., CO, BTN, SB, BB). A rotation of ALL_POSITIONS — derive
# it rather than writing a second list, so tables can't drift out of sync.
# Note this is NOT sorted alphabetically: plain `ORDER BY position` puts BB
# first and UTG last, which reads as noise in a HUD table.
POSITION_DISPLAY_ORDER = ALL_POSITIONS[2:] + ALL_POSITIONS[:2]


def _assign_positions(seats: list[dict], button_seat: int, table_size: int) -> None:
    """Assign position labels to seated players based on button seat.

    Sorts players clockwise from button and assigns positions.
    seats is a list of dicts with 'seat' key, mutated to add 'position'.
    """
    if not seats:
        return

    num_players = len(seats)
    pos_labels = POSITIONS_BY_COUNT.get(num_players)
    if pos_labels is None:
        # Fallback for unusual counts
        pos_labels = POSITIONS_BY_COUNT.get(min(num_players, 9), POSITIONS_6MAX)

    # Sort seats clockwise starting from button
    seat_numbers = sorted(s["seat"] for s in seats)
    # Find button index
    btn_idx = None
    for i, sn in enumerate(seat_numbers):
        if sn == button_seat:
            btn_idx = i
            break
    if btn_idx is None:
        # Button seat not occupied — find the closest seat before button going backwards
        # (the seat that would act as button)
        for i in range(len(seat_numbers) - 1, -1, -1):
            if seat_numbers[i] < button_seat:
                btn_idx = i
                break
        if btn_idx is None:
            btn_idx = len(seat_numbers) - 1

    # Reorder clockwise from button
    ordered = seat_numbers[btn_idx:] + seat_numbers[:btn_idx]

    seat_to_pos = {}
    for i, sn in enumerate(ordered):
        if i < len(pos_labels):
            seat_to_pos[sn] = pos_labels[i]
        else:
            seat_to_pos[sn] = f"S{sn}"

    for s in seats:
        s["position"] = seat_to_pos[s["seat"]]
