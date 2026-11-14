# CUI // SP-CTI
"""ICDEV Cortex — read models behind the console pages and the read MCP tools (icx-cnf-05).

The feature-conformance audit (icx-cnf-01) found Cortex features a person could
reach only over REST and an agent could reach only through a mutating tool. Each
function here is the ONE read both surfaces share: a console page renders it and a
read-only MCP tool returns it, so the page and the agent never see two answers.

Every function only reads. None writes a row, calls a model, or runs the TRUST
chain (running the chain writes ``cortex_audit`` + provenance rows, which is why
``cortex_govern`` stays declared mutating and ``trust_chain`` is its read).
Every function degrades to a stated ``status``/``error`` rather than raising, so a
page renders and a tool answers on a fresh database.

    pmo_status(opportunity_id)   win themes, key personnel, latest cost volume
    intake_sessions()            bridge-created RICOAS intake sessions
    trust_chain()                gate order, profiles, recent gate outcomes
    agent_launches()             governed cortex.agent calls from cortex_audit
    service_keys()               service keys, hashes never read, scope vocabulary
    airgap_posture()             air-gap switch, exclusions, readiness invariant
    findings()                   persisted resolution findings + why a set is empty
    metrics()                    the cortex_audit rollup /cortex/metrics renders
"""
from __future__ import annotations

import json
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.cortex.console")

# What each PMO verb does to the platform, and the scope a service key needs for
# it. The console page and the MCP read show the same catalog. ``decision`` marks
# the verbs a PERSON decides: their workflow is icx-cnf-02's, so the console here
# never takes them (award is not called at all; bom is called without a
# credibility designation, so the engine only proposes).
PMO_VERBS = (
    {"verb": "slides", "path": "/cortex/api/v1/slides", "writes": "nothing (renders a deck)",
     "scope": "cortex:slides", "default_grant": True, "decision": False},
    {"verb": "win_themes", "path": "/cortex/api/v1/win_themes", "writes": "pg_win_themes",
     "scope": "cortex:win_themes", "default_grant": False, "decision": False},
    {"verb": "staffing_matrix", "path": "/cortex/api/v1/staffing_matrix",
     "writes": "key personnel mappings", "scope": "cortex:staffing_matrix",
     "default_grant": False, "decision": False},
    {"verb": "cost_volume", "path": "/cortex/api/v1/cost_volume", "writes": "pg_cost_volumes",
     "scope": "cortex:cost_volume", "default_grant": False, "decision": False},
    {"verb": "dashboard", "path": "/cortex/api/v1/dashboard", "writes": "nothing (exports a report)",
     "scope": "cortex:dashboard", "default_grant": False, "decision": False},
    {"verb": "bom", "path": "/cortex/api/v1/bom", "writes": "nothing (reconciles in a temp dir)",
     "scope": "cortex:bom", "default_grant": False, "decision": True},
    {"verb": "award", "path": "/cortex/api/v1/award", "writes": "a draft cpmp_contracts row",
     "scope": "cortex:award", "default_grant": False, "decision": True},
)

_MAX_LIMIT = 200


def _limit(value: Any, default: int = 50) -> int:
    try:
        return max(1, min(int(value or default), _MAX_LIMIT))
    except (TypeError, ValueError):
        return default


def _conn():
    from tools.db.storage import get_connection

    return get_connection()


def _close(conn) -> None:
    try:
        conn.close()
    except Exception as exc:  # noqa: BLE001 — a failed close must not mask the read
        logger.debug("cortex console: close failed: %s", exc)


# ── PMO ──────────────────────────────────────────────────────────────────────


