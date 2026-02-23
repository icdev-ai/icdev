#!/usr/bin/env python3
# CUI // SP-CTI
"""Register external framework patterns as innovation signals (Phase 44 — D279).

Registers patterns discovered from Agent Zero and InsForge as innovation
signals using existing innovation_signals and innovation_solutions tables.
Source type: external_framework_analysis.

Usage:
    python tools/innovation/register_external_patterns.py --register-all --json
    python tools/innovation/register_external_patterns.py --status --json
    python tools/innovation/register_external_patterns.py --score-all --json
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Centralized DB path resolution (D145 pattern)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tools.compat.db_utils import get_icdev_db_path

DB_PATH = get_icdev_db_path()

# ---------------------------------------------------------------------------
# External patterns from Agent Zero + InsForge analysis
# ---------------------------------------------------------------------------

EXTERNAL_PATTERNS = [
    {
        "source": "agent-zero",
        "title": "Multi-Stream Parallel Chat",
        "description": (
            "Thread-per-context execution model with independent message queues "
            "and agent threads. Enables multiple simultaneous conversations without "
            "blocking. Adapted from Agent Zero's DeferredTask pattern."
        ),
        "category": "architecture",
        "gotcha_layer": "tool",
        "url": "https://github.com/agent0ai/agent-zero",
        "scoring_hints": {
            "novelty": 0.85,
            "feasibility": 0.90,
            "compliance_alignment": 0.80,
            "user_impact": 0.90,
            "effort": 0.60,
        },
        "implementation_status": "implemented",
    },
    {
        "source": "agent-zero",
        "title": "Active Extension Hook System",
        "description": (
            "Numbered Python file extensions loaded from directories with "
            "behavioral (modify data) and observational (log only) tiers. "
            "Layered override: project > tenant > default. From Agent Zero's "
            "extension point architecture."
        ),
        "category": "architecture",
        "gotcha_layer": "tool",
        "url": "https://github.com/agent0ai/agent-zero",
        "scoring_hints": {
            "novelty": 0.70,
            "feasibility": 0.95,
            "compliance_alignment": 0.85,
            "user_impact": 0.75,
            "effort": 0.80,
        },
        "implementation_status": "implemented",
    },
    {
        "source": "agent-zero",
        "title": "Mid-Stream Intervention",
        "description": (
            "Atomic intervention flag on chat contexts checked at 3 points "
            "per agent loop iteration (pre-LLM, post-LLM, pre-queue-pop). "
            "Does not kill thread. Current progress saved to checkpoint. "
            "From Agent Zero's user intervention pattern."
        ),
        "category": "architecture",
        "gotcha_layer": "tool",
        "url": "https://github.com/agent0ai/agent-zero",
        "scoring_hints": {
            "novelty": 0.80,
            "feasibility": 0.85,
            "compliance_alignment": 0.90,
            "user_impact": 0.85,
            "effort": 0.70,
        },
        "implementation_status": "implemented",
    },
    {
        "source": "agent-zero",
        "title": "Dirty-Tracking State Push",
        "description": (
            "Per-client dirty/pushed version counters with debounced SSE "
            "coalescing. Clients send ?since_version=N, receive only changes. "
            "Adapted from Agent Zero's StateMonitor pattern."
        ),
        "category": "architecture",
        "gotcha_layer": "tool",
        "url": "https://github.com/agent0ai/agent-zero",
        "scoring_hints": {
            "novelty": 0.65,
            "feasibility": 0.95,
            "compliance_alignment": 0.80,
            "user_impact": 0.70,
            "effort": 0.85,
        },
        "implementation_status": "implemented",
    },
    {
        "source": "agent-zero",
        "title": "3-Tier History Compression",
        "description": (
            "Current Topic 50%, Historical Topics 30%, Bulk 20% budget allocation. "
            "Topic boundary via time gap >30min OR keyword shift >60%. "
            "LLM summarization with truncation fallback. Air-gap safe."
        ),
        "category": "memory",
        "gotcha_layer": "tool",
        "url": "https://github.com/agent0ai/agent-zero",
        "scoring_hints": {
            "novelty": 0.75,
            "feasibility": 0.85,
            "compliance_alignment": 0.85,
            "user_impact": 0.80,
            "effort": 0.70,
        },
        "implementation_status": "implemented",
    },
    {
        "source": "insforge",
        "title": "Semantic Layer MCP Tools",
        "description": (
            "On-demand CLAUDE.md section delivery via MCP tools. Section indexing "
            "by ## headers, keyword search, role-tailored context, live metadata. "
            "Adapted from InsForge's context delivery pattern."
        ),
        "category": "architecture",
        "gotcha_layer": "context",
        "url": "https://github.com/InsForge/InsForge",
        "scoring_hints": {
            "novelty": 0.80,
            "feasibility": 0.90,
            "compliance_alignment": 0.85,
            "user_impact": 0.85,
            "effort": 0.75,
        },
        "implementation_status": "implemented",
    },
    {
        "source": "insforge",
        "title": "Shared Schema Enforcement",
        "description": (
            "stdlib dataclass models (ProjectStatus, AgentHealth, AuditEvent, etc.) "
            "with to_dict()/from_dict() for backward compatibility. validate_output() "
            "and wrap_mcp_response() for schema validation. From InsForge's Zod pattern."
        ),
        "category": "architecture",
        "gotcha_layer": "tool",
        "url": "https://github.com/InsForge/InsForge",
        "scoring_hints": {
            "novelty": 0.60,
            "feasibility": 0.95,
            "compliance_alignment": 0.90,
            "user_impact": 0.70,
            "effort": 0.90,
        },
        "implementation_status": "implemented",
    },
    {
        "source": "agent-zero",
        "title": "AI-Driven Memory Consolidation",
        "description": (
            "Hybrid search finds similar entries, LLM decides MERGE/REPLACE/"
            "KEEP_SEPARATE/UPDATE/SKIP. Jaccard keyword fallback when LLM unavailable. "
            "Consolidation log is append-only (D6)."
        ),
        "category": "memory",
        "gotcha_layer": "tool",
        "url": "https://github.com/agent0ai/agent-zero",
        "scoring_hints": {
            "novelty": 0.75,
            "feasibility": 0.80,
            "compliance_alignment": 0.85,
            "user_impact": 0.75,
            "effort": 0.65,
        },
        "implementation_status": "implemented",
    },
    {
        "source": "insforge",
        "title": "Dangerous Pattern Detection Enhancement",
        "description": (
            "Unified scanner across 6 languages (Python, Java, Go, Rust, C#, TypeScript). "
            "Declarative YAML patterns. Callable from marketplace, translation, "
            "child app gen, and security scanning. From InsForge's serverless guard."
        ),
        "category": "security",
        "gotcha_layer": "tool",
        "url": "https://github.com/InsForge/InsForge",
        "scoring_hints": {
            "novelty": 0.70,
            "feasibility": 0.90,
            "compliance_alignment": 0.95,
            "user_impact": 0.80,
            "effort": 0.80,
        },
        "implementation_status": "implemented",
    },
    {
        "source": "both",
        "title": "Innovation Signal Registration",
        "description": (
            "External framework patterns registered as innovation signals with "
            "5-dimension weighted scoring. Enables tracking which patterns from "
            "Agent Zero and InsForge have been implemented and their impact."
        ),
        "category": "innovation",
        "gotcha_layer": "tool",
        "url": "",
        "scoring_hints": {
            "novelty": 0.50,
            "feasibility": 0.95,
            "compliance_alignment": 0.80,
            "user_impact": 0.60,
            "effort": 0.90,
        },
        "implementation_status": "implemented",
    },
]

# Scoring weights (5-dimension)
SCORING_WEIGHTS = {
    "novelty": 0.20,
    "feasibility": 0.25,
    "compliance_alignment": 0.25,
    "user_impact": 0.20,
    "effort": 0.10,
}


# ---------------------------------------------------------------------------
# Registration functions
# ---------------------------------------------------------------------------

def _content_hash(text: str) -> str:
    """Generate content hash for dedup."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def register_pattern(pattern: dict, db_path: Path = DB_PATH) -> dict:
    """Register a single external pattern as an innovation signal.

    Returns: {signal_id, status, is_duplicate}
    """
    title = pattern["title"]
    description = pattern["description"]
    content_hash = _content_hash(title + description)

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Check for duplicate
        existing = conn.execute(
            "SELECT id FROM innovation_signals WHERE content_hash = ?",
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

        # Create signal
        signal_id = f"sig-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        # Compute score
        hints = pattern.get("scoring_hints", {})
        score = sum(
            hints.get(dim, 0.5) * weight
            for dim, weight in SCORING_WEIGHTS.items()
        )

        conn.execute(
            """INSERT INTO innovation_signals
               (id, source, source_type, title, description, url,
                category, innovation_score, score_breakdown, content_hash,
                status, gotcha_layer, implementation_status,
                classification, discovered_at, created_at)
               VALUES (?, ?, 'external_framework_analysis', ?, ?, ?,
                       ?, ?, ?, ?,
                       'triaged', ?, ?,
                       'CUI', ?, ?)""",
            (
                signal_id, pattern["source"], title, description,
                pattern.get("url", ""),
                pattern.get("category", "architecture"),
                round(score, 4),
                json.dumps(hints),
                content_hash,
                pattern.get("gotcha_layer", "Tools"),
                pattern.get("implementation_status", "pending"),
                now, now,
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
        }

    except sqlite3.OperationalError as exc:
        return {"error": str(exc), "title": title}


def register_all(db_path: Path = DB_PATH) -> dict:
    """Register all external patterns.

    Returns: {registered, skipped_duplicates, signals}
    """
    registered = 0
    skipped = 0
    signals = []

    for pattern in EXTERNAL_PATTERNS:
        result = register_pattern(pattern, db_path)
        signals.append(result)
        if result.get("is_duplicate"):
            skipped += 1
        elif "error" not in result:
            registered += 1

    return {
        "registered": registered,
        "skipped_duplicates": skipped,
        "total_patterns": len(EXTERNAL_PATTERNS),
        "signals": signals,
    }


def score_patterns() -> dict:
    """Apply 5-dimension weighted average scoring to all patterns.

    Returns: {patterns: [{title, score, breakdown}]}
    """
    scored = []
    for pattern in EXTERNAL_PATTERNS:
        hints = pattern.get("scoring_hints", {})
        score = sum(
            hints.get(dim, 0.5) * weight
            for dim, weight in SCORING_WEIGHTS.items()
        )
        scored.append({
            "title": pattern["title"],
            "source": pattern["source"],
            "score": round(score, 4),
            "breakdown": hints,
            "category": pattern.get("category", ""),
            "implementation_status": pattern.get("implementation_status", "pending"),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"patterns": scored, "scoring_weights": SCORING_WEIGHTS}


def get_implementation_status(db_path: Path = DB_PATH) -> dict:
    """Track which patterns have been implemented.

    Returns: {total, implemented, pending, patterns: [...]}
    """
    patterns = []
    for p in EXTERNAL_PATTERNS:
        patterns.append({
            "title": p["title"],
            "source": p["source"],
            "category": p.get("category", ""),
            "implementation_status": p.get("implementation_status", "pending"),
        })

    implemented = sum(1 for p in patterns if p["implementation_status"] == "implemented")
    return {
        "total": len(patterns),
        "implemented": implemented,
        "pending": len(patterns) - implemented,
        "patterns": patterns,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register external innovation patterns")
    parser.add_argument("--register-all", action="store_true", help="Register all patterns")
    parser.add_argument("--status", action="store_true", help="Show implementation status")
    parser.add_argument("--score-all", action="store_true", help="Score all patterns")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.register_all:
        result = register_all()
    elif args.status:
        result = get_implementation_status()
    elif args.score_all:
        result = score_patterns()
    else:
        result = {"error": "Use --register-all, --status, or --score-all"}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if "patterns" in result:
            for p in result["patterns"]:
                status = p.get("implementation_status", p.get("status", ""))
                score = p.get("score", "")
                print(f"  [{status}] {p['title']} (score={score})")
        if "registered" in result:
            print(f"\nRegistered: {result['registered']}, Duplicates: {result['skipped_duplicates']}")
        if "implemented" in result:
            print(f"\nImplemented: {result['implemented']}/{result['total']}")
