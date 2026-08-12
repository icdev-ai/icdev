#!/usr/bin/env python3
# CUI // SP-CTI
"""NOVA Skill Generator — auto-generate skill specs from session patterns (adapt-hermes-04).

Reads repeated command/tool patterns from session history (via session_indexer FTS5),
identifies high-frequency patterns that suggest a reusable skill is missing, and
generates an ICDEV™ skill specification for review.  Generated specs are queued in
agent_improvement_artifacts for Continuous Harness evaluation (SELA pipeline).

Usage:
    python tools/nova/skill_generator.py --analyze --json
    python tools/nova/skill_generator.py --generate "database migration" --json
    python tools/nova/skill_generator.py --list-queued --json
    python tools/nova/skill_generator.py --generate "pytest tests/" --dry-run --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Patterns to detect in session turn content
_COMMAND_PATTERNS = [
    (r"python tools/\w+/\w+\.py(?:\s+--\S+)*", "tool_invocation"),
    (r"pytest tests/test_\w+\.py", "test_run"),
    (r"icdev \w[\w-]*", "cli_command"),
    (r"from (?:tools|icdev\.tools)\.\w+\.\w+ import \w+", "import_pattern"),
    (r"python -m tools\.\w+(?:\.\w+)?", "module_run"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gen_id(prefix: str = "sg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


def _ph(conn) -> str:
    """Return the correct SQL placeholder for the active connection."""
    try:
        conn.execute("SELECT sqlite_version()")
        return "?"
    except Exception:
        return "%s"


# ---------------------------------------------------------------------------
# Pattern analysis
# ---------------------------------------------------------------------------

def analyze_patterns(limit: int = 50, min_count: int = 2) -> list[dict]:
    """Scan session history for repeated command patterns.

    Reads memory_entries rows with type LIKE 'session_%' and applies regex
    matching to surface high-frequency command patterns that suggest a
    reusable ICDEV™ skill might be worth generating.

    Returns:
        List of {pattern, count, category, example} dicts, sorted by count desc.
    """
    conn = _get_conn()
    pattern_counts: dict[str, dict] = {}
    ph = _ph(conn)

    try:
        rows = conn.execute(
            f"""SELECT content FROM memory_entries
                WHERE type LIKE {ph}
                ORDER BY created_at DESC
                LIMIT {ph}""",
            ("session_%", limit * 10),
        ).fetchall()

        for row in rows:
            content = row[0] if isinstance(row, (list, tuple)) else row["content"]
            if not content:
                continue
            for regex, category in _COMMAND_PATTERNS:
                for match in re.finditer(regex, content):
                    key = match.group(0)[:80]
                    if key not in pattern_counts:
                        pattern_counts[key] = {
                            "pattern": key,
                            "count": 0,
                            "category": category,
                            "example": content[:120],
                        }
                    pattern_counts[key]["count"] += 1

    except Exception:
        pass
    finally:
        conn.close()

    results = sorted(
        [v for v in pattern_counts.values() if v["count"] >= min_count],
        key=lambda x: x["count"],
        reverse=True,
    )
    return results[:limit]


# ---------------------------------------------------------------------------
# Skill spec generation
# ---------------------------------------------------------------------------

def generate_skill_spec(
    pattern: str,
    category: str = "general",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate an ICDEV™ skill spec markdown for a given pattern.

    Uses scanner-tier LLM when available; falls back to a structured template.
    Queues the result in agent_improvement_artifacts unless dry_run=True.

    Returns:
        {skill_id, skill_name, pattern, category, spec_preview, queued, dry_run}
    """
    skill_id = _gen_id("sg")
    slug = re.sub(r"[^a-z0-9]+", "-", pattern.lower())[:40].strip("-")
    skill_name = f"icdev-{slug}"

    spec_text = _llm_generate_spec(pattern, skill_name)

    result: dict[str, Any] = {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "pattern": pattern,
        "category": category,
        "spec_preview": spec_text[:300],
        "queued": False,
        "dry_run": dry_run,
    }

    if not dry_run:
        result["queued"] = _queue_for_harness(skill_id, skill_name, spec_text, pattern)

    return result


# exa-refine-02: this was an f-string literal inside _llm_generate_spec. The text
# is unchanged; the two interpolations became named placeholders so the template
# can be versioned in the prompt registry under SPEC_PROMPT_NAME. Only the LLM
# PROMPT is registered — the deterministic fallback spec below it stays a module
# literal, because it is the output used when the LLM is unreachable and must not
# depend on the database being reachable either.
SPEC_PROMPT_NAME = "call_site/nova_skill_spec"
SPEC_PROMPT_TEMPLATE = (
    "Generate a concise ICDEV™ skill specification in markdown for automating:\n\n"
    "Pattern: {pattern}\n\n"
    "Format:\n"
    "# {skill_name}\n"
    "## When to use\n[1-2 sentences]\n\n"
    "## Steps\n1. ...\n\n"
    "## Acceptance criteria\n- [ ] ...\n"
)


