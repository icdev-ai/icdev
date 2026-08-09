# CUI // SP-CTI
"""ICDEV LLM proxy virtual-key issuance (lpx-keys-01).

Operators run /gameday and /academy cohorts on a SHARED provider account. The
strategy doc's key-provisioning step is a raw ``curl`` against the LiteLLM admin
API — not something a cohort lead can operate safely. This module turns that into
an ICDEV tool with ``issue`` / ``list`` / ``show`` operations and a ``--json``
CLI (house convention).

Security model (mirrors ``tools/saas/auth/api_key_auth.py``):

* The **master/admin key** for the proxy is read from the environment
  (``ICDEV_LLM_PROXY_MASTER_KEY``), NEVER logged and NEVER returned in any
  output.
* An issued **virtual key** is returned exactly ONCE, at creation time. Only a
  SHA-256 hash of it is persisted (``llm_proxy_keys.key_hash``). A lost key is
  rotated (see ``lpx-keys-03``), never recovered.
* The virtual key is what a local canvas copy / cohort member presents to the
  gateway; the real provider key stays server-side on the proxy and is never
  distributed (see ``lpx-keys-04``).

OPT-IN, OFF BY DEFAULT. Issuance always records the key locally so a cohort lead
can pre-provision before the proxy is stood up; when ``ICDEV_LLM_PROXY_ENABLED``
is truthy and the proxy is reachable, the key is also registered with LiteLLM
(``POST /key/generate``) so it works immediately. LiteLLM sync is best-effort and
never blocks local issuance — ``litellm_synced`` records whether it succeeded.

This module lives under ``tools/llm/`` on purpose: the ``provider_bypass``
coherence gate (lpx-router-03) flags provider-URL literals / API-key env reads
OUTSIDE ``tools/llm/``. Key management is an LLM-proxy concern and belongs here.

Budgets and rate ceilings (``max_budget_usd`` / ``rpm_limit`` / ``tpm_limit`` /
``budget_window``) are accepted and stored here so a cohort lead can issue a
budgeted key in one step; the enforcement wiring onto ICDEV's tenancy and the
per-team fairness ceilings are built in ``lpx-keys-02`` / ``lpx-teams-01``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection

# --- Constants --------------------------------------------------------------

# Scopes ICDEV recognises. These map onto EXISTING grouping units, not a new
# notion of "student" (see lpx-keys-02): a platform tenant, a gameday ttx team,
# an academy guild, or an individual dashboard user (local canvas copy).
SCOPE_TYPES = ("tenant", "team", "guild", "user")

# Budget window shapes. gameday budgets are per bounded EXERCISE (a ttx session,
# duration_minutes DEFAULT 120); academy/tenant budgets may be monthly. Decided
# per-scope in lpx-keys-02 / lpx-teams-02 — this module only records the shape.
BUDGET_WINDOWS = ("exercise", "day", "month", "none")

KEY_STATUSES = ("active", "revoked", "rotated", "expired")

# Displayed prefix length (enough to identify a key in a list without exposing
# enough to be useful if leaked). Mirrors api_key_auth's key[:12] logging.
_PREFIX_LEN = 16

_KEY_PREFIX = "sk-icdev-"

ENV_MASTER_KEY = "ICDEV_LLM_PROXY_MASTER_KEY"

# Default key lifetime (lpx-keys-03). A cohort key must not outlive the cohort,
# so issuance applies a default expiry when the caller gives none. Operators tune
# it via env; 0/negative disables the default (an explicit expiry always wins).
ENV_DEFAULT_TTL_DAYS = "ICDEV_LLM_PROXY_KEY_TTL_DAYS"
_DEFAULT_TTL_DAYS = 30

# Audit actions (lpx-keys-03, NIST AU). The audit table is APPEND-ONLY — see
# APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py. Rows are never UPDATE/DELETE.
AUDIT_ACTIONS = ("issued", "rotated", "revoked", "expired")

_DDL = """
CREATE TABLE IF NOT EXISTS llm_proxy_keys (
    key_id          TEXT PRIMARY KEY,
    key_hash        TEXT NOT NULL UNIQUE,
    key_prefix      TEXT NOT NULL,
    alias           TEXT,
    scope_type      TEXT NOT NULL DEFAULT 'tenant',
    scope_ref       TEXT,
    session_id      TEXT,
    max_budget_usd  REAL,
    budget_window   TEXT NOT NULL DEFAULT 'none',
    rpm_limit       INTEGER,
    tpm_limit       INTEGER,
    status          TEXT NOT NULL DEFAULT 'active',
    expires_at      TEXT,
    litellm_synced  INTEGER NOT NULL DEFAULT 0,
    rotated_from    TEXT,
    tenant_id       TEXT,
    classification  TEXT,
    created_by      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT
);
"""

# Append-only key lifecycle audit (lpx-keys-03, NIST AU). Never UPDATE/DELETE.
_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS llm_proxy_key_audit (
    audit_id       TEXT PRIMARY KEY,
    key_id         TEXT NOT NULL,
    action         TEXT NOT NULL,
    actor          TEXT,
    detail         TEXT,
    tenant_id      TEXT,
    classification TEXT,
    recorded_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_llm_proxy_keys_scope ON llm_proxy_keys(scope_type, scope_ref)",
    "CREATE INDEX IF NOT EXISTS idx_llm_proxy_keys_session ON llm_proxy_keys(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_llm_proxy_keys_status ON llm_proxy_keys(status)",
    "CREATE INDEX IF NOT EXISTS idx_llm_proxy_key_audit_key ON llm_proxy_key_audit(key_id)",
)

_migrated = False


# --- Schema -----------------------------------------------------------------

def ensure_schema(conn=None) -> None:
    """Idempotently create the key-storage table. Safe to call repeatedly."""
    global _migrated
    if _migrated and conn is None:
        return
    own = conn is None
    c = conn or get_connection()
    try:
        c.execute(_DDL.strip())
        c.execute(_AUDIT_DDL.strip())
        for stmt in _INDEXES:
            try:
                c.execute(stmt)
            except Exception:
                pass
        c.commit()
    finally:
        if own:
            _migrated = True
    return


# --- Hashing / key generation ----------------------------------------------

def _hash_key(key: str) -> str:
    """SHA-256 hash of a virtual key (mirrors api_key_auth._hash_key)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _generate_virtual_key() -> str:
    """Generate a fresh virtual key. Cryptographically random, URL-safe."""
    return _KEY_PREFIX + secrets.token_urlsafe(32)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- LiteLLM admin sync (best-effort) --------------------------------------

