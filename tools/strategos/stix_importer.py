#!/usr/bin/env python3
# CUI // SP-CTI
"""STIX 2.1 / CERT-UA cyber threat importer for Strategos.

Parses STIX 2.1 bundles and writes:
  - sg_conflict_events  (event_type='cyber_op') — one row per processable object
  - canvas_kg_nodes     (node_type = CyberOperation | ThreatActor | Malware | …)
  - canvas_kg_edges     (STIX relationship objects → KG edges)

MITRE ATT&CK technique IDs are extracted from three sources:
  1. attack-pattern objects via external_references (source_name = 'mitre-attack')
  2. indicator.pattern fields (regex for T-code tokens)
  3. Relationship chains: object → uses/indicates → attack-pattern

Deduplication uses the existing UNIQUE INDEX idx_sg_ce_source_ext on
(source, external_id).  Re-importing the same bundle is safe.

Usage
-----
  python tools/strategos/stix_importer.py --file bundle.json
  python tools/strategos/stix_importer.py --file bundle.json --source cert-ua
  python tools/strategos/stix_importer.py --file bundle.json --dry-run
  python tools/strategos/stix_importer.py --file bundle.json --json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# STIX object types mapped to sg_conflict_events rows
_PROCESSABLE_TYPES = frozenset(
    {
        "campaign",
        "intrusion-set",
        "threat-actor",
        "malware",
        "tool",
        "indicator",
    }
)

# STIX type → KG node_type label
_NODE_TYPE_MAP: dict[str, str] = {
    "campaign":       "CyberOperation",
    "intrusion-set":  "CyberOperation",
    "threat-actor":   "ThreatActor",
    "malware":        "Malware",
    "tool":           "Tool",
    "indicator":      "Indicator",
    "attack-pattern": "Technique",
    "identity":       "Identity",
    "course-of-action": "Mitigation",
}

# Regex for MITRE T-codes (T1234 or T1234.001)
_TECHNIQUE_RE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b")

# Canvas key for KG nodes written by this importer
_KG_CANVAS = "sg"

# ---------------------------------------------------------------------------
# KG table DDL (mirrors canvas/kg_builder.py)
# ---------------------------------------------------------------------------

_CREATE_KG_NODES = """\
CREATE TABLE IF NOT EXISTS canvas_kg_nodes (
    id            TEXT PRIMARY KEY,
    canvas        TEXT NOT NULL,
    design_id     TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    node_type     TEXT,
    label         TEXT,
    metadata_json TEXT,
    updated_at    TEXT
)"""

_CREATE_KG_EDGES = """\
CREATE TABLE IF NOT EXISTS canvas_kg_edges (
    id            TEXT PRIMARY KEY,
    canvas        TEXT NOT NULL,
    design_id     TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    edge_type     TEXT,
    metadata_json TEXT,
    updated_at    TEXT
)"""


def _ensure_kg_tables(conn) -> None:
    """Create KG tables if absent via StorageConnection (handles PG and SQLite)."""
    conn.execute(_CREATE_KG_NODES)
    conn.execute(_CREATE_KG_EDGES)


# ---------------------------------------------------------------------------
# Bundle loading
# ---------------------------------------------------------------------------


def _load_bundle(file: Path) -> dict:
    """Load a STIX 2.1 JSON bundle from a local file.

    Accepts:
      - A STIX bundle object  {"type": "bundle", "objects": [...]}
      - A raw JSON array       [...]
      - A single STIX object   {"type": "...", ...}
    """
    with open(file, encoding="utf-8") as fh:
        data = json.load(fh)

    if isinstance(data, list):
        return {"type": "bundle", "id": f"bundle--{uuid.uuid4()}", "objects": data}
    if isinstance(data, dict) and data.get("type") == "bundle":
        return data
    return {"type": "bundle", "id": f"bundle--{uuid.uuid4()}", "objects": [data]}


# ---------------------------------------------------------------------------
# STIX bundle parsing
# ---------------------------------------------------------------------------


def _extract_technique_id(ext_refs: list[dict]) -> str | None:
    """Return first MITRE ATT&CK T-code from an external_references list."""
    for ref in ext_refs:
        src = ref.get("source_name", "")
        if src in ("mitre-attack", "mitre-mobile-attack", "mitre-ics-attack"):
            eid = ref.get("external_id", "")
            if re.match(r"T\d{4}", eid):
                return eid
    return None


def _iso_ts(val: str | None) -> str | None:
    if not val:
        return None
    # Already ISO-8601 with T separator
    if "T" in val:
        return val
    return val + "T00:00:00Z"


def parse_bundle(bundle: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Parse a STIX 2.1 bundle into rows ready for DB insertion.

    Returns
    -------
    sg_rows   : list[dict]  — rows for sg_conflict_events
    kg_nodes  : list[dict]  — rows for canvas_kg_nodes
    kg_edges  : list[dict]  — rows for canvas_kg_edges
    """
    objects: list[dict] = bundle.get("objects") or []
    bundle_id: str = bundle.get("id") or f"bundle--{uuid.uuid4()}"
    now_iso = datetime.now(timezone.utc).isoformat()

    # -- Build object lookup ---------------------------------------------------
    by_id: dict[str, dict] = {}
    for obj in objects:
        oid = obj.get("id")
        if oid:
            by_id[oid] = obj

    # -- Build attack-pattern → T-code map ------------------------------------
    technique_map: dict[str, str] = {}  # stix_id → T-code
    for obj in objects:
        if obj.get("type") == "attack-pattern":
            tid = _extract_technique_id(obj.get("external_references") or [])
            if tid:
                technique_map[obj["id"]] = tid

    # -- Build relationships (source → [(target, rel_type)]) ------------------
    relations_from: dict[str, list[tuple[str, str]]] = {}
    for obj in objects:
        if obj.get("type") == "relationship":
            src = obj.get("source_ref", "")
            tgt = obj.get("target_ref", "")
            rtype = obj.get("relationship_type", "related-to")
            if src and tgt:
                relations_from.setdefault(src, []).append((tgt, rtype))

    # -- Attributed-to map (campaign/intrusion-set → threat-actor name) -------
    actor_name_for: dict[str, str] = {}
    for obj in objects:
        if (
            obj.get("type") == "relationship"
            and obj.get("relationship_type") == "attributed-to"
        ):
            src = obj.get("source_ref", "")
            tgt = obj.get("target_ref", "")
            target_obj = by_id.get(tgt, {})
            if target_obj.get("type") == "threat-actor":
                actor_name_for[src] = target_obj.get("name", "")

    # -- Collect technique IDs via relationship chains -------------------------
    def _collect_techniques(obj_id: str, depth: int = 0) -> set[str]:
        if depth > 4:
            return set()
        tids: set[str] = set()
        for target_id, _ in relations_from.get(obj_id, []):
            if target_id in technique_map:
                tids.add(technique_map[target_id])
            elif target_id in by_id:
                tids |= _collect_techniques(target_id, depth + 1)
        return tids

    # -- Process each relevant object -----------------------------------------
    sg_rows: list[dict] = []
    kg_nodes: list[dict] = []
    kg_edges: list[dict] = []

    for obj in objects:
        obj_type = obj.get("type", "")
        if obj_type not in _PROCESSABLE_TYPES:
            continue

        obj_id: str = obj.get("id") or f"{obj_type}--{uuid.uuid4()}"
        name: str = obj.get("name") or obj_id
        description: str = (obj.get("description") or name)[:2000]
        created: str | None = obj.get("created") or obj.get("first_seen")
        modified: str | None = obj.get("modified")
        confidence: int | None = obj.get("confidence")

        # Technique IDs via relationship graph
        tech_set: set[str] = _collect_techniques(obj_id)

        # Indicators: also scan the pattern field for inline T-codes
        if obj_type == "indicator":
            pattern = obj.get("pattern") or ""
            tech_set.update(_TECHNIQUE_RE.findall(pattern))
            for phase in obj.get("kill_chain_phases") or []:
                tech_set.update(_TECHNIQUE_RE.findall(phase.get("phase_name", "")))

        technique_ids: str | None = (
            ",".join(sorted(tech_set)) if tech_set else None
        )

        # Threat actor attribution
        threat_actor: str | None = actor_name_for.get(obj_id)
        if obj_type == "threat-actor":
            threat_actor = name

        # Malware / tool family names
        if obj_type in ("malware", "tool"):
            malware_family: str | None = name
        else:
            families = [
                by_id[tgt].get("name", "")
                for tgt, _ in relations_from.get(obj_id, [])
                if by_id.get(tgt, {}).get("type") in ("malware", "tool")
            ]
            malware_family = ",".join(f for f in families if f) or None

        # actor1 maps to the primary identity for narrative compatibility
        actor1: str | None = None
        if obj_type in ("campaign", "intrusion-set", "threat-actor"):
            actor1 = threat_actor or name

        sg_rows.append(
            {
                "id":             f"stix_{obj_id.replace('--', '_')}",
                "external_id":    obj_id,
                "event_ts":       _iso_ts(created) or now_iso,
                "description":    description,
                "actor1":         actor1,
                "technique_ids":  technique_ids,
                "threat_actor":   threat_actor,
                "malware_family": malware_family,
                "confidence":     confidence,
                "metadata_json":  json.dumps(
                    {
                        "stix_type":  obj_type,
                        "stix_id":    obj_id,
                        "name":       name,
                        "modified":   modified,
                        "labels":     obj.get("labels") or [],
                        "aliases":    obj.get("aliases") or [],
                        "bundle_id":  bundle_id,
                    },
                    ensure_ascii=False,
                ),
            }
        )

        kg_nodes.append(
            {
                "id":           str(uuid.uuid4()),
                "canvas":       _KG_CANVAS,
                "design_id":    bundle_id,
                "node_id":      obj_id,
                "node_type":    _NODE_TYPE_MAP.get(obj_type, "StixObject"),
                "label":        name,
                "metadata_json": json.dumps(
                    {
                        "stix_type":     obj_type,
                        "technique_ids": technique_ids,
                        "confidence":    confidence,
                        "created":       created,
                    }
                ),
                "updated_at":   now_iso,
            }
        )

    # -- KG edges from STIX relationship objects ------------------------------
    for obj in objects:
        if obj.get("type") != "relationship":
            continue
        src = obj.get("source_ref", "")
        tgt = obj.get("target_ref", "")
        if not (src and tgt):
            continue
        kg_edges.append(
            {
                "id":           str(uuid.uuid4()),
                "canvas":       _KG_CANVAS,
                "design_id":    bundle_id,
                "source_id":    src,
                "target_id":    tgt,
                "edge_type":    obj.get("relationship_type", "related-to"),
                "metadata_json": json.dumps({"stix_id": obj.get("id", "")}),
                "updated_at":   now_iso,
            }
        )

    return sg_rows, kg_nodes, kg_edges


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------


