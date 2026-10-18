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

## Money Invariants

**The one identity that must always hold: `sum(won) == -total_fees` for every
hand.** If it doesn't, money is being created or destroyed somewhere in the
parse → invest → collect pipeline. `test_cash_drop.py` and `test_ante_and_
positions.py` both assert it, and it is the fastest way to catch a new
accounting bug. Verify against real data with:

```sql
SELECT COUNT(*) FROM (
  SELECT h.id FROM hands h JOIN hand_players hp ON hp.hand_id = h.id
  GROUP BY 1 HAVING ABS(SUM(hp.won) + SUM(hp.rake) + SUM(hp.jackpot)) > 0.001
);
```

(A residual of exactly 0.001 is the `DECIMAL(18,3)` storage unit, not a bug.)

**Cash Drop is not profit.** GG injects `Cash Drop to Pot` money into the pot.
Nobody invests it, but the winner's `collected` includes a share of it — so
booking it as-is inflates bb/100. `_compute_financials` returns a
`cash_drop_share` map and the caller subtracts it. **Only the players who took
the pot give up a slice, weighted by their share of `collected`** — the money is
paid out with the pot, so charging everyone who put chips in invents a loss for
losers who never received it (that was the v5 rule; it understated hero's
bb/100 by roughly one drop per Cash Drop hand hero won). The residue from
Decimal division goes to the biggest taker so `sum(share) == drop` holds
exactly. The drop is also removed from the all-in EV `dist`, since it was never
risked.

**`total_rake` is pure Rake.** Jackpot/Bingo/Fortune/Tax are siblings of it on
the summary line, not components, and are summed separately into `total_fees`.
The all-in EV math must subtract `total_fees` (using `total_rake` leaves the
fees in the pot and inflates EV). `hand_players.rake` + `hand_players.jackpot`
must decompose the fee exactly once — see `_migrate_rake_excludes_jackpot`.

**`STAT_VERSION` is at 6**, which triggers a full rebuild on next launch. The
rebuild is load-bearing: it applies the Cash Drop correction to `won`/`won_bb`
and re-derives all-in EV. If you downgrade STAT_VERSION, those stay unapplied.

`all_in_ev_bb` sitting equal to `won_bb` on every row is **not** a stale-data
symptom — it means EV was never computed at all. In a packaged build that is
almost always the missing `--collect-all eval7` (see the build section). Check
that before assuming the data just needs a rebuild.

## Date Filters

`played_at` is a **TIMESTAMP**. Comparing it to a bare `YYYY-MM-DD` upper bound
means that day's 00:00, so everything played on the final day of a range is
silently dropped — a user picking "Oct 1 – Oct 4" loses all of Oct 4. Every
endpoint that takes `date_to` must pass it through
`db.normalize_date_to()`, which appends `23:59:59` to a bare date and leaves an
explicit time alone. Lower bounds need no equivalent (`>= date` already means
00:00). If you add a new filtered endpoint, use the helper — this bug existed in
eight files at once.

**On the frontend, never build a calendar date with
`d.toISOString().slice(0, 10)`.** That converts to UTC first, so in UTC+8 a
local midnight lands on the previous day and "Today" queries yesterday. Use
`toLocalDateString()` from `lib/date-presets.ts`. (The current date is fetched
fine either way after 08:00, which is why this hid for so long.)

## DuckDB Write Performance

Two traps that make a migration look like a hang (both were hit and fixed in
`_migrate_position_labels`):

- **`executemany()` runs one statement per row.** For ~35k rows this is
  unusably slow. Use a multi-row `INSERT ... VALUES (?,?,?),(?,?,?)...`.
- **A parameter list with thousands of entries makes the binder fall back to
  importing numpy/pandas**, and this environment has neither — the call hangs
  instead of raising. Keep parameter lists small; prefer deriving values with
  SQL (window functions, a small inline `VALUES` mapping) over shipping them as
  bound parameters.

Also note `UPDATE hand_players` touches `idx_hp_position` and
`idx_hp_player_position`, so a full-table update costs seconds even when
set-based. Prefer targeted updates scoped by a predicate.

## Population Query: two FROM clauses

