#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed ``db_connections`` from ``args/databridge_connections.yaml``.

``db_connections`` held ZERO rows, which is why the DataBridge agent broker's
``connection_id`` was decorative: every grant pointed at a record that did not
exist. This is the writer for that table.

WHAT IT REFUSES, AND WHY THE REFUSAL IS THE POINT
-------------------------------------------------

A connection descriptor may carry a credential REFERENCE and never a credential.
``auth_secret_ref`` must match one of the prefixes
``tools/databridge/connection_manager.py::resolve_secret`` dispatches on
(``env:``, ``vault:``, ``aws:``, ``file:``); anything else is refused before the
INSERT, including the empty-looking cases — a bare ``hunter2`` is not a
reference, it is a password. A literal in this file would be committed to a
public repository, would survive every later rotation, and would be
indistinguishable from a reference to anything reading the column.

The refusal is a hard error rather than a warning. A warning here is the
``|| true`` failure: the seed still lands, the secret is still in git, and the
only trace is a log line. ``--seed`` writes nothing at all when any descriptor
fails validation — a partial seed leaves a grant half-wired, which is harder to
diagnose than none.

``auth_method: none`` must carry no ref. Declaring a credential for a source
that has none is a claim nobody can verify, and it sends the next reader looking
for a secret that was never issued.

CLASSIFICATION IS A LABEL
-------------------------

``db_connections.classification`` defaults to the BANNER ``'CUI // SP-CTI'``.
``get_connection()`` injects ``classification IN (<labels the caller's clearance
dominates>)`` into every SELECT and that set is drawn from the label vocabulary
(``PUBLIC``, ``UNCLASSIFIED``, ``CUI``, ``SECRET``, ``ECI``, ``TOP SECRET``). A
row carrying the banner matches no member of it at any clearance: written,
retained, and invisible to every reader including this tool's own ``--verify``.
Descriptors therefore declare a label and the seeder validates it.

CLI::

    python -m tools.databridge.seed_connections --seed --json
    python -m tools.databridge.seed_connections --dry-run --json
    python -m tools.databridge.seed_connections --verify --json
    python -m tools.databridge.seed_connections --list --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger("databridge.seed_connections")

# get_data_path, NOT parents[2] / "args". This file is mirrored at tools/ and
# icdev/tools/, and parents[2] from the mirror is `icdev/` — so the mirrored copy
# would look for `icdev/args/databridge_connections.yaml`, find nothing, and seed
# zero connections while reporting success. The broker resolves its own manifest
# through the same helper for the same reason.
def _args_dir() -> Path:
    from icdev._paths import get_data_path

    return get_data_path("args")


DESCRIPTOR_PATH = _args_dir() / "databridge_connections.yaml"
GRANT_PATH = _args_dir() / "databridge_agent_access.yaml"

#: Prefixes resolve_secret() dispatches on. A ref that is not one of these is a
#: literal, whatever it looks like.
SECRET_REF_PREFIXES: Tuple[str, ...] = ("env:", "vault:", "aws:", "file:")

#: The RLS label vocabulary. NOT banner strings — see the module docstring.
CLASSIFICATION_LABELS: Tuple[str, ...] = (
    "PUBLIC", "UNCLASSIFIED", "CUI", "SECRET", "ECI", "TOP SECRET",
)

#: From the db_connections CHECK constraints. Derived here as constants rather
#: than re-stated in prose so a mismatch fails at validate time with a readable
#: message instead of as an IntegrityError mid-INSERT.
CONNECTOR_TYPES: Tuple[str, ...] = (
    "database", "cloud_storage", "file", "streaming", "saas_api", "on_prem",
)
AUTH_METHODS: Tuple[str, ...] = (
    "none", "api_key", "oauth2", "iam_role", "connection_string", "pki",
    "password", "pat",
)
SYNC_DIRECTIONS: Tuple[str, ...] = ("read", "write", "bidirectional")
STATUSES: Tuple[str, ...] = ("configured", "connected", "syncing", "error", "disabled")
IMPACT_LEVELS: Tuple[str, ...] = ("IL2", "IL4", "IL5", "IL6")


class DescriptorError(ValueError):
    """A connection descriptor is not fit to be written."""


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


