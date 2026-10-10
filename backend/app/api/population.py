import json

from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional

from app.db import get_read_cursor, get_hero_player_id, normalize_date_to
from app.models import HeroStats
from app.stats_engine import _AGG_SQL, _compute_stats_from_query

router = APIRouter()


def _resolve_excluded_player_ids(
    db,
    workspace_id: int,
    exclude_identity_ids: Optional[str] = None,
    exclude_tags: Optional[str] = None,
) -> list[int]:
    """Resolve identity IDs and tags to a list of player_ids to exclude."""
    identity_ids: set[int] = set()

    if exclude_identity_ids:
        for s in exclude_identity_ids.split(","):
            s = s.strip()
            if s.isdigit():
                identity_ids.add(int(s))

    if exclude_tags:
        tag_list = [t.strip() for t in exclude_tags.split(",") if t.strip()]
        if tag_list:
            rows = db.execute(
                "SELECT id, tags FROM player_identities"
            ).fetchall()
            for row in rows:
                try:
                    row_tags = json.loads(row[1]) if row[1] else []
                except (json.JSONDecodeError, TypeError):
                    row_tags = []
                if any(t in row_tags for t in tag_list):
                    identity_ids.add(row[0])

    if not identity_ids:
        return []

    ph = ",".join("?" for _ in identity_ids)
    alias_rows = db.execute(
        f"SELECT player_id FROM player_aliases "
        f"WHERE identity_id IN ({ph}) AND workspace_id = ?",
        list(identity_ids) + [workspace_id],
    ).fetchall()

    return [r[0] for r in alias_rows]


def _build_where(
    stakes: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    min_hands: int,
    exclude_hero: bool,
    player_type: Optional[str],
    db,
    workspace_id: int = 1,
    exclude_identity_ids: Optional[str] = None,
    exclude_tags: Optional[str] = None,
) -> tuple[str, list, str, str, list]:
    """Build WHERE clause for population queries.

    Returns (where_sql, params, having_sql, main_where_sql, main_params).

    `where_sql` may reference `pc` (player_classifications) and is only valid in
    a query that actually LEFT JOINs it. `main_where_sql` / `main_params` are
    the same filters minus any `pc` reference, for the aggregation queries whose
    FROM clause is just `hand_players hp JOIN hands h` — feeding the `pc`
    variant to those used to raise `Binder Error: Referenced table "pc" not
    found`. Player-type filtering still applies through the `eligible` CTE.
    """
    clauses = ["h.workspace_id = ?"]
    params: list = [workspace_id]
    # Clauses referencing only hp/h — safe for the aggregation query too.
    shared = ["h.workspace_id = ?"]
    shared_params: list = [workspace_id]

    if stakes:
        stakes_list = [s.strip() for s in stakes.split(",") if s.strip()]
        if stakes_list:
            ph = ",".join("?" for _ in stakes_list)
            clauses.append(f"h.stakes IN ({ph})")
            params.extend(stakes_list)
            shared.append(f"h.stakes IN ({ph})")
            shared_params.extend(stakes_list)

    if date_from:
        clauses.append("h.played_at >= ?")
        params.append(date_from)
        shared.append("h.played_at >= ?")
        shared_params.append(date_from)
    if date_to:
        norm = normalize_date_to(date_to)
        clauses.append("h.played_at <= ?")
        params.append(norm)
        shared.append("h.played_at <= ?")
        shared_params.append(norm)

    if exclude_hero:
        hero_id = get_hero_player_id(db, workspace_id)
        if hero_id:
            clauses.append("hp.player_id != ?")
            params.append(hero_id)
            shared.append("hp.player_id != ?")
            shared_params.append(hero_id)

    # Exclude players by identity IDs or tags
    excluded_pids = _resolve_excluded_player_ids(
        db, workspace_id, exclude_identity_ids, exclude_tags,
    )
    if excluded_pids:
        ph = ",".join("?" for _ in excluded_pids)
        clauses.append(f"hp.player_id NOT IN ({ph})")
        params.extend(excluded_pids)
        shared.append(f"hp.player_id NOT IN ({ph})")
        shared_params.extend(excluded_pids)

    if player_type:
        types = [t.strip().upper() for t in player_type.split(",") if t.strip()]
        if types:
            ph = ",".join("?" for _ in types)
            # pc-only: this one deliberately stays out of `shared`.
            clauses.append(f"pc.player_type IN ({ph})")
            params.extend(types)

    where_sql = " AND " + " AND ".join(clauses)
    main_where_sql = " AND " + " AND ".join(shared)
    having_sql = f"HAVING COUNT(*) >= {int(min_hands)}" if min_hands > 0 else ""

    return where_sql, params, having_sql, main_where_sql, shared_params