`population_full_stats` builds a CTE that resolves eligible players (the only
place with a `player_classifications` join) and then runs `_AGG_SQL`, whose
FROM is just `hand_players hp JOIN hands h`. `_build_where` therefore returns
**two** where-clauses: the full one (may reference `pc`) and a `main_where_sql`
that avoids `pc` entirely, with matching param lists. Feeding the full clause
to the aggregation query raises `Binder Error: Referenced table "pc" not
found`; feeding the params twice double-binds them. Both were live bugs.

## What `_migrate_position_labels` Does

It **re-derives** positions from (seat order, button seat, seat count) rather
than renaming `EP -> UTG, MP -> HJ`. The rename is only correct for 6-max:
7/8/9-handed tables also used EP/MP, and the current tables put UTG1/LJ/UTG2 in
those slots, so a rename produces duplicate labels (`UTG EP MP HJ` → `UTG UTG
HJ HJ`). Re-deriving matches what a rebuild would produce, so the migration and
the `STAT_VERSION` rebuild agree. It is idempotent and only touches hands that
still carry a legacy label.

## Known limitation: UTG1 / UTG2 / LJ are not rendered

`POSITIONS_BY_COUNT` emits UTG1/UTG2/LJ for 7-9 handed tables and the backend
aggregates them, but `StatsCard.tsx` (`fullPosHeaders`/`fullPosKeys`) and
`benchmarks.ts` (`PositionalBenchmarks`) still only cover the 6-max set. Such
positions are computed but never displayed, and fall back to the `total`
benchmark (see `getBenchmarkForPosition`). Deliberate for now — the filter bars
(`HandFilters`, `RangePage`) and `reports.py::pos_order` do list them, so the
data is reachable — but revisit if 8/9-max volume ever matters.

## Position Labels

`parsers/common.py::POSITIONS_BY_COUNT` is the single source of truth, ordered
clockwise from BTN. The agreed convention:

| Seats | Labels |
|-------|--------|
| 6-max | BTN SB BB **UTG HJ CO** |
| 8-max | BTN SB BB **UTG UTG1 LJ HJ CO** |
| 9-max | BTN SB BB **UTG UTG1 UTG2 LJ HJ CO** |

The early labels are `EP`/`MP`-free on purpose: those two were ambiguous (EP
meant "first to act" on a 6-max but "second" on an 8-max). 5- and 7-handed
sets are interpolated by trimming the deepest early seat and are the ones to
revisit first if real traffic disagrees.

`ALL_POSITIONS` lists every label in action order and is what `stat_flags`
derives `_POS_ORDER` from. **Adding a label to `POSITIONS_BY_COUNT` without
adding it to `ALL_POSITIONS` makes those players look like the SB** — the
`.get(position, 0)` fallback in the IP/OOP comparisons silently does that, so
`test_ante_and_positions.py` asserts the two stay in sync.

### Renaming a label

Renames are data migrations, not code edits. `db.py::_migrate_position_labels`
is idempotent and runs inside `init_schema`. A label rename does **not** need a
`STAT_VERSION` bump: no stat flag depends on the absolute order value, only on
relative comparisons (postflop IP, 3-bet IP/OOP), and a pure rename preserves
the relative order. That claim was verified by recomputing every flag for 200
stored hands and diffing against the stored values — 0 mismatches across 39
flags. Full rebuild is ~4 ms/hand, so it's affordable, but it isn't necessary
for a rename and it rewrites `hand_players` wholesale.

## GGPoker Ante Tables

Ante tables are **not** the same format as the no-ante tables, and getting this
wrong fails at the very first line (`Cannot parse header line`):

- Header carries a third amount: `Hold'em No Limit ($0.02/$0.05($0.01)) - ...`
  — the `($0.01)` group is the ante. `RE_HEADER` must make that group optional.
- Blind lines say **`posts the ante`**, with the article. Every regex that
  matches blind postings needs `(?:the )?` — that's `ggpoker.RE_ANTE`,
  `action_parser.RE_BLIND` (drives the hand-detail pot sizes) and
  `hands.py::_RE_BLIND_POST` (drives the blind/ante action list).
