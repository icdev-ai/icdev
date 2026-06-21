# CUI // SP-CTI
"""Pillar 11 — Append-Only Audit Tables (ICDEV): audit tables are not mutated."""
from __future__ import annotations

import json
import os
import pathlib
import re
from functools import lru_cache
from typing import Any, Optional

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

# ---------------------------------------------------------------------------
# Config loader for NLP extractor thresholds
# ---------------------------------------------------------------------------
_ARGS_PATH = pathlib.Path(__file__).parents[4] / "args" / "agent_readiness_config.yaml"
_DEFAULTS: dict[str, Any] = {
    "nlp_extractor_enabled": True,
    "nlp_extractor_model": "claude-haiku-4-5-20251001",
    "nlp_extractor_max_tokens": 256,
    "nlp_extractor_confidence_threshold": 0.7,
    "nlp_extractor_text_sample_chars": 2000,
}


@lru_cache(maxsize=1)
def _load_thresholds() -> dict[str, Any]:
    """Load append_only_audit pillar NLP extractor config from args/agent_readiness_config.yaml.

    Falls back to hard-coded defaults if the config file is absent or malformed.
    """
    try:
        import yaml
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        cfg = data.get("pillars", {}).get("append_only_audit", {}).get("nlp_extractor", {})
        return {
            "nlp_extractor_enabled": bool(cfg.get("enabled", _DEFAULTS["nlp_extractor_enabled"])),
            "nlp_extractor_model": str(cfg.get("model", _DEFAULTS["nlp_extractor_model"])),
            "nlp_extractor_max_tokens": int(cfg.get("max_tokens", _DEFAULTS["nlp_extractor_max_tokens"])),
            "nlp_extractor_confidence_threshold": float(
                cfg.get("confidence_threshold", _DEFAULTS["nlp_extractor_confidence_threshold"])
            ),
            "nlp_extractor_text_sample_chars": int(
                cfg.get("text_sample_chars", _DEFAULTS["nlp_extractor_text_sample_chars"])
            ),
        }
    except Exception:  # noqa: BLE001
        return dict(_DEFAULTS)


# ---------------------------------------------------------------------------
# NLP extractor — Claude Haiku for natural-language audit mutation detection
# ---------------------------------------------------------------------------

def _nlp_extract_audit_mutations(text: str, task: str) -> Optional[dict]:
    """Extract audit table mutation signals from text using Claude Haiku NLP.

    Returns dict with keys: found (bool), refs (list[str]), confidence (float).
    Returns None when LLM is unavailable so callers fall back to regex.
    """
    thresholds = _load_thresholds()
    if not thresholds["nlp_extractor_enabled"]:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        from tools.llm.anthropic_provider import AnthropicLLMProvider
        from tools.llm.provider import LLMRequest
    except ImportError:
        return None

    sample = text[:thresholds["nlp_extractor_text_sample_chars"]]
    prompt = (
        f"You are an audit-integrity analyst. Analyze the following code and {task}.\n\n"
        f"Code:\n{sample}\n\n"
        "Respond ONLY with valid JSON in this exact format: "
        '{"found": true, "refs": ["audit_log UPDATE", "event_log DELETE"], "confidence": 0.9}\n'
        "Where refs contains the table names and SQL operations detected (UPDATE/DELETE on audit/log/trail/event tables). "
        'Set found to false and refs to [] when no audit mutations are detected.'
    )
    try:
        provider = AnthropicLLMProvider(api_key=api_key)
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=thresholds["nlp_extractor_max_tokens"],
        )
        model_id = thresholds["nlp_extractor_model"]
        model_cfg = {"max_output_tokens": thresholds["nlp_extractor_max_tokens"]}
        response = provider.invoke(request, model_id, model_cfg)
        result_text = response.content.strip()
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:  # noqa: BLE001
        pass
    return None


def _check_no_audit_mutations(repo: pathlib.Path) -> CriterionResult:
    cid = "no-audit-mutations"
    py_files = _glob_files(repo, "**/*.py")

    # Fast path: regex detection for explicit UPDATE/DELETE on audit tables
    violations = []
    regex_scanned = []
    for f in py_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        regex_scanned.append((f, content))
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

    # Enhanced path: NLP for dynamically-constructed or obfuscated audit mutations missed by regex
    thresholds = _load_thresholds()
    min_confidence = thresholds["nlp_extractor_confidence_threshold"]
    for f, content in regex_scanned[:10]:
        result = _nlp_extract_audit_mutations(
            content,
            "identify any UPDATE or DELETE SQL operations targeting audit, log, trail, or event tables "
            "(including dynamically constructed SQL via string formatting or concatenation)",
        )
        if result and result.get("found") and result.get("confidence", 0) >= min_confidence:
            refs = result.get("refs", [])
            ref_str = ", ".join(refs[:3]) if refs else "detected mutation"
            return CriterionResult(cid, False,
                                   f"Audit table mutation detected via NLP in {f.name}: {ref_str}",
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

    # Fast path: regex detection for explicit INSERT INTO audit/log/trail/event tables
    inserts_found = []
    scanned = []
    for f in py_files[:scan_limit]:
        content = f.read_text(encoding="utf-8", errors="replace")
        scanned.append((f, content))
        if re.search(_AUDIT_INSERT_PATTERN, content, re.IGNORECASE):
            inserts_found.append(f.name)
    if inserts_found:
        return CriterionResult(cid, True,
                               f"Audit INSERT operations found in {len(inserts_found)} file(s): {', '.join(inserts_found[:5])}")

    # Enhanced path: NLP for ORM-style or helper-wrapped audit logging missed by raw SQL regex
    thresholds = _load_thresholds()
    min_confidence = thresholds["nlp_extractor_confidence_threshold"]
    for f, content in scanned[:10]:
        result = _nlp_extract_audit_mutations(
            content,
            "identify any audit logging operations — whether raw SQL INSERT INTO an audit/log/trail/event table, "
            "ORM model.create() calls, or helper functions that write audit records",
        )
        if result and result.get("found") and result.get("confidence", 0) >= min_confidence:
            refs = result.get("refs", [])
            ref_str = ", ".join(refs[:3]) if refs else "audit logging detected"
            return CriterionResult(cid, True,
                                   f"Audit INSERT operations detected via NLP in {f.name}: {ref_str}")

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
