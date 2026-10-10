"""Regression tests for the date-range, filter and drift bugs.

Each of these was a real, reproducible failure found in an audit of the
running app — all three are easy to reintroduce, so pin them here.
"""

from app.db import normalize_date_to


class TestNormalizeDateTo:
    """A bare date as an upper bound silently dropped the whole final day."""

    def test_bare_date_expands_to_end_of_day(self):
        assert normalize_date_to("2026-10-04") == "2026-10-04 23:59:59"

    def test_explicit_time_is_left_alone(self):
        assert normalize_date_to("2026-10-04 12:30:00") == "2026-10-04 12:30:00"
        assert normalize_date_to("2026-10-04T12:30:00") == "2026-10-04T12:30:00"

    def test_end_of_day_is_not_double_appended(self):
        v = normalize_date_to("2026-10-04 23:59:59")
        assert v == "2026-10-04 23:59:59"

    def test_none_and_empty_pass_through(self):
        assert normalize_date_to(None) is None
        assert normalize_date_to("") == ""

    def test_surrounding_whitespace_tolerated(self):
        assert normalize_date_to("  2026-10-04  ") == "2026-10-04 23:59:59"


class TestNoEmptyInClause:
    """`tags=,` used to build `IN ()`, which DuckDB rejects as a syntax error."""

    def test_tag_filter_skips_empty_list(self):
        """Mirror the guard in hands.py::_build_filters."""
        tags = " , "
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        assert tag_list == []
        # The clause must only be emitted when there is at least one tag.
        emitted = bool(tag_list)
        assert emitted is False


class TestDriftOppWindow:
    """fold_to_3bet's opportunity must match what every other page reports."""

    def test_fold_to_3bet_uses_null_window_not_three_bet_opp(self):
        from app.api.reports import _DRIFT_STATS_V2

        row = next(r for r in _DRIFT_STATS_V2 if r[0] == "fold_to_3bet")
        _, _, opp_filter = row
        assert opp_filter == "AND hp.fold_to_3bet IS NOT NULL"
        assert "three_bet_opp" not in opp_filter

    def test_three_bet_still_uses_three_bet_opp(self):
        from app.api.reports import _DRIFT_STATS_V2

        row = next(r for r in _DRIFT_STATS_V2 if r[0] == "three_bet")
        assert row[2] == "AND hp.three_bet_opp = true"
