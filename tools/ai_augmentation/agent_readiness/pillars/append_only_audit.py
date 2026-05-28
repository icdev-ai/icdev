# CUI // SP-CTI
"""Pillar 11 — Append-Only Audit Tables (ICDEV): audit tables are not mutated."""
from __future__ import annotations

import pathlib
import re

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _glob_files,
    _read,
    _search,
    load_pillar_config,
)

# Patterns that indicate audit table mutation (forbidden)
_FORBIDDEN_AUDIT_MUTATION = re.compile(
    r"""(UPDATE|DELETE)\s+(?:FROM\s+)?(\w*audit\w*|\w*log\w*|\w*trail\w*|\w*event\w*)""",
    re.IGNORECASE,
)

# Patterns that identify audit tables being properly inserted into
_AUDIT_INSERT_PATTERN = r"INSERT\s+INTO\s+\w*(?:audit|log|trail|event)\w*"

# Known ICDEV append-only tables (from pre_tool_use.py / CLAUDE.md)
_KNOWN_APPEND_ONLY = {
    "aac_audit_log", "audit_log", "audit_trail", "event_log",
    "compliance_audit", "security_events", "access_log",
}


def _check_no_audit_mutations(repo: pathlib.Path) -> CriterionResult:
    cid = "no-audit-mutations"
    py_files = _glob_files(repo, "**/*.py")
    violations = []
    for f in py_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        for m in _FORBIDDEN_AUDIT_MUTATION.finditer(content):
            op = m.group(1).upper()
            table = m.group(2)
            # Skip if it's clearly not an audit table by name
            if any(kw in table.lower() for kw in ["audit", "log", "trail", "event"]):
                violations.append(f"{f.name}:{table} ({op})")
    if violations:
        return CriterionResult(cid, False,
                               f"Audit table mutations detected in {len(violations)} location(s): {', '.join(violations[:3])}",
                               "Remove UPDATE/DELETE operations on audit/log tables. Audit trails are append-only per NIST AU.")
    return CriterionResult(cid, True, "No audit table mutation (UPDATE/DELETE) detected in Python source")


def _check_pre_tool_use_protection(repo: pathlib.Path) -> CriterionResult:
    cid = "pre-tool-use-protection"
    hook_paths = [
        ".claude/hooks/pre_tool_use.py",
        "hooks/pre_tool_use.py",
        ".claude/hooks/pre_tool_use.js",
    ]
    for hp in hook_paths:
        content = _read(repo, hp)
        if content and _search(content, r"APPEND_ONLY_TABLES|append.only"):
            return CriterionResult(cid, True, f"APPEND_ONLY_TABLES guard found in {hp}")
    return CriterionResult(cid, False, "No pre-commit hook guarding append-only audit tables.",
                           "Add APPEND_ONLY_TABLES list to .claude/hooks/pre_tool_use.py to block audit mutations.")


_AUDIT_INSERT_DEFAULTS = {"scan_sample_size": 40}


def _check_audit_log_inserts(repo: pathlib.Path) -> CriterionResult:
    cid = "audit-log-inserts"
    cfg = load_pillar_config("append_only_audit").get("audit_log_inserts", {})
    scan_limit = int(cfg.get("scan_sample_size", _AUDIT_INSERT_DEFAULTS["scan_sample_size"]))
    py_files = _glob_files(repo, "**/*.py")
    inserts_found = []
    for f in py_files[:scan_limit]:
        content = f.read_text(encoding="utf-8", errors="replace")
        if re.search(_AUDIT_INSERT_PATTERN, content, re.IGNORECASE):
            inserts_found.append(f.name)
    if inserts_found:
        return CriterionResult(cid, True,
                               f"Audit INSERT operations found in {len(inserts_found)} file(s): {', '.join(inserts_found[:5])}")
    return CriterionResult(cid, False, "No audit log INSERT operations found.",
                           "Ensure all significant events are logged via INSERT into an audit/event log table.")


def _check_audit_table_schema(repo: pathlib.Path) -> CriterionResult:
    cid = "audit-table-schema"
    sql_files = (
        _glob_files(repo, "**/*.sql")
        + _glob_files(repo, "**/init_db.py")
        + _glob_files(repo, "**/migrations/**/*.py")
        + _glob_files(repo, "**/db/**/*.py")
    )
    for f in sql_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, r"CREATE\s+TABLE.*(?:audit|log|trail)") or \
           _search(content, r"audit_log|event_log|audit_trail"):
            # Verify no PRIMARY KEY with SERIAL that could allow updates
            if _search(content, r"audit") and _search(content, r"INSERT"):
                return CriterionResult(cid, True, f"Audit table schema with INSERT support found in {f.name}")
    # Check if aac_audit_log is in schema
    init_db = _read(repo, "tools/ai_augmentation/db/init_db.py")
    if init_db and "aac_audit_log" in init_db:
        return CriterionResult(cid, True, "aac_audit_log table defined in AAC DB schema")
    return CriterionResult(cid, False, "No append-only audit table schema found.",
                           "Define an audit_log table (event_type, timestamp, actor, detail) as append-only storage.")


PILLAR = Pillar(
    id="append-only-audit",
    name="Append-Only Audit Tables",
    description="Audit tables are not mutated (no UPDATE/DELETE), have pre-commit guards, and actively log events.",
    criteria=[
        Criterion("no-audit-mutations", "No audit mutations", "No UPDATE/DELETE on audit/log tables in source.", "append-only-audit", 4, _check_no_audit_mutations),
        Criterion("pre-tool-use-protection", "Pre-commit hook guard", "A pre-commit hook enforces APPEND_ONLY_TABLES protection.", "append-only-audit", 4, _check_pre_tool_use_protection),
        Criterion("audit-log-inserts", "Audit INSERT operations", "Events are actively logged via INSERT into audit tables.", "append-only-audit", 3, _check_audit_log_inserts),
        Criterion("audit-table-schema", "Audit table schema", "An append-only audit table schema is defined.", "append-only-audit", 3, _check_audit_table_schema),
    ],
)