def _upsert_conflict_rows(conn, rows: list[dict], source: str) -> tuple[int, int]:
    """Insert cyber_op rows; skip duplicates via INSERT OR IGNORE (→ ON CONFLICT DO NOTHING).

    Using INSERT OR IGNORE avoids aborting the PostgreSQL transaction on
    UNIQUE violations (source, external_id), unlike bare INSERT + exception catch.
    """
    inserted = skipped = 0
    for row in rows:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO sg_conflict_events
                (id, event_type, source, external_id, event_ts, description,
                 actor1, technique_ids, threat_actor, malware_family,
                 confidence, metadata_json)
            VALUES
                (%s, 'cyber_op', %s, %s, %s, %s,
                 %s, %s, %s, %s,
                 %s, %s)
            """,
            (
                row["id"],
                source,
                row["external_id"],
                row["event_ts"],
                row["description"],
                row["actor1"],
                row["technique_ids"],
                row["threat_actor"],
                row["malware_family"],
                row["confidence"],
                row["metadata_json"],
            ),
        )
        # rowcount == 1 means inserted; 0 means skipped (duplicate)
        rc = getattr(cur, "rowcount", None)
        if rc == 0:
            skipped += 1
        else:
            inserted += 1
    return inserted, skipped


def _write_kg(
    conn,
    bundle_id: str,
    nodes: list[dict],
    edges: list[dict],
) -> tuple[int, int]:
    """Refresh KG data for this bundle: delete stale rows then re-insert."""
    _ensure_kg_tables(conn)

    conn.execute(
        "DELETE FROM canvas_kg_nodes WHERE canvas = %s AND design_id = %s",
        (_KG_CANVAS, bundle_id),
    )
    conn.execute(
        "DELETE FROM canvas_kg_edges WHERE canvas = %s AND design_id = %s",
        (_KG_CANVAS, bundle_id),
    )

    n_ok = e_ok = 0
    for node in nodes:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO canvas_kg_nodes "
                "(id, canvas, design_id, node_id, node_type, label, metadata_json, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    node["id"],
                    node["canvas"],
                    node["design_id"],
                    node["node_id"],
                    node["node_type"],
                    node["label"],
                    node["metadata_json"],
                    node["updated_at"],
                ),
            )
            n_ok += 1
        except Exception as exc:
            logger.debug("KG node insert failed: %s", exc)

    for edge in edges:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO canvas_kg_edges "
                "(id, canvas, design_id, source_id, target_id, edge_type, metadata_json, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    edge["id"],
                    edge["canvas"],
                    edge["design_id"],
                    edge["source_id"],
                    edge["target_id"],
                    edge["edge_type"],
                    edge["metadata_json"],
                    edge["updated_at"],
                ),
            )
            e_ok += 1
        except Exception as exc:
            logger.debug("KG edge insert failed: %s", exc)

    return n_ok, e_ok


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(
    *,
    file: Path,
    source: str = "stix",
    dry_run: bool = False,
    as_json: bool = False,
) -> dict:
    """Parse a STIX 2.1 bundle file and import into Strategos DB.

    Parameters
    ----------
    file    : local path to the STIX JSON bundle
    source  : source label stored in sg_conflict_events.source
              (use 'cert-ua' for CERT-UA advisories)
    dry_run : parse only — no DB writes
    as_json : print JSON summary to stdout

    Returns
    -------
    dict with keys: dry_run, objects_found, by_type, inserted,
                    skipped_duplicates, kg_nodes_written, kg_edges_written
    """
    try:
        bundle = _load_bundle(file)
    except Exception as exc:
        logger.error("Failed to load bundle: %s", exc)
        result: dict[str, Any] = {
            "error": str(exc),
            "inserted": 0,
            "skipped_duplicates": 0,
        }
        if as_json:
            print(json.dumps(result, indent=2))
        return result

    sg_rows, kg_nodes, kg_edges = parse_bundle(bundle)
    bundle_id = bundle.get("id", "")

    # Count by STIX type for the summary
    type_counts: dict[str, int] = {}
    for row in sg_rows:
        meta = json.loads(row.get("metadata_json", "{}"))
        t = meta.get("stix_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    if dry_run:
        summary: dict[str, Any] = {
            "dry_run":       True,
            "objects_found": len(sg_rows),
            "by_type":       type_counts,
            "kg_nodes":      len(kg_nodes),
            "kg_edges":      len(kg_edges),
        }
        if as_json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"[DRY RUN] {len(sg_rows)} STIX objects: {type_counts}")
            print(f"  KG: {len(kg_nodes)} nodes, {len(kg_edges)} edges")
        return summary

    conn = get_connection()
    try:
        inserted, skipped = _upsert_conflict_rows(conn, sg_rows, source)
        kg_n, kg_e = _write_kg(conn, bundle_id, kg_nodes, kg_edges)
        conn.commit()
    finally:
        conn.close()

    summary = {
        "dry_run":             False,
        "source":              source,
        "bundle_id":           bundle_id,
        "objects_found":       len(sg_rows),
        "by_type":             type_counts,
        "inserted":            inserted,
        "skipped_duplicates":  skipped,
        "kg_nodes_written":    kg_n,
        "kg_edges_written":    kg_e,
    }

    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"STIX import ({source}): {inserted} inserted, "
            f"{skipped} duplicate(s) skipped"
        )
        print(f"  Types: {type_counts}")
        print(f"  KG: {kg_n} nodes, {kg_e} edges")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import STIX 2.1 cyber threat bundles into sg_conflict_events"
    )
    parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="Local path to a STIX 2.1 JSON bundle",
    )
    parser.add_argument(
        "--source",
        default="stix",
        help="Source label for sg_conflict_events.source "
             "(e.g. 'cert-ua', 'misp', 'stix'; default: stix)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and count objects without writing to DB",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="JSON output",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    result = run(
        file=args.file,
        source=args.source,
        dry_run=args.dry_run,
        as_json=args.as_json,
    )
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
