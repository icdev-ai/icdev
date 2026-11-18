# CUI // SP-CTI
"""Seed what the data discovery export e2e needs (osx-cap-02).

* Two FRESH, card-named test people (synthetic): ``cap02i-e2eprep`` and ``cap02i-e2epub``, both role reviewer, which
  holds the publish step, each with its own dashboard API key so the spec signs each into its own browser context.
  The preparer holds the role on purpose: what stops them publishing their own bundle is separation of duties
  (``separate_from: builders``), not a missing role. Their emails carry the run id, so every run has new people.
* A SYNTHETIC SQLite source, written to ``tests/e2e/fixtures/.cap02i_source.db`` (git-ignored): track summaries with a
  column of fake example.org emails, and relay outages with a duplicate id. Nothing in it describes a real system.

The design, its quality rules and their run, and the profile are made by the spec through the dashboard (the
product's own routes and the Explore page), never here and never by a hand-written INSERT. The server must be on a
throwaway database (globalSetup asserts it) and read the synthetic source declaration
(``ICDEV_DISCOVERY_SOURCES_E2E=tests/e2e/fixtures/discovery_sources.yaml``). Prints one JSON line: the run id, each
person's id and raw key (a throwaway key on a throwaway database; the spec only uses it to sign in) and the source.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "tests" / "e2e" / "fixtures" / ".cap02i_source.db"


def person(name: str, role: str, run: str) -> dict:
    from tools.dashboard.auth import create_api_key_for_user, create_user

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="osx-cap-02-e2e-seed")
    key = create_api_key_for_user(user["id"], label=f"osx-cap-02 e2e {run}", created_by="osx-cap-02-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"]}


def source() -> str:
    SOURCE.unlink(missing_ok=True)
    conn = sqlite3.connect(str(SOURCE))
    conn.executescript("""
        CREATE TABLE track_summary (id TEXT, ts TEXT, area TEXT, track_count INTEGER, source_detail TEXT,
                                    analyst_email TEXT);
        CREATE TABLE relay_outages (id TEXT, relay TEXT, started_at TEXT, minutes INTEGER);
    """)
    conn.executemany("INSERT INTO track_summary VALUES (?,?,?,?,?,?)", [
        ("t1", "2026-11-17T00:00:00Z", "north", 12, "radar seven", "ana.lyst@example.org"),
        ("t2", "2026-11-17T01:00:00Z", "south", 7, "relay feed", "bo.watch@example.org"),
        ("t3", "2026-11-17T02:00:00Z", "north", 40, "radar seven", "ana.lyst@example.org"),
    ])
    conn.executemany("INSERT INTO relay_outages VALUES (?,?,?,?)", [
        ("o1", "relay-north", "2026-11-16T08:00:00Z", 10),
        ("o1", "relay-north", "2026-11-16T09:00:00Z", 12),
        ("o2", "relay-south", "2026-11-16T10:00:00Z", 3),
    ])
    conn.commit()
    conn.close()
    return str(SOURCE)


def main() -> int:
    run = uuid.uuid4().hex[:8]
    out = {"run": run, "preparer": person("cap02i-e2eprep", "reviewer", run),
           "publisher": person("cap02i-e2epub", "reviewer", run), "source": source(),
           "key": "syn-track-export", "design": "cap02 synthetic track export"}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