def _build_spec_prompt(pattern: str, skill_name: str) -> str:
    """Render the skill-spec prompt: active registry version, else the module default."""
    from tools.llm.prompt_registry import render_prompt

    return render_prompt(
        SPEC_PROMPT_NAME,
        SPEC_PROMPT_TEMPLATE,
        pattern=pattern,
        skill_name=skill_name,
    )


def _llm_generate_spec(pattern: str, skill_name: str) -> str:
    """Generate skill spec via scanner-tier LLM; falls back to template."""
    prompt = _build_spec_prompt(pattern, skill_name)
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        response = router.invoke("memory_consolidation", request)
        if response and response.content and response.content.strip():
            return response.content.strip()
    except Exception:
        pass

    # Fallback template
    return (
        f"# {skill_name}\n\n"
        f"## When to use\n"
        f"When you need to automate: `{pattern}`\n\n"
        f"## Steps\n"
        f"1. Identify input parameters\n"
        f"2. Execute the pattern\n"
        f"3. Verify output matches expected format\n\n"
        f"## Acceptance criteria\n"
        f"- [ ] Pattern executes without error\n"
        f"- [ ] Output is valid JSON when `--json` flag supplied\n"
        f"- [ ] Audit trail entry written on completion\n"
    )


def _queue_for_harness(
    skill_id: str, skill_name: str, spec_text: str, source_pattern: str
) -> bool:
    """Insert generated spec into agent_improvement_artifacts for SELA evaluation."""
    conn = _get_conn()
    ph = _ph(conn)
    try:
        evidence = json.dumps({"source_pattern": source_pattern, "generator": "hermes_skill_generator"})
        conn.execute(
            f"""INSERT INTO agent_improvement_artifacts
                (artifact_id, task_type, skill_used, generation_n,
                 improvement_text, composite_score, baseline_score,
                 evidence_traces, applied_count, status, created_at)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
            (
                skill_id,
                "skill_generation",
                skill_name,
                1,
                spec_text,
                0.0,
                0.0,
                evidence,
                0,
                "pending",
                _now_iso(),
            ),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# List queued specs
# ---------------------------------------------------------------------------

def list_queued(limit: int = 20) -> list[dict]:
    """Return queued skill specs from agent_improvement_artifacts."""
    conn = _get_conn()
    ph = _ph(conn)
    try:
        rows = conn.execute(
            f"""SELECT artifact_id, skill_used, composite_score, status, created_at
                FROM agent_improvement_artifacts
                WHERE task_type = {ph}
                ORDER BY created_at DESC
                LIMIT {ph}""",
            ("skill_generation", limit),
        ).fetchall()

        results = []
        for row in rows:
            if hasattr(row, "keys"):
                results.append(dict(row))
            else:
                results.append({
                    "artifact_id": row[0],
                    "skill_used": row[1],
                    "composite_score": row[2],
                    "status": row[3],
                    "created_at": str(row[4]) if row[4] else None,
                })
        return results
    except Exception:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="NOVA Skill Generator — Hermes adapt-hermes-04"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze session history for recurring command patterns",
    )
    parser.add_argument(
        "--generate", metavar="PATTERN",
        help="Generate a skill spec for the given pattern string",
    )
    parser.add_argument(
        "--list-queued", action="store_true",
        help="List queued skill specs awaiting Continuous Harness evaluation",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument(
        "--min-count", type=int, default=2,
        help="Minimum pattern occurrences to surface (default: 2)",
    )
    parser.add_argument("--category", default="general", help="Category for --generate")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate spec without queuing to DB",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    if args.analyze:
        patterns = analyze_patterns(limit=args.limit, min_count=args.min_count)
        if args.as_json:
            print(json.dumps({
                "classification": "CUI // SP-CTI",
                "count": len(patterns),
                "patterns": patterns,
            }))
        else:
            for p in patterns:
                print(f"[{p['count']}x] [{p['category']}] {p['pattern']}")

    elif args.generate:
        result = generate_skill_spec(
            args.generate, category=args.category, dry_run=args.dry_run
        )
        if args.as_json:
            print(json.dumps({"classification": "CUI // SP-CTI", **result}))
        else:
            print(f"Skill   : {result['skill_name']} (id: {result['skill_id']})")
            print(f"Queued  : {result['queued']}")
            print(f"Preview : {result['spec_preview'][:120]}")

    elif args.list_queued:
        queued = list_queued(limit=args.limit)
        if args.as_json:
            print(json.dumps({
                "classification": "CUI // SP-CTI",
                "count": len(queued),
                "queued": queued,
            }))
        else:
            for q in queued:
                name = q.get("skill_used", "unknown")
                aid = q.get("artifact_id", "?")
                status = q.get("status", "?")
                ts = q.get("created_at", "?")
                print(f"[{status}] {name} ({aid}) @ {ts}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli()
