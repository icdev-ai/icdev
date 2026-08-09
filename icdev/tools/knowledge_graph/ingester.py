#!/usr/bin/env python3
# CUI // SP-CTI
"""Knowledge Graph document ingestion pipeline.

Reads documents (text, markdown) or DB table rows, extracts entities and
relationships via text_network, merges and deduplicates across chunks,
and stores the resulting graph in the ICDEV™ knowledge graph tables.

Usage:
    python tools/knowledge_graph/ingester.py --file /path/to/doc --project-id "sparkpilot" --json
    python tools/knowledge_graph/ingester.py --dir /path/to/dir --project-id "sparkpilot" --json
    python tools/knowledge_graph/ingester.py --source-table research_signals --project-id "sparkpilot" --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_db():
    conn = get_connection()
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id(prefix: str = "kg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create knowledge graph tables if they do not exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kg_graphs (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            entity_count INTEGER DEFAULT 0,
            edge_count INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            label TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            embedding BLOB,
            centrality REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS kg_edges (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            source_id TEXT NOT NULL REFERENCES kg_nodes(id),
            target_id TEXT NOT NULL REFERENCES kg_nodes(id),
            relationship TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------


def _chunk_text(text: str, chunk_size: int = 2000, overlap: int = 200) -> List[str]:
    """Split *text* into overlapping chunks.

    Attempts to break at paragraph boundaries (double newline) when possible,
    falling back to character-level splitting with overlap.
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    chunks: List[str] = []
    start = 0
    length = len(text)

    while start < length:
        end = min(start + chunk_size, length)

        # Try to break on a paragraph boundary
        if end < length:
            boundary = text.rfind("\n\n", start, end)
            if boundary > start:
                end = boundary + 2  # include the newline pair

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= length:
            break

        # Overlap: step back by *overlap* characters for context continuity
        start = max(end - overlap, start + 1)

    return chunks


# ---------------------------------------------------------------------------
# Entity / edge extraction (deterministic keyword-based)
# ---------------------------------------------------------------------------

# Deterministic entity extraction using keyword heuristics.
# This avoids depending on text_network.py which may not exist yet.
# When text_network is available, callers can swap in its extractor.

_ENTITY_KEYWORDS: Dict[str, List[str]] = {
    "organization": [
        "NIST",
        "CISA",
        "DoD",
        "NSA",
        "DISA",
        "FedRAMP",
        "OMB",
        "GAO",
        "DHS",
        "FBI",
        "CIA",
        "NRO",
        "NGA",
        "CYBERCOM",
        "DARPA",
        "IARPA",
        "ISO",
        "IEEE",
        "OWASP",
        "MITRE",
        "CMS",
        "FDA",
        "OCC",
        "FDIC",
        "SEC",
        "FINRA",
        "CFTC",
        "HHS",
        "ONC",
        "FTC",
        "DOT",
        "CBP",
    ],
    "framework": [
        "NIST 800-53",
        "NIST 800-171",
        "FedRAMP",
        "CMMC",
        "CJIS",
        "HIPAA",
        "HITRUST",
        "SOC 2",
        "PCI DSS",
        "ISO 27001",
        "MOSA",
        "ATLAS",
        "OWASP LLM",
        "NIST AI RMF",
        "ISO 42001",
        "FIPS 199",
        "FIPS 200",
        "CNSSI 1253",
        "NIST 800-207",
        "EU AI Act",
        "OSCAL",
        "STIX",
        "STRIDE",
    ],
    "system": [
        "Kubernetes",
        "Docker",
        "Terraform",
        "Ansible",
        "Helm",
        "GitLab",
        "GitHub",
        "Jenkins",
        "Splunk",
        "Prometheus",
        "Grafana",
        "ELK",
        "PostgreSQL",
        "SQLite",
        "Redis",
        "Istio",
        "Linkerd",
        "Kyverno",
        "OPA",
        "Bedrock",
    ],
    "concept": [
        "zero trust",
        "continuous ATO",
        "cATO",
        "DevSecOps",
        "supply chain",
        "SBOM",
        "STIG",
        "SSP",
        "POAM",
        "risk management",
        "threat model",
        "digital thread",
        "knowledge graph",
        "RAG",
        "fine-tuning",
        "LoRA",
    ],
}


