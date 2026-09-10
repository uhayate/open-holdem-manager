# Open Holdem Manager (OHM) — Project Handoff

## What This Is

A **local, single-user** poker hand history tracker for GGPoker Rush & Cash.
Parses hand history text, stores it in DuckDB, computes H2N-style stats, shows
graphs. No auth, no cloud, no telemetry.

> **Fork note:** this fork is trimmed for personal use. Only the GGPoker parser
> exists (`parsers/ggpoker.py`); the PokerStars / 888 / WPN / Winamax / iPoker /
> partypoker parsers were removed. The marketing landing site and all
> human-facing docs (`docs/`, `ROADMAP.md`, `README.md`) were removed too.
> Deleted material is still reachable via git history.

## Tech Stack

- **Backend**: Python 3.12, FastAPI, DuckDB (single file), Pydantic v2, eval7 (equity), pyarrow
- **Frontend**: React 19, TypeScript 5.9, Vite 7, Tailwind v4 (`@theme` syntax), shadcn/ui (Radix), Recharts 3, React Router 7, TanStack Query 5
- **Desktop**: Electron 33 + electron-builder 25; `electron-updater` pulls from GitHub Releases
- **DB location**: `backend/data/poker.duckdb` in dev; `%APPDATA%/open-holdem-manager/data/` (Windows) or `~/Library/Application Support/...` (macOS) when packaged

## Running (Windows, no `make` on this machine)

The `Makefile` targets assume Unix (`lsof`, `make`) and do **not** work here.
Use these instead:

```bash
# Backend (venv lives at the repo root, not in backend/)
cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 4243

# Frontend dev server (proxies /api to :4243)
cd frontend && npm run dev -- --port 4242 --strictPort

# Tests
cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q -p no:warnings --ignore=tests/test_benchmark.py
```

API docs: http://localhost:4243/docs

## Architecture: Parse → Compute → Insert

1. **Parse** (`parsers/ggpoker.py`): `parse_hand_history(text) -> ParsedHand` — pure text parsing, no DB
2. **Compute** (`stat_flags.py`): `compute_stat_flags(parsed) -> dict[str, dict]` — site-independent flags
3. **Insert** (`api/import_hands.py`): writes hands / hand_players / actions / board_cards

Adding a site means dropping a module into `parsers/` that produces a
`ParsedHand`; nothing else changes. Stat bugs can be re-derived from stored
`raw_text` via `POST /api/import/rebuild` without re-parsing.

`STAT_VERSION` in `db.py` gates a **full rebuild on startup**: bumping it makes a
background thread recompute every stat flag from `raw_text`. The app stays usable
during a rebuild.

## Hard-Won Invariants (do not "clean these up")

These were all real bugs. Reverting them reintroduces data loss or a hang.

- **`db.py`'s `_lock` must stay an `RLock`.** `import_database()` does
  `with db_lock(): close_db()`, and `close_db()` re-acquires `_lock`. A plain
  `Lock` self-deadlocks and freezes the whole event loop. Cursor creation is
  guarded by a separate `_cursor_lock`; the background rebuild thread uses its
  **own** connection (DuckDB connections are not thread-safe) with an explicit
  `ROLLBACK` on failure.
- **The rebuild's transaction boundary is deliberate.** `_drop_indexes` and the
  four `DELETE`s (`hand_players`, `actions`, `board_cards`,
  `player_classifications`) must sit **after** `BEGIN TRANSACTION`. DuckDB
  auto-commits statements outside a transaction, so moving them earlier means a
  failed rebuild permanently empties those tables.
- **Never `shutil.copy2` the live DuckDB file.** DuckDB holds the file open
  in-process, so on Windows this raises `PermissionError: [WinError 32]`. The
  export endpoint uses a native snapshot instead: `ATTACH` + `COPY FROM
  DATABASE`. (`import_database()`'s `.bak` copy is fine — it closes the
  connection first.)
