#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Knowledge Graph — Entity/Relationship extraction from text via regex patterns.

Extracts entities (organizations, systems, controls, requirements, components,
persons, documents, standards) and relationships from free text using compiled
regex patterns.  Air-gap safe — no LLM needed for extraction (D-KARL-1).

Results are stored in ``kg_graphs``, ``kg_nodes``, and ``kg_edges`` tables in
``data/icdev.db``.

Usage:
    python tools/knowledge_graph/text_network.py --text "input text" --project-id "sparkpilot" --json
    python tools/knowledge_graph/text_network.py --file /path/to/doc.txt --project-id "sparkpilot" --json
    python tools/knowledge_graph/text_network.py --graph-id "kg-xxxx" --json
    python tools/knowledge_graph/text_network.py --search "NIST" --project-id "sparkpilot" --json
"""

import argparse
import hashlib
import json
import os
import re
import sys
import textwrap
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

ICDEV_DB = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    from tools.audit.audit_logger import log_event as audit_log_event

    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1


# =========================================================================
# CONSTANTS
# =========================================================================
ENTITY_TYPES = (
    "organization",
    "system",
    "control",
    "requirement",
    "component",
    "person",
    "document",
    "standard",
    # Financial entity types (FathomDesk KG extension)
    "ticker",
    "sector",
    "executive",
    "supplier",
    "commodity",
    "etf",
    "macro_indicator",
)

RELATIONSHIP_TYPES = (
    "DEPLOYED_ON",
    "MEMBER_OF",
    "OPERATES",
    "RUNS",
    "SATISFIES",
    "PROTECTS",
    "REFERENCES",
    "IMPLEMENTS",
    "DEPENDS_ON",
    # Financial relationship types (FathomDesk KG extension)
    "SUPPLIES_TO",
    "CUSTOMER_OF",
    "COMPETES_WITH",
    "MEMBER_OF_SECTOR",
    "EXPOSED_TO_COMMODITY",
    "LED_BY",
    "TRACKS_INDEX",
    "CORRELATED_WITH",
    "MACRO_SENSITIVE_TO",
    # Geographic + cascade analysis types
    "geography",
    "industry",
    # Cascade analysis relationship types
    "REQUIRES_RESOURCE",
    "CONSTRAINED_BY",
    "ENABLES",
    "DISPLACES",
)

# Verb phrase -> relationship type mapping
VERB_MAP = {
    "implements": "IMPLEMENTS",
    "implement": "IMPLEMENTS",
    "implementing": "IMPLEMENTS",
    "satisfies": "SATISFIES",
    "satisfy": "SATISFIES",
    "satisfying": "SATISFIES",
    "protects": "PROTECTS",
    "protect": "PROTECTS",
    "protecting": "PROTECTS",
    "depends on": "DEPENDS_ON",
    "depend on": "DEPENDS_ON",
    "depending on": "DEPENDS_ON",
    "requires": "DEPENDS_ON",
    "references": "REFERENCES",
    "reference": "REFERENCES",
    "referencing": "REFERENCES",
    "runs": "RUNS",
    "runs on": "RUNS",
    "running": "RUNS",
    "operates": "OPERATES",
    "operating": "OPERATES",
    "deployed on": "DEPLOYED_ON",
    "deploys to": "DEPLOYED_ON",
    "member of": "MEMBER_OF",
    "belongs to": "MEMBER_OF",
    # Financial relationship verbs (FathomDesk KG extension)
    "supplies": "SUPPLIES_TO",
    "supplies to": "SUPPLIES_TO",
    "supplier of": "SUPPLIES_TO",
    "customer of": "CUSTOMER_OF",
    "buys from": "CUSTOMER_OF",
    "competes with": "COMPETES_WITH",
    "competitor of": "COMPETES_WITH",
    "in sector": "MEMBER_OF_SECTOR",
    "sector of": "MEMBER_OF_SECTOR",
    "exposed to": "EXPOSED_TO_COMMODITY",
    "sensitive to": "MACRO_SENSITIVE_TO",
    "led by": "LED_BY",
    "ceo of": "LED_BY",
    "tracks": "TRACKS_INDEX",
    "correlated with": "CORRELATED_WITH",
    # Cascade verbs
    "needs": "REQUIRES_RESOURCE",
    "consumes": "REQUIRES_RESOURCE",
    "constrained by": "CONSTRAINED_BY",
    "limited by": "CONSTRAINED_BY",
    "bottlenecked by": "CONSTRAINED_BY",
    "enables": "ENABLES",
    "drives demand for": "ENABLES",
    "creates demand for": "ENABLES",
    "displaces": "DISPLACES",
    "crowds out": "DISPLACES",
    "reduces demand for": "DISPLACES",
}

# =========================================================================
# ENTITY EXTRACTION PATTERNS (compiled for performance)
# =========================================================================
# NIST-style control IDs: AC-2, SC-13, AU-2(1), SI-4.a
_RE_CONTROL = re.compile(r"\b([A-Z]{2,3}-\d{1,2}(?:\(\d+\))?(?:\.[a-z])?)\b")

# Requirement patterns: REQ-001, SHALL/MUST statements
_RE_REQUIREMENT_ID = re.compile(r"\b(REQ-\d{2,5})\b")
_RE_SHALL_MUST = re.compile(
    r"(?:system|application|platform|service)\s+"
    r"(SHALL|MUST)\s+(.{10,80}?)(?:\.|;|$)",
    re.IGNORECASE,
)

# Standard bodies / standards
_RE_STANDARD = re.compile(
    r"\b(NIST\s+(?:SP\s+)?(?:800-\d{2,3}(?:\s+Rev\s*\d)?|AI\s+\d{3}(?:-\d)?|RMF)"
    r"|ISO(?:/IEC)?\s+\d{4,5}(?::\d{4})?"
    r"|FIPS\s+\d{2,3}(?:-\d)?"
    r"|CISA\s+S[bB]D"
    r"|OWASP\s+(?:LLM|Agentic|ASI|Top\s*10)"
    r"|FedRAMP(?:\s+\w+)?"
    r"|CMMC(?:\s+Level\s*\d)?"
    r"|CJIS(?:\s+Security)?"
    r"|HIPAA(?:\s+Security)?"
    r"|SOC\s*2"
    r"|PCI\s+DSS"
    r"|MITRE\s+ATLAS)\b",
    re.IGNORECASE,
)

# Document artifact types
_RE_DOCUMENT = re.compile(r"\b(SSP|POAM|POA&M|SBOM|STIG|CONOPS|CDD|SOW|RTM|ICD|TSP|OSCAL|ATO)\b")

# Organizations: capitalized multi-word near keywords
_RE_ORG_KEYWORD = re.compile(
    r"\b((?:Department|Agency|Office|Bureau|Division|Command|Directorate)"
    r"\s+(?:of\s+)?(?:[A-Z][a-z]+\s*){1,4})\b"
)
_RE_ORG_ABBREV = re.compile(
    r"\b(DoD|DHS|DISA|NSA|CISA|NASA|FAA|FBI|CIA|NGA|NRO|USCYBERCOM"
    r"|NIST|OMB|GSA|OPM|VA|HHS|CMS|ONC|FTC|SEC|FINRA|CFTC)\b"
)

# Systems: one to four capitalized tokens (proper nouns) immediately preceding
# a system keyword.  Case-sensitive on the name to avoid matching verbs/articles.
_RE_SYSTEM = re.compile(
    r"(?<!\w)([A-Z][a-zA-Z0-9]{2,}(?:\s+[A-Z][a-zA-Z0-9]{2,}){0,3})\s+"
    r"(?:system|platform|application|service|tool|framework)\b",
)

# Components: a proper noun or technical term (at least 3 chars) preceding
# a component keyword.
_RE_COMPONENT = re.compile(
    r"\b([A-Za-z][a-zA-Z0-9_-]{2,}(?:\s+[a-zA-Z0-9_-]{2,}){0,2})\s+"
    r"(?:component|module|library|package|plugin|extension)\b",
    re.IGNORECASE,
)

# Persons: near person keywords (simple heuristic)
_RE_PERSON = re.compile(
    r"(?:user|admin|administrator|operator|officer|analyst|engineer|manager"
    r"|developer|architect)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b"
)


# =========================================================================
# HELPERS
# =========================================================================
def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kg_id():
    """Generate kg-prefixed short ID."""
    return f"kg-{uuid.uuid4().hex[:10]}"


def _get_db(path=None):
    """Return an sqlite3 connection with Row factory."""
    p = path or ICDEV_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_tables(conn=None):
    """Create knowledge-graph tables if they don't exist."""
    close = False
    if conn is None:
        conn = _get_db()
        close = True

    stmts = [
        textwrap.dedent("""\
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
            );"""),
        textwrap.dedent("""\
            CREATE TABLE IF NOT EXISTS kg_nodes (
                id TEXT PRIMARY KEY,
                graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
                label TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                properties TEXT DEFAULT '{}',
                embedding BLOB,
                centrality REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );"""),
        textwrap.dedent("""\
            CREATE TABLE IF NOT EXISTS kg_edges (
                id TEXT PRIMARY KEY,
                graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
                source_id TEXT NOT NULL REFERENCES kg_nodes(id),
                target_id TEXT NOT NULL REFERENCES kg_nodes(id),
                relationship TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                properties TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );"""),
    ]
    for sql in stmts:
        conn.execute(sql)
    conn.commit()
    if close:
        conn.close()


