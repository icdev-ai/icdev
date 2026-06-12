#!/usr/bin/env python3
# CUI // SP-CTI
"""Dark Web Monitor — signal ingestion, TOR availability check, status management.

Signals represent dark web intelligence items: leaked credentials, threat actor
chatter, exposed endpoints, and marketplace listings relevant to monitored entities.

SIGNAL_TYPES: Categories of dark web signals tracked.
STATUS_VALUES: Lifecycle states for signal triage workflow.
"""

from __future__ import annotations

import shutil
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIGNAL_TYPES = [
    "leaked_creds",
    "threat_actor",
    "exploit_sale",
    "data_dump",
    "marketplace",
    "forum_post",
    "ransomware",
    "api_exposure",
]

STATUS_VALUES = ["new", "bridged", "reviewed"]

# ---------------------------------------------------------------------------
# Seed data — representative dark web signal feed
# ---------------------------------------------------------------------------

_SEED_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "dw-001",
        "score": 0.94,
        "signal_type": "leaked_creds",
        "title": "DoD contractor email/hash dump — 14k records",
        "source": "BreachForum .onion",
        "age_hours": 2,
        "status": "new",
    },
    {
        "id": "dw-002",
        "score": 0.87,
        "signal_type": "ransomware",
        "title": "LockBit 3.0 claims defense supplier breach",
        "source": "lockbit3o.onion",
        "age_hours": 6,
        "status": "new",
    },
    {
        "id": "dw-003",
        "score": 0.81,
        "signal_type": "exploit_sale",
        "title": "0-day for VPN appliance — asking $45k",
        "source": "XSS.is mirror",
        "age_hours": 11,
        "status": "bridged",
    },
    {
        "id": "dw-004",
        "score": 0.74,
        "signal_type": "threat_actor",
        "title": "APT28 infra reuse — new C2 cluster observed",
        "source": "Intel Raven feed",
        "age_hours": 18,
        "status": "new",
    },
    {
        "id": "dw-005",
        "score": 0.68,
        "signal_type": "api_exposure",
        "title": "Cloud API keys posted in Dread forum thread",
        "source": "dread.fail .onion",
        "age_hours": 24,
        "status": "bridged",
    },
    {
        "id": "dw-006",
        "score": 0.59,
        "signal_type": "data_dump",
        "title": "OPSEC failure — operator PII in clearnet paste",
        "source": "Raidforums archive",
        "age_hours": 36,
        "status": "reviewed",
    },
    {
        "id": "dw-007",
        "score": 0.52,
        "signal_type": "marketplace",
        "title": "Bulk SIM swaps offered for .mil domains",
        "source": "Genesis Market clone",
        "age_hours": 48,
        "status": "new",
    },
    {
        "id": "dw-008",
        "score": 0.43,
        "signal_type": "forum_post",
        "title": "Recon discussion: SCIF physical access methods",
        "source": "KickAss forum .onion",
        "age_hours": 72,
        "status": "reviewed",
    },
    {
        "id": "dw-009",
        "score": 0.38,
        "signal_type": "threat_actor",
        "title": "Low-confidence chatter re: satellite link disruption",
        "source": "Nulled.to archive",
        "age_hours": 96,
        "status": "reviewed",
    },
    {
        "id": "dw-010",
        "score": 0.29,
        "signal_type": "forum_post",
        "title": "OSINT thread on base perimeter fence gaps",
        "source": "Telegram .onion proxy",
        "age_hours": 120,
        "status": "reviewed",
    },
]


def _age_label(hours: int) -> str:
    if hours < 1:
        return "< 1h"
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


def get_signals(status_filter: str | None = None) -> list[dict[str, Any]]:
    """Return dark web signals, optionally filtered by status."""
    rows = [
        {**s, "age_label": _age_label(s["age_hours"])}
        for s in _SEED_SIGNALS
    ]
    if status_filter and status_filter in STATUS_VALUES:
        rows = [r for r in rows if r["status"] == status_filter]
    return sorted(rows, key=lambda r: r["score"], reverse=True)


def tor_status() -> bool:
    """Return True if the tor binary is present on PATH."""
    return shutil.which("tor") is not None


# ---------------------------------------------------------------------------
# CLI entry point — triggered by POST /api/strategos/darkweb/run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Dark Web Monitor scan")
    parser.add_argument("--scan", action="store_true", help="Run signal refresh")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.scan:
        import json

        signals = get_signals()
        result = {
            "status": "ok",
            "tor_available": tor_status(),
            "signal_count": len(signals),
            "signals": signals,
        }
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Scan complete — {result['signal_count']} signals, TOR={result['tor_available']}")
        sys.exit(0)

    sys.exit(1)