def load_descriptors(path: Path | None = None) -> List[Dict[str, Any]]:
    """Read the descriptor file. A missing file yields no connections."""
    src = path or DESCRIPTOR_PATH
    try:
        raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001
        raise DescriptorError(f"{src} is unreadable: {exc}") from exc
    entries = raw.get("connections") or []
    if not isinstance(entries, list):
        raise DescriptorError(f"{src}: `connections` must be a list")
    return [e for e in entries if isinstance(e, dict)]


def validate(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return a normalized row for *entry*, or raise DescriptorError.

    Every rejection names the field and the value's shape, never the value: a
    validation error on a credential must not print the credential.
    """
    def _req(key: str) -> str:
        value = str(entry.get(key) or "").strip()
        if not value:
            raise DescriptorError(f"connection is missing required field {key!r}")
        return value

    conn_id = _req("id")

    def _enum(key: str, allowed: Tuple[str, ...], default: str = "") -> str:
        value = str(entry.get(key) or default).strip()
        if value not in allowed:
            raise DescriptorError(
                f"connection {conn_id!r}: {key}={value!r} is not one of {list(allowed)}"
            )
        return value

    auth_method = _enum("auth_method", AUTH_METHODS, "none")
    secret_ref = str(entry.get("auth_secret_ref") or "").strip()

    if secret_ref and not secret_ref.startswith(SECRET_REF_PREFIXES):
        raise DescriptorError(
            f"connection {conn_id!r}: auth_secret_ref is a LITERAL, not a reference. "
            f"It must start with one of {list(SECRET_REF_PREFIXES)}. "
            f"A secret value must never appear in a YAML file in this repository — "
            f"put it in the configured secret backend and reference it here."
        )
    if auth_method == "none" and secret_ref:
        raise DescriptorError(
            f"connection {conn_id!r}: auth_method is 'none' but an auth_secret_ref "
            f"is declared. A credential nobody uses cannot be verified and sends the "
            f"next reader looking for a secret that was never issued."
        )
    if auth_method != "none" and not secret_ref:
        raise DescriptorError(
            f"connection {conn_id!r}: auth_method={auth_method!r} requires an "
            f"auth_secret_ref (a reference, resolved at use time)."
        )

    config = entry.get("config") or {}
    if not isinstance(config, dict):
        raise DescriptorError(f"connection {conn_id!r}: `config` must be a mapping")

    return {
        "id": conn_id,
        "name": _req("name"),
        "connector_type": _enum("connector_type", CONNECTOR_TYPES),
        "connector_name": _req("connector_name"),
        # Serialized here rather than by the caller so the round trip through
        # the broker's yaml.safe_load is the one this tool validated.
        "config_yaml": yaml.safe_dump(config, sort_keys=True, default_flow_style=False),
        "auth_method": auth_method,
        "auth_secret_ref": secret_ref or None,
        "sync_direction": _enum("sync_direction", SYNC_DIRECTIONS, "read"),
        "status": _enum("status", STATUSES, "configured"),
        "classification": _enum("classification", CLASSIFICATION_LABELS, "CUI"),
        "impact_level": _enum("impact_level", IMPACT_LEVELS, "IL4"),
        "tenant_id": str(entry.get("tenant_id") or "default"),
        "created_by": "tools.databridge.seed_connections",
    }


def validate_all(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate every descriptor, collecting ALL failures before raising.

    All-or-nothing: a partial seed leaves a grant half-wired, which is harder to
    diagnose than an unseeded one.
    """
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for entry in entries:
        try:
            rows.append(validate(entry))
        except DescriptorError as exc:
            errors.append(str(exc))

    seen: Dict[str, int] = {}
    for row in rows:
        seen[row["id"]] = seen.get(row["id"], 0) + 1
    for conn_id, count in seen.items():
        if count > 1:
            errors.append(f"connection id {conn_id!r} is declared {count} times")

    if errors:
        raise DescriptorError("; ".join(errors))
    return rows


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_COLUMNS = (
    "id", "name", "connector_type", "connector_name", "config_yaml",
    "auth_method", "auth_secret_ref", "sync_direction", "status",
    "classification", "impact_level", "tenant_id", "created_by",
    "created_at", "updated_at",
)


def _upsert(conn, row: Dict[str, Any]) -> str:
    """Insert or refresh one row. Returns 'created' or 'updated'.

    DELETE-then-INSERT rather than ON CONFLICT so the statement is identical on
    both backends. db_connections is a mutable config table, not an append-only
    audit one — re-seeding is expected to be idempotent.
    """
    existing = conn.execute(
        "SELECT id FROM db_connections WHERE id = %s", (row["id"],)
    ).fetchone()
    action = "updated" if existing else "created"
    if existing:
        conn.execute("DELETE FROM db_connections WHERE id = %s", (row["id"],))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    values = tuple(row.get(c) for c in _COLUMNS[:-2]) + (now, now)
    placeholders = ", ".join(["%s"] * len(_COLUMNS))
    # The only interpolated parts are _COLUMNS (a module constant, never caller
    # input) and a placeholder run derived from its length. Every VALUE is bound.
    conn.execute(
        f"INSERT INTO db_connections ({', '.join(_COLUMNS)}) "  # nosec B608
        f"VALUES ({placeholders})",
        values,
    )
    return action


def seed(dry_run: bool = False, path: Path | None = None) -> Dict[str, Any]:
    """Validate every descriptor, then write them all (or none)."""
    rows = validate_all(load_descriptors(path))
    result: Dict[str, Any] = {
        "descriptors": len(rows),
        "dry_run": bool(dry_run),
        "created": [],
        "updated": [],
    }
    if dry_run:
        result["would_write"] = [r["id"] for r in rows]
        return result
    if not rows:
        return result

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        for row in rows:
            result[_upsert(conn, row)].append(row["id"])
        conn.commit()
    finally:
        conn.close()
    return result


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify(path: Path | None = None) -> Dict[str, Any]:
    """Report whether every grant's connection exists and its credential resolves.

    Reads back through ``connection_manager.get_connection``, i.e. through the
    same RLS-injected path the broker uses — a row this cannot see is a row the
    broker cannot see either, which is the failure mode a raw SELECT would hide.

    Never returns or logs a secret VALUE; only whether the reference resolved.
    """
    from tools.databridge.connection_manager import get_connection as _row
    from tools.databridge.connection_manager import resolve_secret

    grants: List[Dict[str, Any]] = []
    try:
        raw = yaml.safe_load(GRANT_PATH.read_text(encoding="utf-8")) or {}
        grants = [g for g in (raw.get("connectors") or []) if isinstance(g, dict)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read %s: %s", GRANT_PATH, exc)

    checks: List[Dict[str, Any]] = []
    for row in validate_all(load_descriptors(path)):
        stored = _row(row["id"])
        entry: Dict[str, Any] = {
            "connection_id": row["id"],
            "present": bool(stored),
            "connector_name": row["connector_name"],
            "auth_method": row["auth_method"],
            "secret_ref_declared": bool(row["auth_secret_ref"]),
            "secret_resolves": None,   # None = no credential to resolve
            "granted_to_agents": False,
        }
        if row["auth_secret_ref"]:
            try:
                entry["secret_resolves"] = bool(resolve_secret(row["auth_secret_ref"]))
            except Exception as exc:  # noqa: BLE001
                entry["secret_resolves"] = False
                entry["secret_error"] = str(exc)
        entry["granted_to_agents"] = any(
            str(g.get("connection_id") or "") == row["id"] for g in grants
        )
        checks.append(entry)

    return {
        "checks": checks,
        "all_present": all(c["present"] for c in checks) if checks else False,
        "orphan_grants": [
            str(g.get("connection_id"))
            for g in grants
            if str(g.get("connection_id") or "")
            and not any(c["connection_id"] == str(g.get("connection_id")) for c in checks)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed db_connections from args/databridge_connections.yaml"
    )
    parser.add_argument("--seed", action="store_true", help="write the descriptors")
    parser.add_argument("--dry-run", action="store_true", help="validate, write nothing")
    parser.add_argument("--verify", action="store_true",
                        help="check each grant's row exists and its credential resolves")
    parser.add_argument("--list", action="store_true", help="print validated descriptors")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        if args.verify:
            result: Dict[str, Any] = verify()
        elif args.list:
            result = {"connections": [
                {k: v for k, v in r.items() if k != "config_yaml"}
                for r in validate_all(load_descriptors())
            ]}
        elif args.seed or args.dry_run:
            result = seed(dry_run=args.dry_run)
        else:
            parser.print_help()
            return 0
    except DescriptorError as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, indent=2) if args.json else f"REFUSED: {exc}")
        return 1

    print(json.dumps(result, indent=2, default=str) if args.json else result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
