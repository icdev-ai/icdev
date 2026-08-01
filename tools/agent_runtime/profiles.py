# CUI // SP-CTI
"""Profile isolation for the standalone agent (sag-prof-01).

``icdev profile`` already applies *core* env overrides from
``args/core_profiles.yaml``. This module adds a separate, genuinely-missing
capability: **directory-based operator profiles** so one machine can host several
isolated agent identities (``work`` / ``personal`` / ``client-x``) without forking
the storage backend into per-profile ``.db`` files.

Each profile owns a state directory ``~/.icdev/profiles/<name>/``::

    ~/.icdev/profiles/<name>/
        env            # dotenv overlay applied on top of the base .env
        skills/        # profile-scoped skills (parsed by the skills registry)
        profile.json   # metadata (created_at, description)

A sticky **active profile** pointer (``~/.icdev/active_profile``) is read by the
SAG runtime at startup so ``icdev chat`` / ``sessions`` operate under the chosen
profile. Session + memory isolation is achieved by *namespacing the tenant*
(:func:`scoped_tenant`) rather than a separate database — PostgreSQL stays the
single primary and the existing RLS/tenant plumbing does the filtering. The
default profile is a strict no-op: nothing is namespaced, so existing behaviour is
byte-for-byte unchanged.

A durable registry table ``sag_profiles`` (migration 290) records each profile so
``icdev profile list`` can enumerate them and other tools can discover state
directories; it self-creates via :func:`_ensure_schema` so an un-migrated checkout
still works. Everything degrades gracefully — a missing DB or unreadable pointer
yields the default profile, never an exception.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.profiles")

_TABLE = "sag_profiles"
DEFAULT_PROFILE = "default"
_ENV_OVERRIDE = "ICDEV_SAG_PROFILE"  # process-level override of the sticky pointer
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def icdev_home() -> Path:
    return Path(os.environ.get("ICDEV_HOME", str(Path.home() / ".icdev")))


def profiles_root() -> Path:
    return icdev_home() / "profiles"


def pointer_file() -> Path:
    return icdev_home() / "active_profile"


def profile_dir(name: str) -> Path:
    return profiles_root() / name


def overlay_env_path(name: str) -> Path:
    return profile_dir(name) / "env"


def skills_dir(name: str) -> Path:
    return profile_dir(name) / "skills"


def _meta_path(name: str) -> Path:
    return profile_dir(name) / "profile.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_default(name: str | None) -> bool:
    return not name or name == DEFAULT_PROFILE


def validate_name(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(
            f"invalid profile name {name!r}: use lowercase letters, digits, '-' or '_' (max 64)"
        )
    if name == DEFAULT_PROFILE:
        raise ValueError("'default' is reserved (it means 'no isolation')")
    return name


# ---------------------------------------------------------------------------
# Sticky active-profile pointer
# ---------------------------------------------------------------------------
def active_profile() -> str:
    """Return the active profile name, or ``''`` for the default (no isolation).

    Precedence: ``ICDEV_SAG_PROFILE`` env override > sticky pointer file > default.
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override is not None:
        return "" if is_default(override) else override.strip()
    try:
        p = pointer_file()
        if p.exists():
            name = p.read_text(encoding="utf-8").strip()
            return "" if is_default(name) else name
    except Exception as exc:  # noqa: BLE001 — pointer is best-effort
        logger.debug("profiles: could not read pointer: %s", exc)
    return ""


def set_active(name: str | None) -> str:
    """Set the sticky active profile. ``None``/``'default'`` clears isolation."""
    home = icdev_home()
    home.mkdir(parents=True, exist_ok=True)
    ptr = pointer_file()
    if is_default(name):
        try:
            ptr.unlink()
        except FileNotFoundError:
            pass
        return DEFAULT_PROFILE
    validate_name(name)  # type: ignore[arg-type]
    ptr.write_text(name + "\n", encoding="utf-8")  # type: ignore[operator]
    return name  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tenant namespacing (session + memory isolation without per-profile DB files)
# ---------------------------------------------------------------------------
_SCOPE_SEP = "::prof:"


def scoped_tenant(tenant_id: str, profile: str | None) -> str:
    """Return the effective tenant id for a profile.

    Default profile → the tenant unchanged (no isolation). A named profile →
    ``<tenant>::prof:<name>`` so ``chat_contexts`` / ``sag_user_profiles`` rows
    are naturally partitioned by profile under the existing tenant plumbing.
    """
    if is_default(profile):
        return tenant_id or ""
    return f"{tenant_id or ''}{_SCOPE_SEP}{profile}"


