# CUI // SP-CTI
# ICDEV GovCon Knowledge Base — Phase 59 (D368)
# Reusable content blocks for proposal response drafting.

"""
Knowledge Base — CRUD for reusable proposal content blocks.

Organized by:
    - category: capability_description, approach, staffing, tools_used, past_performance, differentiator
    - domain: devsecops, ai_ml, ato_rmf, cloud, security, compliance, agile, data, management
    - volume_type: technical, management, past_performance, cost, staffing

Stores in proposal_knowledge_base table (allows UPDATE for refinement).

Usage:
    python tools/govcon/knowledge_base.py --list --json
    python tools/govcon/knowledge_base.py --search --query "DevSecOps pipeline" --json
    python tools/govcon/knowledge_base.py --add --title "..." --content "..." --category capability_description --domain devsecops --json
    python tools/govcon/knowledge_base.py --get --block-id <id> --json
    python tools/govcon/knowledge_base.py --seed --json
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _ROOT / "data" / "icdev.db"
_CATALOG_PATH = _ROOT / "context" / "govcon" / "icdev_capability_catalog.json"

_CATEGORIES = [
    "capability_description",
    "approach",
    "staffing",
    "tools_used",
    "past_performance",
    "differentiator",
    "management_approach",
    "transition_plan",
]

_DOMAINS = [
    "devsecops", "ai_ml", "ato_rmf", "cloud", "security",
    "compliance", "agile", "data", "management", "general",
]

_VOLUME_TYPES = [
    "technical", "management", "past_performance", "cost", "staffing",
]


# ── helpers ───────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _content_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _audit(conn, action, details="", actor="knowledge_base"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), _now(), "govcon.knowledge_base", actor, action, details, "govcon"),
        )
    except Exception:
        pass


# ── CRUD ──────────────────────────────────────────────────────────────

def add_block(title, content, category, domain, volume_type="technical",
              keywords=None, naics_codes=None, capability_ids=None):
    """Add a reusable content block to the knowledge base."""
    if category not in _CATEGORIES:
        return {"status": "error", "message": f"Invalid category: {category}. Valid: {_CATEGORIES}"}
    if domain not in _DOMAINS:
        return {"status": "error", "message": f"Invalid domain: {domain}. Valid: {_DOMAINS}"}

    block_id = str(uuid.uuid4())
    conn = _get_db()

    conn.execute(
        "INSERT INTO proposal_knowledge_base "
        "(id, title, content, category, domain, volume_type, keywords, "
        "naics_codes, usage_count, status, created_at, updated_at, classification) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            block_id, title, content, category, domain, volume_type,
            json.dumps(keywords or []),
            json.dumps(naics_codes or []),
            0,
            "active",
            _now(), _now(),
            "CUI // SP-CTI",
        ),
    )
    _audit(conn, "add_block", f"Added '{title}' ({category}/{domain})")
    conn.commit()
    conn.close()

    return {"status": "ok", "block_id": block_id, "title": title}


def get_block(block_id):
    """Get a single knowledge block."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM proposal_knowledge_base WHERE id = ?", (block_id,)).fetchone()
    conn.close()

    if not row:
        return {"status": "error", "message": f"Block {block_id} not found"}

    return {"status": "ok", "block": dict(row)}


def list_blocks(domain=None, category=None, volume_type=None, limit=50):
    """List knowledge blocks with optional filters."""
    conn = _get_db()

    query = "SELECT * FROM proposal_knowledge_base WHERE 1=1"
    params = []
    if domain:
        query += " AND domain = ?"
        params.append(domain)
    if category:
        query += " AND category = ?"
        params.append(category)
    if volume_type:
        query += " AND volume_type = ?"
        params.append(volume_type)
    query += " ORDER BY usage_count DESC, updated_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return {
        "status": "ok",
        "total": len(rows),
        "blocks": [dict(r) for r in rows],
    }


def search_blocks(query_text, domain=None, top_k=5):
    """Keyword search across knowledge blocks.

    Simple TF-based ranking: count query terms in title + content + keywords.
    """
    conn = _get_db()

    sql = "SELECT * FROM proposal_knowledge_base WHERE 1=1"
    params = []
    if domain:
        sql += " AND domain = ?"
        params.append(domain)

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        return {"status": "ok", "results": [], "query": query_text}

    query_terms = [t.lower().strip() for t in query_text.split() if len(t) > 2]
    scored = []

    for row in rows:
        r = dict(row)
        searchable = f"{r.get('title', '')} {r.get('content', '')} {r.get('keywords', '')}".lower()

        score = 0
        for term in query_terms:
            score += searchable.count(term)

        if score > 0:
            r["relevance_score"] = score
            scored.append(r)

    scored.sort(key=lambda x: x["relevance_score"], reverse=True)

    return {
        "status": "ok",
        "query": query_text,
        "results": scored[:top_k],
    }