# Words to strip from entity label starts (articles, prepositions, etc.)
_STRIP_PREFIXES = {"the", "a", "an", "this", "that", "each", "every", "its"}

# Extension hook: (compiled_regex, entity_type) pairs consulted by
# _extract_entities AFTER the built-in patterns. Populated at runtime by
# tools.doc_modernization.pack_loader (entity types: hardware_model,
# software_product, protocol, crypto_algorithm) — do NOT hardcode new
# domain types here; declare them in args/docmod/packs/*.yaml instead.
EXTRA_ENTITY_PATTERNS: list = []


# =========================================================================
# ENTITY EXTRACTION
# =========================================================================
def _extract_entities(text):
    """Extract entities from *text* using compiled regex patterns.

    Returns a list of ``(label, entity_type)`` tuples, deduplicated.
    """
    seen = set()
    entities = []

    def _add(label, etype):
        label = label.strip()
        if not label or len(label) < 2:
            return
        # Strip leading articles / stop-words from extracted labels
        words = label.split()
        while words and words[0].lower() in _STRIP_PREFIXES:
            words.pop(0)
        if not words:
            return
        label = " ".join(words)
        key = (label.lower(), etype)
        if key not in seen:
            seen.add(key)
            entities.append((label, etype))

    # Controls  (AC-2, SC-13, …)
    for m in _RE_CONTROL.finditer(text):
        _add(m.group(1), "control")

    # Requirement IDs
    for m in _RE_REQUIREMENT_ID.finditer(text):
        _add(m.group(1), "requirement")

    # SHALL / MUST statement requirements (synthesize label)
    for m in _RE_SHALL_MUST.finditer(text):
        verb = m.group(1).upper()
        snippet = m.group(2).strip()
        label = f"{verb}: {snippet}"
        _add(label, "requirement")

    # Standards
    for m in _RE_STANDARD.finditer(text):
        _add(m.group(1), "standard")

    # Documents
    for m in _RE_DOCUMENT.finditer(text):
        _add(m.group(1), "document")

    # Organizations (keyword-based)
    for m in _RE_ORG_KEYWORD.finditer(text):
        _add(m.group(1), "organization")
    for m in _RE_ORG_ABBREV.finditer(text):
        _add(m.group(1), "organization")

    # Systems
    for m in _RE_SYSTEM.finditer(text):
        _add(m.group(1), "system")

    # Components
    for m in _RE_COMPONENT.finditer(text):
        _add(m.group(1), "component")

    # Persons
    for m in _RE_PERSON.finditer(text):
        _add(m.group(1), "person")

    # Runtime-registered domain patterns (see EXTRA_ENTITY_PATTERNS above)
    for pattern, etype in EXTRA_ENTITY_PATTERNS:
        for m in pattern.finditer(text):
            _add(m.group(0), etype)

    return entities


