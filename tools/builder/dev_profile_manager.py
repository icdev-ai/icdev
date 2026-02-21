#!/usr/bin/env python3
# CUI // SP-CTI
"""Tenant Development Profile Manager — per-scope dev standards with 5-layer cascade.

ADR D183: Version-based immutability (no UPDATE on dev_profiles).
ADR D184: 5-layer deterministic cascade: platform -> tenant -> program -> project -> user.
ADR D185: Auto-detection is advisory only.
ADR D187: LLM injection uses selective dimension extraction per task context.

Usage:
    python tools/builder/dev_profile_manager.py --scope tenant --scope-id tenant-abc \\
        --create --template dod_baseline --json
    python tools/builder/dev_profile_manager.py --scope project --scope-id proj-123 --get --json
    python tools/builder/dev_profile_manager.py --scope project --scope-id proj-123 --resolve --json
    python tools/builder/dev_profile_manager.py --scope project --scope-id proj-123 \\
        --update --changes '{"style": {"max_line_length": 120}}' --updated-by admin@gov --json
    python tools/builder/dev_profile_manager.py --scope tenant --scope-id tenant-abc \\
        --lock --dimension security --role isso --locked-by isso@gov --json
    python tools/builder/dev_profile_manager.py --scope project --scope-id proj-123 \\
        --unlock --dimension security --role isso --unlocked-by isso@gov --json
    python tools/builder/dev_profile_manager.py --scope project --scope-id proj-123 \\
        --diff --version1 1 --version2 2 --json
    python tools/builder/dev_profile_manager.py --scope project --scope-id proj-123 \\
        --rollback --to-version 1 --rolled-back-by admin@gov --json
    python tools/builder/dev_profile_manager.py --scope project --scope-id proj-123 \\
        --history --json
    python tools/builder/dev_profile_manager.py --inject --scope-id proj-123 \\
        --task-type code_generation --json
"""

import argparse
import copy
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "dev_profile_config.yaml"
TEMPLATES_DIR = BASE_DIR / "context" / "profiles"

VALID_SCOPES = ("platform", "tenant", "program", "project", "user")
VALID_LOCK_ROLES = ("isso", "architect", "pm", "admin")


# ── Helpers ──────────────────────────────────────────────────────────


def _generate_id(prefix="dprof"):
    """Generate a unique ID with prefix."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    h = hashlib.sha256(ts.encode()).hexdigest()[:8]
    return f"{prefix}-{h}"


def _get_connection(db_path=None):
    """Get a DB connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _load_config():
    """Load dev_profile_config.yaml with hardcoded fallback."""
    try:
        import yaml

        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
    except ImportError:
        pass
    # Minimal fallback
    return {
        "dimensions": {
            "language": {"cascade_behavior": "override", "enforcement": "enforced"},
            "style": {"cascade_behavior": "merge", "enforcement": "enforced"},
            "testing": {"cascade_behavior": "override_if_stricter", "enforcement": "enforced"},
            "architecture": {"cascade_behavior": "override", "enforcement": "enforced"},
            "security": {"cascade_behavior": "strictest_wins", "enforcement": "enforced"},
            "compliance": {"cascade_behavior": "union", "enforcement": "enforced"},
            "operations": {"cascade_behavior": "merge", "enforcement": "advisory"},
            "documentation": {"cascade_behavior": "merge", "enforcement": "advisory"},
            "git": {"cascade_behavior": "override", "enforcement": "advisory"},
            "ai": {"cascade_behavior": "merge", "enforcement": "advisory"},
        },
        "cascade_rules": {
            "order": ["platform", "tenant", "program", "project", "user"],
        },
        "task_dimension_map": {
            "code_generation": ["language", "style", "testing", "architecture"],
            "code_review": ["language", "style", "testing", "security", "compliance"],
            "planning": ["architecture", "compliance", "operations", "documentation"],
            "testing": ["testing", "security"],
            "deployment": ["operations", "security", "compliance"],
        },
    }


