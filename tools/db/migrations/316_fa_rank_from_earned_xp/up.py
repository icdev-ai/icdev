# CUI // SP-CTI
"""Migration 316 — make the displayed rank match the rank the ledger supports.

Migration 315 gave XP provenance and aca-int-07 moved rank onto *earned* XP, but
``fa_users.level`` is a cache: it is written only inside ``update_user_xp``, so an
existing learner kept whatever rank their pre-ledger total had already bought until
their next award happened to refresh it. Measured on the live board straight after
315 applied:

    fa_users.xp        1715
    ledger total       1715   (reconciles)
      attendance       1465   41 daily logins
      earned            250   2 completed steps
    fa_users.level     'operative'   <-- still the total-based rank

So the fix was in place and invisible: the profile went on advertising a rank that
1465 points of logging in had paid for.

Written in Python rather than SQL on purpose. The thresholds live in
``xp_to_level()``; expressing them again as a SQL CASE ladder would duplicate a
Python constant into the schema, which is exactly the drift the repo rule about
deriving CHECK constraints from Python constants exists to prevent.

Idempotent: recomputing an already-correct level writes the same value. Safe to run
before any learner has a ledger row — ``earned_xp`` returns 0 and the learner drops
to the entry rank, which is the truthful answer when nothing has been demonstrated.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _academy_db():
    """Import the child app's db module without assuming the CWD."""
    apps = REPO_ROOT / "apps"
    if str(apps) not in sys.path:
        sys.path.insert(0, str(apps))
    from forge_academy import db as fadb  # noqa: PLC0415

    return fadb


def up(conn=None) -> None:
    fadb = _academy_db()
    own = conn is None
    if own:
        from tools.db.storage import get_connection  # noqa: PLC0415

        conn = get_connection()

    rows = conn.execute("SELECT id, xp, level FROM fa_users").fetchall()
    changed = 0
    for row in rows:
        user_id = row["id"] if not isinstance(row, tuple) else row[0]
        current = row["level"] if not isinstance(row, tuple) else row[2]
        earned = fadb.earned_xp(user_id, conn=conn)
        wanted = fadb.xp_to_level(earned)["slug"]
        if wanted != current:
            conn.execute(
                "UPDATE fa_users SET level=%s WHERE id=%s", (wanted, user_id)
            )
            changed += 1
    if own:
        conn.commit()
    print(f"316: recomputed rank for {changed} of {len(rows)} learners from earned XP")