# =========================================================================
# RELATIONSHIP EXTRACTION
# =========================================================================
def _extract_relationships(text, entities):
    """Identify relationships between entities found in *text*.

    Scans sentences for known verb phrases between entity mentions and maps
    them to canonical relationship types via ``VERB_MAP``.

    Returns list of ``(source_label, target_label, relationship)`` tuples.
    """
    if len(entities) < 2:
        return []

    # Build lookup: lowered label -> (label, entity_type)
    entity_lookup = {}
    for label, etype in entities:
        entity_lookup[label.lower()] = (label, etype)

    # Sort entity labels longest-first so longer matches take priority
    sorted_labels = sorted(entity_lookup.keys(), key=len, reverse=True)

    # Split text into sentences (simple heuristic)
    sentences = re.split(r"[.;!\n]+", text)

    relationships = []
    seen_edges = set()

    for sentence in sentences:
        s_lower = sentence.lower()

        # Find all entity mentions in this sentence
        mentions = []
        for lbl in sorted_labels:
            idx = s_lower.find(lbl)
            if idx >= 0:
                orig_label = entity_lookup[lbl][0]
                mentions.append((idx, orig_label))

        if len(mentions) < 2:
            continue

        # Sort by position
        mentions.sort(key=lambda x: x[0])

        # Check for verb phrases between consecutive entity pairs
        for i in range(len(mentions) - 1):
            pos_a, label_a = mentions[i]
            pos_b, label_b = mentions[i + 1]

            # Extract text between the two mentions
            between_start = pos_a + len(label_a)
            between_text = sentence[between_start:pos_b].strip().lower()

            # Match against verb map
            for verb, rel_type in VERB_MAP.items():
                if verb in between_text:
                    edge_key = (label_a, label_b, rel_type)
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        relationships.append((label_a, label_b, rel_type))
                    break  # first match wins per pair

    return relationships


