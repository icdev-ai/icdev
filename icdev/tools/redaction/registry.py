#!/usr/bin/env python3

# CUI // SP-CTI
"""Redaction Registry — conversation-scoped real<->surrogate mapping.

Maintains consistent mappings between real PII values and their surrogates
within a session. Same real entity always maps to same surrogate.

Features:
    - Session-scoped: each session/conversation gets isolated mappings
    - Persistent: mappings stored in SQLite for session resume
    - Reversible: de-anonymize output by reversing the mapping
    - Faker-powered: generates realistic surrogates for person names, locations
    - TTL-based cleanup: old mappings auto-expire

CLI:
    python tools/redaction/registry.py --session <id> --list --json
    python tools/redaction/registry.py --cleanup --json
    python tools/redaction/registry.py --health --json
"""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger("icdev.redaction.registry")


# ---------------------------------------------------------------------------
# Surrogate generators
# ---------------------------------------------------------------------------


def _generate_person_surrogate(index: int) -> str:
    """Generate a realistic person name surrogate."""
    try:
        from faker import Faker

        fake = Faker()
        Faker.seed(index)
        return fake.name()
    except ImportError:
        # NATO phonetic alphabet fallback
        names = [
            "Alex Alpha",
            "Blake Bravo",
            "Casey Charlie",
            "Drew Delta",
            "Eden Echo",
            "Finley Foxtrot",
            "Gray Golf",
            "Harper Hotel",
            "Indigo India",
            "Jordan Juliet",
            "Kai Kilo",
            "Lane Lima",
            "Morgan Mike",
            "Nova November",
            "Oakley Oscar",
            "Parker Papa",
            "Quinn Quebec",
            "Riley Romeo",
            "Sage Sierra",
            "Taylor Tango",
        ]
        return names[index % len(names)]


def _generate_location_surrogate(index: int) -> str:
    """Generate a realistic location surrogate."""
    try:
        from faker import Faker

        fake = Faker()
        Faker.seed(index + 1000)
        return fake.city()
    except ImportError:
        locations = [
            "Springfield",
            "Riverside",
            "Fairview",
            "Georgetown",
            "Madison",
            "Franklin",
            "Clinton",
            "Arlington",
            "Burlington",
            "Greenville",
            "Chester",
            "Salem",
        ]
        return locations[index % len(locations)]


def _generate_org_surrogate(index: int) -> str:
    """Generate an organization codename."""
    codenames = [
        "Organization ALPHA",
        "Organization BRAVO",
        "Organization CHARLIE",
        "Organization DELTA",
        "Organization ECHO",
        "Organization FOXTROT",
        "Organization GOLF",
        "Organization HOTEL",
        "Organization INDIA",
        "Organization JULIET",
        "Organization KILO",
        "Organization LIMA",
    ]
    return codenames[index % len(codenames)]


def _generate_agency_surrogate(index: int, agency_map: Dict[str, str], real_value: str) -> str:
    """Generate agency surrogate from map or generic."""
    # Check if there's a configured surrogate
    for agency, codename in agency_map.items():
        if agency.lower() == real_value.lower():
            return codename
    # Generic fallback
    generic = [
        "Agency ALPHA",
        "Agency BRAVO",
        "Agency CHARLIE",
        "Agency DELTA",
        "Agency ECHO",
        "Agency FOXTROT",
        "Agency GOLF",
        "Agency HOTEL",
    ]
    return generic[index % len(generic)]


def _generate_program_surrogate(index: int) -> str:
    """Generate a program name surrogate."""
    codenames = [
        "Project PHOENIX",
        "Project ATLAS",
        "Project SENTINEL",
        "Project MERIDIAN",
        "Project HORIZON",
        "Project CITADEL",
        "Project VANGUARD",
        "Project KEYSTONE",
        "Project BASTION",
        "Project ECLIPSE",
        "Project TRIDENT",
        "Project COMPASS",
    ]
    return codenames[index % len(codenames)]


def _generate_custom_surrogate(index: int) -> str:
    """Generate a generic surrogate for custom terms."""
    codenames = [
        "[REDACTED-1]",
        "[REDACTED-2]",
        "[REDACTED-3]",
        "[REDACTED-4]",
        "[REDACTED-5]",
        "[REDACTED-6]",
        "[REDACTED-7]",
        "[REDACTED-8]",
        "[REDACTED-9]",
        "[REDACTED-10]",
        "[REDACTED-11]",
        "[REDACTED-12]",
    ]
    return codenames[index % len(codenames)]


