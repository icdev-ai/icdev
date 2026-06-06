# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Foundry constants — source engines, signal status, thresholds, toggles.

Centralised so harvester / synthesizer / scorer share one vocabulary and the
SQL CHECK constraints in db/init_db.py are derived from Python constants
(never hardcoded — per the ICDEV™ entity-type guardrail).
"""

import os

# ---- Master feature toggle ----
# ACF only runs autonomously when this is explicitly enabled.
ICDEV_FOUNDRY_ENABLED = os.environ.get("ICDEV_FOUNDRY_ENABLED", "").lower() in ("true", "1", "yes")

# ---- Source engines that feed foundry_signals ----
# A signal's source_engine column is CHECK-constrained to this set.
SOURCE_ENGINES = (
    "innovation",   # tools/innovation web/competitive signal store (innovation_signals)
    "creative",     # creative engine outputs
    "research",     # industry research engine outputs
    "genesis",      # Oracle predictions + KG self-awareness gap nodes (oracle_predictions)
    "telemetry",    # introspective_analyzer internal telemetry (gate/unused/slow/knowledge gaps)
)

# ---- Signal lifecycle status ----
SIGNAL_STATUSES = (
    "new",          # freshly harvested, not yet clustered
    "clustered",    # consumed by synthesizer into a concept candidate
    "discarded",    # below relevance / superseded
)

# ---- Severity / weight defaults ----
# Maps a source severity label to a numeric weight used when collapsing
# cross-source duplicates (the higher-weight engine becomes the primary).
SEVERITY_WEIGHTS = {
    "critical": 1.0,
    "high": 0.8,
    "medium": 0.5,
    "low": 0.3,
    "info": 0.2,
}
DEFAULT_SEVERITY = "medium"

# When two engines surface the same normalized signal, the primary source_engine
# is chosen by this precedence (higher index wins ties on equal weight). Internal
# telemetry and Genesis predictions are higher-signal than raw external scrapes.
SOURCE_PRECEDENCE = {
    "innovation": 1,
    "creative": 1,
    "research": 1,
    "genesis": 2,
    "telemetry": 3,
}

# Default classification stamped on every harvested signal.
DEFAULT_CLASSIFICATION = "CUI"
