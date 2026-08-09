
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Prompt Version Control for ICDEV™.

Tracks prompt templates with full versioning, A/B testing, rollback,
and audit trail.  100% local SQLite — no cloud dependencies.

Usage:
    python tools/llm/prompt_registry.py --list --json
    python tools/llm/prompt_registry.py --register --name "code_gen" \\
        --template-file hardprompts/code_gen.md --function code_generation --json
    python tools/llm/prompt_registry.py --activate --name "code_gen" --version 2 --json
    python tools/llm/prompt_registry.py --rollback --name "code_gen" --to-version 1 --json
    python tools/llm/prompt_registry.py --diff --name "code_gen" --v1 1 --v2 2 --json
    python tools/llm/prompt_registry.py --import-hardprompts --json
    python tools/llm/prompt_registry.py --gate
    python tools/llm/prompt_registry.py --start-ab --name "code_gen" --va 1 --vb 2 --split 0.5 --json
"""

import argparse
import difflib
import hashlib
import json
import random
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection

logger = get_logger("icdev.llm.prompt_registry")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
HARDPROMPTS_DIR = BASE_DIR / "hardprompts"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prompt_versions (
    id              TEXT PRIMARY KEY,
    prompt_name     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    template_text   TEXT NOT NULL,
    template_hash   TEXT NOT NULL,
    variables       TEXT DEFAULT '[]',
    function_name   TEXT,
    status          TEXT NOT NULL DEFAULT 'draft'
                        CHECK(status IN ('draft', 'active', 'deprecated', 'archived')),
    ab_weight       REAL NOT NULL DEFAULT 1.0,
    created_by      TEXT DEFAULT 'system',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(prompt_name, version)
);

CREATE TABLE IF NOT EXISTS prompt_ab_tests (
    id                  TEXT PRIMARY KEY,
    prompt_name         TEXT NOT NULL,
    variant_a_version   INTEGER NOT NULL,
    variant_b_version   INTEGER NOT NULL,
    traffic_split       REAL NOT NULL DEFAULT 0.5,
    status              TEXT NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active', 'completed', 'cancelled')),
    metrics_json        TEXT DEFAULT '{"a": [], "b": []}',
    winner              TEXT,
    created_at          TEXT NOT NULL,
    completed_at        TEXT
);

CREATE TABLE IF NOT EXISTS prompt_audit_log (
    id          TEXT PRIMARY KEY,
    prompt_name TEXT NOT NULL,
    version     INTEGER,
    action      TEXT NOT NULL
                    CHECK(action IN ('created', 'activated', 'deprecated',
                                     'archived', 'rolled_back',
                                     'ab_started', 'ab_completed', 'ab_cancelled')),
    actor       TEXT DEFAULT 'system',
    details     TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);
"""


def _init_tables(conn) -> None:
    """Ensure all prompt registry tables exist."""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _extract_variables(template_text: str) -> List[str]:
    """Extract Jinja2/Python-style template variables from text."""
    jinja_vars = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", template_text))
    python_vars = set(re.findall(r"\{(\w+)\}", template_text))
    return sorted(jinja_vars | python_vars)


def _audit(
    conn, prompt_name: str, version: Optional[int], action: str, actor: str = "system", details: Optional[dict] = None
) -> None:
    """Write to append-only audit log."""
    conn.execute(
        "INSERT INTO prompt_audit_log (id, prompt_name, version, action, actor, details, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (_new_id("paud-"), prompt_name, version, action, actor, json.dumps(details or {}), _now()),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_prompt(
    name: str, template_text: str, function_name: str, variables: Optional[List[str]] = None, created_by: str = "system"
) -> Dict[str, Any]:
    """Register a new version of a prompt template.

    Auto-increments version for the given name.  Deduplicates by hash — if the
    template text is identical to the latest version, returns the existing record.
    """
    conn = get_connection()
    _init_tables(conn)
    try:
        # Determine next version
        row = conn.execute(
            "SELECT MAX(version) AS mv FROM prompt_versions WHERE prompt_name = %s",
            (name,),
        ).fetchone()
        max_ver = (row["mv"] if isinstance(row, dict) else row[0]) if row else None
        next_ver = (max_ver or 0) + 1

        t_hash = _hash(template_text)

        # Dedup: if latest version has same hash, return it
        if max_ver:
            existing = conn.execute(
                "SELECT * FROM prompt_versions WHERE prompt_name = %s AND version = %s",
                (name, max_ver),
            ).fetchone()
            existing_hash = existing["template_hash"] if isinstance(existing, dict) else existing[4]
            if existing_hash == t_hash:
                return {
                    "status": "duplicate",
                    "prompt_name": name,
                    "version": max_ver,
                    "message": "Template unchanged from latest version",
                }

        detected_vars = variables or _extract_variables(template_text)
        now = _now()
        pid = _new_id("prom-")

        conn.execute(
            "INSERT INTO prompt_versions "
            "(id, prompt_name, version, template_text, template_hash, variables, "
            " function_name, status, ab_weight, created_by, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft', 1.0, %s, %s, %s)",
            (
                pid,
                name,
                next_ver,
                template_text,
                t_hash,
                json.dumps(detected_vars),
                function_name,
                created_by,
                now,
                now,
            ),
        )
        _audit(conn, name, next_ver, "created", created_by, {"hash": t_hash, "function_name": function_name})
        conn.commit()
        return {
            "status": "ok",
            "prompt_name": name,
            "version": next_ver,
            "id": pid,
            "hash": t_hash,
            "variables": detected_vars,
        }
    finally:
        conn.close()


