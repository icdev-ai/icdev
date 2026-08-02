# CUI // SP-CTI
"""Migration 342 — state what each mission teaches (aca-trn-03).

``fa_missions`` carried three numbers a learner pays — ``xp_reward``,
``estimated_minutes``, ``difficulty`` — and nothing about what they get. For
training used in a compliance context that is backwards: the objective is the
auditable unit, because "this learner was trained on X" needs an X, and a
tagline is marketing copy.

Python rather than SQL for two reasons:

* The column must be added idempotently on BOTH engines. ``ADD COLUMN IF NOT
  EXISTS`` is PostgreSQL-only, and a bare ``ADD COLUMN`` raises on a second
  SQLite run — the constraint migration 324 documents. Checking the live schema
  first is the portable form.
* The backfill values come from ``content_loader.extract_learning_objective``.
  Pasting ~124 extracted strings into a .sql file would freeze them at the shape
  the extractor had on the day it was written and drift from the content the
  moment either changed.

Missions whose content states no objective are left NULL. That is the point of
the change, not a shortfall in it: the surfaces omit the line, so the gap is
visible to whoever owns the content. Synthesising something plausible from the
tagline would put un-authored text on an auditable field.

Idempotent: re-running re-extracts and rewrites the same values. Only rows whose
stored objective differs are touched, so a hand-edited objective that the content
no longer supports is reported rather than silently reverted.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

TABLE = "fa_missions"
COLUMN = "learning_objective"


def _content_loader():
    """Import the child app's content loader without assuming the CWD."""
    apps = REPO_ROOT / "apps"
    if str(apps) not in sys.path:
        sys.path.insert(0, str(apps))
    from forge_academy import content_loader  # noqa: PLC0415

    return content_loader


def _is_pg() -> bool:
    try:
        from tools.db.storage import is_pg  # noqa: PLC0415

        return bool(is_pg())
    except Exception:
        return False


def _has_column(conn) -> bool:
    """Whether fa_missions already carries the column, on either engine."""
    if _is_pg():
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name=%s AND column_name=%s",
            (TABLE, COLUMN),
        ).fetchone()
        return row is not None
    rows = conn.execute(f"PRAGMA table_info({TABLE})").fetchall()
    for row in rows:
        # PRAGMA rows are (cid, name, type, notnull, dflt_value, pk).
        name = row[1] if isinstance(row, tuple) else row["name"]
        if name == COLUMN:
            return True
    return False


def up(conn=None) -> None:
    own = conn is None
    if own:
        from tools.db.storage import get_connection  # noqa: PLC0415

        conn = get_connection()

    # The table is created by the child app on first import. A database that has
    # never run FORGE Academy has nothing to alter and nothing to backfill.
    try:
        existing = _has_column(conn)
    except Exception as exc:
        print(f"342: {TABLE} not present ({exc}) — nothing to do")
        return

    if not existing:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} TEXT")
        if own:
            conn.commit()
        print(f"342: added {TABLE}.{COLUMN}")
    else:
        print(f"342: {TABLE}.{COLUMN} already present")

    loader = _content_loader()
    discovered = loader.discover_steps()

    rows = conn.execute(
        f"SELECT id, slug, {COLUMN} FROM {TABLE}"
    ).fetchall()

    filled = 0
    for row in rows:
        slug = row["slug"] if not isinstance(row, tuple) else row[1]
        current = row[COLUMN] if not isinstance(row, tuple) else row[2]
        objective = loader.objective_for_mission(discovered.get(slug)) or None
        if objective is None or objective == current:
            continue
        conn.execute(
            f"UPDATE {TABLE} SET {COLUMN}=%s WHERE id=%s",
            (objective, row["id"] if not isinstance(row, tuple) else row[0]),
        )
        filled += 1

    if own:
        conn.commit()
    print(
        f"342: backfilled an objective for {filled} of {len(rows)} missions "
        "from authored content (the rest state none)"
    )