def _extract_entities(text: str) -> List[Dict[str, Any]]:
    """Extract entities from text via keyword matching."""
    found: List[Dict[str, Any]] = []
    text_lower = text.lower()
    seen: set = set()

    for entity_type, keywords in _ENTITY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                key = (kw.lower(), entity_type)
                if key not in seen:
                    seen.add(key)
                    found.append(
                        {
                            "label": kw,
                            "entity_type": entity_type,
                            "properties": {},
                        }
                    )
    return found


def _extract_edges(entities: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    """Infer co-occurrence edges between entities found in the same text."""
    edges: List[Dict[str, Any]] = []
    text_lower = text.lower()

    for i, src in enumerate(entities):
        for tgt in entities[i + 1 :]:
            # Simple proximity heuristic: if both appear within 500 chars
            src_pos = text_lower.find(src["label"].lower())
            tgt_pos = text_lower.find(tgt["label"].lower())
            if src_pos >= 0 and tgt_pos >= 0:
                distance = abs(src_pos - tgt_pos)
                if distance < 500:
                    weight = max(0.1, 1.0 - distance / 500.0)
                    edges.append(
                        {
                            "source": src["label"],
                            "target": tgt["label"],
                            "relationship": "co_occurs_with",
                            "weight": round(weight, 3),
                        }
                    )
    return edges


# ---------------------------------------------------------------------------
# Merge / dedup helpers
# ---------------------------------------------------------------------------


def _merge_entities(entities_list: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Deduplicate entities across chunks by (label_lower, entity_type).

    Keeps the first occurrence of each unique entity.
    """
    seen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for chunk_entities in entities_list:
        for ent in chunk_entities:
            key = (ent["label"].lower(), ent["entity_type"])
            if key not in seen:
                seen[key] = ent
    return list(seen.values())


def _merge_edges(
    edges_list: List[List[Dict[str, Any]]],
    merged_entities: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Deduplicate edges by (source, target, relationship), summing weights.

    Only keeps edges whose source and target are in *merged_entities*.
    """
    valid_labels = {e["label"].lower() for e in merged_entities}
    combined: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for chunk_edges in edges_list:
        for edge in chunk_edges:
            src_lower = edge["source"].lower()
            tgt_lower = edge["target"].lower()
            if src_lower not in valid_labels or tgt_lower not in valid_labels:
                continue
            key = (src_lower, tgt_lower, edge["relationship"])
            if key in combined:
                combined[key]["weight"] += edge.get("weight", 1.0)
            else:
                combined[key] = dict(edge)

    return list(combined.values())


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _populate_kg_ontology(
    conn,
    graph_id: str,
    onto_id_to_entity_type: Dict[str, str],
) -> int:
    """Materialise ontology_subclass_closure rows into kg_ontology for this graph.

    Maps each node's ontology_id (a CURIE like "network:LoadBalancer") through
    the closure table and stores the resulting subject_type / object_type pairs
    using the actual entity_type strings from kg_nodes so that
    _load_ontology_relations() can match them during retrieval.

    Returns the number of rows inserted.
    """
    if not onto_id_to_entity_type:
        return 0

    try:
        closure_rows = conn.execute(
            "SELECT subclass, superclass, distance FROM ontology_subclass_closure"
        ).fetchall()
    except Exception:
        return 0

    # Lower-case reverse index: curie_lower → entity_type
    onto_lower_to_et: Dict[str, str] = {
        k.lower(): v for k, v in onto_id_to_entity_type.items()
    }

    # Delete stale rows for this graph
    try:
        conn.execute("DELETE FROM kg_ontology WHERE graph_id = %s", (graph_id,))
    except Exception:
        return 0

    rows_to_insert: List[Tuple] = []
    seen: set = set()
    now = _utcnow_iso()

    for row in closure_rows:
        sub_curie = row["subclass"] if hasattr(row, "__getitem__") else row[0]
        sup_curie = row["superclass"] if hasattr(row, "__getitem__") else row[1]
        dist = row["distance"] if hasattr(row, "__getitem__") else row[2]

        sub_lower = sub_curie.lower()
        if sub_lower not in onto_lower_to_et:
            continue  # this class isn't in our graph — skip

        subject_type = onto_lower_to_et[sub_lower]
        # Map superclass back to an entity_type if a node has it; else use normalised CURIE
        sup_lower = sup_curie.lower()
        object_type = onto_lower_to_et.get(sup_lower, sup_lower.replace(":", "_"))

        key = (subject_type, "rdfs:subClassOf", object_type)
        if key in seen:
            continue
        seen.add(key)

        rows_to_insert.append((
            _gen_id("ko"),
            graph_id,
            subject_type,
            "rdfs:subClassOf",
            object_type,
            max(int(dist or 1), 1),
            now,
        ))

    if rows_to_insert:
        conn.executemany(
            "INSERT INTO kg_ontology "
            "(id, graph_id, subject_type, predicate, object_type, path_distance, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            rows_to_insert,
        )

    return len(rows_to_insert)


def _persist_graph(
    conn,
    graph_id: str,
    project_id: str,
    graph_name: str,
    entities: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    source_info: str = "",
) -> None:
    """Write a graph, its nodes, and edges to the database."""
    now = _utcnow_iso()

    conn.execute(
        """INSERT OR REPLACE INTO kg_graphs
           (id, project_id, name, description, entity_count, edge_count,
            metadata, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            graph_id,
            project_id,
            graph_name,
            f"Ingested from {source_info}",
            len(entities),
            len(edges),
            json.dumps({"source": source_info}),
            now,
            now,
        ),
    )

    # Pre-load ontology class lookup once per graph (best-effort enrichment)
    _ont_cache: Dict[str, str] = {}
    try:
        rows = conn.execute(
            "SELECT id, LOWER(label) as lbl, LOWER(REPLACE(id, ':', '_')) as id_key "
            "FROM ontology_classes"
        ).fetchall()
        for r in rows:
            _ont_cache[r["lbl"]] = r["id"]
            _ont_cache[r["id_key"]] = r["id"]
    except Exception:
        pass  # ontology tables not yet built

    def _resolve_ontology_id(entity_type: str) -> str:
        if not _ont_cache:
            return None
        key = entity_type.lower().replace("-", "_").replace(" ", "_")
        return _ont_cache.get(key) or _ont_cache.get(entity_type.lower())

    # Build label → node_id map; collect ontology_id → entity_type for kg_ontology
    label_to_id: Dict[str, str] = {}
    onto_id_to_et: Dict[str, str] = {}
    for ent in entities:
        node_id = _gen_id("kn")
        label_to_id[ent["label"].lower()] = node_id
        entity_type = ent.get("entity_type", "")
        ontology_id = _resolve_ontology_id(entity_type)
        if ontology_id and entity_type:
            onto_id_to_et[ontology_id] = entity_type
        conn.execute(
            """INSERT INTO kg_nodes
               (id, graph_id, label, entity_type, ontology_id, properties, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                node_id,
                graph_id,
                ent["label"],
                entity_type,
                ontology_id,
                json.dumps(ent.get("properties", {})),
                now,
            ),
        )

    for edge in edges:
        src_id = label_to_id.get(edge["source"].lower())
        tgt_id = label_to_id.get(edge["target"].lower())
        if not src_id or not tgt_id:
            continue
        conn.execute(
            """INSERT INTO kg_edges
               (id, graph_id, source_id, target_id, relationship, weight,
                properties, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                _gen_id("ke"),
                graph_id,
                src_id,
                tgt_id,
                edge["relationship"],
                edge.get("weight", 1.0),
                json.dumps(edge.get("properties", {})),
                now,
            ),
        )

    # Materialise ontology relations for this graph into kg_ontology
    _populate_kg_ontology(conn, graph_id, onto_id_to_et)

    conn.commit()


def _entity_type_counts(entities: List[Dict[str, Any]]) -> Dict[str, int]:
    """Return entity counts grouped by entity_type."""
    counts: Dict[str, int] = {}
    for ent in entities:
        t = ent["entity_type"]
        counts[t] = counts.get(t, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ingest_file(
    file_path: str,
    project_id: str,
    graph_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Read a file, chunk if >2000 chars, extract entities, merge, persist."""
    fp = Path(file_path)
    if not fp.exists():
        return {"status": "error", "error": f"File not found: {file_path}"}

    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    if not text.strip():
        return {"status": "error", "error": "File is empty"}

    name = graph_name or fp.stem
    graph_id = _gen_id("kg")

    chunks = _chunk_text(text)
    all_entities: List[List[Dict[str, Any]]] = []
    all_edges: List[List[Dict[str, Any]]] = []

    for chunk in chunks:
        ents = _extract_entities(chunk)
        edgs = _extract_edges(ents, chunk)
        all_entities.append(ents)
        all_edges.append(edgs)

    merged_ents = _merge_entities(all_entities)
    merged_edgs = _merge_edges(all_edges, merged_ents)

    conn = _get_db()
    try:
        _ensure_tables(conn)
        _persist_graph(
            conn,
            graph_id,
            project_id,
            name,
            merged_ents,
            merged_edgs,
            source_info=str(fp),
        )
    finally:
        conn.close()

    return {
        "status": "ok",
        "graph_id": graph_id,
        "files_processed": 1,
        "total_entities": len(merged_ents),
        "total_edges": len(merged_edgs),
        "entity_types": _entity_type_counts(merged_ents),
    }


def ingest_directory(
    dir_path: str,
    project_id: str,
    graph_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Iterate .md and .txt files in *dir_path*, ingest each into a graph."""
    dp = Path(dir_path)
    if not dp.is_dir():
        return {"status": "error", "error": f"Not a directory: {dir_path}"}

    files = sorted(p for p in dp.iterdir() if p.is_file() and p.suffix.lower() in (".md", ".txt"))

    if not files:
        return {"status": "error", "error": "No .md or .txt files found"}

    total_entities = 0
    total_edges = 0
    all_type_counts: Dict[str, int] = {}
    graph_ids: List[str] = []

    for fp in files:
        result = ingest_file(str(fp), project_id, graph_name=fp.stem)
        if result.get("status") == "ok":
            total_entities += result["total_entities"]
            total_edges += result["total_edges"]
            graph_ids.append(result["graph_id"])
            for t, c in result.get("entity_types", {}).items():
                all_type_counts[t] = all_type_counts.get(t, 0) + c

    return {
        "status": "ok",
        "graph_id": graph_ids[0] if len(graph_ids) == 1 else graph_ids,
        "files_processed": len(files),
        "total_entities": total_entities,
        "total_edges": total_edges,
        "entity_types": all_type_counts,
    }


def ingest_from_table(
    table_name: str,
    project_id: str,
    content_column: str = "content",
    limit: int = 100,
) -> Dict[str, Any]:
    """Read rows from an ICDEV™ DB table and extract entities from content."""
    conn = _get_db()
    try:
        _ensure_tables(conn)

        # Validate table exists
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if table_name not in tables:
            return {"status": "error", "error": f"Table '{table_name}' not found"}

        # Validate column exists
        cursor = conn.execute(f"PRAGMA table_info({table_name})")  # noqa: S608
        columns = [r[1] if isinstance(r, tuple) else r["name"] for r in cursor.fetchall()]
        if content_column not in columns:
            # Fallback: try common content columns
            fallbacks = ["description", "title", "body", "text", "summary"]
            content_column_found = None
            for fb in fallbacks:
                if fb in columns:
                    content_column_found = fb
                    break
            if not content_column_found:
                return {
                    "status": "error",
                    "error": (f"Column '{content_column}' not found in '{table_name}'. Available: {columns}"),
                }
            content_column = content_column_found

        # Determine a title column if available
        title_col = None
        for candidate in ("title", "name", "label"):
            if candidate in columns and candidate != content_column:
                title_col = candidate
                break

        # Build safe query
        select_cols = [content_column]
        if title_col:
            select_cols.append(title_col)
        col_list = ", ".join(select_cols)

        rows = conn.execute(
            f"SELECT {col_list} FROM {table_name} LIMIT %s",  # noqa: S608  # nosec B608 -- table/column names are internal constants, not user input
            (limit,),
        ).fetchall()

        if not rows:
            return {"status": "error", "error": f"No rows in '{table_name}'"}

        graph_id = _gen_id("kg")
        graph_name = f"table_{table_name}"

        all_entities: List[List[Dict[str, Any]]] = []
        all_edges: List[List[Dict[str, Any]]] = []

        for row in rows:
            content = row[0] or ""
            if title_col:
                title_text = row[1] or ""
                content = f"{title_text}. {content}" if title_text else content
            if not content.strip():
                continue

            chunks = _chunk_text(content)
            for chunk in chunks:
                ents = _extract_entities(chunk)
                edgs = _extract_edges(ents, chunk)
                all_entities.append(ents)
                all_edges.append(edgs)

        merged_ents = _merge_entities(all_entities)
        merged_edgs = _merge_edges(all_edges, merged_ents)

        _persist_graph(
            conn,
            graph_id,
            project_id,
            graph_name,
            merged_ents,
            merged_edgs,
            source_info=f"table:{table_name}",
        )
    finally:
        conn.close()

    return {
        "status": "ok",
        "graph_id": graph_id,
        "files_processed": len(rows),
        "total_entities": len(merged_ents),
        "total_edges": len(merged_edgs),
        "entity_types": _entity_type_counts(merged_ents),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge Graph document ingester")
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Path to a single file to ingest (.md, .txt)",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Path to directory of files to ingest",
    )
    parser.add_argument(
        "--source-table",
        type=str,
        default=None,
        help="Name of ICDEV™ DB table to ingest from",
    )
    parser.add_argument(
        "--content-column",
        type=str,
        default="content",
        help="Column name containing text content (default: content)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max rows to read from source table (default: 100)",
    )
    parser.add_argument(
        "--project-id",
        type=str,
        required=True,
        help="Project identifier",
    )
    parser.add_argument(
        "--graph-name",
        type=str,
        default=None,
        help="Optional graph name (defaults to filename or table name)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()

    # Validate exactly one source is specified
    sources = [args.file, args.dir, args.source_table]
    specified = sum(1 for s in sources if s is not None)
    if specified != 1:
        parser.error("Specify exactly one of --file, --dir, or --source-table")

    result: Dict[str, Any]

    if args.file:
        result = ingest_file(args.file, args.project_id, args.graph_name)
    elif args.dir:
        result = ingest_directory(args.dir, args.project_id, args.graph_name)
    else:
        result = ingest_from_table(
            args.source_table,
            args.project_id,
            content_column=args.content_column,
            limit=args.limit,
        )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = result.get("status", "unknown")
        if status == "ok":
            print(f"Status:          {status}")
            print(f"Graph ID:        {result.get('graph_id', 'N/A')}")
            print(f"Files processed: {result.get('files_processed', 0)}")
            print(f"Total entities:  {result.get('total_entities', 0)}")
            print(f"Total edges:     {result.get('total_edges', 0)}")
            types = result.get("entity_types", {})
            if types:
                print("Entity types:")
                for t, c in sorted(types.items()):
                    print(f"  {t}: {c}")
        else:
            print(f"Error: {result.get('error', 'Unknown error')}")
            sys.exit(1)


if __name__ == "__main__":
    main()
