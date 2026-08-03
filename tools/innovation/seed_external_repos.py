#!/usr/bin/env python3
# CUI // SP-CTI
"""Register the watchlist's adaptation candidates as innovation signals.

Follows the same pattern as register_external_patterns.py (Phase 44 — D279).
Source type: external_repo_scouting.

The targets come from context/genesis/competitors.yaml — the same file the
Genesis scout reflex polls. Any entry carrying an ``adaptation`` block is a
candidate; entries without one are watched but not registered. Adding a repo
to that file is therefore the whole change: no edit here is needed.

The signal's content hash is sha256(title + description)[:16], and dedup is
keyed on it, so an entry's ``adaptation.title`` and ``notes`` are load-bearing
— reword either and the next run registers a second row rather than matching
the existing one.

Usage:
    python tools/innovation/seed_external_repos.py --register-all --json
    python tools/innovation/seed_external_repos.py --status --json
    python tools/innovation/seed_external_repos.py --score-all --json
"""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tools.db.storage import get_connection
from tools.compat.db_utils import get_icdev_db_path

DB_PATH = get_icdev_db_path()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Checkout layout first, packaged layout second. The icdev/ copy of this module
# sits beside icdev/data/context/, not icdev/context/.
WATCHLIST_CANDIDATES = (
    BASE_DIR / "context" / "genesis" / "competitors.yaml",
    BASE_DIR / "data" / "context" / "genesis" / "competitors.yaml",
)

# 5-dimension scoring weights (matches innovation_config.yaml)
SCORING_WEIGHTS = {
    "novelty": 0.20,
    "feasibility": 0.25,
    "compliance_alignment": 0.25,
    "user_impact": 0.20,
    "effort": 0.10,
}

class WatchlistError(RuntimeError):
    """The watchlist could not be read.

    Raised rather than returning an empty list: a seeder that silently
    registers nothing reports success and looks identical to one that had
    nothing to do.
    """


def watchlist_path(candidates=WATCHLIST_CANDIDATES) -> Path:
    for path in candidates:
        if path.exists():
            return path
    raise WatchlistError(
        "no watchlist found; looked for: " + ", ".join(str(p) for p in candidates)
    )


def _scoring(entry: Dict[str, Any]) -> Dict[str, float]:
    """Five dimensions, defaulting to neutral. A partial block scores the rest 0.5."""
    raw = entry.get("scoring") or {}
    return {dim: float(raw.get(dim, 0.5)) for dim in SCORING_WEIGHTS}


def _normalize(target: Dict[str, Any]) -> Dict[str, Any]:
    """One watchlist entry -> the repo dict the rest of this module consumes."""
    adaptation = target.get("adaptation") or {}
    repo_slug = target.get("repo") or ""
    name = repo_slug.split("/", 1)[1] if "/" in repo_slug else repo_slug

    url = target.get("url") or (f"https://github.com/{repo_slug}" if repo_slug else "")

    return {
        "source": adaptation.get("source") or name.lower() or target.get("name", ""),
        "title": adaptation.get("title") or target.get("name") or repo_slug,
        # `notes` is the long-form rationale and doubles as the signal
        # description, so the watchlist carries the text exactly once.
        "description": adaptation.get("description") or target.get("notes") or "",
        "category": target.get("category", "developer_experience"),
        "gotcha_layer": adaptation.get("gotcha_layer", "tool"),
        "url": url,
        "scoring_hints": _scoring(adaptation),
        "adaptation_target": adaptation.get("target", ""),
        "implementation_status": adaptation.get("status", "pending"),
        "repo": repo_slug,
        "subsystem": target.get("subsystem", ""),
    }


