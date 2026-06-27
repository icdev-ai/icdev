#!/usr/bin/env python3
# CUI // SP-CTI
"""Citation Trust Score calculator for blockchain-provenance-backed citations.

Scores range from 0.0 (no verification) to 1.0 (fully verified on-chain).
Algorithm:
  - source_hash present          : +0.2
  - merkle_root anchored        : +0.3
  - blockchain_tx_id confirmed  : +0.3
  - cross-referenced            : +0.2

Usage:
    from tools.provenance.trust_scorer import compute_trust_score, score_project

    score = compute_trust_score(registry_entry)
    project_scores = score_project("proj-test")
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

DB_PATH = BASE_DIR / "data" / "icdev.db"


def has_cross_reference(registry_id: str, source_hash: str, db_path: Path = None) -> bool:
    """Check if a source_hash appears in multiple source_tables."""
    conn = get_connection(db_path=str(db_path or DB_PATH))
    try:
        row = conn.execute(
            """SELECT COUNT(DISTINCT source_table) as cnt FROM source_citation_registry
               WHERE source_hash = %s""",
            (source_hash,),
        ).fetchone()
        return row is not None and row["cnt"] > 1
    except Exception:
        return False
    finally:
        conn.close()


def compute_trust_score(entry: dict, db_path: Path = None) -> float:
    """Compute a 0.0-1.0 trust score for a registry entry."""
    score = 0.0

    if entry.get("source_hash"):
        score += 0.2

    if entry.get("merkle_root"):
        score += 0.3

    if entry.get("blockchain_tx_id"):
        score += 0.3

    if has_cross_reference(entry.get("id", ""), entry.get("source_hash", ""), db_path):
        score += 0.2

    return round(min(score, 1.0), 2)


def score_project(project_id: str, db_path: Path = None) -> dict:
    """Compute trust scores for all citations in a project."""
    conn = get_connection(db_path=str(db_path or DB_PATH))
    try:
        rows = conn.execute(
            "SELECT * FROM source_citation_registry WHERE project_id = %s",
            (project_id,),
        ).fetchall()

        results = []
        total = 0.0
        for r in rows:
            entry = dict(r)
            score = compute_trust_score(entry, db_path)
            entry["trust_score"] = score
            results.append(entry)
            total += score

        avg = round(total / len(results), 2) if results else 0.0

        return {
            "project_id": project_id,
            "citation_count": len(results),
            "average_trust_score": avg,
            "citations": results,
        }
    finally:
        conn.close()


def update_all_scores(db_path: Path = None) -> dict:
    """Recompute and update trust scores for all registry entries."""
    conn = get_connection(db_path=str(db_path or DB_PATH))
    updated = 0
    try:
        rows = conn.execute("SELECT id, source_hash, merkle_root, blockchain_tx_id FROM source_citation_registry").fetchall()
        for r in rows:
            score = compute_trust_score(dict(r), db_path)
            conn.execute(
                "UPDATE source_citation_registry SET trust_score = %s WHERE id = %s",
                (score, r["id"]),
            )
            updated += 1
        conn.commit()
        return {"updated": updated}
    except Exception as e:
        return {"updated": updated, "error": str(e)}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Trust Scorer")
    parser.add_argument("--project", help="Score all citations for a project")
    parser.add_argument("--update-all", action="store_true", help="Update all scores in DB")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.project:
        result = score_project(args.project)
        print(json.dumps(result, indent=2, default=str) if args.json else result)
        return

    if args.update_all:
        result = update_all_scores()
        print(json.dumps(result, indent=2) if args.json else result)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
