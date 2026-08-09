"""OSINT + executor tier resolver for ICDEV™ Strategos.

Resolves which connectivity tier is active at runtime for both OSINT
collection and task execution. Used by osint_harvester, kanban_scheduler,
and any importer that hits an external source.

OSINT tier resolution (first reachable wins):
    is_airgap() == False              -> TIER_INTERNET
    is_airgap() + gitlab_reachable()  -> TIER_GITLAB
    file_inbox_has_data()             -> TIER_FILE_INBOX
    else                              -> TIER_NONE

Executor tier resolution:
    is_airgap() == False              -> EXEC_CLAUDE_CLI
    is_airgap() + gitlab_reachable()  -> EXEC_GITLAB
    else                              -> EXEC_OLLAMA_LOCAL

Result is cached for 5 minutes to avoid repeated probes per scheduler cycle.
Tier changes are logged at INFO level.
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import json
import logging
import os
import time
import requests
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = get_logger(__name__)

# OSINT tiers
TIER_INTERNET = "INTERNET"
TIER_GITLAB = "GITLAB"
TIER_FILE_INBOX = "FILE_INBOX"
TIER_NONE = "NONE"

# Executor tiers
EXEC_CLAUDE_CLI = "CLAUDE_CLI"
EXEC_GITLAB = "GITLAB"
EXEC_OLLAMA_LOCAL = "OLLAMA_LOCAL"

CACHE_TTL = 300  # 5 minutes

_ROOT = Path(__file__).resolve().parents[2]
_INBOX = _ROOT / "data" / "osint_inbox"

_cache: TierStatus | None = None
_cache_time: float = 0


@dataclass
class TierStatus:
    osint_tier: str
    exec_tier: str
    gitlab_reachable: bool
    file_inbox_count: int
    ollama_reachable: bool
    resolved_at: datetime

    def to_dict(self) -> dict:
        d = asdict(self)
        d["resolved_at"] = self.resolved_at.isoformat()
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def gitlab_reachable() -> bool:
    """GET {GITLAB_URL}/api/v4/version with 3s timeout. Returns False on any exception."""
    try:
        gitlab_url = os.getenv("GITLAB_URL", "").rstrip("/")
        if not gitlab_url:
            return False
        url = f"{gitlab_url}/api/v4/version"
        resp = requests.get(url, headers={"User-Agent": "ICDEV-TierResolver/1.0"}, timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def file_inbox_has_data() -> int:
    """Return count of *.json and *.txt files in data/osint_inbox/."""
    try:
        if not _INBOX.is_dir():
            return 0
        return sum(1 for f in os.listdir(_INBOX) if f.endswith(".json") or f.endswith(".txt"))
    except Exception:
        return 0


def _ollama_reachable() -> bool:
    try:
        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        resp = requests.get(f"{base}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def resolve_tiers(use_cache: bool = True) -> TierStatus:
    """Resolve OSINT and executor tiers, caching the result for 5 minutes.

    Pass use_cache=False to force a fresh probe (e.g., in tests).
    """
    global _cache, _cache_time

    now = time.monotonic()
    if use_cache and _cache is not None and (now - _cache_time) < CACHE_TTL:
        return _cache

    from tools.airgap import is_airgap  # imported late to allow test monkeypatching

    airgap = is_airgap()
    gitlab_ok = False
    file_count = 0
    ollama_ok = False

    if not airgap:
        osint_tier = TIER_INTERNET
        exec_tier = EXEC_CLAUDE_CLI
    else:
        gitlab_ok = gitlab_reachable()
        file_count = file_inbox_has_data()

        # OSINT tier: first reachable wins
        if gitlab_ok:
            osint_tier = TIER_GITLAB
        elif file_count > 0:
            osint_tier = TIER_FILE_INBOX
        else:
            osint_tier = TIER_NONE

        # Executor tier
        if gitlab_ok:
            exec_tier = EXEC_GITLAB
        else:
            exec_tier = EXEC_OLLAMA_LOCAL
            ollama_ok = _ollama_reachable()

    # Log changes since last resolution
    if _cache is not None:
        if _cache.osint_tier != osint_tier:
            logger.info("OSINT tier changed: %s -> %s", _cache.osint_tier, osint_tier)
        if _cache.exec_tier != exec_tier:
            logger.info("Executor tier changed: %s -> %s", _cache.exec_tier, exec_tier)

    status = TierStatus(
        osint_tier=osint_tier,
        exec_tier=exec_tier,
        gitlab_reachable=gitlab_ok,
        file_inbox_count=file_count,
        ollama_reachable=ollama_ok,
        resolved_at=datetime.now(timezone.utc),
    )

    _cache = status
    _cache_time = now
    return status


def invalidate_cache() -> None:
    """Force the next resolve_tiers() call to re-probe."""
    global _cache, _cache_time
    _cache = None
    _cache_time = 0


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Resolve OSINT + executor tiers")
    parser.add_argument("--json", action="store_true", help="Output TierStatus as JSON")
    parser.add_argument("--no-cache", action="store_true", help="Bypass 5-minute cache")
    args = parser.parse_args()

    status = resolve_tiers(use_cache=not args.no_cache)

    if args.json:
        print(status.to_json())
    else:
        print(f"OSINT tier    : {status.osint_tier}")
        print(f"Executor tier : {status.exec_tier}")
        print(f"GitLab reach  : {status.gitlab_reachable}")
        print(f"Inbox files   : {status.file_inbox_count}")
        print(f"Ollama reach  : {status.ollama_reachable}")
        print(f"Resolved at   : {status.resolved_at.isoformat()}")