# ---------------------------------------------------------------------------
# DB registry (durable list of profiles)
# ---------------------------------------------------------------------------
_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    name           TEXT PRIMARY KEY,
    state_dir      TEXT,
    description    TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT,
    updated_at     TEXT
)
"""


def _ensure_schema(conn) -> None:
    try:
        conn.execute(_DDL)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("profiles: ensure_schema failed: %s", exc)


def _register(name: str, description: str, conn=None) -> None:
    """Best-effort upsert of a profile into the registry (never raises)."""
    try:
        if conn is None:
            from tools.db.storage import get_connection

            conn = get_connection()
        _ensure_schema(conn)
        now = _utcnow()
        cur = conn.execute(f"SELECT 1 FROM {_TABLE} WHERE name = %s", (name,))
        if cur.fetchone():
            conn.execute(
                f"UPDATE {_TABLE} SET state_dir = %s, description = %s, updated_at = %s "
                f"WHERE name = %s",
                (str(profile_dir(name)), description, now, name),
            )
        else:
            conn.execute(
                f"INSERT INTO {_TABLE} (name, state_dir, description, created_at, updated_at) "
                f"VALUES (%s, %s, %s, %s, %s)",
                (name, str(profile_dir(name)), description, now, now),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — registry is best-effort
        logger.debug("profiles: register failed: %s", exc)


def _deregister(name: str, conn=None) -> None:
    try:
        if conn is None:
            from tools.db.storage import get_connection

            conn = get_connection()
        _ensure_schema(conn)
        conn.execute(f"DELETE FROM {_TABLE} WHERE name = %s", (name,))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("profiles: deregister failed: %s", exc)


def _registry_rows(conn=None) -> dict[str, dict[str, Any]]:
    try:
        if conn is None:
            from tools.db.storage import get_connection

            conn = get_connection()
        _ensure_schema(conn)
        cur = conn.execute(
            f"SELECT name, state_dir, description, created_at FROM {_TABLE}"
        )
        return {
            r[0]: {"name": r[0], "state_dir": r[1], "description": r[2], "created_at": r[3]}
            for r in cur.fetchall()
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("profiles: registry read failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def create_profile(name: str, description: str = "", *, conn=None) -> dict[str, Any]:
    """Scaffold ``~/.icdev/profiles/<name>/`` and register it. Idempotent."""
    validate_name(name)
    d = profile_dir(name)
    skills_dir(name).mkdir(parents=True, exist_ok=True)
    env = overlay_env_path(name)
    if not env.exists():
        env.write_text(
            "# CUI // SP-CTI\n"
            f"# dotenv overlay for the '{name}' SAG profile — applied on top of the\n"
            "# base .env when this profile is active. Put per-profile LLM routing,\n"
            "# toggles, or credentials here.\n",
            encoding="utf-8",
        )
    meta = {"name": name, "description": description, "created_at": _utcnow()}
    _meta_path(name).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _register(name, description, conn=conn)
    return {
        "name": name,
        "state_dir": str(d),
        "env": str(env),
        "skills": str(skills_dir(name)),
        "description": description,
    }


def remove_profile(name: str, *, purge: bool = False, conn=None) -> bool:
    """Deregister a profile. With ``purge`` also delete its state directory.

    If the removed profile is the active one, the pointer is cleared.
    """
    validate_name(name)
    existed = profile_dir(name).exists() or name in _registry_rows(conn=conn)
    _deregister(name, conn=conn)
    if active_profile() == name:
        set_active(None)
    if purge:
        import shutil

        try:
            shutil.rmtree(profile_dir(name))
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("profiles: purge failed: %s", exc)
    return existed


def list_profiles(*, conn=None) -> list[dict[str, Any]]:
    """Merge filesystem profiles with the DB registry (newest metadata wins)."""
    rows = _registry_rows(conn=conn)
    root = profiles_root()
    if root.exists():
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name not in rows:
                rows[child.name] = {
                    "name": child.name,
                    "state_dir": str(child),
                    "description": "",
                    "created_at": None,
                }
    active = active_profile()
    out = []
    for name in sorted(rows):
        r = dict(rows[name])
        r["active"] = name == active
        out.append(r)
    return out


def get_profile(name: str, *, conn=None) -> Optional[dict[str, Any]]:
    for p in list_profiles(conn=conn):
        if p["name"] == name:
            return p
    if profile_dir(name).exists():
        return {"name": name, "state_dir": str(profile_dir(name)), "active": active_profile() == name}
    return None


# ---------------------------------------------------------------------------
# Overlay env application (process-local)
# ---------------------------------------------------------------------------
def load_overlay(name: str) -> dict[str, str]:
    """Parse a profile's dotenv overlay into a dict (empty if absent)."""
    out: dict[str, str] = {}
    if is_default(name):
        return out
    path = overlay_env_path(name)
    try:
        if not path.exists():
            return out
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                out[k] = v
    except Exception as exc:  # noqa: BLE001
        logger.debug("profiles: overlay read failed: %s", exc)
    return out


def apply_overlay(name: str, *, override: bool = True) -> list[str]:
    """Apply a profile's overlay into ``os.environ`` (process-local).

    Returns the list of keys applied. ``override`` (default) lets a profile's
    value win over the base ``.env``; pass ``False`` to only fill unset keys.
    Callers in long-running hosts that build runtimes for many profiles should
    avoid this — the SAG CLI (a short-lived process) is the intended caller.
    """
    applied = []
    for k, v in load_overlay(name).items():
        if override or k not in os.environ:
            os.environ[k] = v
            applied.append(k)
    return applied