# =========================================================================
# CORE API
# =========================================================================
def extract_entities_and_relationships(text, project_id, graph_name=None):
    """Extract entities and relationships from *text*, persist to DB.

    Args:
        text:        Free text to analyse.
        project_id:  ICDEV™ project identifier.
        graph_name:  Optional human-readable graph name.

    Returns:
        Summary dict with graph_id, entity_count, edge_count, entities,
        edges.
    """
    if not text or not text.strip():
        return {"status": "error", "message": "No text provided."}

    now = _now()
    conn = _get_db()
    _ensure_tables(conn)

    # --- Create graph record ------------------------------------------------
    graph_id = _kg_id()
    name = graph_name or f"text-network-{now[:10]}"
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

    conn.execute(
        "INSERT INTO kg_graphs (id, project_id, name, description, metadata, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            graph_id,
            project_id,
            name,
            f"Auto-extracted from text ({len(text)} chars)",
            json.dumps({"content_hash": content_hash, "source": "text_network"}),
            now,
            now,
        ),
    )

    # --- Entity extraction ---------------------------------------------------
    raw_entities = _extract_entities(text)
    node_map = {}  # label -> node_id

    for label, etype in raw_entities:
        node_id = _kg_id()
        node_map[label] = node_id
        conn.execute(
            "INSERT INTO kg_nodes (id, graph_id, label, entity_type, properties, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (node_id, graph_id, label, etype, "{}", now),
        )

    # --- Relationship extraction ---------------------------------------------
    raw_edges = _extract_relationships(text, raw_entities)
    edge_records = []

    for src_label, tgt_label, rel_type in raw_edges:
        src_id = node_map.get(src_label)
        tgt_id = node_map.get(tgt_label)
        if not src_id or not tgt_id:
            continue
        edge_id = _kg_id()
        conn.execute(
            "INSERT INTO kg_edges (id, graph_id, source_id, target_id, relationship, weight, properties, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (edge_id, graph_id, src_id, tgt_id, rel_type, 1.0, "{}", now),
        )
        edge_records.append(
            {
                "id": edge_id,
                "source": src_label,
                "target": tgt_label,
                "relationship": rel_type,
                "weight": 1.0,
            }
        )

    # --- Update counts -------------------------------------------------------
    entity_count = len(raw_entities)
    edge_count = len(edge_records)
    conn.execute(
        "UPDATE kg_graphs SET entity_count = %s, edge_count = %s, updated_at = %s WHERE id = %s",
        (entity_count, edge_count, now, graph_id),
    )

    conn.commit()
    conn.close()

    # --- Audit ---------------------------------------------------------------
    if _HAS_AUDIT:
        audit_log_event(
            event_type="knowledge_recorded",
            actor="text_network",
            action=f"Extracted {entity_count} entities and {edge_count} edges (graph: {graph_id})",
            project_id=project_id,
            details={"graph_id": graph_id},
        )

    entity_list = [{"label": label, "entity_type": etype} for label, etype in raw_entities]

    return {
        "status": "ok",
        "graph_id": graph_id,
        "project_id": project_id,
        "name": name,
        "entity_count": entity_count,
        "edge_count": edge_count,
        "entities": entity_list,
        "edges": edge_records,
    }


