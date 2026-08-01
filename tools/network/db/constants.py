"""Network Design Canvas — enumerations for SQL CHECK constraints.

Single source of truth for every enumerated value set used by a CHECK
constraint in ``tools/network/db/init_db.py``.  The DDL derives its
``CHECK(col IN (...))`` clauses from these tuples via :func:`_check`, so the
SQL can never silently drift away from the Python enum (the drift class that
broke ACE live — a constraint diverged from code and every write failed).

CLAUDE.md guardrail: *SQL CHECK constraints derive from Python constants,
never hardcode.*  Pattern copied from ``tools/ace/db/init_db.py``.

Only string-valued enumerations live here.  Boolean ``IN (0,1)`` flags and
numeric ``BETWEEN`` ranges in the schema are fixed domains, not drift-prone
enumerations, and are intentionally left inline.
"""
from __future__ import annotations


def _check(col: str, values: tuple[str, ...]) -> str:
    """Render a ``CHECK(<col> IN ('a', 'b', ...))`` clause from an enum tuple."""
    joined = ", ".join(f"'{v}'" for v in values)
    return f"CHECK({col} IN ({joined}))"


# ── Partners / peering ────────────────────────────────────────────────────────
PARTNER_TYPES: tuple[str, ...] = ("isp", "carrier", "cloud", "content", "enterprise", "ix")
PEER_STATUS: tuple[str, ...] = ("active", "suspended", "terminated")

# ── Classification / impact ───────────────────────────────────────────────────
CLASSIFICATIONS: tuple[str, ...] = ("PUBLIC", "CUI", "SECRET", "TS")
IMPACT_LEVELS: tuple[str, ...] = ("IL2", "IL4", "IL5", "IL6")
FLOW_CLASSIFICATIONS: tuple[str, ...] = ("NIPR", "SIPR", "IL2", "IL4", "IL5", "IL6")

# ── Ingestion ─────────────────────────────────────────────────────────────────
INGEST_STATUS: tuple[str, ...] = ("pending", "ingested", "failed")
INGEST_CHANNELS: tuple[str, ...] = ("api", "upload", "folder_watch", "nms_pull")
INGEST_RUN_STATUS: tuple[str, ...] = ("started", "completed", "failed")

# ── Stencils / icons ──────────────────────────────────────────────────────────
ICON_TYPES: tuple[str, ...] = ("png", "svg", "emf", "none")

# ── Documents ─────────────────────────────────────────────────────────────────
DOC_TYPES: tuple[str, ...] = (
    "runbook",
    "sop",
    "as_built",
    "change_request",
    "ip_plan",
    "design_doc",
    "general",
)

# ── Findings / compliance ─────────────────────────────────────────────────────
DOC_SOURCES: tuple[str, ...] = ("document", "runbook", "sop", "external")
SEVERITY_LEVELS: tuple[str, ...] = ("critical", "high", "medium", "low", "informational")
SEVERITY_HML: tuple[str, ...] = ("high", "medium", "low")
FINDING_DATA_SOURCES: tuple[str, ...] = ("nqe", "manual", "nvd", "vendor")
HITL_STATUS: tuple[str, ...] = ("pending", "approved", "rejected")
FINDING_STATUS: tuple[str, ...] = ("open", "in-progress", "mitigated", "excepted", "verified")
REMEDIATION_STATUS: tuple[str, ...] = ("open", "in-progress", "completed", "delayed", "cancelled")
EXCEPTION_TYPES: tuple[str, ...] = (
    "risk-acceptance",
    "temporary-deviation",
    "operational-necessity",
    "vendor-constraint",
)
APPROVAL_STATUS: tuple[str, ...] = (
    "pending",
    "isso-approved",
    "issm-approved",
    "fully-approved",
    "rejected",
    "expired",
)
RESULT_STATUS: tuple[str, ...] = ("pending", "success", "failed", "skipped")
ALERT_ACTIONS: tuple[str, ...] = ("acknowledged", "resolved")

# ── Audit-log action verbs ────────────────────────────────────────────────────
AUDIT_ACTIONS: tuple[str, ...] = (
    "translate",
    "explain",
    "run",
    "assess",
    "approve",
    "export",
    "upload",
    "simulate",
    "predict",
    "triage_score",
    "triage_approve",
    "plan_create",
)

# ── Risk ladder (shared severity domain) ──────────────────────────────────────
RISK_LEVELS: tuple[str, ...] = ("critical", "high", "medium", "low")

# ── CVE / exposure / NQE ──────────────────────────────────────────────────────
CVE_DATA_SOURCES: tuple[str, ...] = ("fwd-live", "icdev-internal")
TRENDS: tuple[str, ...] = ("rising", "stable", "declining")
EXPOSURE_TYPES: tuple[str, ...] = ("network", "local", "combined", "unknown")
NQE_SOURCES: tuple[str, ...] = ("nqe_api", "local_mapping", "local_heuristic", "llm_translation")
NQE_SOURCES_STATIC: tuple[str, ...] = ("nqe_api", "local_mapping", "local_heuristic", "static_registry")

# ── Patch / maintenance ───────────────────────────────────────────────────────
PATCH_STATUS: tuple[str, ...] = ("pending", "approved", "deferred", "scheduled")
RECURRENCES: tuple[str, ...] = ("none", "weekly", "biweekly", "monthly")
SIMULATION_STATUS: tuple[str, ...] = ("pending", "pass", "warn", "fail", "skipped")

# ── Link events ───────────────────────────────────────────────────────────────
LINK_EVENT_TYPES: tuple[str, ...] = ("up", "down", "flap", "reset", "timeout")

# ── Traffic flows / security domains / walkthroughs ───────────────────────────
APPLICATION_TYPES: tuple[str, ...] = (
    "sso_saml",
    "sso_oauth",
    "api_rest",
    "rdp",
    "ssh",
    "https_web",
    "ipsec_tunnel",
    "bgp",
    "dns",
    "custom",
)
DOMAIN_TYPES: tuple[str, ...] = (
    "on_prem",
    "nipr",
    "sipr",
    "bcap_vdms",
    "bcap_vdss",
    "csp_il2",
    "csp_il4",
    "csp_il5",
    "csp_il6",
    "internet",
    "inter_csp",
    "dmz",
    "custom",
)
ACTION_TYPES: tuple[str, ...] = (
    "originate",
    "route_lookup",
    "security_inspect",
    "encrypt_vpn",
    "decrypt_vpn",
    "tls_inspect",
    "authenticate",
    "authorize",
    "load_balance",
    "failover_check",
    "dns_resolve",
    "nat_translate",
    "deliver",
    "custom",
)
PERSONA_IDS: tuple[str, ...] = (
    "seceng",
    "neteng",
    "cloudarch",
    "compofficer",
    "appdev",
    "missionowner",
    "ciso",
)