_SURROGATE_GENERATORS = {
    "PERSON": _generate_person_surrogate,
    "LOCATION": _generate_location_surrogate,
    "GOVCON_PROTECTED_ORG": _generate_org_surrogate,
    "PROTECTED_ORG": _generate_org_surrogate,
    "GOVCON_PROGRAM_NAME": _generate_program_surrogate,
    "PROGRAM_NAME": _generate_program_surrogate,
    "CUSTOM_TERM": _generate_custom_surrogate,
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class RedactionRegistry:
    """Conversation-scoped real<->surrogate mapping with persistence."""

    CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS redaction_registry (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        real_hash TEXT NOT NULL,
        surrogate TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        UNIQUE(session_id, entity_type, real_hash)
    );
    """

    def __init__(
        self, session_id: Optional[str] = None, agency_map: Optional[Dict[str, str]] = None, ttl_hours: int = 72
    ):
        self.session_id = session_id or str(uuid.uuid4().hex[:12])
        self._agency_map = agency_map or {}
        self._ttl_hours = ttl_hours
        self._cache: Dict[str, str] = {}  # real_hash -> surrogate
        self._reverse_cache: Dict[str, str] = {}  # surrogate -> real_value
        self._counters: Dict[str, int] = {}  # entity_type -> next index
        self._ensure_table()
        self._load_session()

    def _ensure_table(self) -> None:
        """Create registry table if needed."""
        try:
            conn = get_connection()
            conn.execute(self.CREATE_TABLE_SQL)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Could not create registry table: %s", e)

    def _load_session(self) -> None:
        """Load existing mappings for this session."""
        try:
            conn = get_connection()
            rows = conn.execute(
                "SELECT entity_type, real_hash, surrogate FROM redaction_registry "
                "WHERE session_id = %s AND (expires_at IS NULL OR expires_at > %s)",
                (self.session_id, datetime.now(timezone.utc).isoformat()),
            ).fetchall()
            for row in rows:
                key = f"{row['entity_type']}:{row['real_hash']}"
                self._cache[key] = row["surrogate"]
                # Count per entity type for index tracking
                etype = row["entity_type"]
                self._counters[etype] = self._counters.get(etype, 0) + 1
            conn.close()
        except Exception:
            pass

    def get_or_create_surrogate(self, entity_type: str, real_value: str) -> str:
        """Get existing surrogate or create a new one.

        Args:
            entity_type: PII entity type (PERSON, LOCATION, etc.)
            real_value: The actual PII text.

        Returns:
            Surrogate replacement text.
        """
        # Hash the real value (never store plaintext in DB)
        real_hash = hashlib.sha256(real_value.lower().encode("utf-8")).hexdigest()[:16]
        cache_key = f"{entity_type}:{real_hash}"

        # Check cache
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Generate new surrogate
        index = self._counters.get(entity_type, 0)

        if entity_type in ("GOVCON_AGENCY_NAME", "AGENCY_NAME"):
            surrogate = _generate_agency_surrogate(index, self._agency_map, real_value)
        elif entity_type in _SURROGATE_GENERATORS:
            surrogate = _SURROGATE_GENERATORS[entity_type](index)
        else:
            # Generic placeholder
            surrogate = f"[{entity_type}_{index + 1}]"

        self._counters[entity_type] = index + 1
        self._cache[cache_key] = surrogate
        self._reverse_cache[surrogate.lower()] = real_value

        # Persist
        self._persist(entity_type, real_hash, surrogate)

        return surrogate

    def _persist(self, entity_type: str, real_hash: str, surrogate: str) -> None:
        """Save mapping to database."""
        try:
            conn = get_connection()
            now = datetime.now(timezone.utc)
            expires = (now + timedelta(hours=self._ttl_hours)).isoformat() if self._ttl_hours > 0 else None
            conn.execute(
                "INSERT OR IGNORE INTO redaction_registry "
                "(id, session_id, entity_type, real_hash, surrogate, created_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), self.session_id, entity_type, real_hash, surrogate, now.isoformat(), expires),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Could not persist surrogate mapping: %s", e)

    def de_anonymize(self, text: str) -> str:
        """Reverse surrogate replacements in text.

        Args:
            text: Anonymized text with surrogates.

        Returns:
            Text with surrogates replaced by real values.
        """
        result = text
        # Sort by length descending to avoid partial replacements
        for surrogate, real_value in sorted(self._reverse_cache.items(), key=lambda x: -len(x[0])):
            # Case-insensitive replacement
            import re

            result = re.sub(re.escape(surrogate), real_value, result, flags=re.IGNORECASE)
        return result

    def list_mappings(self) -> List[Dict[str, str]]:
        """List all mappings for this session (surrogates only, no real values)."""
        mappings = []
        for cache_key, surrogate in self._cache.items():
            entity_type, real_hash = cache_key.split(":", 1)
            mappings.append(
                {
                    "entity_type": entity_type,
                    "real_hash": real_hash,
                    "surrogate": surrogate,
                }
            )
        return mappings

    def cleanup_expired(self) -> int:
        """Remove expired registry entries."""
        try:
            conn = get_connection()
            cursor = conn.execute(
                "DELETE FROM redaction_registry WHERE expires_at IS NOT NULL AND expires_at < %s",
                (datetime.now(timezone.utc).isoformat(),),
            )
            count = cursor.rowcount
            conn.commit()
            conn.close()
            return count
        except Exception:
            return 0

    def health(self) -> Dict[str, Any]:
        """Return registry health."""
        try:
            conn = get_connection()
            total = conn.execute("SELECT COUNT(*) FROM redaction_registry").fetchone()[0]
            sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM redaction_registry").fetchone()[0]
            conn.close()
        except Exception:
            total = 0
            sessions = 0

        return {
            "status": "ok",
            "session_id": self.session_id,
            "session_mappings": len(self._cache),
            "total_mappings": total,
            "active_sessions": sessions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Redaction Registry")
    parser.add_argument("--session", type=str, default=None, help="Session ID")
    parser.add_argument("--list", action="store_true", help="List session mappings")
    parser.add_argument("--cleanup", action="store_true", help="Remove expired entries")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    registry = RedactionRegistry(session_id=args.session)

    if args.health:
        result = registry.health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for k, v in result.items():
                print(f"{k}: {v}")
        return

    if args.cleanup:
        count = registry.cleanup_expired()
        result = {"cleaned": count}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Cleaned {count} expired entries.")
        return

    if args.list:
        mappings = registry.list_mappings()
        if args.json:
            print(json.dumps(mappings, indent=2))
        else:
            if not mappings:
                print("No mappings for this session.")
            else:
                for m in mappings:
                    print(f"  {m['entity_type']}: hash={m['real_hash']} → {m['surrogate']}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
