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


REGISTRY: List[ReflexEntry] = [
    # ── CORE ─────────────────────────────────────────────────────────────────
    ReflexEntry("research",          CORE,      1.0,  "Autonomous research and knowledge synthesis"),
    ReflexEntry("scout",             CORE,      2.0,  "Scan for new tasks and surface opportunities"),
    ReflexEntry("ingest",            CORE,      1.0,  "Ingest external data into the knowledge graph"),
    ReflexEntry("learn",             CORE,      6.0,  "Consolidate memory and update embeddings"),
    ReflexEntry("heal",              CORE,      4.0,  "Self-healing: detect and remediate drift"),
    ReflexEntry("evolve",            CORE,      12.0, "Propose and apply system improvements"),
    ReflexEntry("awareness",         CORE,      3.0,  "Internal Awareness Engine — 5-phase self-model"),
    ReflexEntry("canvas_indexer",    CORE,      3.0,  "Re-index canvas components into awareness graph"),

    # ── STRATEGOS ─────────────────────────────────────────────────────────────
    ReflexEntry("strategos.osint_harvester", STRATEGOS, 4.0,
                "Background OSINT collection from RSS, ACLED, Telegram, and file inbox"),
    ReflexEntry("strategos.signal_scout",    STRATEGOS, 2.0,
                "Score and prioritize raw signals into sg_prioritized_signals"),

    # ── DOMAIN ────────────────────────────────────────────────────────────────
    ReflexEntry("market",                     DOMAIN, 1.0,  "Market data refresh and signal detection"),
    ReflexEntry("alphadesk_trap_scenarios",   DOMAIN, 4.0,  "AlphaDesk trap scenario analysis"),
    ReflexEntry("fathomdesk_trap_sweep",      DOMAIN, 4.0,  "FathomDesk trap detection sweep"),
    ReflexEntry("fathomdesk_openbb_refresh",  DOMAIN, 6.0,  "Refresh OpenBB market data cache"),
    ReflexEntry("govcon_scan",                DOMAIN, 6.0,  "GovCon/SAM.gov opportunity scan"),
    ReflexEntry("migration_intel",            DOMAIN, 6.0,  "Migration intelligence signal harvester"),

    # ── SUPPORT ───────────────────────────────────────────────────────────────
    ReflexEntry("audit",       SUPPORT, 6.0,  "Compliance and security audit sweep"),
    ReflexEntry("comply",      SUPPORT, 12.0, "NIST 800-53 control compliance check"),
    ReflexEntry("report",      SUPPORT, 6.0,  "Generate operational status reports"),
    ReflexEntry("publish",     SUPPORT, 12.0, "Publish Pulse articles from pending drafts"),
    ReflexEntry("test",        SUPPORT, 4.0,  "Run test suite and surface failures"),
    ReflexEntry("docs",        SUPPORT, 12.0, "Update auto-generated documentation"),
    ReflexEntry("experiment",  SUPPORT, 24.0, "Run experimental hypothesis checks"),
    ReflexEntry("synthesize",  SUPPORT, 6.0,  "Cross-domain signal synthesis"),
    ReflexEntry("kanban",      SUPPORT, 0.25, "Kanban scheduler — advance ready tasks"),
    ReflexEntry("quality",     SUPPORT, 6.0,  "Code and artifact quality gate"),
    ReflexEntry("goal_learner",       SUPPORT, 12.0, "Learn from goal execution outcomes"),
    ReflexEntry("remediation_lens",   SUPPORT, 4.0,  "Surface remediation opportunities"),
    ReflexEntry("failure_triage",     SUPPORT, 2.0,  "Triage and route failure events"),
    ReflexEntry("alphadesk_news_patterns", SUPPORT, 4.0, "AlphaDesk news pattern detection"),
    ReflexEntry("alphadesk_correlation_monitor", SUPPORT, 4.0, "AlphaDesk cross-asset correlation"),
    ReflexEntry("bdc_isa_expiry",     SUPPORT, 24.0, "BDC ISA expiry tracking"),
    ReflexEntry("cato_monitor",       SUPPORT, 6.0,  "cATO compliance monitoring"),
    ReflexEntry("cato_twin",          SUPPORT, 6.0,  "cATO digital twin sync"),
]

# Quick lookup: name → entry
_BY_NAME = {e.name: e for e in REGISTRY}


def get(name: str) -> ReflexEntry:
    """Return registry entry for the given reflex name. Raises KeyError if missing."""
    return _BY_NAME[name]


def by_tier(tier: str) -> List[ReflexEntry]:
    """Return all registered reflexes for a given tier."""
    return [e for e in REGISTRY if e.tier == tier]
