#!/usr/bin/env python3
# CUI // SP-CTI
"""WATCHCON alert tier constants for the ICDEV monitoring subsystem."""

# Alert tier identifiers
WATCHCON_4 = 4  # Routine
WATCHCON_3 = 3  # Elevated
WATCHCON_2 = 2  # High

WATCHCON_TIERS = (WATCHCON_4, WATCHCON_3, WATCHCON_2)

WATCHCON_LABELS = {
    WATCHCON_4: "routine",
    WATCHCON_3: "elevated",
    WATCHCON_2: "high",
}

# Maps WATCHCON tier → canonical severity string used in the alerts table
WATCHCON_TO_SEVERITY = {
    WATCHCON_4: "info",
    WATCHCON_3: "warning",
    WATCHCON_2: "critical",
}

# Maps canonical severity → WATCHCON tier (inverse of above)
SEVERITY_TO_WATCHCON = {v: k for k, v in WATCHCON_TO_SEVERITY.items()}

# Valid tier values for DB CHECK constraints
VALID_WATCHCON_TIERS = [str(t) for t in WATCHCON_TIERS]
