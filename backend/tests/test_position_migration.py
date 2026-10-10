"""Tests for the EP/MP -> current-label data migration.

Positions are a function of (seat order, button seat, seat count), so the
migration re-derives them. A plain string rename would be wrong: 7/8/9-handed
tables also used EP/MP, and the current tables put UTG1/LJ/UTG2 in the slots
EP/MP used to occupy, so renaming produced duplicate labels.
"""

import duckdb
import pytest

from app.db import init_schema, _migrate_position_labels
from app.parsers.common import POSITIONS_BY_COUNT


# (seat_count, labels the OLD parser would have written, button seat)
LEGACY_SHAPES = {
    "H9": ["BTN", "SB", "BB", "UTG", "UTG1", "EP", "MP", "HJ", "CO"],
    "H8": ["BTN", "SB", "BB", "UTG", "EP", "MP", "HJ", "CO"],
    "H7": ["BTN", "SB", "BB", "EP", "MP", "HJ", "CO"],
    "H6": ["BTN", "SB", "BB", "EP", "MP", "CO"],
    "H5": ["BTN", "SB", "BB", "MP", "CO"],
}


def _seed(conn, shapes=LEGACY_SHAPES, button_seat=1):
    pid = 0
    for hid, poss in shapes.items():
        conn.execute(
            "INSERT INTO hands (id, site_id, played_at, game_type, stakes, "
            "sb_amount, bb_amount, table_size, button_seat, workspace_id) "
            "VALUES (?, 1, now(), 'NLH', 's', 0.02, 0.05, ?, ?, 1)",
            [hid, len(poss), button_seat],
        )
        for i, pos in enumerate(poss, start=1):
            pid += 1
            conn.execute(
                "INSERT INTO hand_players (id, hand_id, player_id, seat, "
                "position, workspace_id) VALUES (?, ?, ?, ?, ?, 1)",
                [pid, hid, pid, i, pos],
            )


@pytest.fixture()
def conn():
    c = duckdb.connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def _positions(c, hid):
    return [
        r[0]
        for r in c.execute(
            "SELECT position FROM hand_players WHERE hand_id = ? ORDER BY seat", [hid]
        ).fetchall()
    ]


class TestPositionLabelMigration:
    @pytest.mark.parametrize("hid", list(LEGACY_SHAPES))
    def test_result_matches_current_tables(self, conn, hid):
        _seed(conn)
        _migrate_position_labels(conn)
        expected = POSITIONS_BY_COUNT[len(LEGACY_SHAPES[hid])]
        assert _positions(conn, hid) == expected

    @pytest.mark.parametrize("hid", list(LEGACY_SHAPES))
    def test_no_duplicate_labels(self, conn, hid):
        """The naive rename collides on 7/8/9-handed hands — this guards that."""
        _seed(conn)
        _migrate_position_labels(conn)
        got = _positions(conn, hid)
        assert len(got) == len(set(got))

    def test_no_legacy_labels_remain(self, conn):
        _seed(conn)
        _migrate_position_labels(conn)
        left = conn.execute(
            "SELECT COUNT(*) FROM hand_players WHERE position IN ('EP', 'MP')"
        ).fetchone()[0]
        assert left == 0

    def test_idempotent(self, conn):
        _seed(conn)
        _migrate_position_labels(conn)
        first = {h: _positions(conn, h) for h in LEGACY_SHAPES}
        _migrate_position_labels(conn)
        assert {h: _positions(conn, h) for h in LEGACY_SHAPES} == first

    def test_rotation_follows_button_seat(self, conn):
        """A different button seat must rotate the labels, not shift them."""
        _seed(conn, shapes={"H6": LEGACY_SHAPES["H6"]}, button_seat=3)
        _migrate_position_labels(conn)
        # Seats 1..6 with the button on seat 3, so the clockwise order is
        # 3,4,5,6,1,2 and the labels land BTN,SB,BB,UTG,HJ,CO in that order.
        # Read back by seat number that is: seat1=HJ, 2=CO, 3=BTN, 4=SB,
        # 5=BB, 6=UTG.
        assert _positions(conn, "H6") == [
            "HJ", "CO", "BTN", "SB", "BB", "UTG",
        ]

    def test_hand_without_button_seat_is_skipped(self, conn):
        """No button seat means no derivable position — leave it untouched."""
        _seed(conn, shapes={"H6": LEGACY_SHAPES["H6"]}, button_seat=3)
        conn.execute("UPDATE hands SET button_seat = NULL WHERE id = 'H6'")
        _migrate_position_labels(conn)
        assert set(_positions(conn, "H6")) == {"EP", "MP", "BTN", "SB", "BB", "CO"}

    def test_already_migrated_db_is_untouched(self, conn):
        """A fresh DB has no legacy labels, so the migration is a no-op."""
        _seed(conn, shapes={"H6": POSITIONS_BY_COUNT[6]})
        before = _positions(conn, "H6")
        _migrate_position_labels(conn)
        assert _positions(conn, "H6") == before