# ── Overview ────────────────────────────────────────────────────────

class PopulationOverview(BaseModel):
    player_count: int
    observation_count: int
    date_min: str | None = None
    date_max: str | None = None


@router.get("/population/overview", response_model=PopulationOverview)
def population_overview(
    stakes: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_hands: int = Query(20, ge=0),
    exclude_hero: bool = Query(True),
    player_type: Optional[str] = None,
    workspace_id: int = Query(1),
    exclude_identity_ids: Optional[str] = None,
    exclude_tags: Optional[str] = None,
):
    db = get_read_cursor()
    where_sql, params, having_sql, _, _ = _build_where(stakes, date_from, date_to, min_hands, exclude_hero, player_type, db, workspace_id, exclude_identity_ids, exclude_tags)

    row = db.execute(f"""
        SELECT COUNT(*), SUM(hands), MIN(min_t), MAX(max_t) FROM (
            SELECT hp.player_id, COUNT(*) as hands,
                   MIN(h.played_at) as min_t, MAX(h.played_at) as max_t
            FROM hand_players hp
            JOIN hands h ON hp.hand_id = h.id AND hp.workspace_id = h.workspace_id
            JOIN players p ON p.id = hp.player_id
            LEFT JOIN player_classifications pc ON pc.player_id = p.id AND pc.workspace_id = h.workspace_id
            WHERE 1=1 {where_sql}
            GROUP BY hp.player_id
            {having_sql}
        ) sub
    """, params).fetchone()

    pc = row[0] if row else 0
    oc = row[1] if row and row[1] else 0

    return PopulationOverview(
        player_count=pc,
        observation_count=oc,
        date_min=row[2].isoformat() if row and row[2] else None,
        date_max=row[3].isoformat() if row and row[3] else None,
    )


# ── Full Stats (HeroStats-shaped) ──────────────────────────────────


@router.get("/population/full-stats", response_model=HeroStats)
def population_full_stats(
    stakes: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_hands: int = Query(20, ge=0),
    exclude_hero: bool = Query(True),
    player_type: Optional[str] = None,
    workspace_id: int = Query(1),
    exclude_identity_ids: Optional[str] = None,
    exclude_tags: Optional[str] = None,
):
    db = get_read_cursor()
    where_sql, params, having_sql, main_where_sql, main_params = _build_where(
        stakes, date_from, date_to, min_hands, exclude_hero,
        player_type, db, workspace_id, exclude_identity_ids, exclude_tags,
    )

    # The CTE resolves which players qualify (it is the only place with a `pc`
    # join); the aggregation query then restricts to those players and repeats
    # only the hp/h-level filters. Echoing the full `where_sql` here used to
    # reference an unbound `pc` alias, and its params were bound twice.
    eligible_cte = f"""WITH eligible AS (
        SELECT hp.player_id
        FROM hand_players hp
        JOIN hands h ON hp.hand_id = h.id AND hp.workspace_id = h.workspace_id
        JOIN players p ON p.id = hp.player_id
        LEFT JOIN player_classifications pc ON pc.player_id = p.id AND pc.workspace_id = h.workspace_id
        WHERE 1=1 {where_sql}
        GROUP BY hp.player_id
        {having_sql}
    )
    """

    main_where = f"1=1 {main_where_sql} AND hp.player_id IN (SELECT player_id FROM eligible)"
    full_sql = eligible_cte + _AGG_SQL.format(where=main_where)
    all_params = params + main_params

    return _compute_stats_from_query(db, main_where, all_params, sql_override=full_sql)


# ── Preflop ─────────────────────────────────────────────────────────









# ── Segments ────────────────────────────────────────────────────────







# ── Postflop ────────────────────────────────────────────────────────







# ── Pot Types ───────────────────────────────────────────────────────







# ── Showdown ────────────────────────────────────────────────────────







# ── HU vs Multiway ─────────────────────────────────────────────────







# ── Comparison (hero vs pop) ────────────────────────────────────────





