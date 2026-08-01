# CUI // SP-CTI
"""Migration Canvas Dossier Advisor.

Reads challenge intelligence from the Research Engine DB and surfaces
contextually relevant guidance for each step of the Migration Wizard.

Target dossier: rdoss-903cbd6858e5 (Server Migration Analysis)
Target session: rsess-1e2fb0fe6c96
"""
from __future__ import annotations

import sqlite3
from tools.logging.icdev_logger import get_logger

from typing import Optional

from tools.db.storage import sql_placeholder
from tools.migration_canvas.db.init_db import get_connection

logger = get_logger("icdev.migration_canvas.dossier_advisor")

# DB error types to tolerate (backend unavailable / transient query error).
# Narrower than a bare ``except Exception`` so genuine programming errors surface.
try:  # pragma: no cover - psycopg2 optional in sqlite-only environments
    import psycopg2  # type: ignore

    _DB_ERRORS: tuple[type[BaseException], ...] = (sqlite3.Error, psycopg2.Error)
except ImportError:  # pragma: no cover
    _DB_ERRORS = (sqlite3.Error,)

TARGET_DOSSIER_ID = "rdoss-903cbd6858e5"
TARGET_SESSION_ID = "rsess-1e2fb0fe6c96"

# Valid category values from the research_challenges CHECK constraint.
_DB_CATEGORIES = frozenset({
    "infrastructure", "compliance", "security", "ux", "performance",
    "integration", "data", "cost", "scalability", "automation",
    "governance", "other",
})

# Conceptual categories that aren't DB values map to the nearest valid one.
_CATEGORY_FALLBACK: dict[str, str] = {
    "discovery": "other",
    "inventory": "infrastructure",
    "api": "integration",
    "compatibility": "governance",
    "support": "other",
    "storage": "infrastructure",
    "network": "infrastructure",
    "cutover": "other",
    "rollback": "other",
}

# step → conceptual category list (forward-compatible with future DB categories)
STEP_CATEGORY_MAP: dict[int, list[str]] = {
    1: ["compliance", "security"],           # MigrationType
    2: ["discovery", "inventory", "automation"],  # Inventory
    3: ["performance", "scalability"],       # Performance
    4: ["ux", "integration", "api"],         # TargetSelection
    5: ["compatibility", "support"],         # Compatibility
    6: ["storage", "network"],               # NICStorage
    7: ["cutover", "rollback", "automation"],  # CutoverPlanner
}


def _resolve_categories(conceptual: list[str]) -> list[str]:
    """Map conceptual names to DB-valid category values, order-preserving dedup."""
    seen: set[str] = set()
    resolved: list[str] = []
    for cat in conceptual:
        db_cat = cat if cat in _DB_CATEGORIES else _CATEGORY_FALLBACK.get(cat)
        if db_cat and db_cat not in seen:
            seen.add(db_cat)
            resolved.append(db_cat)
    return resolved


def get_guidance_for_step(
    wizard_step: int,
    migration_type: Optional[str] = None,  # noqa: ARG001 — reserved for future scoring
    top_k: int = 3,
) -> list[dict]:
    """Return top_k research challenges relevant to a Migration Wizard step.

    Args:
        wizard_step: 1=MigrationType, 2=Inventory, 3=Performance,
                     4=TargetSelection, 5=Compatibility, 6=NICStorage,
                     7=CutoverPlanner
        migration_type: optional hint (e.g. 'P2V', 'V2V'); reserved for
                        future score weighting.
        top_k: maximum number of challenges to return.

    Returns:
        List of dicts — each with keys: title, description, severity, category.
        Returns an empty list if the step is unknown or the DB is unavailable.
    """
    conceptual = STEP_CATEGORY_MAP.get(wizard_step, [])
    if not conceptual:
        return []

    categories = _resolve_categories(conceptual)
    if not categories:
        return []

    try:
        # Canvas-safe connection (RLS disabled) so the research_challenges read
        # is not filtered by the global RLS predicate, and so a single backend
        # is resolved consistently for placeholder selection.
        conn = get_connection()
        ph = sql_placeholder(conn)
        in_clause = ", ".join([ph] * len(categories))
        rows = conn.execute(
            f"""
            SELECT rc.title, rc.description, rc.severity, rc.category
            FROM   research_challenges rc
            WHERE  rc.session_id = {ph}
              AND  rc.category IN ({in_clause})
              AND  rc.composite_score IS NOT NULL
            ORDER  BY rc.composite_score DESC
            LIMIT  {ph}
            """,
            [TARGET_SESSION_ID, *categories, top_k],
        ).fetchall()
        return [
            {
                "title": r[0],
                "description": r[1] or "",
                "severity": r[2] or "notable",
                "category": r[3],
            }
            for r in rows
        ]
    except _DB_ERRORS as exc:
        logger.warning(
            "Dossier DB query failed for step %d (%s): %s",
            wizard_step, type(exc).__name__, exc,
        )
        return []


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="MCE Dossier Advisor — surface migration guidance per wizard step")
    ap.add_argument("--step", type=int, required=True,
                    help="Wizard step (1=MigrationType, 2=Inventory, 3=Performance, 4=Target, 5=Compat, 6=NIC/Storage, 7=Cutover)")
    ap.add_argument("--migration-type", default=None, help="Migration type hint (P2V, V2V, P2C, ...)")
    ap.add_argument("--top-k", type=int, default=3, help="Max guidance items to return (default: 3)")
    ap.add_argument("--output-json", action="store_true", help="Emit JSON to stdout")
    args = ap.parse_args()

    result = get_guidance_for_step(args.step, args.migration_type, args.top_k)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        if not result:
            print(f"[dossier_advisor] No guidance available for step {args.step}")
        else:
            print(f"[dossier_advisor] Step {args.step} — {len(result)} item(s)")
            for item in result:
                print(f"  [{item.get('severity','?').upper():8s}] {item.get('title','?')}")
                if item.get("description"):
                    print(f"    {item['description'][:100]}")


if __name__ == "__main__":
    main()