def _load_template(template_name):
    """Load a starter template from context/profiles/."""
    try:
        import yaml

        template_path = TEMPLATES_DIR / f"{template_name}.yaml"
        if not template_path.exists():
            # Try with _v1 suffix
            template_path = TEMPLATES_DIR / f"{template_name}_v1.yaml"
        if template_path.exists():
            with open(template_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                # Remove metadata fields, keep only profile dimensions
                for key in ("name", "version", "description", "applicable_to", "impact_levels"):
                    data.pop(key, None)
                return data
    except ImportError:
        pass
    return None


def _parse_profile_yaml(row):
    """Parse profile_yaml JSON from DB row."""
    try:
        return json.loads(row["profile_yaml"])
    except (json.JSONDecodeError, TypeError):
        return {}


def _log_event(conn, event_type, details, actor="system"):
    """Log to audit trail if table exists."""
    try:
        conn.execute(
            """INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, project_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                _generate_id("audit"),
                datetime.now(timezone.utc).isoformat(),
                event_type,
                actor,
                event_type,
                json.dumps(details) if isinstance(details, dict) else str(details),
                details.get("scope_id", "") if isinstance(details, dict) else "",
            ),
        )
    except Exception:
        pass  # Audit trail is best-effort


# ── CRUD Operations ──────────────────────────────────────────────────


def create_profile(scope, scope_id, profile_data=None, template_name=None,
                   created_by="system", inherits_from=None, profile_md=None,
                   change_summary="Initial creation", db_path=None):
    """Create a new profile version for a scope.

    If template_name is given, loads template defaults and merges profile_data on top.
    Returns dict with profile ID and version.
    'profile_md' stores the generated PROFILE.md narrative (D186).
    """
    if scope not in VALID_SCOPES:
        return {"error": f"Invalid scope: {scope}. Must be one of {VALID_SCOPES}"}

    conn = _get_connection(db_path)
    try:
        # Determine next version
        row = conn.execute(
            "SELECT MAX(version) as max_v FROM dev_profiles WHERE scope = ? AND scope_id = ?",
            (scope, scope_id),
        ).fetchone()
        next_version = (row["max_v"] or 0) + 1

        # Deactivate previous versions
        conn.execute(
            "UPDATE dev_profiles SET is_active = 0 WHERE scope = ? AND scope_id = ? AND is_active = 1",
            (scope, scope_id),
        )

        # Build profile data
        data = {}
        if template_name:
            template_data = _load_template(template_name)
            if template_data:
                data = template_data
                inherits_from = inherits_from or template_name
            else:
                return {"error": f"Template not found: {template_name}"}

        if profile_data:
            data = _deep_merge(data, profile_data)

        profile_id = _generate_id("dprof")
        conn.execute(
            """INSERT INTO dev_profiles
               (id, scope, scope_id, version, profile_md, profile_yaml,
                inherits_from, created_by, created_at, is_active, change_summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                profile_id,
                scope,
                scope_id,
                next_version,
                profile_md or "",
                json.dumps(data, indent=2),
                inherits_from,
                created_by,
                datetime.now(timezone.utc).isoformat(),
                change_summary,
            ),
        )

        _log_event(conn, "dev_profile.create", {
            "scope": scope, "scope_id": scope_id,
            "version": next_version, "template": template_name,
        }, actor=created_by)

        conn.commit()
        return {
            "status": "created",
            "profile_id": profile_id,
            "scope": scope,
            "scope_id": scope_id,
            "version": next_version,
            "inherits_from": inherits_from,
            "dimensions": list(data.keys()),
        }
    finally:
        conn.close()


def get_profile(scope, scope_id, version=None, db_path=None):
    """Get current (or specific version) profile for a scope."""
    conn = _get_connection(db_path)
    try:
        if version:
            row = conn.execute(
                "SELECT * FROM dev_profiles WHERE scope = ? AND scope_id = ? AND version = ?",
                (scope, scope_id, version),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM dev_profiles
                   WHERE scope = ? AND scope_id = ? AND is_active = 1
                   ORDER BY version DESC LIMIT 1""",
                (scope, scope_id),
            ).fetchone()

        if not row:
            return {"error": f"No profile found for {scope}:{scope_id}" +
                    (f" version {version}" if version else "")}

        return {
            "profile_id": row["id"],
            "scope": row["scope"],
            "scope_id": row["scope_id"],
            "version": row["version"],
            "is_active": bool(row["is_active"]),
            "inherits_from": row["inherits_from"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "change_summary": row["change_summary"],
            "approved_by": row["approved_by"],
            "profile_data": _parse_profile_yaml(row),
            "profile_md": row["profile_md"],
        }
    finally:
        conn.close()


def get_profile_history(scope, scope_id, db_path=None):
    """Get all versions for a scope (audit trail)."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT id, scope, scope_id, version, is_active, inherits_from,
                      created_by, created_at, change_summary, approved_by
               FROM dev_profiles WHERE scope = ? AND scope_id = ?
               ORDER BY version DESC""",
            (scope, scope_id),
        ).fetchall()

        return {
            "scope": scope,
            "scope_id": scope_id,
            "total_versions": len(rows),
            "versions": [
                {
                    "profile_id": r["id"],
                    "version": r["version"],
                    "is_active": bool(r["is_active"]),
                    "created_by": r["created_by"],
                    "created_at": r["created_at"],
                    "change_summary": r["change_summary"],
                    "approved_by": r["approved_by"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


def update_profile(scope, scope_id, changes, change_summary="",
                   updated_by="system", db_path=None):
    """Create new version with changes merged into current. Respects locks."""
    current = get_profile(scope, scope_id, db_path=db_path)
    if "error" in current:
        return current

    # Check locks
    conn = _get_connection(db_path)
    try:
        locks = _get_active_locks(conn, scope, scope_id)
        locked_dims = {lock["dimension_path"] for lock in locks}

        # Verify changes don't violate locks
        for dim_path in changes:
            if dim_path in locked_dims:
                lock_info = next(l for l in locks if l["dimension_path"] == dim_path)
                return {
                    "error": f"Dimension '{dim_path}' is locked by {lock_info['lock_owner_role']}",
                    "locked_by": lock_info["locked_by"],
                    "reason": lock_info.get("reason", ""),
                }

        # Merge changes into current profile data
        merged = _deep_merge(current["profile_data"], changes)

        return create_profile(
            scope=scope,
            scope_id=scope_id,
            profile_data=merged,
            created_by=updated_by,
            inherits_from=current.get("inherits_from"),
            change_summary=change_summary or f"Updated by {updated_by}",
            db_path=db_path,
        )
    finally:
        conn.close()


# ── Cascade Resolution ───────────────────────────────────────────────


def resolve_profile(scope, scope_id, db_path=None):
    """Walk the 5-layer cascade and produce the effective resolved profile.

    Resolution algorithm (D184):
    1. Start with platform profile
    2. Overlay tenant profile (skip locked dimensions from platform)
    3. Overlay program profile (skip locked from platform+tenant)
    4. Overlay project profile (skip locked from above)
    5. Overlay user profile (skip locked from above)

    Returns merged profile with provenance metadata.
    """
    config = _load_config()
    dim_config = config.get("dimensions", {})

    conn = _get_connection(db_path)
    try:
        # Build ancestry chain
        ancestry = _build_ancestry(conn, scope, scope_id)

        resolved = {}
        provenance = {}
        all_locks = set()

        for anc_scope, anc_scope_id in ancestry:
            # Load profile at this scope
            row = conn.execute(
                """SELECT * FROM dev_profiles
                   WHERE scope = ? AND scope_id = ? AND is_active = 1
                   ORDER BY version DESC LIMIT 1""",
                (anc_scope, anc_scope_id),
            ).fetchone()

            if not row:
                continue

            profile_data = _parse_profile_yaml(row)

            # Load locks at this scope
            scope_locks = _get_active_locks(conn, anc_scope, anc_scope_id)
            for lock in scope_locks:
                all_locks.add(lock["dimension_path"])

            # Merge each dimension
            for dim_name, dim_value in profile_data.items():
                if dim_name in all_locks and anc_scope != _lock_source(conn, dim_name, ancestry):
                    # Dimension is locked at a higher scope — skip
                    continue

                cascade = dim_config.get(dim_name, {}).get("cascade_behavior", "override")

                if dim_name not in resolved:
                    resolved[dim_name] = copy.deepcopy(dim_value)
                    provenance[dim_name] = {
                        "source_scope": anc_scope,
                        "source_id": anc_scope_id,
                        "locked": dim_name in all_locks,
                        "enforcement": dim_config.get(dim_name, {}).get("enforcement", "advisory"),
                    }
                else:
                    resolved[dim_name] = _merge_dimension(
                        resolved[dim_name], dim_value, cascade
                    )
                    provenance[dim_name] = {
                        "source_scope": anc_scope,
                        "source_id": anc_scope_id,
                        "locked": dim_name in all_locks,
                        "enforcement": dim_config.get(dim_name, {}).get("enforcement", "advisory"),
                    }

        return {
            "status": "resolved",
            "scope": scope,
            "scope_id": scope_id,
            "resolved": resolved,
            "provenance": provenance,
            "locks": list(all_locks),
            "ancestry": [{"scope": s, "scope_id": sid} for s, sid in ancestry],
        }
    finally:
        conn.close()


def _build_ancestry(conn, scope, scope_id):
    """Build the ancestry chain from platform down to the given scope.

    Returns list of (scope, scope_id) tuples in cascade order.
    """
    chain = []

    # Platform is always first
    chain.append(("platform", "default"))

    if scope == "platform":
        return chain

    # Try to find tenant from project/program
    tenant_id = None
    program_id = None

    if scope == "project":
        try:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?",
                (scope_id,),
            ).fetchone()
            if row:
                keys = row.keys()
                tenant_id = row["tenant_id"] if "tenant_id" in keys else None
                program_id = row["program_id"] if "program_id" in keys else None
        except Exception:
            pass  # Table may not have these columns
        # Fallback: use scope_id itself
        if not tenant_id:
            tenant_id = scope_id
    elif scope == "program":
        program_id = scope_id
    elif scope == "tenant":
        tenant_id = scope_id
    elif scope == "user":
        # User scope — try to find tenant from user context
        tenant_id = scope_id  # Simplified: user scope_id may encode tenant

    if tenant_id:
        chain.append(("tenant", tenant_id))
    if program_id:
        chain.append(("program", program_id))
    if scope in ("project", "user"):
        if scope == "project":
            chain.append(("project", scope_id))
        elif scope == "user":
            chain.append(("user", scope_id))

    return chain


def _lock_source(conn, dimension_path, ancestry):
    """Find the scope that locked a dimension."""
    for anc_scope, anc_scope_id in ancestry:
        row = conn.execute(
            """SELECT dpl.* FROM dev_profile_locks dpl
               JOIN dev_profiles dp ON dpl.profile_id = dp.id
               WHERE dp.scope = ? AND dp.scope_id = ? AND dp.is_active = 1
                 AND dpl.dimension_path = ? AND dpl.is_active = 1""",
            (anc_scope, anc_scope_id, dimension_path),
        ).fetchone()
        if row:
            return anc_scope
    return None


def _merge_dimension(parent_val, child_val, cascade_behavior):
    """Merge a single dimension respecting cascade behavior."""
    if cascade_behavior == "override":
        return copy.deepcopy(child_val)

    elif cascade_behavior == "merge":
        if isinstance(parent_val, dict) and isinstance(child_val, dict):
            return _deep_merge(parent_val, child_val)
        return copy.deepcopy(child_val)

    elif cascade_behavior == "union":
        if isinstance(parent_val, list) and isinstance(child_val, list):
            combined = list(parent_val)
            for item in child_val:
                if item not in combined:
                    combined.append(item)
            return combined
        if isinstance(parent_val, dict) and isinstance(child_val, dict):
            return _deep_merge(parent_val, child_val)
        return copy.deepcopy(child_val)

    elif cascade_behavior == "strictest_wins":
        if isinstance(parent_val, dict) and isinstance(child_val, dict):
            result = copy.deepcopy(parent_val)
            for k, v in child_val.items():
                if k in result:
                    result[k] = _pick_stricter(result[k], v)
                else:
                    result[k] = v
            return result
        return copy.deepcopy(child_val)

    elif cascade_behavior == "override_if_stricter":
        if isinstance(parent_val, dict) and isinstance(child_val, dict):
            result = copy.deepcopy(parent_val)
            for k, v in child_val.items():
                if k in result and isinstance(result[k], (int, float)) and isinstance(v, (int, float)):
                    result[k] = max(result[k], v)  # Higher = stricter for coverage
                else:
                    result[k] = v
            return result
        return copy.deepcopy(child_val)

    return copy.deepcopy(child_val)


def _pick_stricter(a, b):
    """Pick the stricter of two values for security dimensions."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return max(a, b)
    # For SLA strings like "24h", "7d" — pick shorter duration
    if isinstance(a, str) and isinstance(b, str):
        a_hours = _parse_duration_hours(a)
        b_hours = _parse_duration_hours(b)
        if a_hours is not None and b_hours is not None:
            return a if a_hours <= b_hours else b
    return b


def _parse_duration_hours(s):
    """Parse duration string to hours. Returns None if unparseable."""
    s = s.strip().lower()
    try:
        if s.endswith("h"):
            return int(s[:-1])
        if s.endswith("d"):
            return int(s[:-1]) * 24
    except (ValueError, IndexError):
        pass
    return None


def _deep_merge(base, override):
    """Deep merge two dicts. Override values win for non-dict values."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# ── Locks ────────────────────────────────────────────────────────────


def lock_dimension(scope, scope_id, dimension_path, lock_owner_role,
                   locked_by, reason="", db_path=None):
    """Lock a dimension at a scope level."""
    if lock_owner_role not in VALID_LOCK_ROLES:
        return {"error": f"Invalid role: {lock_owner_role}. Must be one of {VALID_LOCK_ROLES}"}

    conn = _get_connection(db_path)
    try:
        # Find active profile at this scope
        profile = conn.execute(
            """SELECT id FROM dev_profiles
               WHERE scope = ? AND scope_id = ? AND is_active = 1
               ORDER BY version DESC LIMIT 1""",
            (scope, scope_id),
        ).fetchone()

        if not profile:
            return {"error": f"No active profile at {scope}:{scope_id}"}

        # Check for existing lock
        existing = conn.execute(
            """SELECT id FROM dev_profile_locks
               WHERE profile_id = ? AND dimension_path = ? AND is_active = 1""",
            (profile["id"], dimension_path),
        ).fetchone()

        if existing:
            return {"error": f"Dimension '{dimension_path}' is already locked"}

        lock_id = _generate_id("lock")
        conn.execute(
            """INSERT INTO dev_profile_locks
               (id, profile_id, dimension_path, lock_owner_role, locked_by, locked_at, reason, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (lock_id, profile["id"], dimension_path, lock_owner_role,
             locked_by, datetime.now(timezone.utc).isoformat(), reason),
        )

        _log_event(conn, "dev_profile.lock", {
            "scope": scope, "scope_id": scope_id,
            "dimension": dimension_path, "role": lock_owner_role,
        }, actor=locked_by)

        conn.commit()
        return {
            "status": "locked",
            "lock_id": lock_id,
            "dimension_path": dimension_path,
            "lock_owner_role": lock_owner_role,
            "locked_by": locked_by,
        }
    finally:
        conn.close()


def unlock_dimension(scope, scope_id, dimension_path, unlocked_by,
                     role, db_path=None):
    """Unlock a previously locked dimension. Must match lock_owner_role or be admin."""
    conn = _get_connection(db_path)
    try:
        profile = conn.execute(
            """SELECT id FROM dev_profiles
               WHERE scope = ? AND scope_id = ? AND is_active = 1
               ORDER BY version DESC LIMIT 1""",
            (scope, scope_id),
        ).fetchone()

        if not profile:
            return {"error": f"No active profile at {scope}:{scope_id}"}

        lock = conn.execute(
            """SELECT * FROM dev_profile_locks
               WHERE profile_id = ? AND dimension_path = ? AND is_active = 1""",
            (profile["id"], dimension_path),
        ).fetchone()

        if not lock:
            return {"error": f"No active lock on '{dimension_path}'"}

        if lock["lock_owner_role"] != role and role != "admin":
            return {
                "error": f"Only '{lock['lock_owner_role']}' or 'admin' can unlock '{dimension_path}'",
                "lock_owner_role": lock["lock_owner_role"],
            }

        conn.execute(
            "UPDATE dev_profile_locks SET is_active = 0 WHERE id = ?",
            (lock["id"],),
        )

        _log_event(conn, "dev_profile.unlock", {
            "scope": scope, "scope_id": scope_id,
            "dimension": dimension_path, "role": role,
        }, actor=unlocked_by)

        conn.commit()
        return {
            "status": "unlocked",
            "dimension_path": dimension_path,
            "unlocked_by": unlocked_by,
        }
    finally:
        conn.close()


def _get_active_locks(conn, scope, scope_id):
    """Get all active locks for a scope's current profile."""
    rows = conn.execute(
        """SELECT dpl.* FROM dev_profile_locks dpl
           JOIN dev_profiles dp ON dpl.profile_id = dp.id
           WHERE dp.scope = ? AND dp.scope_id = ? AND dp.is_active = 1
             AND dpl.is_active = 1""",
        (scope, scope_id),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Versioning ───────────────────────────────────────────────────────


def diff_versions(scope, scope_id, version1, version2, db_path=None):
    """Compute diff between two profile versions."""
    p1 = get_profile(scope, scope_id, version=version1, db_path=db_path)
    p2 = get_profile(scope, scope_id, version=version2, db_path=db_path)

    if "error" in p1:
        return p1
    if "error" in p2:
        return p2

    d1 = p1["profile_data"]
    d2 = p2["profile_data"]

    added = {}
    removed = {}
    changed = {}

    all_keys = set(list(d1.keys()) + list(d2.keys()))
    for key in all_keys:
        if key not in d1:
            added[key] = d2[key]
        elif key not in d2:
            removed[key] = d1[key]
        elif d1[key] != d2[key]:
            changed[key] = {"from": d1[key], "to": d2[key]}

    return {
        "scope": scope,
        "scope_id": scope_id,
        "version1": version1,
        "version2": version2,
        "added": added,
        "removed": removed,
        "changed": changed,
        "total_changes": len(added) + len(removed) + len(changed),
    }


def rollback_to_version(scope, scope_id, target_version,
                        rolled_back_by="system", db_path=None):
    """Create a new version that copies content from target_version."""
    target = get_profile(scope, scope_id, version=target_version, db_path=db_path)
    if "error" in target:
        return target

    return create_profile(
        scope=scope,
        scope_id=scope_id,
        profile_data=target["profile_data"],
        created_by=rolled_back_by,
        inherits_from=target.get("inherits_from"),
        profile_md=target.get("profile_md"),
        change_summary=f"Rollback to version {target_version}",
        db_path=db_path,
    )


# ── LLM Injection ───────────────────────────────────────────────────


def inject_for_task(scope_id, task_type, db_path=None):
    """Resolve profile and extract dimensions relevant to task_type.

    Returns markdown string suitable for LLM prompt injection (D187).
    """
    config = _load_config()
    task_dims = config.get("task_dimension_map", {}).get(task_type, [])

    if not task_dims:
        return ""

    result = resolve_profile("project", scope_id, db_path=db_path)
    if "error" in result or not result.get("resolved"):
        return ""

    resolved = result["resolved"]
    provenance = result.get("provenance", {})

    lines = [f"## Development Profile (task: {task_type})\n"]
    for dim in task_dims:
        if dim in resolved:
            enforcement = provenance.get(dim, {}).get("enforcement", "advisory")
            source = provenance.get(dim, {}).get("source_scope", "unknown")
            locked = provenance.get(dim, {}).get("locked", False)

            lines.append(f"### {dim.replace('_', ' ').title()}")
            lines.append(f"_Source: {source} | Enforcement: {enforcement}"
                         + (" | LOCKED" if locked else "") + "_\n")

            dim_data = resolved[dim]
            if isinstance(dim_data, dict):
                for k, v in dim_data.items():
                    if isinstance(v, dict):
                        lines.append(f"- **{k}**: {json.dumps(v)}")
                    elif isinstance(v, list):
                        lines.append(f"- **{k}**: {', '.join(str(i) for i in v)}")
                    else:
                        lines.append(f"- **{k}**: {v}")
            else:
                lines.append(f"- {dim_data}")
            lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Tenant Development Profile Manager (Phase 34, D183-D188)"
    )
    parser.add_argument("--scope", choices=VALID_SCOPES, help="Profile scope")
    parser.add_argument("--scope-id", help="Scope entity ID")

    # Actions
    parser.add_argument("--create", action="store_true", help="Create new profile")
    parser.add_argument("--get", action="store_true", help="Get current profile")
    parser.add_argument("--resolve", action="store_true", help="Resolve 5-layer cascade")
    parser.add_argument("--update", action="store_true", help="Update profile (new version)")
    parser.add_argument("--lock", action="store_true", help="Lock a dimension")
    parser.add_argument("--unlock", action="store_true", help="Unlock a dimension")
    parser.add_argument("--diff", action="store_true", help="Diff two versions")
    parser.add_argument("--rollback", action="store_true", help="Rollback to version")
    parser.add_argument("--history", action="store_true", help="Get version history")
    parser.add_argument("--inject", action="store_true", help="Generate LLM injection context")
    parser.add_argument("--list-templates", action="store_true", help="List starter templates")

    # Parameters
    parser.add_argument("--template", help="Starter template name")
    parser.add_argument("--changes", help="JSON changes for update")
    parser.add_argument("--dimension", help="Dimension path for lock/unlock")
    parser.add_argument("--role", choices=VALID_LOCK_ROLES, help="Role for lock/unlock")
    parser.add_argument("--locked-by", help="Who locked the dimension")
    parser.add_argument("--unlocked-by", help="Who unlocked the dimension")
    parser.add_argument("--reason", default="", help="Lock reason")
    parser.add_argument("--version1", type=int, help="First version for diff")
    parser.add_argument("--version2", type=int, help="Second version for diff")
    parser.add_argument("--to-version", type=int, help="Target version for rollback")
    parser.add_argument("--rolled-back-by", default="system", help="Who rolled back")
    parser.add_argument("--updated-by", default="system", help="Who updated")
    parser.add_argument("--created-by", default="system", help="Who created")
    parser.add_argument("--task-type", help="Task type for injection")
    parser.add_argument("--version", type=int, help="Specific version to get")
    parser.add_argument("--db-path", type=Path, help="Database path override")

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()
    db = str(args.db_path) if args.db_path else None

    result = None

    if args.list_templates:
        templates = []
        if TEMPLATES_DIR.exists():
            for f in sorted(TEMPLATES_DIR.glob("*.yaml")):
                try:
                    import yaml
                    with open(f, "r", encoding="utf-8") as fh:
                        data = yaml.safe_load(fh)
                        templates.append({
                            "name": data.get("name", f.stem),
                            "file": f.name,
                            "description": data.get("description", ""),
                            "impact_levels": data.get("impact_levels", []),
                        })
                except Exception:
                    templates.append({"name": f.stem, "file": f.name})
        result = {"templates": templates, "count": len(templates)}

    elif args.create:
        profile_data = json.loads(args.changes) if args.changes else None
        result = create_profile(
            scope=args.scope, scope_id=args.scope_id,
            profile_data=profile_data, template_name=args.template,
            created_by=args.created_by, db_path=db,
        )

    elif args.get:
        result = get_profile(args.scope, args.scope_id, version=args.version, db_path=db)

    elif args.resolve:
        result = resolve_profile(args.scope, args.scope_id, db_path=db)

    elif args.update:
        changes = json.loads(args.changes) if args.changes else {}
        result = update_profile(
            scope=args.scope, scope_id=args.scope_id,
            changes=changes, updated_by=args.updated_by, db_path=db,
        )

    elif args.lock:
        result = lock_dimension(
            scope=args.scope, scope_id=args.scope_id,
            dimension_path=args.dimension, lock_owner_role=args.role,
            locked_by=args.locked_by, reason=args.reason, db_path=db,
        )

    elif args.unlock:
        result = unlock_dimension(
            scope=args.scope, scope_id=args.scope_id,
            dimension_path=args.dimension, unlocked_by=args.unlocked_by,
            role=args.role, db_path=db,
        )

    elif args.diff:
        result = diff_versions(
            scope=args.scope, scope_id=args.scope_id,
            version1=args.version1, version2=args.version2, db_path=db,
        )

    elif args.rollback:
        result = rollback_to_version(
            scope=args.scope, scope_id=args.scope_id,
            target_version=args.to_version, rolled_back_by=args.rolled_back_by,
            db_path=db,
        )

    elif args.history:
        result = get_profile_history(args.scope, args.scope_id, db_path=db)

    elif args.inject:
        text = inject_for_task(args.scope_id, args.task_type, db_path=db)
        result = {"injection_text": text, "task_type": args.task_type}

    else:
        parser.print_help()
        return

    if result:
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Human-readable
            for k, v in result.items():
                if isinstance(v, (dict, list)):
                    print(f"{k}: {json.dumps(v, indent=2, default=str)}")
                else:
                    print(f"{k}: {v}")


if __name__ == "__main__":
    main()