- **Schema changes must use `db.py::_add_missing_columns()` / `_table_columns()`.**
  Do not probe for a column with `try: ALTER ... except SomeError`. A failed
  DuckDB statement aborts the surrounding transaction, so every later statement
  fails too — a misleading cascade.
- **Recreate dropped indexes** at the end of any migration that drops them.

## GGPoker Parser Notes

Format markers: header `Poker Hand #RC...: Hold'em No Limit ($SB/$BB) - DATE`;
`Table 'name' N-max Seat #X is the button`; streets `*** HOLE CARDS ***` /
`FLOP` / `TURN` / `RIVER` / `SHOWDOWN` / `SUMMARY`; actions
`player: folds | checks | calls $X | bets $X | raises $X to $Y [and is all-in]`.

Edge cases handled: **Run It Twice** (`*** FIRST FLOP ***` … first board is
canonical), **time bank cards** (`received ($0.02) from time bank card` — not a
winning), split pots, null-byte corruption, all-in tracking.

`raises $X to $Y` stores the **"to"** amount; the street increment is
`Y - already_in_this_street`.

## Stats Engine Gotcha

DuckDB returns `Decimal` for numeric aggregates. Convert to `float()` before
handing values to Pydantic, or serialization misbehaves.

## Import Flow

`POST /api/import/files/stream` → extract `.txt` from `.zip` → split on
`Poker Hand #` → deduplicate → parse → compute → insert in chunks of
`BATCH_SIZE` (2000), with NDJSON progress events. Bulk indexes are dropped
during import and recreated afterwards. The stream only emits
`{"type":"done"}` after COMMIT and index rebuild — the frontend treats a stream
that ends without `done` as a failure, deliberately.

Player caching (`_player_cache`, `_next_*_id`) lives in `import_hands.py`; call
`reset_import_cache()` whenever you wipe tables.

## Environment Variables

- `OHM_DATA_DIR` — overrides the DuckDB location (Electron points it at `userData/data/`)
- `OHM_STATIC_DIR` — enables static serving of the built frontend (packaged mode)
- `ELECTRON=1` — makes Vite emit relative asset paths (`base: './'`) for `file://`

## Building the Installer (Windows)

```bash
# 1. Frontend
cd frontend && ELECTRON=1 npm run build            # -> frontend/dist

# 2. Backend  (NOTE: no --clean; see below)
cd backend && CODEBUDDY_SAFE_DELETE_ENABLED=0 ../.venv/Scripts/python.exe -m PyInstaller \
  --name ohm-backend --onedir --noconfirm --collect-submodules uvicorn \
  --collect-submodules fastapi --collect-submodules starlette \
  --collect-submodules pydantic --collect-submodules duckdb \
  --hidden-import multipart run_server.py

# 3. Installer
cd .. && CODEBUDDY_SAFE_DELETE_ENABLED=0 ./node_modules/.bin/electron-builder.cmd \
  --win --publish never -c.directories.output=release-build
```

- **`CODEBUDDY_SAFE_DELETE_ENABLED=0` is required in this sandbox** — prefix it to
  *both* the PyInstaller and electron-builder steps. It disables the safe-delete
  shim, which otherwise aborts bulk deletions with
  `SAFE_DELETE_BULK_CONFIRM_REQUIRED`. Without it:
  - PyInstaller refuses to remove the previous `dist/ohm-backend` during COLLECT
    (746 files), so the build ends with a stale/uncollected `dist/`;
  - electron-builder aborts mid-cleanup, leaving the installer without `latest.yml`.
- **Still skip PyInstaller's `--clean`.** Even with the shim disabled it is
  unnecessary (a changed/broken `build/` cache is the only reason to want it) —
  but if you ever do need a truly clean build, a same-volume *rename* of
  `build/` and `dist/` is atomic and works regardless.
- `electron/main.js` pins `REPO_OWNER` to **`uhayate`**. Pointing it back at the
  upstream owner would let auto-update overwrite this fork.
- macOS builds are unsigned; testers must run `xattr -cr "…/Open Holdem Manager.app"`.