def activate_prompt(name: str, version: int, actor: str = "system") -> Dict[str, Any]:
    """Set a prompt version to active, deprecating the previously active one."""
    conn = get_connection()
    _init_tables(conn)
    try:
        row = conn.execute(
            "SELECT id, status FROM prompt_versions WHERE prompt_name = %s AND version = %s",
            (name, version),
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"Prompt {name} v{version} not found"}

        # Deprecate current active
        conn.execute(
            "UPDATE prompt_versions SET status = 'deprecated', updated_at = %s "
            "WHERE prompt_name = %s AND status = 'active'",
            (_now(), name),
        )
        # Activate requested version
        now = _now()
        conn.execute(
            "UPDATE prompt_versions SET status = 'active', updated_at = %s WHERE prompt_name = %s AND version = %s",
            (now, name, version),
        )
        _audit(conn, name, version, "activated", actor)
        conn.commit()
        return {"status": "ok", "prompt_name": name, "version": version, "message": f"v{version} is now active"}
    finally:
        conn.close()


def get_active_prompt(name: str) -> Optional[Dict[str, Any]]:
    """Return the current active prompt version, respecting A/B split."""
    conn = get_connection()
    _init_tables(conn)
    try:
        # Check for active A/B test first
        ab = conn.execute(
            "SELECT * FROM prompt_ab_tests WHERE prompt_name = %s AND status = 'active'",
            (name,),
        ).fetchone()
        if ab:
            ab_dict = dict(ab) if isinstance(ab, sqlite3.Row) else ab
            split = ab_dict["traffic_split"] if isinstance(ab_dict, dict) else ab_dict[4]
            va_ver = ab_dict["variant_a_version"] if isinstance(ab_dict, dict) else ab_dict[2]
            vb_ver = ab_dict["variant_b_version"] if isinstance(ab_dict, dict) else ab_dict[3]
            chosen_ver = vb_ver if random.random() < split else va_ver
            row = conn.execute(
                "SELECT * FROM prompt_versions WHERE prompt_name = %s AND version = %s",
                (name, chosen_ver),
            ).fetchone()
            if row:
                result = dict(row) if isinstance(row, sqlite3.Row) else row
                if isinstance(result, dict):
                    result["ab_test_id"] = ab_dict["id"] if isinstance(ab_dict, dict) else ab_dict[0]
                    result["ab_variant"] = "b" if chosen_ver == vb_ver else "a"
                return result

        # No A/B — return active version
        row = conn.execute(
            "SELECT * FROM prompt_versions WHERE prompt_name = %s AND status = 'active' ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
        if row:
            return dict(row) if isinstance(row, sqlite3.Row) else row
        return None
    finally:
        conn.close()


def rollback_prompt(name: str, to_version: int, actor: str = "system") -> Dict[str, Any]:
    """Reactivate a previous version (deprecates current active)."""
    conn = get_connection()
    _init_tables(conn)
    try:
        row = conn.execute(
            "SELECT id FROM prompt_versions WHERE prompt_name = %s AND version = %s",
            (name, to_version),
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"Prompt {name} v{to_version} not found"}

        conn.execute(
            "UPDATE prompt_versions SET status = 'deprecated', updated_at = %s "
            "WHERE prompt_name = %s AND status = 'active'",
            (_now(), name),
        )
        now = _now()
        conn.execute(
            "UPDATE prompt_versions SET status = 'active', updated_at = %s WHERE prompt_name = %s AND version = %s",
            (now, name, to_version),
        )
        _audit(conn, name, to_version, "rolled_back", actor)
        conn.commit()
        return {"status": "ok", "prompt_name": name, "version": to_version, "message": f"Rolled back to v{to_version}"}
    finally:
        conn.close()


def diff_versions(name: str, v1: int, v2: int) -> Dict[str, Any]:
    """Return a unified diff between two prompt versions."""
    conn = get_connection()
    _init_tables(conn)
    try:
        r1 = conn.execute(
            "SELECT template_text FROM prompt_versions WHERE prompt_name = %s AND version = %s",
            (name, v1),
        ).fetchone()
        r2 = conn.execute(
            "SELECT template_text FROM prompt_versions WHERE prompt_name = %s AND version = %s",
            (name, v2),
        ).fetchone()
        if not r1 or not r2:
            missing = []
            if not r1:
                missing.append(f"v{v1}")
            if not r2:
                missing.append(f"v{v2}")
            return {"status": "error", "message": f"Version(s) not found: {', '.join(missing)}"}

        text1 = r1["template_text"] if isinstance(r1, dict) else r1[0]
        text2 = r2["template_text"] if isinstance(r2, dict) else r2[0]

        diff_lines = list(
            difflib.unified_diff(
                text1.splitlines(keepends=True),
                text2.splitlines(keepends=True),
                fromfile=f"{name} v{v1}",
                tofile=f"{name} v{v2}",
            )
        )
        return {
            "status": "ok",
            "prompt_name": name,
            "from_version": v1,
            "to_version": v2,
            "diff": "".join(diff_lines),
            "lines_added": sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")),
            "lines_removed": sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---")),
        }
    finally:
        conn.close()


def start_ab_test(
    name: str, version_a: int, version_b: int, split: float = 0.5, actor: str = "system"
) -> Dict[str, Any]:
    """Start an A/B test between two prompt versions."""
    if not 0.0 <= split <= 1.0:
        return {"status": "error", "message": "split must be between 0.0 and 1.0"}

    conn = get_connection()
    _init_tables(conn)
    try:
        # Verify both versions exist
        for v in (version_a, version_b):
            row = conn.execute(
                "SELECT id FROM prompt_versions WHERE prompt_name = %s AND version = %s",
                (name, v),
            ).fetchone()
            if not row:
                return {"status": "error", "message": f"Prompt {name} v{v} not found"}

        # Cancel any existing active test for this prompt
        conn.execute(
            "UPDATE prompt_ab_tests SET status = 'cancelled', completed_at = %s "
            "WHERE prompt_name = %s AND status = 'active'",
            (_now(), name),
        )

        test_id = _new_id("abt-")
        now = _now()
        conn.execute(
            "INSERT INTO prompt_ab_tests "
            "(id, prompt_name, variant_a_version, variant_b_version, "
            " traffic_split, status, metrics_json, created_at) "
            "VALUES (%s, %s, %s, %s, %s, 'active', %s, %s)",
            (test_id, name, version_a, version_b, split, json.dumps({"a": [], "b": []}), now),
        )
        _audit(
            conn,
            name,
            None,
            "ab_started",
            actor,
            {"test_id": test_id, "variant_a": version_a, "variant_b": version_b, "split": split},
        )
        conn.commit()
        return {
            "status": "ok",
            "test_id": test_id,
            "prompt_name": name,
            "variant_a": version_a,
            "variant_b": version_b,
            "traffic_split": split,
        }
    finally:
        conn.close()


def record_ab_result(test_id: str, variant: str, quality_score: float) -> Dict[str, Any]:
    """Record a quality score for an A/B test variant."""
    if variant not in ("a", "b"):
        return {"status": "error", "message": "variant must be 'a' or 'b'"}

    conn = get_connection()
    _init_tables(conn)
    try:
        row = conn.execute(
            "SELECT metrics_json, status FROM prompt_ab_tests WHERE id = %s",
            (test_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"Test {test_id} not found"}
        row_status = row["status"] if isinstance(row, dict) else row[1]
        if row_status != "active":
            return {"status": "error", "message": f"Test {test_id} is not active"}

        metrics_raw = row["metrics_json"] if isinstance(row, dict) else row[0]
        metrics = json.loads(metrics_raw)
        metrics[variant].append(quality_score)

        conn.execute(
            "UPDATE prompt_ab_tests SET metrics_json = %s WHERE id = %s",
            (json.dumps(metrics), test_id),
        )
        conn.commit()
        return {
            "status": "ok",
            "test_id": test_id,
            "variant": variant,
            "score": quality_score,
            "total_a": len(metrics["a"]),
            "total_b": len(metrics["b"]),
        }
    finally:
        conn.close()


def complete_ab_test(test_id: str, actor: str = "system") -> Dict[str, Any]:
    """Complete an A/B test — pick the winner by average score, activate it."""
    conn = get_connection()
    _init_tables(conn)
    try:
        row = conn.execute(
            "SELECT * FROM prompt_ab_tests WHERE id = %s AND status = 'active'",
            (test_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"Active test {test_id} not found"}

        row_dict = dict(row) if isinstance(row, sqlite3.Row) else row
        metrics = json.loads(row_dict["metrics_json"] if isinstance(row_dict, dict) else row_dict[6])

        avg_a = sum(metrics["a"]) / len(metrics["a"]) if metrics["a"] else 0.0
        avg_b = sum(metrics["b"]) / len(metrics["b"]) if metrics["b"] else 0.0
        winner = "b" if avg_b > avg_a else "a"

        prompt_name = row_dict["prompt_name"] if isinstance(row_dict, dict) else row_dict[1]
        winner_ver = (
            (row_dict["variant_b_version"] if isinstance(row_dict, dict) else row_dict[3])
            if winner == "b"
            else (row_dict["variant_a_version"] if isinstance(row_dict, dict) else row_dict[2])
        )

        now = _now()
        conn.execute(
            "UPDATE prompt_ab_tests SET status = 'completed', winner = %s, completed_at = %s WHERE id = %s",
            (winner, now, test_id),
        )

        # Activate the winning version
        conn.execute(
            "UPDATE prompt_versions SET status = 'deprecated', updated_at = %s "
            "WHERE prompt_name = %s AND status = 'active'",
            (now, prompt_name),
        )
        conn.execute(
            "UPDATE prompt_versions SET status = 'active', updated_at = %s WHERE prompt_name = %s AND version = %s",
            (now, prompt_name, winner_ver),
        )

        _audit(
            conn,
            prompt_name,
            winner_ver,
            "ab_completed",
            actor,
            {
                "test_id": test_id,
                "winner": winner,
                "avg_a": round(avg_a, 4),
                "avg_b": round(avg_b, 4),
                "samples_a": len(metrics["a"]),
                "samples_b": len(metrics["b"]),
            },
        )
        conn.commit()
        return {
            "status": "ok",
            "test_id": test_id,
            "winner": winner,
            "winner_version": winner_ver,
            "prompt_name": prompt_name,
            "avg_a": round(avg_a, 4),
            "avg_b": round(avg_b, 4),
        }
    finally:
        conn.close()


def list_prompts() -> List[Dict[str, Any]]:
    """List all registered prompts with their active version info."""
    conn = get_connection()
    _init_tables(conn)
    try:
        rows = conn.execute(
            "SELECT prompt_name, version, status, function_name, template_hash, "
            "       ab_weight, created_by, created_at, updated_at "
            "FROM prompt_versions ORDER BY prompt_name, version",
        ).fetchall()
        results = []
        for r in rows:
            if isinstance(r, (dict, sqlite3.Row)):
                results.append(dict(r))
            else:
                results.append(
                    {
                        "prompt_name": r[0],
                        "version": r[1],
                        "status": r[2],
                        "function_name": r[3],
                        "template_hash": r[4],
                        "ab_weight": r[5],
                        "created_by": r[6],
                        "created_at": r[7],
                        "updated_at": r[8],
                    }
                )
        return results
    finally:
        conn.close()


def import_from_hardprompts(directory: Optional[Path] = None) -> Dict[str, Any]:
    """Scan hardprompts/ directory and register existing templates as v1."""
    hp_dir = directory or HARDPROMPTS_DIR
    if not hp_dir.is_dir():
        return {"status": "error", "message": f"Directory not found: {hp_dir}"}

    imported = []
    skipped = []
    for fp in sorted(hp_dir.iterdir()):
        if fp.suffix not in (".md", ".txt", ".jinja2", ".j2"):
            continue
        name = fp.stem
        template_text = fp.read_text(encoding="utf-8")
        # Derive function name from filename (best-effort)
        fn_name = name.replace("-", "_").replace(" ", "_")
        result = register_prompt(name, template_text, fn_name)
        if result.get("status") == "duplicate":
            skipped.append(name)
        else:
            # Auto-activate imported prompts
            activate_prompt(name, result["version"])
            imported.append({"name": name, "version": result["version"]})

    return {
        "status": "ok",
        "imported": imported,
        "skipped": skipped,
        "total_imported": len(imported),
        "total_skipped": len(skipped),
    }


def gate_check() -> Dict[str, Any]:
    """Gate check: every registered prompt must have at least one active version.

    Returns exit code 0 if all pass, 1 if any prompt lacks an active version.
    """
    conn = get_connection()
    _init_tables(conn)
    try:
        all_names = conn.execute(
            "SELECT DISTINCT prompt_name FROM prompt_versions",
        ).fetchall()
        if not all_names:
            return {
                "status": "ok",
                "gate": "pass",
                "message": "No prompts registered — gate passes vacuously",
                "total": 0,
                "missing_active": [],
            }

        missing = []
        for row in all_names:
            name = row["prompt_name"] if isinstance(row, dict) else row[0]
            active = conn.execute(
                "SELECT 1 FROM prompt_versions WHERE prompt_name = %s AND status = 'active' LIMIT 1",
                (name,),
            ).fetchone()
            if not active:
                missing.append(name)

        total = len(all_names)
        if missing:
            return {
                "status": "error",
                "gate": "fail",
                "message": f"{len(missing)}/{total} prompt(s) lack an active version",
                "total": total,
                "missing_active": missing,
            }
        return {
            "status": "ok",
            "gate": "pass",
            "message": f"All {total} prompt(s) have an active version",
            "total": total,
            "missing_active": [],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="ICDEV™ Prompt Version Control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--gate", action="store_true", help="Gate check — exit 1 if any prompt lacks active version")

    g = p.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="List all prompts")
    g.add_argument("--register", action="store_true", help="Register a new prompt version")
    g.add_argument("--activate", action="store_true", help="Activate a prompt version")
    g.add_argument("--rollback", action="store_true", help="Rollback to a previous version")
    g.add_argument("--diff", action="store_true", help="Diff two prompt versions")
    g.add_argument("--import-hardprompts", action="store_true", help="Import templates from hardprompts/ directory")
    g.add_argument("--start-ab", action="store_true", help="Start an A/B test")

    p.add_argument("--name", help="Prompt name")
    p.add_argument("--template-file", help="Path to template file")
    p.add_argument("--template-text", help="Inline template text")
    p.add_argument("--function", dest="function_name", help="ICDEV™ function name")
    p.add_argument("--version", type=int, help="Prompt version number")
    p.add_argument("--to-version", type=int, help="Target version for rollback")
    p.add_argument("--v1", type=int, help="First version for diff")
    p.add_argument("--v2", type=int, help="Second version for diff")
    p.add_argument("--va", type=int, help="A/B test variant A version")
    p.add_argument("--vb", type=int, help="A/B test variant B version")
    p.add_argument("--split", type=float, default=0.5, help="A/B traffic split (0.0-1.0, portion going to B)")
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    result: Any = None
    exit_code = 0

    if args.gate:
        result = gate_check()
        if result.get("gate") == "fail":
            exit_code = 1

    elif args.list:
        result = list_prompts()

    elif args.register:
        if not args.name:
            parser.error("--register requires --name")
        if not args.template_file and not args.template_text:
            parser.error("--register requires --template-file or --template-text")
        if args.template_file:
            tp = Path(args.template_file)
            if not tp.is_absolute():
                tp = BASE_DIR / tp
            template = tp.read_text(encoding="utf-8")
        else:
            template = args.template_text
        result = register_prompt(
            args.name,
            template,
            args.function_name or args.name.replace("-", "_"),
        )

    elif args.activate:
        if not args.name or args.version is None:
            parser.error("--activate requires --name and --version")
        result = activate_prompt(args.name, args.version)

    elif args.rollback:
        if not args.name or args.to_version is None:
            parser.error("--rollback requires --name and --to-version")
        result = rollback_prompt(args.name, args.to_version)

    elif args.diff:
        if not args.name or args.v1 is None or args.v2 is None:
            parser.error("--diff requires --name, --v1, and --v2")
        result = diff_versions(args.name, args.v1, args.v2)

    elif args.import_hardprompts:
        result = import_from_hardprompts()

    elif args.start_ab:
        if not args.name or args.va is None or args.vb is None:
            parser.error("--start-ab requires --name, --va, and --vb")
        result = start_ab_test(args.name, args.va, args.vb, args.split)

    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if isinstance(result, list):
            for item in result:
                print(item)
        elif isinstance(result, dict):
            for k, v in result.items():
                print(f"  {k}: {v}")
        else:
            print(result)

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