def _master_key() -> Optional[str]:
    """The proxy admin key, from env. NEVER logged or returned."""
    val = os.environ.get(ENV_MASTER_KEY, "").strip()
    return val or None


def _proxy_base_url() -> str:
    from tools.llm.proxy_gateway import proxy_base_url

    return proxy_base_url()


def _register_with_litellm(
    virtual_key: str,
    *,
    alias: Optional[str],
    max_budget_usd: Optional[float],
    rpm_limit: Optional[int],
    tpm_limit: Optional[int],
    duration: Optional[str],
) -> bool:
    """Register *virtual_key* with LiteLLM via ``POST /key/generate``.

    Best-effort: returns True on success, False on any failure (proxy disabled,
    unreachable, no master key, non-2xx). NEVER raises — local issuance must not
    depend on the proxy being up. The master key is sent only in the Authorization
    header and is never logged.
    """
    from tools.llm.proxy_gateway import is_proxy_enabled

    if not is_proxy_enabled():
        return False
    master = _master_key()
    if not master:
        return False

    payload: Dict[str, Any] = {"key": virtual_key}
    if alias:
        payload["key_alias"] = alias
    if max_budget_usd is not None:
        payload["max_budget"] = float(max_budget_usd)
    if rpm_limit is not None:
        payload["rpm_limit"] = int(rpm_limit)
    if tpm_limit is not None:
        payload["tpm_limit"] = int(tpm_limit)
    if duration:
        payload["duration"] = duration

    url = _proxy_base_url() + "/key/generate"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # nosec B310 — operator-configured proxy URL
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {master}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


# --- Audit (append-only, NIST AU) ------------------------------------------

def _default_ttl_days() -> int:
    raw = os.environ.get(ENV_DEFAULT_TTL_DAYS, "").strip()
    if not raw:
        return _DEFAULT_TTL_DAYS
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_TTL_DAYS


def _default_expiry(now: Optional[datetime] = None) -> Optional[str]:
    """Default expiry so a cohort key cannot outlive the cohort. None when the
    operator disables the default (TTL <= 0)."""
    days = _default_ttl_days()
    if days <= 0:
        return None
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(days=days)).isoformat()