def load_repos(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Adaptation candidates from the scout watchlist.

    Entries with no ``adaptation`` block are watched by the scout but are not
    adaptation candidates, so they are skipped here.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - pyyaml is a hard dependency
        raise WatchlistError(f"pyyaml is required to read the watchlist: {exc}") from exc

    path = path or watchlist_path()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise WatchlistError(f"{path} is not valid YAML: {exc}") from exc

    targets = data.get("targets") or []
    return [_normalize(t) for t in targets if t.get("adaptation") is not None]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def register_repo(repo: dict, db_path: Path = DB_PATH) -> dict:
    title = repo["title"]
    description = repo["description"]
    content_hash = _content_hash(title + description)

    try:
        conn = get_connection(db_path=str(db_path))

        existing = conn.execute(
            "SELECT id FROM innovation_signals WHERE content_hash = %s",
            (content_hash,),
        ).fetchone()

        if existing:
            conn.close()
            return {
                "signal_id": dict(existing)["id"],
                "status": "duplicate",
                "is_duplicate": True,
                "title": title,
            }

        signal_id = f"sig-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        hints = repo.get("scoring_hints", {})
        score = sum(hints.get(dim, 0.5) * weight for dim, weight in SCORING_WEIGHTS.items())
        score_meta = {
            "dimensions": hints,
            "weights": SCORING_WEIGHTS,
            "composite": round(score, 4),
            "adaptation_target": repo.get("adaptation_target", ""),
        }

        conn.execute(
            """INSERT INTO innovation_signals
               (id, source, source_type, title, description, url,
                category, innovation_score, score_breakdown, content_hash,
                status, gotcha_layer, implementation_status,
                classification, discovered_at, created_at)
               VALUES (%s, %s, 'external_repo_scouting', %s, %s, %s,
                       %s, %s, %s, %s,
                       'triaged', %s, %s,
                       'CUI', %s, %s)""",
            (
                signal_id,
                repo["source"],
                title,
                description,
                repo.get("url", ""),
                repo.get("category", "developer_experience"),
                round(score, 4),
                json.dumps(score_meta),
                content_hash,
                repo.get("gotcha_layer", "tool"),
                repo.get("implementation_status", "pending"),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()

        return {
            "signal_id": signal_id,
            "status": "registered",
            "is_duplicate": False,
            "title": title,
            "innovation_score": round(score, 4),
            "category": repo.get("category"),
            "adaptation_target": repo.get("adaptation_target"),
        }

    except Exception as exc:
        return {"error": str(exc), "title": title}


def register_all(db_path: Path = DB_PATH, repos: Optional[List[Dict[str, Any]]] = None) -> dict:
    registered = 0
    skipped = 0
    signals = []

    repos = load_repos() if repos is None else repos

    for repo in repos:
        result = register_repo(repo, db_path)
        signals.append(result)
        if result.get("is_duplicate"):
            skipped += 1
        elif "error" not in result:
            registered += 1

    signals.sort(key=lambda x: x.get("innovation_score", 0), reverse=True)
    return {
        "registered": registered,
        "skipped_duplicates": skipped,
        "total_repos": len(repos),
        "watchlist": str(watchlist_path()),
        "signals": signals,
    }


def score_repos(repos: Optional[List[Dict[str, Any]]] = None) -> dict:
    scored = []
    for repo in load_repos() if repos is None else repos:
        hints = repo.get("scoring_hints", {})
        score = sum(hints.get(dim, 0.5) * weight for dim, weight in SCORING_WEIGHTS.items())
        scored.append(
            {
                "title": repo["title"],
                "source": repo["source"],
                "score": round(score, 4),
                "breakdown": hints,
                "category": repo.get("category", ""),
                "adaptation_target": repo.get("adaptation_target", ""),
                "implementation_status": repo.get("implementation_status", "pending"),
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"repos": scored, "scoring_weights": SCORING_WEIGHTS}


def get_status(db_path: Path = DB_PATH) -> dict:
    conn = get_connection(db_path=str(db_path))
    rows = conn.execute(
        "SELECT id, title, innovation_score, implementation_status "
        "FROM innovation_signals WHERE source_type='external_repo_scouting' "
        "ORDER BY innovation_score DESC"
    ).fetchall()
    conn.close()
    repos = [dict(r) for r in rows]
    return {
        "total": len(repos),
        "pending": sum(1 for r in repos if r.get("implementation_status") == "pending"),
        "repos": repos,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed external GitHub repos as innovation signals")
    parser.add_argument("--register-all", action="store_true", help="Register all repos")
    parser.add_argument("--status", action="store_true", help="Show seeded repo status")
    parser.add_argument("--score-all", action="store_true", help="Score all repos (dry-run)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        if args.register_all:
            result = register_all()
        elif args.status:
            result = get_status()
        elif args.score_all:
            result = score_repos()
        else:
            result = {"error": "Use --register-all, --status, or --score-all"}
    except WatchlistError as exc:
        # Exit non-zero: an unreadable watchlist is a failure, not an empty run.
        print(json.dumps({"error": str(exc)}) if args.json else f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if "repos" in result and "score" in (result["repos"][0] if result.get("repos") else {}):
            for r in result["repos"]:
                print(f"  [{r.get('implementation_status','?')}] {r['title']} (score={r['score']})")
                print(f"    → {r.get('adaptation_target','')}")
        elif "signals" in result:
            for s in result["signals"]:
                status = s.get("status", "?")
                score = s.get("innovation_score", "")
                print(f"  [{status}] {s['title']} (score={score})")
            print(f"\nRegistered: {result['registered']}, Duplicates: {result['skipped_duplicates']}")
        elif "repos" in result:
            for r in result["repos"]:
                print(f"  {r['title']} ({r.get('implementation_status','?')})")