def increment_usage(block_id):
    """Increment usage count for a knowledge block."""
    conn = _get_db()
    conn.execute(
        "UPDATE proposal_knowledge_base SET usage_count = usage_count + 1, updated_at = ? WHERE id = ?",
        (_now(), block_id),
    )
    conn.commit()
    conn.close()


# ── seeding from capability catalog ──────────────────────────────────

def seed_from_catalog():
    """Seed knowledge base from ICDEV capability catalog.

    Creates one 'capability_description' and one 'tools_used' block per capability.
    """
    if not _CATALOG_PATH.exists():
        return {"status": "error", "message": "Capability catalog not found"}

    with open(_CATALOG_PATH) as f:
        catalog = json.load(f)

    capabilities = catalog.get("capabilities", [])
    conn = _get_db()
    created = 0

    for cap in capabilities:
        cap_id = cap["id"]
        name = cap["name"]
        category = cap.get("category", "general")
        description = cap.get("description", "")
        evidence = cap.get("evidence", "")
        tools = cap.get("tools", [])
        controls = cap.get("compliance_controls", [])
        keywords = cap.get("keywords", [])

        # Check if already seeded
        existing = conn.execute(
            "SELECT id FROM proposal_knowledge_base WHERE title = ? AND category = 'capability_description'",
            (name,),
        ).fetchone()
        if existing:
            continue

        # Capability description block
        content = (
            f"{description}\n\n"
            f"Key Evidence: {evidence}\n\n"
            f"NIST 800-53 Controls: {', '.join(controls)}\n\n"
            f"This capability is implemented through {len(tools)} dedicated tools "
            f"in the ICDEV platform, providing automated, repeatable, and auditable "
            f"execution of {category} operations."
        )
        conn.execute(
            "INSERT INTO proposal_knowledge_base "
            "(id, title, content, category, domain, volume_type, keywords, "
            "naics_codes, usage_count, status, created_at, updated_at, classification) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), name, content, "capability_description",
                category if category in _DOMAINS else "general",
                "technical",
                json.dumps(keywords[:10]),
                json.dumps([]),
                0, "active", _now(), _now(), "CUI // SP-CTI",
            ),
        )
        created += 1

        # Tools block
        if tools:
            tools_content = (
                f"ICDEV implements {name} through the following automated tools:\n\n"
                + "\n".join(f"- {t}" for t in tools) + "\n\n"
                f"All tools are deterministic Python scripts following the GOTCHA framework. "
                f"They produce reproducible, auditable output with CUI markings and audit trail logging."
            )
            conn.execute(
                "INSERT INTO proposal_knowledge_base "
                "(id, title, content, category, domain, volume_type, keywords, "
                "naics_codes, usage_count, status, created_at, updated_at, classification) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), f"{name} — Tools", tools_content, "tools_used",
                    category if category in _DOMAINS else "general",
                    "technical",
                    json.dumps(keywords[:5]),
                    json.dumps([]),
                    0, "active", _now(), _now(), "CUI // SP-CTI",
                ),
            )
            created += 1

    _audit(conn, "seed_catalog", f"Seeded {created} blocks from {len(capabilities)} capabilities")
    conn.commit()
    conn.close()

    return {"status": "ok", "blocks_created": created, "capabilities_processed": len(capabilities)}


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovCon Knowledge Base (D368)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List knowledge blocks")
    group.add_argument("--search", action="store_true", help="Search knowledge blocks")
    group.add_argument("--add", action="store_true", help="Add a knowledge block")
    group.add_argument("--get", action="store_true", help="Get a single block")
    group.add_argument("--seed", action="store_true", help="Seed from capability catalog")

    parser.add_argument("--query", help="Search query text")
    parser.add_argument("--block-id", help="Block ID for --get")
    parser.add_argument("--title", help="Block title for --add")
    parser.add_argument("--content", help="Block content for --add")
    parser.add_argument("--category", help="Category filter or for --add")
    parser.add_argument("--domain", help="Domain filter or for --add")
    parser.add_argument("--volume-type", help="Volume type filter or for --add")
    parser.add_argument("--top-k", type=int, default=5, help="Search result limit")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.list:
        result = list_blocks(domain=args.domain, category=args.category, volume_type=args.volume_type)
    elif args.search:
        if not args.query:
            print("Error: --query required for search", file=sys.stderr)
            sys.exit(1)
        result = search_blocks(args.query, domain=args.domain, top_k=args.top_k)
    elif args.add:
        if not all([args.title, args.content, args.category, args.domain]):
            print("Error: --title, --content, --category, --domain required", file=sys.stderr)
            sys.exit(1)
        result = add_block(args.title, args.content, args.category, args.domain,
                          volume_type=args.volume_type or "technical")
    elif args.get:
        if not args.block_id:
            print("Error: --block-id required", file=sys.stderr)
            sys.exit(1)
        result = get_block(args.block_id)
    elif args.seed:
        result = seed_from_catalog()

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
