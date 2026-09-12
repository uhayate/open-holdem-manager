"""Guards for the graceful-shutdown path.

Killing the backend (`backendProcess.kill()` in Electron) is TerminateProcess on
Windows: Python's atexit never runs, so `close_db()` never runs, DuckDB never
checkpoints and a half-written, unreplayable WAL is left behind — which then
bricks every later launch. These tests pin the two halves of the fix: the
shutdown endpoint asks uvicorn to exit, and long-running writers bail out
instead of holding the DB lock until they are killed.
"""
import asyncio

import pytest

from app.db import (
    ShutdownInterrupted,
    STAT_VERSION,
    _shutdown_requested,
    request_shutdown,
    shutdown_requested,
)


@pytest.fixture(autouse=True)
def _reset_shutdown_flag():
    """The flag is process-global, so leaking it would break other tests."""
    _shutdown_requested.clear()
    yield
    _shutdown_requested.clear()


def test_request_shutdown_sets_the_flag():
    assert shutdown_requested() is False
    request_shutdown()
    assert shutdown_requested() is True


def test_shutdown_endpoint_asks_uvicorn_to_exit(monkeypatch):
    from app.main import app, shutdown_server

    class FakeServer:
        should_exit = False

    fake = FakeServer()
    monkeypatch.setattr(app.state, "uvicorn_server", fake, raising=False)

    result = asyncio.run(shutdown_server())

    assert fake.should_exit is True
    assert shutdown_requested() is True
    assert result == {"status": "shutting-down"}


def test_shutdown_endpoint_without_a_server_still_requests_shutdown(monkeypatch):
    """Plain `uvicorn app.main:app` (dev) has no server instance to stop."""
    from app.main import app, shutdown_server

    monkeypatch.delattr(app.state, "uvicorn_server", raising=False)

    assert asyncio.run(shutdown_server()) == {"status": "no-server"}
    assert shutdown_requested() is True


def _add_one_hand(db):
    db.execute(
        "INSERT INTO hands "
        "(id, site_id, played_at, game_type, stakes, sb_amount, bb_amount, raw_text, workspace_id) "
        "VALUES ('SHUTDOWN-TEST-1', 1, CURRENT_TIMESTAMP, 'HoldemNL', "
        "'NL2', 0.01, 0.02, 'placeholder, never parsed', 1)"
    )


def test_rebuild_aborts_without_touching_data(db):
    """A rebuild that meets a shutdown must unwind, not run to completion."""
    from app.api.import_hands import _run_rebuild_sync

    _add_one_hand(db)
    # Make the rebuild look stale so it has real work to do
    db.execute(
        "INSERT OR REPLACE INTO settings VALUES ('stat_version', ?)", [str(STAT_VERSION - 1)]
    )

    before = db.execute("SELECT COUNT(*) FROM hands").fetchone()[0]

    request_shutdown()
    with pytest.raises(ShutdownInterrupted):
        _run_rebuild_sync(db)

    # What both callers do
    db.execute("ROLLBACK")

    assert db.execute("SELECT COUNT(*) FROM hands").fetchone()[0] == before
    # Rollback must leave the connection usable
    assert db.execute("SELECT COUNT(*) FROM hand_players").fetchone()[0] >= 0
    # ...and must restore the indexes dropped inside the transaction
    assert db.execute(
        "SELECT COUNT(*) FROM duckdb_indexes() WHERE table_name = 'hand_players'"
    ).fetchone()[0] > 0


def test_abort_import_rolls_back_and_raises(db):
    from app.api.import_hands import _abort_import

    db.execute("BEGIN TRANSACTION")
    db.execute("DELETE FROM hand_players")

    with pytest.raises(ShutdownInterrupted):
        _abort_import(db)

    # Transaction closed and the connection still works
    assert db.execute("SELECT COUNT(*) FROM hand_players").fetchone()[0] == 0
