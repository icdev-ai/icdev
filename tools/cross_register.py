# CUI // SP-CTI
"""cross_register.py — Cross-registration bridge from ICDEV discovery engines to ACF.

Pulls high-value signals from individual discovery engines and normalizes them into
the ``foundry_signals`` table. Each pull function is engine-specific so bridge logic
is explicit, testable, and idempotent: a ``content_hash`` dedup check (backed by a
DB unique index) blocks duplicate entries when the same concept is re-registered
across runs.

Public API
----------
    pull_from_innovation(run_id=None, *, min_score=0.7, limit=50, conn=None) -> dict

CLI
---
    python tools/cross_register.py --min-score 0.7 --limit 50 --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional




# =========================================================================
# PATH
# =========================================================================
def _find_repo_root() -> Path:
    """Anchor on a marker that exists only at the true repo root."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CLAUDE.md").exists() or (parent / "goals" / "manifest.md").exists():
            return parent
    return here.parent.parent.parent


BASE_DIR = _find_repo_root()


# =========================================================================
# HELPERS
# =========================================================================
def _caller_context() -> tuple[str, str]:
    """(tenant_id, classification) from the active security context."""
    try:
        from tools.security.security_context import get_security_context

        ctx = get_security_context()
    except Exception:
        ctx = None
    tenant_id = (getattr(ctx, "tenant_id", None) or "default") if ctx else "default"
    classification = (getattr(ctx, "classification", None) or "CUI") if ctx else "CUI"
    return tenant_id, classification


def _coerce_score(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _signal_hash(sid: str, title: str, source_engine: str) -> str:
    """Synthesise a content hash when the upstream store does not provide one."""
    payload = f"{source_engine}\x1f{sid}\x1f{title}".lower().strip()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# =========================================================================
# PULL: innovation_signals -> foundry_signals
# =========================================================================
def pull_from_innovation(
    run_id: Optional[str] = None,
    *,
    min_score: float = 0.7,
    limit: int = 50,
    conn: Any = None,
) -> dict:
    """Query ``innovation_signals`` for high-scored concepts and normalize them into
    ``foundry_signals`` with explicit hash-based dedup.

    Parameters
    ----------
    run_id : str, optional
        ACF run identifier to tag harvested signals. Defaults to a synthetic
        ``cross-register-innovation-{iso}`` value.
    min_score : float
        Minimum ``composite_score`` (or ``innovation_score``) to include.
    limit : int
        Max rows to pull from ``innovation_signals``.
    conn : connection, optional
        Existing DB connection (RLS-aware). If None, ``get_connection()`` is used.

    Returns
    -------
    dict
        ``{"run_id": str, "inserted": int, "skipped": int, "signals": list[dict]}``
    """
    init_db()  # idempotent — guarantees foundry_signals + content_hash column exist

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()

    tenant_id, classification = _caller_context()
    run_id_str = run_id or f"cross-register-innovation-{datetime.now(timezone.utc).isoformat()}"
    inserted = 0
    skipped = 0
    signals: list[dict] = []

    try:
        # Pull high-scored innovation signals ordered by composite_score desc.
        # Fallback to innovation_score when composite_score IS NULL.
        sql = (
            "SELECT id, title, composite_score, innovation_score, "
            "category, source, source_type, content_hash "
            "FROM innovation_signals "
            "WHERE (composite_score >= ? OR (composite_score IS NULL AND innovation_score >= ?)) "
            "AND status != 'blocked' "
            "ORDER BY COALESCE(composite_score, innovation_score, 0) DESC "
            "LIMIT ?"
        )
        cur = conn.execute(sql, (min_score, min_score, limit))
        rows = cur.fetchall()

        for row in rows:
            sid, title, composite_score, innovation_score, category, source, source_type, content_hash = row

            raw_score = _coerce_score(
                composite_score if composite_score is not None else innovation_score
            )

            # Build keywords from categorical tags.
            keywords = [
                str(k).strip()
                for k in (category, source, source_type)
                if k is not None and str(k).strip()
            ]

            # Reuse the upstream content_hash for dedup, or synthesise one.
            sig_hash = content_hash or _signal_hash(sid, title, "innovation")

            # Explicit pre-insert dedup check (unique hash constraint).
            dup_check = conn.execute(
                "SELECT 1 FROM foundry_signals WHERE content_hash = %s LIMIT 1",
                (sig_hash,),
            ).fetchone()

            if dup_check is not None:
                skipped += 1
                logger.debug("cross_register skip dup hash=%s ref=%s", sig_hash, sid)
                continue

            conn.execute(
                "INSERT INTO foundry_signals "
                "(run_id, source_engine, source_ref, theme, raw_score, keywords, "
                "tenant_id, classification, content_hash) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    run_id_str,
                    "innovation",
                    f"innovation_signals:{sid}",
                    title,
                    raw_score,
                    json.dumps(keywords),
                    tenant_id,
                    classification,
                    sig_hash,
                ),
            )
            inserted += 1
            signals.append(
                {
                    "source_engine": "innovation",
                    "source_ref": f"innovation_signals:{sid}",
                    "theme": title,
                    "raw_score": raw_score,
                    "keywords": keywords,
                }
            )

        conn.commit()
    finally:
        if own_conn:
            conn.close()

    logger.info(
        "pull_from_innovation run=%s -> inserted=%d skipped=%d",
        run_id_str,
        inserted,
        skipped,
    )
    return {
        "run_id": run_id_str,
        "inserted": inserted,
        "skipped": skipped,
        "signals": signals,
    }


# =========================================================================
# CLI
# =========================================================================
def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-register innovation signals to foundry")
    parser.add_argument("--run-id", default=None, help="foundry run identifier")
    parser.add_argument("--min-score", type=float, default=0.7, help="minimum score threshold")
    parser.add_argument("--limit", type=int, default=50, help="max rows to pull")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    result = pull_from_innovation(
        run_id=args.run_id,
        min_score=args.min_score,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"cross_register run={result['run_id']} inserted={result['inserted']} skipped={result['skipped']}"
        )
        for s in result["signals"]:
            print(f"  [{s['source_engine']}] {s['source_ref']}  score={s['raw_score']:.3f}  {s['theme']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    raise SystemExit(_main())

from tools.foundry.db.init_db import init_db  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger("icdev.cross_register")
