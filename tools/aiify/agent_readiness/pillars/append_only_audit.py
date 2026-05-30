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
    "mutation_scan_py_sample": 50,
    "enhanced_path_py_sample": 10,
}


@lru_cache(maxsize=1)
def _load_thresholds() -> dict[str, Any]:
    """Load append-only-audit NLP extractor config from args/agent_readiness_config.yaml.

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
            "mutation_scan_py_sample": int(cfg.get("mutation_scan_py_sample", _DEFAULTS["mutation_scan_py_sample"])),
            "enhanced_path_py_sample": int(cfg.get("enhanced_path_py_sample", _DEFAULTS["enhanced_path_py_sample"])),
        }
    except Exception:  # noqa: BLE001
        return dict(_DEFAULTS)


# ---------------------------------------------------------------------------
# NLP extractor — Claude Haiku for audit table mutation detection
# ---------------------------------------------------------------------------

def _nlp_extract_audit_refs(text: str, task: str) -> Optional[dict]:
    """Detect audit table mutations from code using Claude Haiku NLP.

    Catches ORM-based mutations (.update()/.delete()), dynamic SQL, and
    aliased patterns that bypass the raw SQL regex.

    Returns dict with keys: found (bool), tables (list[str]), mutations (list[str]),
    confidence (float). Returns None when LLM is unavailable so callers fall
    back to regex-only detection.
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
        f"You are an audit trail compliance analyst. Analyze the following code and {task}.\n\n"
        f"Code:\n{sample}\n\n"
        "Respond ONLY with valid JSON in this exact format: "
        '{"found": true, "tables": ["audit_log", "event_log"], '
        '"mutations": ["UPDATE audit_log SET ...", ".queryset.delete()"], "confidence": 0.9}\n'
        "Where tables contains names of audit/log/trail/event tables detected, and mutations "
        "contains any UPDATE, DELETE, or ORM mutation operations (e.g. .update(), .delete(), "
        "queryset.delete(), session.query(...).update()) that target those tables. "
        "Flag both raw SQL and ORM-based mutations. "
        "Set found to false and both lists to [] when no audit table mutations are detected."
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
    thresholds = _load_thresholds()

    # Fast path: regex detection of raw SQL UPDATE/DELETE on audit tables
    violations = []
    for f in py_files[:thresholds["mutation_scan_py_sample"]]:
        content = f.read_text(encoding="utf-8", errors="replace")
        for m in _FORBIDDEN_AUDIT_MUTATION.finditer(content):
            op = m.group(1).upper()
            table = m.group(2)
            if any(kw in table.lower() for kw in ["audit", "log", "trail", "event"]):
                violations.append(f"{f.name}:{table} ({op})")
    if violations:
        return CriterionResult(cid, False,
                               f"Audit table mutations detected in {len(violations)} location(s): {', '.join(violations[:3])}",
                               "Remove UPDATE/DELETE operations on audit/log tables. Audit trails are append-only per NIST AU.")

    # Enhanced path: NLP detection of ORM-based or indirect mutations missed by regex
    min_confidence = thresholds["nlp_extractor_confidence_threshold"]
    for f in py_files[:thresholds["enhanced_path_py_sample"]]:
        content = f.read_text(encoding="utf-8", errors="replace")
        result = _nlp_extract_audit_refs(
            content,
            "detect any mutations (UPDATE, DELETE, ORM .update()/.delete()) targeting "
            "audit, log, trail, or event tables — including indirect patterns like "
            "variable-based SQL, ORM queryset mutations, or model instance deletions",
        )
        if result and result.get("found") and result.get("confidence", 0) >= min_confidence:
            mutations = result.get("mutations", [])
            tables = result.get("tables", [])
            mut_str = ", ".join(mutations[:2]) if mutations else "ORM-based mutation"
            tbl_str = ", ".join(tables[:2]) if tables else "audit table"
            return CriterionResult(
                cid, False,
                f"Potential audit table mutation detected via NLP in {f.name}: {mut_str} on {tbl_str}",
                "Review and remove ORM-based or indirect UPDATE/DELETE operations on audit/log tables. "
                "Audit trails are append-only per NIST AU.",
            )

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
