#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

from tools.logging.icdev_logger import get_logger
"""Field-Level Security for ICDEV™ API responses.

Recursively strips or redacts fields in dicts/lists based on policies from
``args/security_config.yaml`` ``field_policies``.

Strategies:
    null     -> replace with None
    redact   -> replace with "[REDACTED]"
    hash     -> replace with sha256 hex digest (first 16 chars)
    truncate -> replace string with "..."

Flask integration:
    field_security_after_request(response) -> response
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = get_logger("security.field")

# Audit logging — disabled by default. Enable: ICDEV_AUDIT_FIELD=1
# Or in tests: import tools.security.field_security as fs; fs.AUDIT_FIELD = True
AUDIT_FIELD = os.environ.get("ICDEV_AUDIT_FIELD", "").lower() in ("1", "true", "yes")

_DB_PATH_DEFAULT = str(Path(__file__).resolve().parent.parent.parent / "data" / "icdev.db")


def _write_field_audit(schema: str, role: str, filtered_fields: list) -> None:
    """Append one row to field_filter_audit. Never raises.

    Placeholders are `?`, not `%s`: this opens a RAW ``sqlite3`` connection and
    never passes through ``translate_sql``, so it must speak sqlite's dialect.
    With `%s` every insert raised, the bare ``except`` swallowed it, and the
    audit table stayed empty while reporting as enabled.
    """
    try:
        import sqlite3 as _sq
        from datetime import datetime, timezone
        _db = os.environ.get("ICDEV_DB_PATH", _DB_PATH_DEFAULT)
        _ac = _sq.connect(_db, timeout=5)
        _ac.execute(
            "INSERT INTO field_filter_audit (schema_name, role, filtered_fields, recorded_at)"
            " VALUES (?, ?, ?, ?)",
            (schema, role, json.dumps(filtered_fields), datetime.now(timezone.utc).isoformat()),
        )
        _ac.commit()
        _ac.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_write_field_audit: best-effort INSERT into field_filter_audit failed (non-blocking): %s", exc)

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _BASE_DIR / "args" / "security_config.yaml"


def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------

def _apply_mask(value: Any, strategy: str) -> Any:
    if strategy == "null":
        return None
    if strategy == "redact":
        return "[REDACTED]"
    if strategy == "hash":
        if value is None:
            return None
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
    if strategy == "truncate":
        if value is None:
            return None
        return "..."
    return value


def filter_response_fields(data: Any, policies: Dict[str, str]) -> Any:
    """Recursively apply field policies to a JSON-serializable structure.

    Args:
        data: dict, list, or primitive value
        policies: flat dict of field_name -> strategy

    Returns:
        Mutated structure with masked fields.
    """
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in policies:
                result[key] = _apply_mask(value, policies[key])
            else:
                result[key] = filter_response_fields(value, policies)
        return result
    if isinstance(data, list):
        return [filter_response_fields(item, policies) for item in data]
    return data


def apply_field_policy(schema: str, role: str, data: Any) -> Any:
    """Lookup field policies from config and apply."""
    config = _load_config()
    for policy in config.get("field_policies", []):
        if policy.get("schema") == schema and policy.get("role") == role:
            return filter_response_fields(data, policy.get("fields", {}))
    return data


def get_field_policies_for_role(schema: str, role: str) -> Dict[str, str]:
    """Return raw field->strategy mapping for a schema+role."""
    config = _load_config()
    for policy in config.get("field_policies", []):
        if policy.get("schema") == schema and policy.get("role") == role:
            return dict(policy.get("fields", {}))
    return {}


# ---------------------------------------------------------------------------
# Flask after_request integration
# ---------------------------------------------------------------------------

def field_security_after_request(response) -> Any:
    """Flask ``after_request`` handler that filters JSON response bodies.

    Reads ``g.security_context`` for role and ``g.response_schema`` (optional)
    for schema name.  If no schema is set, attempts to infer from URL path.
    """
    try:
        from flask import g, request
    except ImportError:
        return response

    if response.content_type != "application/json":
        return response

    ctx = getattr(g, "security_context", None)
    if not ctx:
        return response

    role = getattr(ctx, "role", "")
    schema = getattr(g, "response_schema", None) or _infer_schema(request.path)
    if not schema:
        return response

    policies = get_field_policies_for_role(schema, role)
    if not policies:
        return response

    try:
        data = json.loads(response.get_data(as_text=True))
        filtered = filter_response_fields(data, policies)
        response.set_data(json.dumps(filtered))
        response.headers["X-Field-Filtered"] = "true"
        if AUDIT_FIELD:
            _write_field_audit(schema, role, list(policies.keys()))
    except Exception as exc:
        logger.debug("Field filtering failed: %s", exc)

    return response


_SCHEMA_PATH_MAP = [
    # More-specific prefixes must come before shorter overlapping ones.
    ("/profile/api/llm-keys", "api_key"),
    ("/profile/api/keys",     "api_key"),
    ("/profile",              "user_profile"),
    ("/api/users",            "user_profile"),
    ("/users",                "user_profile"),
    ("/user",                 "user_profile"),
    ("/api/tenants",          "tenant"),
    ("/tenants",              "tenant"),
    ("/tenant",               "tenant"),
    ("/api/projects",         "project"),
    ("/projects",             "project"),
    ("/project",              "project"),
    ("/api/compliance",       "compliance"),
    ("/compliance",           "compliance"),
    ("/api/audit",            "audit_trail"),
    ("/audit",                "audit_trail"),
    ("/api/notifications",    "notification"),
    ("/api/alerts",           "notification"),
    ("/api/agents",           "agent"),
    ("/api/tasks",            "task"),
    ("/api/findings",         "finding"),
    ("/api/simulation",       "simulation"),
    ("/api/research",         "research"),
    ("/api/knowledge",        "knowledge"),
    ("/api/rag",              "rag"),
]


def _infer_schema(path: str) -> Optional[str]:
    """Infer schema name from URL path for policy lookup."""
    for prefix, schema in _SCHEMA_PATH_MAP:
        if path.startswith(prefix) or prefix in path:
            return schema
    return None


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def log_field_filter(
    conn,
    schema: str,
    role: str,
    filtered_fields: list,
) -> None:
    """Log an append-only field filter audit event."""
    try:
        from datetime import datetime, timezone
        conn.execute(
            """
            INSERT INTO field_filter_audit (schema_name, role, filtered_fields, recorded_at)
            VALUES (%s, %s, %s, %s)
            """,
            (
                schema,
                role,
                json.dumps(filtered_fields),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("Could not log field filter event: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Field Security CLI")
    parser.add_argument("--filter", action="store_true", help="Run filter demo")
    parser.add_argument("--schema", type=str, default="user_profile")
    parser.add_argument("--role", type=str, default="viewer")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.filter:
        data = {
            "id": 1,
            "name": "Alice",
            "email": "alice@example.com",
            "api_keys": ["ak_123", "ak_456"],
            "nested": {"ssn": "123-45-6789", "salary": 100000},
        }
        filtered = apply_field_policy(args.schema, args.role, data)
        print(json.dumps(filtered, indent=2) if args.json else str(filtered))


if __name__ == "__main__":
    main()