def _write_audit(
    conn,
    key_id: str,
    action: str,
    *,
    actor: Optional[str] = None,
    detail: Optional[str] = None,
    tenant_id: Optional[str] = None,
    classification: Optional[str] = None,
) -> None:
    """Append one immutable audit row. Never mutates or deletes existing rows."""
    conn.execute(
        """
        INSERT INTO llm_proxy_key_audit (
            audit_id, key_id, action, actor, detail, tenant_id, classification, recorded_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (uuid.uuid4().hex, key_id, action, actor, detail, tenant_id, classification, _now()),
    )


def audit_trail(key_id: Optional[str] = None, conn=None) -> List[Dict[str, Any]]:
    """Return the append-only audit trail (all, or for one key), newest first."""
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    if key_id:
        rows = c.execute(
            "SELECT audit_id, key_id, action, actor, detail, tenant_id, "
            "classification, recorded_at FROM llm_proxy_key_audit "
            "WHERE key_id = %s ORDER BY recorded_at DESC",
            (key_id,),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT audit_id, key_id, action, actor, detail, tenant_id, "
            "classification, recorded_at FROM llm_proxy_key_audit "
            "ORDER BY recorded_at DESC"
        ).fetchall()
    if own:
        try:
            c.close()
        except Exception:
            pass
    return [dict(r) for r in rows]


# --- Public API -------------------------------------------------------------

def _validate_choice(name: str, value: str, allowed: tuple) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, got {value!r}")


def issue_key(
    *,
    alias: Optional[str] = None,
    scope_type: str = "tenant",
    scope_ref: Optional[str] = None,
    session_id: Optional[str] = None,
    max_budget_usd: Optional[float] = None,
    budget_window: str = "none",
    rpm_limit: Optional[int] = None,
    tpm_limit: Optional[int] = None,
    expires_at: Optional[str] = None,
    expires_in_days: Optional[int] = None,
    tenant_id: Optional[str] = None,
    classification: Optional[str] = None,
    created_by: Optional[str] = None,
    conn=None,
) -> Dict[str, Any]:
    """Issue a new virtual key. Returns metadata plus the plaintext key ONCE.

    The returned dict contains ``virtual_key`` — the ONLY time it is ever
    available. Only its SHA-256 hash is stored. The master/admin key is never
    read into the return value.

    Expiry (lpx-keys-03): an explicit ``expires_at`` wins; else ``expires_in_days``
    is applied; else a default TTL (``ICDEV_LLM_PROXY_KEY_TTL_DAYS``, default 30)
    so a cohort key cannot outlive the cohort. Set the env to 0 to disable the
    default. Issuance appends an immutable ``issued`` audit row (NIST AU).
    """
    _validate_choice("scope_type", scope_type, SCOPE_TYPES)
    _validate_choice("budget_window", budget_window, BUDGET_WINDOWS)

    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)

    if expires_at is None:
        if expires_in_days is not None and expires_in_days > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=int(expires_in_days))).isoformat()
        else:
            expires_at = _default_expiry()

    virtual_key = _generate_virtual_key()
    key_hash = _hash_key(virtual_key)
    key_prefix = virtual_key[:_PREFIX_LEN]
    key_id = uuid.uuid4().hex
    now = _now()

    synced = _register_with_litellm(
        virtual_key,
        alias=alias,
        max_budget_usd=max_budget_usd,
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        duration=None,
    )

    c.execute(
        """
        INSERT INTO llm_proxy_keys (
            key_id, key_hash, key_prefix, alias, scope_type, scope_ref,
            session_id, max_budget_usd, budget_window, rpm_limit, tpm_limit,
            status, expires_at, litellm_synced, rotated_from, tenant_id,
            classification, created_by, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            key_id, key_hash, key_prefix, alias, scope_type, scope_ref,
            session_id, max_budget_usd, budget_window, rpm_limit, tpm_limit,
            "active", expires_at, 1 if synced else 0, None, tenant_id,
            classification, created_by, now, now,
        ),
    )
    _write_audit(
        c, key_id, "issued",
        actor=created_by,
        detail=f"scope={scope_type}:{scope_ref} window={budget_window} expires_at={expires_at}",
        tenant_id=tenant_id,
        classification=classification,
    )
    c.commit()
    if own:
        try:
            c.close()
        except Exception:
            pass

    return {
        "key_id": key_id,
        "virtual_key": virtual_key,  # returned exactly once
        "key_prefix": key_prefix,
        "alias": alias,
        "scope_type": scope_type,
        "scope_ref": scope_ref,
        "session_id": session_id,
        "max_budget_usd": max_budget_usd,
        "budget_window": budget_window,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "status": "active",
        "expires_at": expires_at,
        "litellm_synced": bool(synced),
        "created_at": now,
    }


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    # Never expose the hash in normal output.
    d.pop("key_hash", None)
    d["litellm_synced"] = bool(d.get("litellm_synced"))
    return d


