#!/usr/bin/env python3
# CUI // SP-CTI
"""Test fixture factory for the ICDEV™ secure log store.

Provides an in-memory / local-file-based mock that satisfies acceptance
criterion: importing the fixture and calling ``append_alert`` writes data
to the specified local directory without any network calls or subprocess
invocations.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class MockSecureLogStore:
    """Local-file-based mock secure log store.

    Accepts a temporary directory path and persists alerts as JSONL so
    tests can assert on written records without touching the network or
    real audit infrastructure.
    """

    def __init__(self, tmp_dir: Path) -> None:
        self.tmp_dir = Path(tmp_dir)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.tmp_dir / "secure_log.jsonl"

    def append_alert(self, alert_data: Dict[str, Any]) -> None:
        """Append a single alert record to the local secure log."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **alert_data,
        }
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def get_alerts(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Return all alerts, optionally filtered by key/value pairs."""
        alerts: List[Dict[str, Any]] = []
        if not self.log_file.exists():
            return alerts

        with open(self.log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if filters:
                    if all(record.get(k) == v for k, v in filters.items()):
                        alerts.append(record)
                else:
                    alerts.append(record)
        return alerts


# Pytest fixture entry-point
def mock_secure_log_store(tmp_path):
    """Fixture: return a :class:`MockSecureLogStore` rooted in ``tmp_path``."""
    return MockSecureLogStore(tmp_path)


# Convenience alias so tests can do:
#   from tests.factories import secure_log_fixture
secure_log_fixture = mock_secure_log_store