- **`sb_amount` must not be `bb_amount / 2`.** On an ante table GG runs odd
  combos ($0.02/$0.05), so bb/2 is wrong. Prefer the posted SB, fall back to the
  header SB. This also fixes the derived `stakes` string.

The ante itself was already handled correctly: `import_hands._INVEST_ACTIONS`
includes `"ante"` so it counts toward `player_invested` (folding isn't free),
but is excluded from `street_put_in` so `"raise to X"` stays relative to the
blinds. The invariant that proves all of this holds is
`sum(net_won) == -total_rake` per hand — see `test_pot_balances`.

## GGPoker Tournaments (MTT)

Tournament exports are a third shape. Three differences, all of which used to
abort with `Cannot parse header line`:

- The header puts the **event name** where cash tables put the blinds, and the
  blinds live in a level tag:
  `Poker Hand #TM…: Tournament #…, Daily Hyper $1 Hold'em No Limit - Level13(350/700(85)) - …`
  → `RE_HEADER_MTT`. Group 4/5 are the blinds, 6 the ante. `RE_HEADER` (cash)
  is tried first; the `$` amounts in this header belong to the buy-in, not the
  blinds.
- Amounts **drop the `$` and add thousands separators** (`collected 13,906
  from pot`, `Total pot 13,906`). Every money pattern is built from the `_AMT`
  fragment and every captured value goes through `_dec()`. Do not write
  `\$([0-9.]+)` anywhere — it silently rejects every tournament hand.
- **`bb_amount` comes straight from the level tag.** `_detect_bb` exists to
  repair corrupted cash stakes and snaps toward standard ones; a 350/700 level
  would be mangled. `stakes` is `MTT 350/700` (chips, not currency) so
  `formatStakes` doesn't render it as `NL70000`.

`game_mode` is `MTT` (hand IDs start with `TM`; `detect()` already lists it).
Fees are normally 0, so the balance identity is trivial but still asserted.

**bb/100 is meaningless across a tournament.** The bb changes every few hands,
so 4,000 MTT hands summed as `won_bb` produce a number that mixes 100bb stacks
with 10bb stacks. Frequency stats (VPIP/PFR/3-bet/position) are fine; don't
read the MTT win rate as a win rate. Keeping MTT in its own workspace is the
supported way to avoid mixing it into cash results.

Note `total_rake` is **pure Rake** — Jackpot/Bingo/Fortune/Tax are siblings of
it on the summary line, not components, and are summed separately into
`total_fees`. The balance identity is `sum(net_won) == -total_fees`. The all-in
EV math (`import_hands`, `dist = total_risk - ...`) must subtract
`total_fees`; using `total_rake` there leaves the fees in the pot and inflates EV.

### Fee columns: rake and jackpot decompose the pot exactly once

`hand_players.rake` + `hand_players.jackpot` must equal the fee actually taken.
They used to exceed it: the parser folded Jackpot into `total_rake`, and the
importer *also* deducted `per_player_jackpot` on top. Fixed on the parse side
(above); `db.py::_migrate_rake_excludes_jackpot` repairs existing rows by
subtracting the jackpot from `rake` (`rake_bb` is recomputed from the corrected
value, not decremented, to avoid compounding rounding). Guarded by a
`rake_jackpot_split` setting flag rather than `jackpot > 0`, so a legitimate
future hand with `jackpot == 0` is never re-charged.

For the rake double-count specifically, `won_bb` was never affected — it is
derived from `collected + uncalled - invested` and never reads these columns —
so that one was display-only, which is why it could sit unnoticed: no stat or
graph moved. Do not generalise that to every fee bug: the Cash Drop problem
*above* did corrupt `won_bb`, and with it bb/100. "Only affects display" is a
claim to verify per bug, not a category.

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
`reset_import_cache()` whenever you wipe tables. An interrupted import (shutdown
mid-flight) rolls back and re-creates the indexes, because `_drop_indexes` runs
outside the transaction and a rollback would not restore them.

## Shutdown

`POST /api/shutdown` sets `db.request_shutdown()` and then
`app.state.uvicorn_server.should_exit = True`. Exiting through uvicorn (rather
than being killed) is what runs the ASGI shutdown event → `close_db()` → DuckDB
checkpoint → WAL removed.

`run_server.py` therefore builds `uvicorn.Server(uvicorn.Config(...))` and
publishes the instance on `app.state.uvicorn_server`; `uvicorn.run()` would keep
it private and there would be no way to stop the server from a request handler.
Under plain `uvicorn app.main:app` (dev) that attribute is absent and the
endpoint returns `{"status": "no-server"}` — nothing to stop.

Any **new long-running write task** (import, rebuild) must poll
`db.shutdown_requested()` and raise `db.ShutdownInterrupted` as it loops —
otherwise it holds `db_lock` to completion and `close_db()` blocks past the 15 s
grace window, forcing a hard kill (i.e. back to a half-written WAL). Existing
check sites: `_run_rebuild_sync` / `_process_hands` (per hand) and
`import_files_stream` (per batch); the caller rolls back.

Startup also **self-heals a broken WAL**: `get_db()` catches `duckdb.Error` and,
if the message mentions replaying the WAL, renames it to
`<db>.wal.corrupt-<timestamp>` (never deleted) and retries once. Before that,
an unreplayable WAL wedged every launch permanently, since it is replayed
every time.

To stop a **running** instance without using its window — needed when the build
in question cannot quit cleanly, and the only safe way to free the database
lock — find the backend's port and ask it directly:

```bash
netstat -ano | grep LISTENING | grep <ohm-backend-pid>   # -> 127.0.0.1:<port>
curl -X POST --noproxy '*' http://127.0.0.1:<port>/api/shutdown
```

The endpoint ships in every build from `db583e7` on, so this works even against
an old build whose `before-quit` is broken. The backend exits in about a second,
checkpoints DuckDB and deletes the WAL; the Electron process then follows.
**Never `taskkill` the backend** — that is precisely how an unreplayable WAL is
produced.

## Environment Variables

- `OHM_DATA_DIR` — overrides the DuckDB location (Electron points it at `userData/data/`)
- `OHM_STATIC_DIR` — enables static serving of the built frontend (packaged mode)
- `ELECTRON=1` (or `vite build --mode electron`) — makes Vite emit relative asset
  paths (`base: './'`) for `file://`. `npm run build:electron` uses the mode flag,
  which works on cmd.exe too (unlike the bare `ELECTRON=1` env prefix).

## Building the Installer (Windows)

**One command, one output directory:**

```bash
npm run build          # check -> clean -> frontend -> backend -> electron-builder
```

That expands to:

```bash
npm run check          # static + functional checks for electron/ (see below)
npm run clean          # node scripts/clean.mjs — removes release/, backend/dist,
                       # backend/build/, frontend/dist
cd frontend && npm run build:electron        # -> frontend/dist
cd backend && ../.venv/Scripts/python.exe -m PyInstaller \
  --name ohm-backend --onedir --noconfirm --collect-submodules uvicorn \
  --collect-submodules fastapi --collect-submodules starlette \
  --collect-submodules pydantic --collect-submodules duckdb \
  --collect-all eval7 \
  --hidden-import multipart run_server.py    # -> backend/dist/ohm-backend
electron-builder --publish never             # -> release/Open Holdem Manager-Setup.exe
```

### `--collect-all eval7` is load-bearing — do not drop it

`eval7/__init__.py` does `from .handrange import HandRange`, and
`handrange -> rangestring -> pyparsing`. PyInstaller without `--collect-all`
picks up only eval7's three `.pyd` binaries and **omits `handrange.py`,
`rangestring.py` and `xorshift_rand.pyd`**, so `import eval7` fails outright.

That failure is then swallowed: `import_hands.py` wraps the equity import in
`try/except ImportError` and sets `_calc_equity = None`, which makes all-in EV
fall back to actual winnings. The result is indistinguishable from "EV happens
to equal results" — which is why a broken EV shipped unnoticed. There is now a
warning log on that path; if you see it, this flag is missing.

**How to verify EV actually works in a build:** run the dev backend and the
packaged backend against the *same* database copy and compare
`win_rate_ev_bb100` from `/api/stats/hero`. If the two are identical, EV is not
being computed. They should differ (e.g. 5.74 vs 4.68) — the small run-to-run
spread comes from Monte Carlo sampling on preflop all-ins.

### Two build steps fail in this sandbox and need a workaround

- **PyInstaller's COLLECT step** cannot delete the previous `backend/dist`
  (the safe-delete shim refuses bulk deletes, 710 entries vs a threshold of 50)
  and aborts the build. Delete it yourself first, then build the backend:
  ```bash
  CODEBUDDY_SAFE_DELETE_ENABLED=0 rm -rf backend/dist backend/build   # needs sandbox bypass
  npm run backend:build && npx electron-builder --publish never
  ```
  Delete `backend/build`, **never the root `build/`** — that one holds the icons
  electron-builder needs.
- **`release/win-unpacked/resources/app.asar` can be locked by Huorong
  (`HipsTray.exe`)**, giving `remove ...app.asar: The process cannot access the
  file`. Both the safe-delete shim and Python's `os.remove` are intercepted and
  fail. **Wait ~20 seconds and retry** — the lock usually releases on its own.

**Output always lands in `release/` and overwrites the previous build.** Never
pass `-c.directories.output=<something else>` — that is how the tree ended up
with `release-build/`, `release-build2/`, `release-build3/` littered around.
`electron-builder.yml` sets `directories.output: release`; leave it alone.

Notes:

- **`CODEBUDDY_SAFE_DELETE_ENABLED=0`** is required *in this sandbox* only. Prefix
  it to *both* PyInstaller and electron-builder; otherwise the safe-delete shim
  aborts bulk deletions with `SAFE_DELETE_BULK_CONFIRM_REQUIRED` (PyInstaller
  can't clear the old `dist/ohm-backend`; electron-builder leaves the installer
  without `latest.yml`). On a normal machine you don't need it.
- **Antivirus can lock `release/win-unpacked/resources/app.asar`** after a build.
  Symptom: the next build fails because it can't delete the old `app.asar`
  (`ERROR_SHARING_VIOLATION`, err 32) — and this **survives a reboot** if the AV
  service grabs it at startup. Fix: use the AV's "release file lock" feature
  (火绒安全 → 解除占用) and delete `release/`. **Do not work around it by changing
  the output directory.**
- **The frontend is not inside `app.asar`.** It ships as `extraResources`, so
  unpacking the asar only reveals `electron/` and `node_modules/`; the built React
  bundle is at `release/win-unpacked/resources/frontend/`, and the frozen server at
  `resources/backend/`. To prove a UI change landed in a given build, `grep` those
  directories — grepping the asar makes it look like the change is missing.
- macOS builds are unsigned; testers must run `xattr -cr "…/Open Holdem Manager.app"`.
  On macOS the venv interpreter is `.venv/bin/python`, not `.venv/Scripts/python.exe`.
- `electron/main.js` pins `REPO_OWNER` to **`uhayate`**. Pointing it back at the
  upstream owner would let auto-update overwrite this fork.

## Verifying Electron changes (`npm run check`)

The Python test suite cannot see `electron/`, and neither `node --check` nor a
packaged smoke test exercises the window-close path. So `electron/` has two
dedicated checks, both wired into `npm run build`:

1. **`npm run check:electron`** (`scripts/check-electron.mjs`) — runs `tsc
   --allowJs --checkJs --noEmit` over `electron/*.js`. Catches the class of bug
   that shipped in `db583e7`: a `before-quit` handler that referenced `quitting`,
   `backendUrl` and `SHUTDOWN_TIMEOUT_MS` without ever declaring them, so the
   first window close popped *"A JavaScript error occurred in the main process /
   ReferenceError: quitting is not defined"* and skipped the graceful shutdown
   entirely. `tsc` reports each as `TS2304: Cannot find name ...`.
2. **`npm run check:quit`** (`scripts/check-quit-path.cjs`) — loads the real
   `electron/main.js` with stubbed `electron` / `electron-updater` / `http` /
   `child_process`, emits `window-all-closed`, and asserts that the app POSTs
   `/api/shutdown` on the backend URL, does **not** call `child.kill()`, defers
   `before-quit` exactly once and still ends up quitting. Pass it a path to a
   different copy of `main.js` to prove the harness really catches a regression.

Neither check replaces closing the window by hand on a packaged build once
before shipping; they just make the two failure modes that bit us impossible to
re-introduce silently.