def list_keys(
    *,
    scope_type: Optional[str] = None,
    scope_ref: Optional[str] = None,
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    conn=None,
) -> List[Dict[str, Any]]:
    """List issued keys (metadata only — never the key or its hash)."""
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)

    clauses: List[str] = []
    params: List[Any] = []
    if scope_type:
        clauses.append("scope_type = %s")
        params.append(scope_type)
    if scope_ref:
        clauses.append("scope_ref = %s")
        params.append(scope_ref)
    if session_id:
        clauses.append("session_id = %s")
        params.append(session_id)
    if status:
        clauses.append("status = %s")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = c.execute(
        "SELECT key_id, key_hash, key_prefix, alias, scope_type, scope_ref, "
        "session_id, max_budget_usd, budget_window, rpm_limit, tpm_limit, "
        "status, expires_at, litellm_synced, rotated_from, tenant_id, "
        "classification, created_by, created_at, updated_at "
        "FROM llm_proxy_keys" + where + " ORDER BY created_at DESC",
        tuple(params),
    ).fetchall()
    if own:
        try:
            c.close()
        except Exception:
            pass
    return [_row_to_dict(r) for r in rows]


def show_key(key_id: str, conn=None) -> Optional[Dict[str, Any]]:
    """Return metadata for one key by id (never the key or its hash)."""
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    row = c.execute(
        "SELECT key_id, key_hash, key_prefix, alias, scope_type, scope_ref, "
        "session_id, max_budget_usd, budget_window, rpm_limit, tpm_limit, "
        "status, expires_at, litellm_synced, rotated_from, tenant_id, "
        "classification, created_by, created_at, updated_at "
        "FROM llm_proxy_keys WHERE key_id = %s",
        (key_id,),
    ).fetchone()
    if own:
        try:
            c.close()
        except Exception:
            pass
    return _row_to_dict(row) if row else None


def lookup_by_key(virtual_key: str, conn=None) -> Optional[Dict[str, Any]]:
    """Resolve a presented virtual key to its metadata by hash, or None.

    Used by enforcement paths (lpx-keys-02+). The plaintext key is hashed and
    matched; it is never stored or logged.
    """
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    row = c.execute(
        "SELECT key_id, key_hash, key_prefix, alias, scope_type, scope_ref, "
        "session_id, max_budget_usd, budget_window, rpm_limit, tpm_limit, "
        "status, expires_at, litellm_synced, rotated_from, tenant_id, "
        "classification, created_by, created_at, updated_at "
        "FROM llm_proxy_keys WHERE key_hash = %s",
        (_hash_key(virtual_key),),
    ).fetchone()
    if own:
        try:
            c.close()
        except Exception:
            pass
    return _row_to_dict(row) if row else None


# --- Rotation / revocation / expiry (lpx-keys-03) --------------------------

def _get_key_row(conn, key_id: str):
    return conn.execute(
        "SELECT key_id, key_hash, key_prefix, alias, scope_type, scope_ref, "
        "session_id, max_budget_usd, budget_window, rpm_limit, tpm_limit, "
        "status, expires_at, litellm_synced, rotated_from, tenant_id, "
        "classification, created_by, created_at, updated_at "
        "FROM llm_proxy_keys WHERE key_id = %s",
        (key_id,),
    ).fetchone()


