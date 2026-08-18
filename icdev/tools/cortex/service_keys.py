# CUI // SP-CTI
"""Cortex service keys — auth + server-side CortexContext binding (ctx-expose-02).

External consumers (compass, idea_lab, future apps) authenticate to the
Cortex REST surface with an ``icdev_ctx_`` API key. The key row — not the
request body — is authoritative for identity and policy:

* ``tenant_id`` comes from the key row. A tenant_id in the request payload
  is IGNORED; callers cannot impersonate another tenant.
* ``classification`` is clamped to the key's ``classification_ceiling``
  (Bell-LaPadula: the caller may request a label the ceiling dominates,
  never one above it).
* ``air_gap`` / ``fail_closed`` follow server policy; a caller may only
  RAISE strictness (request True), never lower it.
* ``trusted_content`` is force-cleared — network callers never skip the
  input injection screen.

Pattern follows ``tools/dashboard/auth.py`` (SHA-256 hash at rest, plaintext
shown exactly once, revocable) and idea_lab's ``tools/qa/api_keys.py``.

CLI::

    python -m tools.cortex.service_keys create --label compass --tenant compass \
        --scopes cortex:search,cortex:ask,cortex:complete --json
    python -m tools.cortex.service_keys list --json
    python -m tools.cortex.service_keys revoke --key-id <id> --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

from .schemas import CortexContext

logger = get_logger("icdev.cortex.service_keys")

API_KEY_PREFIX = "icdev_ctx_"

# The REST v1 operations a key can be scoped to BY DEFAULT (tools/cortex/rest_v1.py).
# ``agent`` is reachable over REST as of hgx-cx-02 but is NOT in this tuple: see
# AGENT_SCOPES below — it is granted explicitly, never by default.
REST_OPERATIONS = ("search", "ask", "complete", "reason", "classify", "extract",
                   "govern",
                   # Deterministic deck assembly from a caller-supplied spec —
                   # no LLM spend, so it rides in the default grant.
                   "slides")

# Scope vocabulary. cortex:<operation> per facade; databridge:<connector>:<rw>
# for the feeds surface (tools/dashboard/api/databridge_feeds.py);
# cortex:intake covers the whole RICOAS intake bridge
# (tools/cortex/rest_intake.py, prem-ricoas-02) — granted explicitly, never
# by default, because intake writes sessions/requirements into the platform.
CORTEX_SCOPES = tuple(f"cortex:{op}" for op in REST_OPERATIONS)
INTAKE_SCOPES = ("cortex:intake",)
# Same rule, same reason (prem-recomp-05): win_themes WRITES to the proposal
# theme registry, and those themes are injected into the /proposals + /rfi
# drafting prompts. A key that can search must not silently also be able to put
# words in a proposal's mouth, so this is never in the default grant.
WIN_THEME_SCOPES = ("cortex:win_themes",)
# Same rule, same reason (prem-pstaff-02): staffing_matrix WRITES the person -> LCAT
# mappings that become a bid's Key Personnel volume. A key that can search must not
# silently also be able to STAFF a bid — to put a named human against a labour
# category the customer will price and evaluate. Never in the default grant.
STAFFING_SCOPES = ("cortex:staffing_matrix",)
# Same rule again (prem-bid-02): cost_volume PRICES A BID. It writes pg_cost_volumes and
# the priced line items that a won bid then carries into /cpmp as the delivery baseline.
# A key that can search must not silently also be able to put a PRICE on a proposal.
PRICING_SCOPES = ("cortex:cost_volume",)
# Same rule again (prem-rpt-02): dashboard builds and EXPORTS a customer-facing report.
# The export leaves the platform by design — that is what an export IS — so a key that
# can search must not silently also be able to render our data into a document and walk
# out with it.
DASHBOARD_SCOPES = ("cortex:dashboard",)
# prem-bid-04: award TRANSITIONS A WON BID INTO A DELIVERY CONTRACT — it creates a
# cpmp_contracts row with real money and CLINs against it. A key that can price a bid
# must not silently also be able to declare it won and open a contract.
AWARD_SCOPES = ("cortex:award",)
# The BOM Evidence Engine. Granted explicitly, never by default, and the reason is
# the payload rather than the write: a caller hands over the CONTENTS of their
# uploaded documents — bills of materials, quotes, architecture drawings, asset
# inventories — which is the most commercially sensitive material a customer has.
# A key that can search the platform must not silently also be able to post a
# competitor's pricing into it.
BOM_SCOPES = ("cortex:bom",)
# hgx-cx-02: agent LAUNCHES EXECUTION — an ACE team, an agent loop, or a Studio
# graph run — against the platform, on the platform's budget, under the key's
# tenant. It is the single most consequential thing a Cortex key can do, and the
# gap between it and every other scope is not one of degree: search reads,
# complete writes text, agent DOES things. It has always been off the REST
# surface for exactly that reason; putting it on does not make it a default.
#
# A key that can search must not silently also be able to spend the platform's
# tokens for an hour, nor to start a workflow whose nodes hold their own tool
# authorizations.
AGENT_SCOPES = ("cortex:agent",)
DATABRIDGE_SCOPES = ("databridge:iris:read", "databridge:iris:write",
                     "databridge:icdev_demand:read",
                     "databridge:icdev_cpmp:read", "databridge:icdev_cpmp:write",
                     # cef-bck-02: the Cortex `external` search rung. A key
                     # holding `cortex:search` reaches five in-boundary corpora;
                     # this is the sixth, and it is the only one whose evidence
                     # comes from off-box, so it is a separate grant rather than
                     # a property of being able to search. The scope is per
                     # CONNECTOR (`databridge:<connector>:read`), matching the
                     # broker's own connector+table allowlist — authorising a key
                     # for one external source must not authorise it for the next
                     # one an operator adds. Never in DEFAULT_SCOPES.
                     "databridge:rss:read")
ALL_SCOPES = (CORTEX_SCOPES + INTAKE_SCOPES + WIN_THEME_SCOPES + STAFFING_SCOPES
              + PRICING_SCOPES + DASHBOARD_SCOPES + AWARD_SCOPES + BOM_SCOPES
              + AGENT_SCOPES + DATABRIDGE_SCOPES)

# Default scopes for a newly created key: the core REST operations (the
# spend-heavier feeds/write scopes are granted explicitly).
DEFAULT_SCOPES = CORTEX_SCOPES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_key() -> str:
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_prefix(raw_key: str) -> str:
    """First 8 chars after the prefix — enough to identify, useless to guess."""
    return raw_key[len(API_KEY_PREFIX):][:8]


def _get_db():
    return get_connection()


def _parse_scopes(value: Any) -> List[str]:
    """Normalize a scopes cell (JSON string from SQLite, list from PG JSONB)."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return [str(s) for s in value] if isinstance(value, list) else []