def pmo_status(opportunity_id: str = "") -> dict:
    """What the PMO verbs have registered against one opportunity."""
    out: dict[str, Any] = {"verbs": [dict(v) for v in PMO_VERBS],
                           "opportunity_id": str(opportunity_id or "").strip()}
    opp = out["opportunity_id"]
    if not opp:
        out["status"] = "no_opportunity"
        return out
    errors: dict[str, str] = {}
    try:
        from tools.govcon.win_theme_manager import list_themes

        out["win_themes"] = [
            {k: t.get(k) for k in ("id", "theme_type", "statement", "supporting_evidence",
                                   "priority", "status", "created_at")}
            for t in list_themes(opp)
        ]
    except Exception as exc:  # noqa: BLE001 — table may not exist on a fresh DB
        errors["win_themes"] = str(exc)[:200]
        out["win_themes"] = []
    try:
        from tools.govcon.key_personnel import list_key_personnel

        out["key_personnel"] = list_key_personnel(opp)
    except Exception as exc:  # noqa: BLE001
        errors["key_personnel"] = str(exc)[:200]
        out["key_personnel"] = []
    conn = None
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT id, contract_type, total_evaluated_price, direct_labor_cost, "
            "ptw_estimate_low, ptw_estimate_high, created_at "
            "FROM pg_cost_volumes WHERE opportunity_id = %s ORDER BY created_at DESC LIMIT 1",
            (opp,),
        ).fetchone()
        out["cost_volume"] = dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        errors["cost_volume"] = str(exc)[:200]
        out["cost_volume"] = None
    finally:
        if conn is not None:
            _close(conn)
    out["errors"] = errors
    out["status"] = "partial" if errors else "ok"
    return out


# ── Intake bridge ────────────────────────────────────────────────────────────