def get_graph(graph_id):
    """Retrieve a graph with all its nodes and edges."""
    conn = _get_db()
    _ensure_tables(conn)

    row = conn.execute("SELECT * FROM kg_graphs WHERE id = %s", (graph_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Graph {graph_id} not found."}

    graph = dict(row)

    nodes = [
        dict(r)
        for r in conn.execute(
            "SELECT id, label, entity_type, properties, centrality, created_at "
            "FROM kg_nodes WHERE graph_id = %s ORDER BY entity_type, label",
            (graph_id,),
        ).fetchall()
    ]

    edges = [
        dict(r)
        for r in conn.execute(
            "SELECT e.id, e.relationship, e.weight, e.properties, e.created_at, "
            "       s.label AS source_label, t.label AS target_label "
            "FROM kg_edges e "
            "JOIN kg_nodes s ON e.source_id = s.id "
            "JOIN kg_nodes t ON e.target_id = t.id "
            "WHERE e.graph_id = %s ORDER BY e.relationship",
            (graph_id,),
        ).fetchall()
    ]

    conn.close()

    return {
        "status": "ok",
        "graph": graph,
        "nodes": nodes,
        "edges": edges,
    }


def search_nodes(query, project_id=None):
    """Search node labels across graphs.

    Args:
        query:      Text substring to search for (case-insensitive).
        project_id: Optionally scope search to a single project.

    Returns:
        Dict with matching nodes and their graph context.
    """
    conn = _get_db()
    _ensure_tables(conn)

    like_pat = f"%{query}%"
    if project_id:
        rows = conn.execute(
            "SELECT n.id, n.label, n.entity_type, n.centrality, "
            "       g.id AS graph_id, g.project_id, g.name AS graph_name "
            "FROM kg_nodes n "
            "JOIN kg_graphs g ON n.graph_id = g.id "
            "WHERE n.label LIKE %s AND g.project_id = %s "
            "ORDER BY n.centrality DESC, n.label "
            "LIMIT 100",
            (like_pat, project_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT n.id, n.label, n.entity_type, n.centrality, "
            "       g.id AS graph_id, g.project_id, g.name AS graph_name "
            "FROM kg_nodes n "
            "JOIN kg_graphs g ON n.graph_id = g.id "
            "WHERE n.label LIKE %s "
            "ORDER BY n.centrality DESC, n.label "
            "LIMIT 100",
            (like_pat,),
        ).fetchall()

    conn.close()

    results = [dict(r) for r in rows]
    return {
        "status": "ok",
        "query": query,
        "project_id": project_id,
        "count": len(results),
        "nodes": results,
    }


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Knowledge Graph — entity/relationship extraction from text (D-KARL-1).",
    )
    parser.add_argument("--text", type=str, help="Input text to analyse.")
    parser.add_argument("--file", type=str, help="Path to a text file to read.")
    parser.add_argument("--project-id", type=str, default="default", help="ICDEV™ project identifier.")
    parser.add_argument("--graph-name", type=str, default=None, help="Optional human-readable graph name.")
    parser.add_argument("--graph-id", type=str, default=None, help="Retrieve an existing graph by ID.")
    parser.add_argument("--search", type=str, default=None, help="Search node labels across graphs.")
    parser.add_argument("--json", action="store_true", help="Output results as formatted JSON.")

    args = parser.parse_args()

    # Dispatch
    if args.graph_id:
        result = get_graph(args.graph_id)
    elif args.search:
        result = search_nodes(args.search, project_id=args.project_id)
    elif args.text or args.file:
        text = args.text or ""
        if args.file:
            fpath = Path(args.file)
            if not fpath.is_file():
                result = {"status": "error", "message": f"File not found: {args.file}"}
                if args.json:
                    print(json.dumps(result, indent=2))
                else:
                    print(result["message"])
                sys.exit(1)
            text = fpath.read_text(encoding="utf-8", errors="replace")
        result = extract_entities_and_relationships(
            text,
            args.project_id,
            graph_name=args.graph_name,
        )
    else:
        parser.print_help()
        sys.exit(0)

    # Output
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result.get("status") == "error":
            print(f"ERROR: {result.get('message', 'Unknown error')}")
            sys.exit(1)
        if "graph_id" in result and "graph" not in result:
            # Extraction result
            print(f"Graph:    {result['graph_id']}")
            print(f"Entities: {result.get('entity_count', 0)}")
            print(f"Edges:    {result.get('edge_count', 0)}")
            for e in result.get("entities", []):
                print(f"  [{e['entity_type']:14s}] {e['label']}")
            for edge in result.get("edges", []):
                print(f"  {edge['source']} --[{edge['relationship']}]--> {edge['target']}")
        elif "graph" in result:
            # get_graph result
            g = result["graph"]
            print(f"Graph: {g['id']}  ({g['name']})")
            print(f"  Entities: {g['entity_count']}  Edges: {g['edge_count']}")
            for n in result.get("nodes", []):
                print(f"  [{n['entity_type']:14s}] {n['label']}")
            for edge in result.get("edges", []):
                print(f"  {edge['source_label']} --[{edge['relationship']}]--> {edge['target_label']}")
        elif "query" in result:
            # search_nodes result
            print(f"Search results for '{result.get('query', '')}' ({result.get('count', 0)} found):")
            for n in result.get("nodes", []):
                print(f"  [{n['entity_type']:14s}] {n['label']}  (graph: {n['graph_name']})")


if __name__ == "__main__":
    main()