def is_expired(key: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """True if the key has an expiry in the past."""
    exp = key.get("expires_at")
    if not exp:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(exp) < now
    except (TypeError, ValueError):
        return False


def revoke_key(
    key_id: str,
    *,
    actor: Optional[str] = None,
    reason: Optional[str] = None,
    _status: str = "revoked",
    conn=None,
) -> Dict[str, Any]:
    """Revoke a key: flip status and append an immutable audit row.

    Revocation is per-key and takes effect immediately for every enforcement path
    (lookup/check see the flipped status) without touching any other key — the
    per-person guarantee lpx-keys-04 relies on. Idempotent: revoking an already
    inactive key is a no-op that still audits the attempt.
    """
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    row = _get_key_row(c, key_id)
    if row is None:
        if own:
            try:
                c.close()
            except Exception:
                pass
        raise ValueError(f"unknown key_id: {key_id}")
    key = _row_to_dict(row)

    c.execute(
        "UPDATE llm_proxy_keys SET status = %s, updated_at = %s WHERE key_id = %s",
        (_status, _now(), key_id),
    )
    action = "rotated" if _status == "rotated" else "revoked"
    _write_audit(
        c, key_id, action,
        actor=actor,
        detail=reason or f"status -> {_status}",
        tenant_id=key.get("tenant_id"),
        classification=key.get("classification"),
    )
    c.commit()
    result = show_key(key_id, conn=c)
    if own:
        try:
            c.close()
        except Exception:
            pass
    return result


def rotate_key(key_id: str, *, actor: Optional[str] = None, conn=None) -> Dict[str, Any]:
    """Rotate a key: revoke the old one and issue a fresh key with the SAME scope,
    budget, limits and remaining lifetime. Returns the NEW key (virtual_key shown
    once). ``rotated_from`` links the new key to its predecessor; both keys get an
    immutable audit row. Rotating one person's key never affects anyone else's."""
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    row = _get_key_row(c, key_id)
    if row is None:
        if own:
            try:
                c.close()
            except Exception:
                pass
        raise ValueError(f"unknown key_id: {key_id}")
    old = _row_to_dict(row)

    # Mark the old key rotated (audited as 'rotated').
    revoke_key(key_id, actor=actor, reason="rotated -> new key", _status="rotated", conn=c)

    # Issue the successor carrying the old key's parameters + remaining expiry.
    new = issue_key(
        alias=old.get("alias"),
        scope_type=old.get("scope_type") or "tenant",
        scope_ref=old.get("scope_ref"),
        session_id=old.get("session_id"),
        max_budget_usd=old.get("max_budget_usd"),
        budget_window=old.get("budget_window") or "none",
        rpm_limit=old.get("rpm_limit"),
        tpm_limit=old.get("tpm_limit"),
        expires_at=old.get("expires_at"),
        tenant_id=old.get("tenant_id"),
        classification=old.get("classification"),
        created_by=actor or old.get("created_by"),
        conn=c,
    )
    # Record the lineage link + a rotation audit row on the new key.
    c.execute(
        "UPDATE llm_proxy_keys SET rotated_from = %s WHERE key_id = %s",
        (key_id, new["key_id"]),
    )
    _write_audit(
        c, new["key_id"], "rotated",
        actor=actor,
        detail=f"rotated_from={key_id}",
        tenant_id=old.get("tenant_id"),
        classification=old.get("classification"),
    )
    c.commit()
    new["rotated_from"] = key_id
    if own:
        try:
            c.close()
        except Exception:
            pass
    return new


def expire_keys(now: Optional[datetime] = None, *, conn=None) -> Dict[str, Any]:
    """Sweep: flip any active key past its expiry to ``expired`` (audited).

    Idempotent and safe to run on a schedule. Returns the ids expired this run.
    """
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    now = now or datetime.now(timezone.utc)
    rows = c.execute(
        "SELECT key_id, expires_at, tenant_id, classification FROM llm_proxy_keys "
        "WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at != ''"
    ).fetchall()
    expired: List[str] = []
    for r in rows:
        d = dict(r)
        try:
            past = datetime.fromisoformat(d["expires_at"]) < now
        except (TypeError, ValueError):
            past = False
        if not past:
            continue
        c.execute(
            "UPDATE llm_proxy_keys SET status = 'expired', updated_at = %s WHERE key_id = %s",
            (_now(), d["key_id"]),
        )
        _write_audit(
            c, d["key_id"], "expired",
            detail=f"expires_at={d['expires_at']}",
            tenant_id=d.get("tenant_id"),
            classification=d.get("classification"),
        )
        expired.append(d["key_id"])
    c.commit()
    if own:
        try:
            c.close()
        except Exception:
            pass
    return {"expired": expired, "count": len(expired)}


# --- CLI --------------------------------------------------------------------

def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ICDEV LLM proxy virtual-key management")
    parser.add_argument("--json", action="store_true", help="JSON output")
    # Parent so --json is accepted BEFORE or AFTER the subcommand (house convention).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON output")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_issue = sub.add_parser("issue", help="Issue a new virtual key", parents=[common])
    p_issue.add_argument("--alias")
    p_issue.add_argument("--scope-type", default="tenant", choices=SCOPE_TYPES)
    p_issue.add_argument("--scope-ref")
    p_issue.add_argument("--session-id")
    p_issue.add_argument("--budget", type=float, dest="max_budget_usd")
    p_issue.add_argument("--budget-window", default="none", choices=BUDGET_WINDOWS)
    p_issue.add_argument("--rpm", type=int, dest="rpm_limit")
    p_issue.add_argument("--tpm", type=int, dest="tpm_limit")
    p_issue.add_argument("--expires-at")
    p_issue.add_argument("--expires-in-days", type=int)
    p_issue.add_argument("--tenant-id")
    p_issue.add_argument("--classification")
    p_issue.add_argument("--created-by")

    p_list = sub.add_parser("list", help="List issued keys", parents=[common])
    p_list.add_argument("--scope-type", choices=SCOPE_TYPES)
    p_list.add_argument("--scope-ref")
    p_list.add_argument("--session-id")
    p_list.add_argument("--status", choices=KEY_STATUSES)

    p_show = sub.add_parser("show", help="Show one key by id", parents=[common])
    p_show.add_argument("key_id")

    p_revoke = sub.add_parser("revoke", help="Revoke a key", parents=[common])
    p_revoke.add_argument("key_id")
    p_revoke.add_argument("--actor")
    p_revoke.add_argument("--reason")

    p_rotate = sub.add_parser("rotate", help="Rotate a key (revoke old, issue new)", parents=[common])
    p_rotate.add_argument("key_id")
    p_rotate.add_argument("--actor")

    sub.add_parser("expire", help="Sweep expired keys", parents=[common])

    p_audit = sub.add_parser("audit", help="Show append-only key audit trail", parents=[common])
    p_audit.add_argument("--key-id")

    args = parser.parse_args(argv)

    if args.cmd == "issue":
        result = issue_key(
            alias=args.alias,
            scope_type=args.scope_type,
            scope_ref=args.scope_ref,
            session_id=args.session_id,
            max_budget_usd=args.max_budget_usd,
            budget_window=args.budget_window,
            rpm_limit=args.rpm_limit,
            tpm_limit=args.tpm_limit,
            expires_at=args.expires_at,
            expires_in_days=args.expires_in_days,
            tenant_id=args.tenant_id,
            classification=args.classification,
            created_by=args.created_by,
        )
        if args.json:
            _print(result, True)
        else:
            print(f"Issued key {result['key_id']} (scope={result['scope_type']}:{result['scope_ref']})")
            print(f"VIRTUAL KEY (shown once, store it now): {result['virtual_key']}")
            print(f"litellm_synced={result['litellm_synced']}")
        return 0

    if args.cmd == "list":
        rows = list_keys(
            scope_type=args.scope_type,
            scope_ref=args.scope_ref,
            session_id=args.session_id,
            status=args.status,
        )
        if args.json:
            _print(rows, True)
        else:
            for r in rows:
                print(f"{r['key_id']}  {r['status']:8}  {r['scope_type']}:{r['scope_ref']}  {r['key_prefix']}…  {r['alias'] or ''}")
        return 0

    if args.cmd == "show":
        row = show_key(args.key_id)
        if row is None:
            _print({"error": "not found", "key_id": args.key_id}, args.json)
            return 1
        _print(row, args.json)
        return 0

    if args.cmd == "revoke":
        try:
            _print(revoke_key(args.key_id, actor=args.actor, reason=args.reason), args.json)
        except ValueError as exc:
            _print({"error": str(exc)}, args.json)
            return 1
        return 0

    if args.cmd == "rotate":
        try:
            result = rotate_key(args.key_id, actor=args.actor)
        except ValueError as exc:
            _print({"error": str(exc)}, args.json)
            return 1
        if args.json:
            _print(result, True)
        else:
            print(f"Rotated {args.key_id} -> {result['key_id']}")
            print(f"NEW VIRTUAL KEY (shown once, store it now): {result['virtual_key']}")
        return 0

    if args.cmd == "expire":
        _print(expire_keys(), args.json)
        return 0

    if args.cmd == "audit":
        _print(audit_trail(args.key_id), args.json)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