def create_key(
    label: str,
    tenant_id: str,
    *,
    scopes: Optional[List[str]] = None,
    classification_ceiling: str = "CUI",
    created_by: str = "",
) -> Dict[str, Any]:
    """Create a service key. Returns the RAW key — the only time it is visible."""
    if not label.strip():
        raise ValueError("label is required")
    if not tenant_id.strip():
        raise ValueError("tenant_id is required")
    scope_list = list(scopes) if scopes else list(DEFAULT_SCOPES)
    unknown = [s for s in scope_list if s not in ALL_SCOPES]
    if unknown:
        raise ValueError(f"unknown scopes: {', '.join(unknown)} (valid: {', '.join(ALL_SCOPES)})")

    raw_key = generate_key()
    key_id = str(uuid.uuid4())
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO cortex_service_keys
               (id, label, key_hash, key_prefix, tenant_id,
                classification_ceiling, scopes, status, created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)""",
            (
                key_id,
                label.strip(),
                hash_key(raw_key),
                key_prefix(raw_key),
                tenant_id.strip(),
                classification_ceiling,
                json.dumps(scope_list),
                created_by or None,
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("cortex service key created: id=%s label=%s tenant=%s", key_id, label, tenant_id)
    return {
        "key_id": key_id,
        "raw_key": raw_key,
        "prefix": key_prefix(raw_key),
        "label": label.strip(),
        "tenant_id": tenant_id.strip(),
        "scopes": scope_list,
        "classification_ceiling": classification_ceiling,
    }


def verify_key(raw_key: str) -> Optional[Dict[str, Any]]:
    """Validate a raw key. Returns the key record dict or None. Never raises."""
    if not raw_key or not raw_key.startswith(API_KEY_PREFIX):
        return None
    try:
        conn = _get_db()
        try:
            row = conn.execute(
                """SELECT id, label, tenant_id, classification_ceiling, scopes, status
                   FROM cortex_service_keys
                   WHERE key_hash = %s AND status = 'active'""",
                (hash_key(raw_key),),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE cortex_service_keys SET last_used_at = %s WHERE id = %s",
                (_now(), row["id"]),
            )
            conn.commit()
            return {
                "key_id": row["id"],
                "label": row["label"],
                "tenant_id": row["tenant_id"],
                "classification_ceiling": row["classification_ceiling"] or "CUI",
                "scopes": _parse_scopes(row["scopes"]),
            }
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — auth must fail closed, not crash
        logger.warning("cortex service key verification failed: %s", exc)
        return None


def grant_scopes(key_id: str, scopes: List[str]) -> Dict[str, Any]:
    """Add scopes to an ACTIVE key, without re-issuing it.

    A key's scopes are frozen at creation, so every key issued before a new
    scope existed would 403 on it forever — the only remedy being revoke +
    re-issue + redistribute the secret to the integrator. That is a bad trade
    for a purely additive grant, so scopes can be widened in place.

    Additive only: this never removes a scope (use revoke_key to kill a key).
    """
    unknown = [s for s in scopes if s not in ALL_SCOPES]
    if unknown:
        raise ValueError(
            f"unknown scopes: {', '.join(unknown)} (valid: {', '.join(ALL_SCOPES)})")

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT scopes FROM cortex_service_keys WHERE id = %s AND status = 'active'",
            (key_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no active service key {key_id}")

        current = _parse_scopes(dict(row)["scopes"])
        merged = sorted(set(current) | set(scopes))
        conn.execute(
            "UPDATE cortex_service_keys SET scopes = %s WHERE id = %s",
            (json.dumps(merged), key_id),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("cortex service key %s granted scopes: %s", key_id,
                ", ".join(sorted(set(scopes) - set(current))) or "(no change)")
    return {"id": key_id, "scopes": merged, "added": sorted(set(scopes) - set(current))}


def revoke_key(key_id: str, revoked_by: str = "") -> bool:
    conn = _get_db()
    try:
        conn.execute(
            """UPDATE cortex_service_keys
               SET status = 'revoked', revoked_at = %s, revoked_by = %s
               WHERE id = %s AND status = 'active'""",
            (_now(), revoked_by or None, key_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_keys() -> List[Dict[str, Any]]:
    """List keys with hashes redacted."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT id, label, key_prefix, tenant_id, classification_ceiling,
                      scopes, status, created_at, last_used_at, revoked_at
               FROM cortex_service_keys ORDER BY created_at DESC"""
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["scopes"] = _parse_scopes(d.get("scopes"))
            out.append(d)
        return out
    finally:
        conn.close()


def _clamp_classification(requested: str, ceiling: str) -> str:
    """Bell-LaPadula clamp: allow ``requested`` only if the ceiling dominates it."""
    requested = (requested or "").strip()
    if not requested:
        return ceiling
    try:
        from tools.security.security_context import classifications_dominated_by

        dominated = classifications_dominated_by(ceiling) or set()
        if requested in dominated or requested == ceiling:
            return requested
    except Exception:  # noqa: BLE001 — on any doubt, clamp to the ceiling
        pass
    return ceiling


def resolve_context(raw_key: str, requested: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Bind a raw key + caller-requested fields into a trusted CortexContext.

    Returns ``{"ctx": CortexContext, "scopes": [...], "key_id": ..., "label":
    ..., "tenant_id": ...}`` or None when the key is invalid/revoked.

    Only attribution fields (user_id, session_id, domain, agent_id) pass
    through from ``requested``; identity and policy fields are derived from
    the key row and server policy. A caller may raise strictness
    (air_gap=True, fail_closed=True) but never lower it.
    """
    record = verify_key(raw_key)
    if record is None:
        return None
    req = requested or {}
    ctx = CortexContext(
        tenant_id=record["tenant_id"],  # NEVER from the request
        user_id=str(req.get("user_id") or ""),
        classification=_clamp_classification(
            str(req.get("classification") or ""), record["classification_ceiling"]
        ),
        domain=str(req.get("domain") or ""),
        session_id=str(req.get("session_id") or ""),
        agent_id=str(req.get("agent_id") or f"cortex:svc:{record['label']}"),
        # Strictness may only be raised: True is honored, anything else defers
        # to server policy (env / args/cortex_config.yaml).
        air_gap=bool(req.get("air_gap") is True),
        fail_closed=True if req.get("fail_closed") is True else None,
        # Network callers never skip the input injection screen.
        trusted_content=False,
    )
    return {
        "ctx": ctx,
        "scopes": record["scopes"],
        "key_id": record["key_id"],
        "label": record["label"],
        "tenant_id": record["tenant_id"],
    }


