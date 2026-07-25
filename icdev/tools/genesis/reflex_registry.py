# CUI // SP-CTI
"""Genesis Reflex Registry — authoritative list of all reflexes and their tiers.

Tiers control scheduling priority and deployment constraints:
  CORE      — always-on, runs in every deployment mode
  STRATEGOS — intelligence/OSINT reflexes; require TIER_INTERNET, TIER_GITLAB,
               or TIER_FILE_INBOX resolution before executing
  DOMAIN    — application-domain reflexes (FathomDesk, Proposal Genesis, etc.)
  SUPPORT   — housekeeping (kanban, docs, quality)

Each entry:
  name        — importlib module name relative to tools.genesis.reflexes.*
  tier        — CORE / STRATEGOS / DOMAIN / SUPPORT
  interval_h  — target run interval in hours (advisory; daemon may adjust)
  description — one-line summary
"""
from __future__ import annotations

from typing import List, NamedTuple

CORE = "CORE"
STRATEGOS = "STRATEGOS"
DOMAIN = "DOMAIN"
SUPPORT = "SUPPORT"


class ReflexEntry(NamedTuple):
    name: str
    tier: str
    interval_h: float
    description: str
    on_demand: bool = False


REGISTRY: List[ReflexEntry] = [
    # ── CORE ─────────────────────────────────────────────────────────────────
    ReflexEntry("research",          CORE,      1.0,  "Autonomous research and knowledge synthesis"),
    ReflexEntry("scout",             CORE,      2.0,  "Scan for new tasks and surface opportunities"),
    ReflexEntry("doc_modernization_sweep", DOMAIN, 24.0, "Nightly document modernization: EOL/defacto evidence refresh, stale-doc scan, TRUST redlines, kanban rollups"),
    ReflexEntry("ingest",            CORE,      1.0,  "Ingest external data into the knowledge graph"),
    ReflexEntry("learn",             CORE,      6.0,  "Consolidate memory and update embeddings"),
    ReflexEntry("heal",              CORE,      4.0,  "Self-healing: detect and remediate drift"),
    ReflexEntry("evolve",            CORE,      12.0, "Propose and apply system improvements"),
    ReflexEntry("awareness",         CORE,      3.0,  "Internal Awareness Engine — 5-phase self-model"),
    ReflexEntry("canvas_indexer",    CORE,      3.0,  "Re-index canvas components into awareness graph"),
    ReflexEntry("self_monitor",      CORE,      0.5,  "Project internal health snapshots into operator alerts + failure_log (/monitoring)"),
    ReflexEntry("integrity_monitor", CORE,      6.0,  "SIPA self-assessment of ICDEV tools/ — open a card per NEW unauthorized capability vs baseline"),
    ReflexEntry("harness",           CORE,      6.0,  "Eval-harness metrics/degradation sweep — check_gates() → degradation cards; co-learning ECHO artifacts when ICDEV_HARNESS_COLEARN enabled"),

    # ── STRATEGOS ─────────────────────────────────────────────────────────────
    ReflexEntry("strategos.osint_harvester", STRATEGOS, 4.0,
                "Background OSINT collection from RSS, ACLED, Telegram, and file inbox"),
    ReflexEntry("strategos.signal_scout",    STRATEGOS, 2.0,
                "Score and prioritize raw signals into sg_prioritized_signals"),
    ReflexEntry("strategos.pattern_learner", STRATEGOS, 24.0,
                "RL weight-adjustment loop: aggregate annotations and update signal-scoring deltas",
                on_demand=True),
    ReflexEntry("dat_refresh", STRATEGOS, 6.0,
                "DAT: ingest diplomatic signals and recompute Diplomatic Tension Index (DTI) for all theaters"),

    # ── DOMAIN ────────────────────────────────────────────────────────────────
    ReflexEntry("market",                     DOMAIN, 1.0,  "Market data refresh and signal detection"),
    ReflexEntry("fathomdesk_trap_scenarios",   DOMAIN, 4.0,  "FathomDesk trap scenario analysis"),
    ReflexEntry("fathomdesk_trap_sweep",          DOMAIN, 4.0,  "FathomDesk trap detection sweep"),
    ReflexEntry("fathomdesk_openbb_refresh",      DOMAIN, 6.0,  "Refresh OpenBB market data cache"),
    ReflexEntry("fathomdesk_fundamentals_sweep",  DOMAIN, 23.0, "Daily PE/P/B/ROE fundamentals refresh from yfinance"),
    ReflexEntry("govcon_scan",                DOMAIN, 6.0,  "GovCon/SAM.gov opportunity scan"),
    ReflexEntry("migration_intel",            DOMAIN, 6.0,  "Migration intelligence signal harvester"),
    ReflexEntry("socmint",                    DOMAIN, 6.0,  "SOCMINT harvester — Telegram milblog → sg_socmint_signals"),
    ReflexEntry("aadc_compliance",            DOMAIN, 4.0,  "AADC compliance scoring — NIST/OWASP/ATLAS node coverage",
                on_demand=True),
    ReflexEntry("foundry_cycle",              DOMAIN, 12.0,
                "ACF: run one Autonomous Capability Foundry cycle (harvest→synth→novelty-gate→"
                "score→CoD→SIPA→seed); clean no-op when ICDEV_FOUNDRY_ENABLED is off"),
    ReflexEntry("mcip_dti_scorer",            DOMAIN, 6.0,  "MCIP DAT — compute and persist Diplomatic Tension Index every 6 h"),

    # ── SUPPORT ───────────────────────────────────────────────────────────────
    ReflexEntry("audit",       SUPPORT, 6.0,  "Compliance and security audit sweep"),
    ReflexEntry("comply",      SUPPORT, 12.0, "NIST 800-53 control compliance check"),
    ReflexEntry("report",      SUPPORT, 6.0,  "Generate operational status reports"),
    ReflexEntry("publish",     SUPPORT, 12.0, "Publish Pulse articles from pending drafts"),
    ReflexEntry("test",        SUPPORT, 4.0,  "Run test suite and surface failures"),
    ReflexEntry("docs",        SUPPORT, 12.0, "Update auto-generated documentation"),
    ReflexEntry("experiment",  SUPPORT, 24.0, "Run experimental hypothesis checks"),
    ReflexEntry("synthesize",  SUPPORT, 6.0,  "Cross-domain signal synthesis"),
    ReflexEntry("kanban",          SUPPORT, 0.25, "Kanban scheduler — advance ready tasks"),
    ReflexEntry("quality",         SUPPORT, 6.0,  "Code and artifact quality gate"),
    ReflexEntry("gepa_optimizer",  SUPPORT, 24.0, "GEPA: patch skill files from reflexion artifacts — self-improvement flywheel"),
    ReflexEntry("goal_learner",       SUPPORT, 12.0, "Learn from goal execution outcomes"),
    ReflexEntry("remediation_lens",   SUPPORT, 4.0,  "Surface remediation opportunities"),
    ReflexEntry("failure_triage",     SUPPORT, 2.0,  "Triage and route failure events"),
    ReflexEntry("fathomdesk_news_patterns", SUPPORT, 4.0, "FathomDesk news pattern detection"),
    ReflexEntry("fathomdesk_correlation_monitor", SUPPORT, 4.0, "FathomDesk cross-asset correlation"),
    ReflexEntry("bdc_isa_expiry",     SUPPORT, 24.0, "BDC ISA expiry tracking"),
    ReflexEntry("cato_monitor",       SUPPORT, 6.0,  "cATO compliance monitoring"),
    ReflexEntry("sdc_control_expiry", SUPPORT, 4.0,  "SDC security control-expiry sweep — IQR anomaly-thresholded review-date alerts"),
    ReflexEntry("cato_twin",               SUPPORT, 6.0,  "cATO digital twin sync"),
    ReflexEntry("wf_feedback_aggregation", SUPPORT, 6.0,
                "HITL feedback aggregation → wf_feedback_insights per canvas/template/type"),
    ReflexEntry("wf_ext_poller",           SUPPORT, 0.25,
                "HITL external step poller — checks Jira/SNOW/GitHub/Confluence/SharePoint status"),
    # Phase 71 — OHC Ops Hub Canvas reflexes
    ReflexEntry("llmops_drift_sweep",      SUPPORT, 4.0,
                "OHC: Quality drift check across all registered LLMs via llmops_engine"),
    ReflexEntry("mlops_data_drift_sweep",  SUPPORT, 4.0,
                "OHC: Evidently adapter data drift check for all registered datasets"),
    # ISP/Telco reflexes
    ReflexEntry("circuit_capacity_monitor", DOMAIN, 4.0,
                "CCC: Flag circuits ≥70% utilization; escalate critical to NOC alarms"),
    ReflexEntry("nocc_alarm_triage",        DOMAIN, 2.0,
                "NOCC: Correlate alarm storms and auto-create P2/P3 incidents"),
    ReflexEntry("nocc_sla_watcher",         DOMAIN, 4.0,
                "NOCC: Project SLA breach risk; set breach flag when compliance < target−0.5%"),
    ReflexEntry("bgp_route_monitor",        DOMAIN, 1.0,
                "NOCC: Query LibreNMS/SolarWinds for BGP session state; raise alarms for down sessions"),
    ReflexEntry("peering_health_monitor",   DOMAIN, 6.0,
                "PMC: Re-sync stale PeeringDB data (>7d); re-validate RPKI for high-traffic peers"),
    ReflexEntry("bgp_alerter_ingest",       DOMAIN, 1.0,
                "NOCC: Ingest BGPalerter JSON alerts into noc_alarms (hijack, route-leak, RPKI, session)"),
    ReflexEntry("peering_agreement_renewal", DOMAIN, 24.0,
                "Network: warn on peering agreements expiring within 90 days"),
    ReflexEntry("xc_order_poller",           DOMAIN, 1.0,
                "CCC: Poll in-flight cross-connect orders; alarm on delayed deliveries"),
    # ACE — ANVIL Co-Worker Engine reflexes
    ReflexEntry("ace_team_monitor",          SUPPORT, 6.0,
                "ACE: detect and escalate stale co-worker instances"),
    ReflexEntry("ace_skill_promoter",        SUPPORT, 24.0,
                "ACE: SIPA-gated daily promotion of success-pattern candidates to roles/candidates/"),
    # PMA — Program Management Analysis reflexes
    ReflexEntry("pma_credential_monitor",    DOMAIN, 24.0,
                "PMA: nightly credential expiry scan and SPOF dependency detection"),
    ReflexEntry("pma_int_gap_monitor",       DOMAIN, 168.0,
                "PMA: weekly INT gap persistence scan; seeds collection requirements and compliance risks"),
    # twx-cov-02 — cross-canvas twin freshness
    ReflexEntry("twin_freshness_sweep",      SUPPORT, 6.0,
                "Twin Core: observer-driven cross-canvas twin freshness sweep; publishes "
                "twin.snapshot.stale for twins with stale/absent snapshots (fills AADC/Mission/residual gap)"),
]

# Quick lookup: name → entry
_BY_NAME = {e.name: e for e in REGISTRY}


def get(name: str) -> ReflexEntry:
    """Return registry entry for the given reflex name. Raises KeyError if missing."""
    return _BY_NAME[name]


def by_tier(tier: str) -> List[ReflexEntry]:
    """Return all registered reflexes for a given tier."""
    return [e for e in REGISTRY if e.tier == tier]


def list_reflexes() -> List[ReflexEntry]:
    """Return all registered reflexes."""
    return list(REGISTRY)
