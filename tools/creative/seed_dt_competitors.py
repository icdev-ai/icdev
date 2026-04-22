"""Seed confirmed digital-twin network-intelligence competitors into creative_competitors."""
import hashlib
import json
import sys
from datetime import datetime, timezone

from tools.db.storage import get_connection

_COMPETITORS = [
    {
        "name": "Forward Networks",
        "domain": "forwardnetworks.com",
        "source": "manual",
        "source_url": "https://forwardnetworks.com",
        "category": "digital twin platform",
        "features": [
            "Mathematical network model for intent verification",
            "Network query language (NQE) for policy compliance",
            "Automated network change simulation and diff",
        ],
    },
    {
        "name": "IP Fabric",
        "domain": "ipfabric.io",
        "source": "manual",
        "source_url": "https://ipfabric.io",
        "category": "digital twin platform",
        "features": [
            "Automated network topology discovery and modeling",
            "Path lookup and end-to-end connectivity analysis",
            "Historical snapshots for change management and auditing",
        ],
    },
    {
        "name": "NetBrain",
        "domain": "netbraintech.com",
        "source": "manual",
        "source_url": "https://netbraintech.com",
        "category": "digital twin platform",
        "features": [
            "Dynamic network maps auto-generated from live topology",
            "Runbook automation for guided troubleshooting workflows",
            "Digital twin simulation for pre-change impact analysis",
        ],
    },
    {
        "name": "SuzieQ",
        "domain": "github.com/netenglabs/suzieq",
        "source": "manual",
        "source_url": "https://github.com/netenglabs/suzieq",
        "category": "digital twin platform",
        "features": [
            "Open-source multi-vendor network observability framework",
            "Structured data collection via SSH/REST across vendors",
            "SQL-like query engine for network state analysis",
        ],
    },
]


def _stable_id(name: str, source: str) -> str:
    digest = hashlib.sha256(f"{name}|{source}".encode("utf-8")).hexdigest()
    return f"comp-{digest[:12]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed(db_path=None) -> dict:
    conn = get_connection(db_path=db_path)
    now = _now()
    inserted = 0
    skipped = 0

    for c in _COMPETITORS:
        cid = _stable_id(c["name"], c["source"])
        features_json = json.dumps(c["features"])
        metadata_json = json.dumps({"category": c["category"]})

        existing = conn.execute(
            "SELECT id FROM creative_competitors WHERE id=?", (cid,)
        ).fetchone()

        if existing:
            skipped += 1
            continue

        conn.execute(
            """
            INSERT OR IGNORE INTO creative_competitors
                (id, name, domain, source, source_url, features, status,
                 metadata, discovered_at, confirmed_at, confirmed_by, classification)
            VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, 'seed_dt_competitors', 'CUI')
            """,
            (cid, c["name"], c["domain"], c["source"], c["source_url"],
             features_json, metadata_json, now, now),
        )
        inserted += 1

    conn.commit()
    conn.close()
    return {"inserted": inserted, "skipped": skipped, "total": len(_COMPETITORS)}


if __name__ == "__main__":
    result = seed()
    print(json.dumps(result, indent=2))
    if result["inserted"] + result["skipped"] < result["total"]:
        sys.exit(1)