def has_scope(scopes: List[str], required: str) -> bool:
    return required in (scopes or [])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli_main() -> int:
    parser = argparse.ArgumentParser(description="Manage Cortex service keys")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a new service key")
    p_create.add_argument("--label", required=True)
    p_create.add_argument("--tenant", required=True)
    p_create.add_argument("--scopes", default="",
                          help=f"Comma-separated (default: {','.join(DEFAULT_SCOPES)})")
    p_create.add_argument("--ceiling", default="CUI", help="Classification ceiling")
    p_create.add_argument("--created-by", default="cli")
    p_create.add_argument("--json", action="store_true")

    p_list = sub.add_parser("list", help="List service keys (hashes redacted)")
    p_list.add_argument("--json", action="store_true")

    p_grant = sub.add_parser(
        "grant", help="Add scopes to an existing active key (additive; no re-issue)")
    p_grant.add_argument("--key-id", required=True)
    p_grant.add_argument("--scopes", required=True, help="Comma-separated")
    p_grant.add_argument("--json", action="store_true")

    p_revoke = sub.add_parser("revoke", help="Revoke a service key")
    p_revoke.add_argument("--key-id", required=True)
    p_revoke.add_argument("--revoked-by", default="cli")
    p_revoke.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.command == "create":
        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()] or None
        result = create_key(
            args.label, args.tenant,
            scopes=scopes, classification_ceiling=args.ceiling, created_by=args.created_by,
        )
        print(json.dumps(result, indent=None if args.json else 2))
        print("\nStore the raw_key now — it is not retrievable later.", flush=True)
    elif args.command == "list":
        print(json.dumps(list_keys(), indent=None if args.json else 2, default=str))
    elif args.command == "grant":
        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
        result = grant_scopes(args.key_id, scopes)
        print(json.dumps(result, indent=None if args.json else 2))
    elif args.command == "revoke":
        ok = revoke_key(args.key_id, revoked_by=args.revoked_by)
        print(json.dumps({"revoked": ok, "key_id": args.key_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