def intake_sessions(tenant_id: str = "", limit: Any = 50) -> dict:
    """RICOAS intake sessions the Cortex bridge created, whitelisted fields only.

    A session counts as a bridge session when its ``context_summary`` carries the
    ``bridge_tenant`` the bridge stamps (``rest_intake.api_v1_intake_session_create``).
    ``tenant_id`` narrows to one tenant's sessions.
    """
    from .rest_intake import _SESSION_FIELDS, _continue_url
    from .rest_intake import _db as _intake_db

    conn = None
    try:
        conn = _intake_db()
        rows = conn.execute(
            "SELECT " + ", ".join(_SESSION_FIELDS) + ", context_summary "
            "FROM intake_sessions ORDER BY created_at DESC LIMIT %s",
            (_MAX_LIMIT * 5,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": str(exc)[:200], "sessions": [], "count": 0}
    finally:
        if conn is not None:
            _close(conn)
    sessions = []
    for raw in rows:
        row = dict(raw)
        try:
            context = json.loads(row.pop("context_summary") or "{}")
        except (TypeError, ValueError):
            context = {}
        if not isinstance(context, dict) or "bridge_tenant" not in context:
            continue
        if tenant_id and context.get("bridge_tenant") != tenant_id:
            continue
        row["origin"] = str(context.get("origin") or "")
        row["bridge_tenant"] = str(context.get("bridge_tenant") or "")
        row["continue_url"] = _continue_url(str(row.get("id") or ""))
        sessions.append(row)
        if len(sessions) >= _limit(limit):
            break
    return {"status": "ok" if sessions else "idle", "sessions": sessions, "count": len(sessions)}


# ── Govern (TRUST chain) ─────────────────────────────────────────────────────


def trust_chain(window_hours: int = 24) -> dict:
    """The TRUST chain as configured, and what its gates did in the window."""
    from . import governance as gov

    out: dict[str, Any] = {
        "gate_order": list(gov.GATE_ORDER),
        "mandatory": list(gov.MANDATORY_GATES),
        "opt_in": list(gov.OPT_IN_GATES),
        "default_profile": gov.DEFAULT_PROFILE,
    }
    try:
        profiles = gov.load_governance_profiles()
        out["profiles"] = {name: [g for g in gov.GATE_ORDER if g in gates]
                           for name, gates in sorted(profiles.items())}
        out["profile_error"] = ""
    except Exception as exc:  # noqa: BLE001 — a broken profiles block is the finding
        out["profiles"] = {gov.DEFAULT_PROFILE: list(gov.DEFAULT_GATES)}
        out["profile_error"] = str(exc)[:300]
    stats = metrics(window_hours)
    out["window_hours"] = stats["window_hours"]
    out["status"] = stats.get("status", "unavailable")
    out["calls"] = stats["summary"]["calls"]
    out["blocked"] = stats["summary"]["blocked"]
    out["by_outcome"] = stats.get("by_outcome") or {}
    out["by_gate"] = stats.get("by_gate") or []
    out["last_call_at"] = stats.get("last_call_at", "")
    return out


# ── Agent launches ───────────────────────────────────────────────────────────


def agent_launches(limit: Any = 25) -> dict:
    """Governed ``cortex.agent`` calls, newest first, from the append-only audit.

    Read through the RLS-aware connection, so a reader sees only its tenant's rows
    at or below its classification.
    """
    conn = None
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT id, session_id, tenant_id, classification, agent_id, user_id, "
            "outcome, blocked, created_at FROM cortex_audit "
            "WHERE function = %s ORDER BY created_at DESC LIMIT %s",
            ("cortex.agent", _limit(limit, 25)),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — the table appears on the first governed call
        return {"status": "unavailable", "error": str(exc)[:200], "launches": [], "count": 0}
    finally:
        if conn is not None:
            _close(conn)
    launches = []
    for raw in rows:
        row = dict(raw)
        row["blocked"] = bool(row.get("blocked"))
        launches.append(row)
    return {"status": "ok" if launches else "idle", "launches": launches, "count": len(launches)}


# ── Service keys ─────────────────────────────────────────────────────────────


def service_keys(include_prefix: bool = True) -> dict:
    """Service keys (the hash column is never selected) and the scope vocabulary.

    ``include_prefix=False`` drops the key prefix too, for a surface (MCP) where
    identifying a key by label and id is enough.
    """
    from . import service_keys as sk

    vocabulary = {
        "default": list(sk.DEFAULT_SCOPES),
        "explicit_only": sorted(set(sk.ALL_SCOPES) - set(sk.DEFAULT_SCOPES)),
    }
    try:
        keys = sk.list_keys()
    except Exception as exc:  # noqa: BLE001 — the table appears with the first key
        return {"status": "unavailable", "error": str(exc)[:200], "keys": [],
                "count": 0, "scopes": vocabulary}
    if not include_prefix:
        keys = [{k: v for k, v in key.items() if k != "key_prefix"} for key in keys]
    active = sum(1 for k in keys if k.get("status") == "active")
    return {"status": "ok" if keys else "idle", "keys": keys, "count": len(keys),
            "active": active, "scopes": vocabulary}


# ── Air-gap guard ────────────────────────────────────────────────────────────


def airgap_posture() -> dict:
    """Is air-gap on, what it excludes, and does the readiness invariant hold now."""
    from . import config as cfg

    out: dict[str, Any] = {"env_var": cfg.AIRGAP_ENV_VAR}
    try:
        out["airgap"] = bool(cfg.airgap_active(None))
        out["excluded_models"] = cfg.airgap_exclusions(None) or []
        out["fail_closed_default"] = bool(cfg.resolve_fail_closed(None))
        out["routing_functions"] = list(cfg.CORTEX_ROUTING_FUNCTIONS)
    except Exception as exc:  # noqa: BLE001
        out.update({"status": "unavailable", "error": str(exc)[:300]})
        return out
    try:
        cfg.assert_airgap_ready()
        out["ready"] = True
        out["ready_error"] = ""
    except Exception as exc:  # noqa: BLE001 — CortexAirgapError names every missing chain
        out["ready"] = False
        out["ready_error"] = str(exc)[:1000]
    out["status"] = "ok" if out["ready"] else "not_ready"
    return out


# ── Findings and metrics (reads the console already renders) ─────────────────


def findings(tenant_id: str = "default", finding_type: str | None = None,
             entity: str = "", limit: Any = 50) -> dict:
    """Persisted resolution findings plus the state that explains an empty set."""
    from . import finding_store as fs

    tenant = str(tenant_id or "default")
    try:
        stats = fs.finding_stats(tenant)
    except Exception as exc:  # noqa: BLE001
        stats = {"state": "unavailable", "error": str(exc)[:200]}
    try:
        rows = fs.list_findings(tenant, finding_type=finding_type or None,
                                entity=str(entity or ""), limit=_limit(limit))
    except Exception as exc:  # noqa: BLE001
        return {"stats": stats, "findings": [], "count": 0, "error": str(exc)[:200]}
    return {"stats": stats, "findings": rows, "count": len(rows)}


def metrics(window_hours: Any = 24) -> dict:
    """The cortex_audit rollup (``metrics.summarize``) for the trailing window."""
    from .metrics import summarize

    try:
        hours = max(1, min(int(window_hours or 24), 24 * 30))
    except (TypeError, ValueError):
        hours = 24
    return summarize(window_hours=hours)
