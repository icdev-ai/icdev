#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Cross-canvas Ontology Federation.

Merges all domain ontologies into a single ICDEV Core Ontology graph,
resolves equivalent classes across domains, adds cross-domain properties,
and enables heuristic keyword/pattern queries over the unified graph.

NOTE: query_federation is a heuristic keyword/pattern matcher over ontology
terms — it is NOT a SPARQL evaluator and does not parse or execute SPARQL.

Functions:
    build_federation      — Merge domain ontologies into ICDEV Core
    query_federation      — Heuristic keyword/pattern query over the unified graph (not SPARQL)
    resolve_equivalents   — Resolve equivalent classes across domains
    subclass_closure      — Pre-compute transitive closure of rdfs:subClassOf

Usage:
    python tools/ontology/federation.py --build-federation --json
    python tools/ontology/federation.py --query "Show all designs referencing AWS VPCs that contain SECRET-classified tables" --json
    python tools/ontology/federation.py --list-domains --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# =========================================================================
# CONSTANTS
# =========================================================================

# Domain namespaces
NAMESPACES: Dict[str, str] = {
    "network":    "https://icdev.dev/ns/network#",
    "data":       "https://icdev.dev/ns/data#",
    "security":   "https://icdev.dev/ns/security#",
    "infra":      "https://icdev.dev/ns/infra#",
    "compliance": "https://icdev.dev/ns/compliance#",
    "core":       "https://icdev.dev/ns/core#",
    "boundary":   "https://icdev.dev/ns/boundary#",
    "pipeline":   "https://icdev.dev/ns/pipeline#",
    "quality":    "https://icdev.dev/ns/quality#",
    "aiml":       "https://icdev.dev/ns/aiml#",
    "mission":    "https://icdev.dev/ns/mission#",
    "icdev":      "https://icdev.dev/ns/icdev#",
    "strategy":   "https://icdev.dev/ns/strategy#",
}

# Domain ontologies: domain -> list of classes with superclasses
DOMAIN_ONTOLOGIES: Dict[str, List[Dict[str, Any]]] = {
    "network": [
        {"id": "network:AWSVPC", "label": "AWS VPC", "superclasses": ["network:VirtualNetwork"]},
        {"id": "network:CloudVPC", "label": "Cloud VPC", "superclasses": ["network:VirtualNetwork"]},
        {"id": "network:VirtualNetwork", "label": "Virtual Network", "superclasses": ["network:NetworkResource"]},
        {"id": "network:Subnet", "label": "Subnet", "superclasses": ["network:NetworkResource"]},
        {"id": "network:NetworkResource", "label": "Network Resource", "superclasses": ["core:Resource"]},
        {"id": "network:Firewall", "label": "Firewall", "superclasses": ["network:NetworkResource"]},
        {"id": "network:LoadBalancer", "label": "Load Balancer", "superclasses": ["network:NetworkResource"]},
    ],
    "data": [
        {"id": "data:RelationalTable", "label": "Relational Table", "superclasses": ["data:DataStore"]},
        {"id": "data:DataStore", "label": "Data Store", "superclasses": ["data:DataResource"]},
        {"id": "data:DataResource", "label": "Data Resource", "superclasses": ["core:Resource"]},
        {"id": "data:DataLake", "label": "Data Lake", "superclasses": ["data:DataStore"]},
        {"id": "data:DataPipeline", "label": "Data Pipeline", "superclasses": ["data:DataResource"]},
        {"id": "data:CloudTable", "label": "Cloud Table", "superclasses": ["data:RelationalTable"]},
    ],
    "security": [
        {"id": "security:Classification", "label": "Classification", "superclasses": ["core:Concept"]},
        {"id": "security:SECRET", "label": "SECRET", "superclasses": ["security:Classification"]},
        {"id": "security:CUI", "label": "CUI", "superclasses": ["security:Classification"]},
        {"id": "security:Control", "label": "Security Control", "superclasses": ["core:Concept"]},
        {"id": "security:Boundary", "label": "Security Boundary", "superclasses": ["core:Resource"]},
    ],
    "infra": [
        {"id": "infra:ComputeInstance", "label": "Compute Instance", "superclasses": ["infra:InfrastructureResource"]},
        {"id": "infra:Container", "label": "Container", "superclasses": ["infra:ComputeInstance"]},
        {"id": "infra:InfrastructureResource", "label": "Infrastructure Resource", "superclasses": ["core:Resource"]},
        {"id": "infra:StorageBucket", "label": "Storage Bucket", "superclasses": ["infra:InfrastructureResource"]},
    ],
    "compliance": [
        {"id": "compliance:NISTControl", "label": "NIST Control", "superclasses": ["core:Concept"]},
        {"id": "compliance:FedRAMPControl", "label": "FedRAMP Control", "superclasses": ["compliance:NISTControl"]},
        {"id": "compliance:STIGRule", "label": "STIG Rule", "superclasses": ["core:Concept"]},
    ],
    "core": [
        {"id": "core:Resource", "label": "Resource", "superclasses": ["core:Entity"]},
        {"id": "core:Concept", "label": "Concept", "superclasses": ["core:Entity"]},
        {"id": "core:Entity", "label": "Entity", "superclasses": []},
        {"id": "core:Design", "label": "Design", "superclasses": ["core:Entity"]},
    ],
    "icdev": [
        {"id": "icdev:Lesson", "label": "Lesson", "superclasses": ["core:Entity"]},
        {"id": "icdev:Lab", "label": "Lab", "superclasses": ["core:Entity"]},
        {"id": "icdev:Assessment", "label": "Assessment", "superclasses": ["core:Entity"]},
        {"id": "icdev:FundamentalCompetency", "label": "Fundamental Competency", "superclasses": ["core:Concept"]},
        {"id": "icdev:IntermediateCompetency", "label": "Intermediate Competency", "superclasses": ["core:Concept"]},
        {"id": "icdev:ExpertCompetency", "label": "Expert Competency", "superclasses": ["core:Concept"]},
        {"id": "icdev:Prerequisite", "label": "Prerequisite", "superclasses": ["core:Concept"]},
    ],
    "strategy": [
        {"id": "strategy:WarCouncilBrief", "label": "War Council Brief", "superclasses": ["core:Concept"]},
        {"id": "strategy:AIROIFramework", "label": "AI ROI Framework", "superclasses": ["core:Concept"]},
        {"id": "strategy:GovernancePosture", "label": "Governance Posture", "superclasses": ["core:Concept"]},
    ],
    "boundary": [
        {"id": "boundary:SystemBoundary", "label": "System Boundary", "superclasses": ["core:Resource"]},
        {"id": "boundary:AuthorizationBoundary", "label": "Authorization Boundary", "superclasses": ["boundary:SystemBoundary"]},
        {"id": "boundary:Interconnection", "label": "Interconnection", "superclasses": ["core:Resource"]},
        {"id": "boundary:BoundaryControl", "label": "Boundary Control", "superclasses": ["core:Concept"]},
        {"id": "boundary:Document", "label": "Boundary Document", "superclasses": ["core:Concept"]},
        {"id": "boundary:DigitalTwin", "label": "Boundary Digital Twin", "superclasses": ["core:Resource"]},
    ],
    "pipeline": [
        {"id": "pipeline:CICDPlatform", "label": "CI/CD Platform", "superclasses": ["core:Resource"]},
        {"id": "pipeline:GitOpsPlatform", "label": "GitOps Platform", "superclasses": ["pipeline:CICDPlatform"]},
        {"id": "pipeline:SCM", "label": "Source Control", "superclasses": ["core:Resource"]},
        {"id": "pipeline:BuildTool", "label": "Build Tool", "superclasses": ["core:Resource"]},
        {"id": "pipeline:SecurityScan", "label": "Security Scan", "superclasses": ["core:Concept"]},
        {"id": "pipeline:Registry", "label": "Artifact Registry", "superclasses": ["core:Resource"]},
        {"id": "pipeline:Attestation", "label": "Supply Chain Attestation", "superclasses": ["core:Concept"]},
        {"id": "pipeline:Policy", "label": "Pipeline Policy", "superclasses": ["core:Concept"]},
        {"id": "pipeline:ApprovalGate", "label": "Approval Gate", "superclasses": ["core:Concept"]},
        {"id": "pipeline:SecretsManager", "label": "Secrets Manager", "superclasses": ["core:Resource"]},
        {"id": "pipeline:DeployStrategy", "label": "Deployment Strategy", "superclasses": ["core:Concept"]},
    ],
    "quality": [
        {"id": "quality:QualityGate", "label": "Quality Gate", "superclasses": ["core:Concept"]},
        {"id": "quality:QualityGate.SAST", "label": "SAST Gate", "superclasses": ["quality:QualityGate"]},
        {"id": "quality:QualityGate.DAST", "label": "DAST Gate", "superclasses": ["quality:QualityGate"]},
        {"id": "quality:QualityGate.SCA", "label": "SCA Gate", "superclasses": ["quality:QualityGate"]},
        {"id": "quality:QualityGate.UnitTest", "label": "Unit Test Gate", "superclasses": ["quality:QualityGate"]},
        {"id": "quality:QualityGate.E2E", "label": "E2E Test Gate", "superclasses": ["quality:QualityGate"]},
    ],
    "aiml": [
        {"id": "aiml:AIModel", "label": "AI Model", "superclasses": ["core:Resource"]},
        {"id": "aiml:Adaptation", "label": "Model Adaptation", "superclasses": ["core:Concept"]},
        {"id": "aiml:AIData", "label": "AI Data Source", "superclasses": ["core:Resource"]},
        {"id": "aiml:AISafety", "label": "AI Safety Component", "superclasses": ["core:Concept"]},
        {"id": "aiml:AIEval", "label": "AI Evaluation", "superclasses": ["core:Concept"]},
        {"id": "aiml:AIDeployment", "label": "AI Deployment Target", "superclasses": ["core:Resource"]},
        {"id": "aiml:AIGovernance", "label": "AI Governance Artifact", "superclasses": ["core:Concept"]},
    ],
    "mission": [
        {"id": "mission:Zone", "label": "Mission Zone", "superclasses": ["core:Resource"]},
        {"id": "mission:Zone.Situation", "label": "Situation Zone", "superclasses": ["mission:Zone"]},
        {"id": "mission:Zone.Intelligence", "label": "Intelligence Zone", "superclasses": ["mission:Zone"]},
        {"id": "mission:Zone.Execution", "label": "Execution Zone", "superclasses": ["mission:Zone"]},
        {"id": "mission:Zone.Security", "label": "Security Zone", "superclasses": ["mission:Zone"]},
        {"id": "mission:Status", "label": "Mission Status", "superclasses": ["core:Concept"]},
    ],
}

# Cross-domain equivalent class alignments
EQUIVALENT_CLASSES: List[Dict[str, str]] = [
    {"source": "network:AWSVPC", "target": "data:CloudVPC", "assertion": "owl:equivalentClass"},
    {"source": "network:CloudVPC", "target": "data:CloudVPC", "assertion": "owl:equivalentClass"},
    {"source": "network:Subnet", "target": "infra:NetworkSegment", "assertion": "owl:equivalentClass"},
    {"source": "security:Control", "target": "compliance:NISTControl", "assertion": "owl:subClassOf"},
]

# Cross-domain properties: domain -> list of property definitions
CROSS_DOMAIN_PROPERTIES: List[Dict[str, Any]] = [
    {
        "id": "network:hosts",
        "label": "hosts",
        "domain": "network:AWSVPC",
        "range": "data:RelationalTable",
        "description": "AWS VPC hosts relational tables",
    },
    {
        "id": "data:classifiedAs",
        "label": "classified as",
        "domain": "data:RelationalTable",
        "range": "security:Classification",
        "description": "Data store has security classification",
    },
    {
        "id": "infra:deployedIn",
        "label": "deployed in",
        "domain": "infra:ComputeInstance",
        "range": "network:AWSVPC",
        "description": "Compute instance deployed in VPC",
    },
    {
        "id": "security:protects",
        "label": "protects",
        "domain": "security:Boundary",
        "range": "core:Resource",
        "description": "Security boundary protects a resource",
    },
    {
        "id": "compliance:implements",
        "label": "implements",
        "domain": "core:Design",
        "range": "compliance:NISTControl",
        "description": "Design implements a compliance control",
    },
]

# Directory containing domain TTL files (loaded at build time)
_TTL_DIR = BASE_DIR / "args" / "ontology"


def _ttl_dir() -> Path:
    """The ontology directory of the PARENT this process belongs to.

    Resolved through ``icdev.core.paths`` so ``ICDEV_PROJECT_ROOT`` -- a second
    ICDEV parent such as ICDEV[FT] -- selects its own ``args/ontology``; with no
    root declared it is this checkout's, exactly as before. A module that roots
    the ontology at its own file location can only ever load the domain it was
    checked out with, which is the self-rooting defect xit-decl-03 censuses.
    """
    try:
        from icdev.core.paths import data_path
    except ImportError:  # an older kernel without icdev.core
        return _TTL_DIR
    return data_path("args", anchor=__file__) / "ontology"


# =========================================================================
# TTL LOADER (lightweight, no rdflib dependency)
# =========================================================================


def _parse_ttl_file(
    path: Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Extract classes and alignments from a Turtle file using regex.

    Handles the subset of TTL used in args/ontology/:
    - @prefix declarations
    - subject rdf:type owl:Class
    - subject rdfs:subClassOf object
    - subject rdf:type <KnownClass>  (treated as rdfs:subClassOf)
    - subject rdfs:label "..."
    - subject owl:equivalentClass object

    Returns:
        (classes, alignments)  — lists ready to merge into DOMAIN_ONTOLOGIES.
    """
    text = path.read_text(encoding="utf-8")

    # 1. Prefix map: prefix_name -> uri_base (used only to validate CURIEs exist)
    known_prefixes: Set[str] = set()
    for m in re.finditer(r"@prefix\s+(\w+):\s+<[^>]+>\s*\.", text):
        known_prefixes.add(m.group(1))

    def _is_curie(term: str) -> bool:
        if ":" not in term or term.startswith("<"):
            return False
        prefix = term.split(":", 1)[0]
        return prefix in known_prefixes

    def _clean(term: str) -> str:
        return term.rstrip(";.,").strip()

    # 2. First pass — owl:Class declarations
    classes: Dict[str, Dict[str, Any]] = {}
    known_class_ids: Set[str] = set()
    for m in re.finditer(r"(\w+:\S+)\s+rdf:type\s+owl:Class", text):
        cid = _clean(m.group(1))
        if _is_curie(cid):
            local = cid.split(":", 1)[1]
            classes.setdefault(cid, {"id": cid, "label": local, "superclasses": []})
            known_class_ids.add(cid)

    # 3. Labels (match subject + rdfs:label on same or adjacent line)
    for m in re.finditer(r"(\w+:\S+)[^.]*?rdfs:label\s+\"([^\"]+)\"", text, re.DOTALL):
        cid = _clean(m.group(1))
        if cid in classes:
            classes[cid]["label"] = m.group(2)

    # 4. Explicit rdfs:subClassOf
    for m in re.finditer(r"(\w+:\S+)\s+rdfs:subClassOf\s+(\w+:\S+)", text):
        sub = _clean(m.group(1))
        sup = _clean(m.group(2))
        if sub in classes and sup not in classes[sub]["superclasses"]:
            classes[sub]["superclasses"].append(sup)

    # 4b. Turtle STATEMENT BLOCKS: a subject at column 0, predicate-object pairs
    # on the indented lines that follow, terminated by " ." -- the layout of
    # every file in args/ontology/. The same-line regex above only ever saw
    # icdev_core.ttl's one-line form: measured 2026-08-21, 4 of the 74
    # rdfs:subClassOf statements in the tree survived parsing, so the closure
    # never learned a TTL class's hierarchy and `superclasses` was '[]' for
    # 83 of 87 classes. The block pass is a superset of the same-line pass.
    for block in re.split(r"\n(?=\S)", text):
        head = block.strip().split(None, 1)
        if not head:
            continue
        subj = _clean(head[0])
        if subj not in classes:
            continue
        for m in re.finditer(r"rdfs:subClassOf\s+(\w+:\S+)", block):
            sup = _clean(m.group(1))
            if sup and sup not in classes[subj]["superclasses"]:
                classes[subj]["superclasses"].append(sup)
        lab = re.search(r"rdfs:label\s+\"([^\"]+)\"", block)
        if lab:
            classes[subj]["label"] = lab.group(1)

    # 5. rdf:type <KnownClass> → treat as rdfs:subClassOf
    for m in re.finditer(r"(\w+:\S+)\s+rdf:type\s+(\w+:\S+)", text):
        sub = _clean(m.group(1))
        rtype = _clean(m.group(2))
        if rtype in ("owl:Class", "owl:Ontology", "owl:ObjectProperty", "owl:DatatypeProperty"):
            continue
        if rtype in known_class_ids and _is_curie(sub):
            if sub not in classes:
                local = sub.split(":", 1)[1]
                classes[sub] = {"id": sub, "label": local, "superclasses": []}
            if rtype not in classes[sub]["superclasses"]:
                classes[sub]["superclasses"].append(rtype)
            # Resolve label from rdfs:label on the same subject if present
    # Re-apply labels for newly added instance entries
    for m in re.finditer(r"(\w+:\S+)[^.]*?rdfs:label\s+\"([^\"]+)\"", text, re.DOTALL):
        cid = _clean(m.group(1))
        if cid in classes:
            classes[cid]["label"] = m.group(2)

    # 6. owl:equivalentClass triples → alignments
    alignments: List[Dict[str, str]] = []
    for m in re.finditer(r"(\w+:\S+)\s+owl:equivalentClass\s+(\w+:\S+)", text):
        src = _clean(m.group(1))
        tgt = _clean(m.group(2))
        alignments.append({"source": src, "target": tgt, "assertion": "owl:equivalentClass"})

    return list(classes.values()), alignments


def _load_ttl_classes(
    ttl_dir: Path,
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, str]]]:
    """Load all *.ttl files from ttl_dir and merge into domain ontology format.

    Returns:
        (extra_domains, extra_alignments) where extra_domains is a dict
        mapping domain_name -> list-of-class-dicts, ready to merge with
        DOMAIN_ONTOLOGIES.
    """
    extra_domains: Dict[str, List[Dict[str, Any]]] = {}
    extra_alignments: List[Dict[str, str]] = []

    if not ttl_dir.is_dir():
        return extra_domains, extra_alignments

    for ttl_path in sorted(ttl_dir.glob("*.ttl")):
        try:
            classes, alignments = _parse_ttl_file(ttl_path)
        except Exception:
            continue
        if classes:
            # Use filename stem as domain name (e.g. icdev_core, external_mappings)
            domain = ttl_path.stem
            extra_domains[domain] = classes
        extra_alignments.extend(alignments)

    return extra_domains, extra_alignments


# =========================================================================
# HELPERS
# =========================================================================


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db(db_path: Optional[str] = None):
    conn = get_connection(db_path=db_path) if db_path else get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables(conn) -> None:
    """Ensure ontology federation tables exist (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ontology_classes (
            id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            label TEXT NOT NULL,
            superclasses TEXT DEFAULT '[]',
            canonical_id TEXT,
            merged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ontology_properties (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            domain_class TEXT,
            range_class TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ontology_alignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            assertion TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ontology_subclass_closure (
            subclass TEXT NOT NULL,
            superclass TEXT NOT NULL,
            distance INTEGER DEFAULT 1,
            PRIMARY KEY (subclass, superclass)
        );
        CREATE TABLE IF NOT EXISTS ontology_federation_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)


def _canonical_id(class_id: str, equivalence_map: Dict[str, str]) -> str:
    """Follow equivalence chain to canonical representative."""
    visited: Set[str] = set()
    current = class_id
    while current in equivalence_map and current not in visited:
        visited.add(current)
        current = equivalence_map[current]
    return current


def _build_equivalence_map(alignments: List[Dict[str, str]]) -> Dict[str, str]:
    """Build a map from class -> canonical equivalent class.

    For owl:equivalentClass assertions, both directions are mapped to
    the lexicographically smaller URI as the canonical representative.
    """
    equiv: Dict[str, Set[str]] = defaultdict(set)
    for a in alignments:
        if a.get("assertion") == "owl:equivalentClass":
            src, tgt = a["source"], a["target"]
            equiv[src].add(tgt)
            equiv[tgt].add(src)

    # Union-find to group equivalents
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        if x not in parent:
            parent[x] = x
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            # Lexicographically smaller as root
            if rx < ry:
                parent[ry] = rx
            else:
                parent[rx] = ry

    for src, tgts in equiv.items():
        for tgt in tgts:
            union(src, tgt)

    # Map each class to its canonical representative
    canon: Dict[str, str] = {}
    all_classes = set(equiv.keys())
    for src in all_classes:
        canon[src] = find(src)
    return canon


# =========================================================================
# 1. BUILD FEDERATION
# =========================================================================


def build_federation(
    db_path: Optional[str] = None,
    *,
    ttl_dir: Optional[Path] = None,
    include_builtin: bool = True,
) -> Dict[str, Any]:
    """Merge all domain ontologies into the ICDEV Core Ontology graph.

    Steps:
        1. Clear existing ontology tables.
        2. Insert all domain classes.
        3. Store equivalence alignments.
        4. Compute and store canonical IDs.
        5. Insert cross-domain properties.
        6. Pre-compute transitive closure of rdfs:subClassOf.
        7. Update federation metadata.

    Args:
        db_path: Optional database path override.
        ttl_dir: Directory of ``*.ttl`` files to load. Default: the owning
            parent's ``args/ontology`` (see ``_ttl_dir``).
        include_builtin: Also load the IT domain ontologies, alignments and
            cross-domain properties hard-coded in this module. A parent whose
            domain is not IT passes ``False`` and gets ONLY its TTL classes --
            the hard-coded tables are ICDEV[IT]'s network / compliance /
            security vocabulary, not a kernel.

    Returns:
        Dict with build stats and canonical class counts.
    """
    start_ms = int(time.time() * 1000)
    ttl_dir = Path(ttl_dir) if ttl_dir is not None else _ttl_dir()
    builtin_domains = DOMAIN_ONTOLOGIES if include_builtin else {}
    builtin_alignments = EQUIVALENT_CLASSES if include_builtin else []
    builtin_properties = CROSS_DOMAIN_PROPERTIES if include_builtin else []
    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)

        # 1. Clear existing data
        conn.execute("DELETE FROM ontology_classes")
        conn.execute("DELETE FROM ontology_properties")
        conn.execute("DELETE FROM ontology_alignments")
        conn.execute("DELETE FROM ontology_subclass_closure")
        conn.execute("DELETE FROM ontology_federation_meta WHERE key = 'last_build'")
        conn.commit()

        # Load extra classes and alignments from <parent>/args/ontology/*.ttl
        ttl_domains, ttl_alignments = _load_ttl_classes(ttl_dir)

        # 2. Insert domain classes (hardcoded + TTL-derived, deduplicated by class id)
        seen_class_ids: Set[str] = set()
        all_classes: List[Tuple[str, str, str, str, str]] = []
        for domain, classes in {**builtin_domains, **ttl_domains}.items():
            for cls in classes:
                cid = cls["id"]
                if cid in seen_class_ids:
                    continue
                seen_class_ids.add(cid)
                label = cls["label"]
                supers = json.dumps(cls.get("superclasses", []))
                all_classes.append((cid, domain, label, supers, cid))

        conn.executemany(
            "INSERT INTO ontology_classes (id, domain, label, superclasses, canonical_id) VALUES (%s, %s, %s, %s, %s)",
            all_classes,
        )

        # 3. Store alignments (hardcoded + TTL-derived, deduplicated)
        all_alignments = list(builtin_alignments) + ttl_alignments
        alignments_rows = list({
            (a["source"], a["target"], a["assertion"])
            for a in all_alignments
        })
        conn.executemany(
            "INSERT INTO ontology_alignments (source, target, assertion) VALUES (%s, %s, %s)",
            alignments_rows,
        )

        # 4. Compute canonical IDs
        canon_map = _build_equivalence_map(all_alignments)
        for src, tgt in canon_map.items():
            conn.execute(
                "UPDATE ontology_classes SET canonical_id = %s WHERE id = %s",
                (tgt, src),
            )
        # Ensure every class has a canonical_id
        conn.execute(
            "UPDATE ontology_classes SET canonical_id = id WHERE canonical_id IS NULL"
        )

        # 5. Insert cross-domain properties
        prop_rows = []
        for p in builtin_properties:
            prop_rows.append((
                p["id"],
                p["label"],
                p.get("domain"),
                p.get("range"),
                p.get("description", ""),
            ))
        conn.executemany(
            "INSERT INTO ontology_properties (id, label, domain_class, range_class, description) VALUES (%s, %s, %s, %s, %s)",
            prop_rows,
        )

        # 6. Pre-compute transitive closure of rdfs:subClassOf
        # Build adjacency from superclasses + alignments
        subclass_of: Dict[str, Set[str]] = defaultdict(set)

        # From domain ontologies (hardcoded + TTL-derived)
        for domain, classes in {**builtin_domains, **ttl_domains}.items():
            for cls in classes:
                cid = cls["id"]
                for sup in cls.get("superclasses", []):
                    subclass_of[cid].add(sup)

        # From alignments (hardcoded + TTL-derived)
        for a in all_alignments:
            if a["assertion"] == "owl:subClassOf":
                subclass_of[a["source"]].add(a["target"])
            elif a["assertion"] == "owl:equivalentClass":
                # In RDFS terms, equivalentClass implies mutual subclassOf
                subclass_of[a["source"]].add(a["target"])
                subclass_of[a["target"]].add(a["source"])

        # Transitive closure via Floyd-Warshall style (sufficient for our scale)
        nodes = set(subclass_of.keys())
        for children in subclass_of.values():
            nodes |= children

        # Warshall's algorithm
        closure: Dict[str, Set[str]] = {n: set(subclass_of.get(n, set())) for n in nodes}
        for k in nodes:
            for i in nodes:
                if k in closure.get(i, set()):
                    closure[i] |= closure.get(k, set())

        closure_rows = []
        for sub, supers in closure.items():
            for sup in supers:
                # Distance = shortest path (BFS would be better but Warshall gives existence)
                # For simplicity, store distance 1 for direct, 2+ for indirect
                dist = 1 if sup in subclass_of.get(sub, set()) else 2
                closure_rows.append((sub, sup, dist))

        conn.executemany(
            "INSERT INTO ontology_subclass_closure (subclass, superclass, distance) VALUES (%s, %s, %s)",
            closure_rows,
        )

        # 7. Update metadata
        conn.execute(
            "INSERT INTO ontology_federation_meta (key, value, updated_at) VALUES (%s, %s, %s)",
            ("last_build", _now(), _now()),
        )
        conn.commit()

        elapsed_ms = int(time.time() * 1000) - start_ms

        # Stats
        class_count = conn.execute("SELECT COUNT(*) FROM ontology_classes").fetchone()[0]
        prop_count = conn.execute("SELECT COUNT(*) FROM ontology_properties").fetchone()[0]
        alignment_count = conn.execute("SELECT COUNT(*) FROM ontology_alignments").fetchone()[0]
        closure_count = conn.execute("SELECT COUNT(*) FROM ontology_subclass_closure").fetchone()[0]
        canon_groups = conn.execute(
            "SELECT canonical_id, COUNT(*) FROM ontology_classes GROUP BY canonical_id HAVING COUNT(*) > 1"
        ).fetchall()

        ttl_class_count = sum(len(v) for v in ttl_domains.values())
        return {
            "status": "ok",
            "action": "build_federation",
            "ttl_dir": str(ttl_dir),
            "builtin_included": include_builtin,
            "classes_total": class_count,
            "classes_from_ttl": ttl_class_count,
            "ttl_domains_loaded": list(ttl_domains.keys()),
            "properties_total": prop_count,
            "alignments_total": alignment_count,
            "alignments_from_ttl": len(ttl_alignments),
            "closure_pairs": closure_count,
            "canonical_groups": len(canon_groups),
            "canonical_merged_classes": [
                {"canonical": row[0], "merged_count": row[1]} for row in canon_groups
            ],
            "build_ms": elapsed_ms,
            "built_at": _now(),
        }
    finally:
        conn.close()


# =========================================================================
# 2. HEURISTIC KEYWORD QUERY ENGINE (NOT SPARQL)
# =========================================================================


def query_federation(
    query_text: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Heuristic keyword/pattern search over the unified ontology graph.

    This is a keyword/pattern matcher over ontology terms — it is NOT a SPARQL
    evaluator. It does not parse SPARQL syntax, variables, or triple patterns.
    It recognizes a small set of hard-coded natural-language shapes via
    substring matching (see ``_parse_query_patterns``) and looks up matching
    kg_nodes, e.g.:
        "Show all designs referencing AWS VPCs that contain SECRET-classified tables"

    Args:
        query_text: Natural-language query string (keyword/pattern matched).
        db_path: Optional database path override.

    Returns:
        Dict with matched entities and the detected patterns. The payload
        carries ``engine="heuristic_keyword_matcher"`` and ``sparql=False`` so
        callers never mistake this for a SPARQL query result.
    """
    start_ms = int(time.time() * 1000)
    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)

        # Parse query heuristically
        qlower = query_text.lower()
        patterns = _parse_query_patterns(qlower)

        results: List[Dict[str, Any]] = []

        # Pattern 1: "designs referencing X that contain Y-classified Z"
        if patterns.get("designs_referencing") and patterns.get("classified"):
            ref_class = patterns["designs_referencing"]
            classification = patterns["classified"]
            target_type = patterns.get("target_type", "")

            # Find canonical class for ref_class
            canon_ref = _resolve_class_alias(conn, ref_class)

            # Find all subclasses of the target type (or ref_class if no target)
            search_classes = _expand_subclasses(conn, canon_ref)
            if target_type:
                search_classes |= _expand_subclasses(conn, _resolve_class_alias(conn, target_type))

            # Query kg_nodes for designs that reference nodes of those classes
            # We use a heuristic: kg_nodes with entity_type matching class labels
            etypes = [c.split(":")[-1].lower() for c in search_classes]
            etypes += [c.replace(":", "_").lower() for c in search_classes]

            ph = ",".join("%s" for _ in etypes)
            rows = conn.execute(
                f"SELECT id, graph_id, label, entity_type, properties FROM kg_nodes "  # nosec B608 -- internal constants
                f"WHERE LOWER(entity_type) IN ({ph})",
                etypes,
            ).fetchall()

            for r in rows:
                props = json.loads(r["properties"] or "{}")
                design_id = props.get("design_id", r["graph_id"])
                design_name = props.get("design_name", "")
                classification_match = _check_classification(conn, r["id"], classification)
                results.append({
                    "design_id": design_id,
                    "design_name": design_name,
                    "node_id": r["id"],
                    "node_label": r["label"],
                    "node_type": r["entity_type"],
                    "classification_match": classification_match,
                    "properties": props,
                })

            # Filter to only classification-matched if classification specified
            if classification:
                results = [r for r in results if r["classification_match"]]

        # Pattern 2: generic "all X" => list instances from kg_nodes by entity_type
        elif patterns.get("list_all"):
            target = patterns["list_all"]
            canon = _resolve_class_alias(conn, target)
            search_classes = _expand_subclasses(conn, canon)
            etypes = [c.split(":")[-1].lower() for c in search_classes]
            etypes += [c.replace(":", "_").lower() for c in search_classes]

            ph = ",".join("%s" for _ in etypes)
            rows = conn.execute(
                f"SELECT id, graph_id, label, entity_type, properties FROM kg_nodes "  # nosec B608 -- internal constants
                f"WHERE LOWER(entity_type) IN ({ph}) LIMIT 100",
                etypes,
            ).fetchall()
            for r in rows:
                results.append({
                    "node_id": r["id"],
                    "graph_id": r["graph_id"],
                    "label": r["label"],
                    "entity_type": r["entity_type"],
                    "properties": json.loads(r["properties"] or "{}"),
                })

        # Pattern 3: "equivalent classes of X"
        elif patterns.get("equivalents_of"):
            target = patterns["equivalents_of"]
            canon = _resolve_class_alias(conn, target)
            eqs = _find_equivalents(conn, canon)
            results = [{"class_id": c, "canonical": canon, "type": "equivalent"} for c in eqs]

        elapsed_ms = int(time.time() * 1000) - start_ms

        return {
            "status": "ok",
            "query": query_text,
            "engine": "heuristic_keyword_matcher",
            "sparql": False,
            "engine_note": "Keyword/pattern matching over ontology terms — not a SPARQL evaluator.",
            "patterns_detected": patterns,
            "results_count": len(results),
            "results": results[:50],
            "query_ms": elapsed_ms,
        }
    finally:
        conn.close()


def _parse_query_patterns(qlower: str) -> Dict[str, str]:
    """Heuristic keyword/substring parser for natural language queries (not SPARQL)."""
    patterns: Dict[str, str] = {}

    # designs referencing X
    if "design" in qlower and ("referenc" in qlower or "contain" in qlower or "with" in qlower):
        # Try to extract the referenced class
        class_aliases = {
            "aws vpc": "network:AWSVPC",
            "cloud vpc": "data:CloudVPC",
            "vpc": "network:AWSVPC",
            "subnet": "network:Subnet",
            "table": "data:RelationalTable",
            "database": "data:DataStore",
            "firewall": "network:Firewall",
            "load balancer": "network:LoadBalancer",
            "instance": "infra:ComputeInstance",
            "container": "infra:Container",
            "bucket": "infra:StorageBucket",
            "datalake": "data:DataLake",
            "pipeline": "data:DataPipeline",
        }
        for alias, canon in class_aliases.items():
            if alias in qlower:
                patterns["designs_referencing"] = canon
                break

    # classification
    class_levels = ["secret", "cui", "top secret", "unclassified", "confidential"]
    for cl in class_levels:
        if cl in qlower:
            patterns["classified"] = cl.upper().replace(" ", "_")
            break

    # target type after "that contain" or "with"
    if "table" in qlower:
        patterns["target_type"] = "data:RelationalTable"
    elif "database" in qlower:
        patterns["target_type"] = "data:DataStore"
    elif "instance" in qlower:
        patterns["target_type"] = "infra:ComputeInstance"

    # "all X" or "list X"
    if qlower.startswith("all ") or qlower.startswith("list ") or qlower.startswith("show all "):
        for alias, canon in {
            "classes": "core:Entity",
            "properties": "core:Concept",
            "designs": "core:Design",
            "vpcs": "network:AWSVPC",
            "tables": "data:RelationalTable",
            "controls": "security:Control",
        }.items():
            if alias in qlower:
                patterns["list_all"] = canon
                break

    # "equivalent classes of X"
    if "equivalent" in qlower:
        for alias, canon in {
            "aws vpc": "network:AWSVPC",
            "cloud vpc": "data:CloudVPC",
            "subnet": "network:Subnet",
        }.items():
            if alias in qlower:
                patterns["equivalents_of"] = canon
                break

    return patterns


def _resolve_class_alias(conn, alias: str) -> str:
    """Resolve a class label or partial ID to full ontology class ID."""
    if ":" in alias:
        # Already namespaced
        row = conn.execute(
            "SELECT id FROM ontology_classes WHERE id = %s", (alias,)
        ).fetchone()
        if row:
            return row["id"]
    # Try label match
    row = conn.execute(
        "SELECT id FROM ontology_classes WHERE LOWER(label) = %s", (alias.lower(),)
    ).fetchone()
    if row:
        return row["id"]
    # Try partial match
    row = conn.execute(
        "SELECT id FROM ontology_classes WHERE LOWER(id) LIKE %s", (f"%{alias.lower()}%",)
    ).fetchone()
    if row:
        return row["id"]
    return alias


def _expand_subclasses(conn, class_id: str) -> Set[str]:
    """Return the set of class IDs that are subclasses of class_id (including itself)."""
    result: Set[str] = {class_id}
    rows = conn.execute(
        "SELECT subclass FROM ontology_subclass_closure WHERE superclass = %s", (class_id,)
    ).fetchall()
    for r in rows:
        result.add(r["subclass"])
    return result


def _check_classification(conn, node_id: str, classification: str) -> bool:
    """Heuristic: check if a kg_node's properties contain the classification."""
    row = conn.execute(
        "SELECT properties FROM kg_nodes WHERE id = %s", (node_id,)
    ).fetchone()
    if not row:
        return False
    props = json.loads(row["properties"] or "{}").get("config", {})
    if isinstance(props, dict):
        cls_val = str(props.get("classification", "")).lower()
        return classification.lower() in cls_val
    return classification.lower() in str(props).lower()


def _find_equivalents(conn, class_id: str) -> List[str]:
    """Find all classes equivalent to class_id."""
    canon_row = conn.execute(
        "SELECT canonical_id FROM ontology_classes WHERE id = %s", (class_id,)
    ).fetchone()
    if not canon_row:
        return [class_id]
    canon = canon_row["canonical_id"]
    rows = conn.execute(
        "SELECT id FROM ontology_classes WHERE canonical_id = %s", (canon,)
    ).fetchall()
    return [r["id"] for r in rows]


# =========================================================================
# 3. LIST DOMAINS / CLASSES
# =========================================================================


def list_domains(db_path: Optional[str] = None) -> Dict[str, Any]:
    """List all registered ontology domains and their class counts."""
    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT domain, COUNT(*) as cnt FROM ontology_classes GROUP BY domain"
        ).fetchall()
        return {
            "status": "ok",
            "domains": [{"domain": r["domain"], "class_count": r["cnt"]} for r in rows],
        }
    finally:
        conn.close()


def list_classes(domain: Optional[str] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """List ontology classes, optionally filtered by domain."""
    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)
        if domain:
            rows = conn.execute(
                "SELECT id, domain, label, canonical_id FROM ontology_classes WHERE domain = %s",
                (domain,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, domain, label, canonical_id FROM ontology_classes"
            ).fetchall()
        return {
            "status": "ok",
            "classes": [
                {
                    "id": r["id"],
                    "domain": r["domain"],
                    "label": r["label"],
                    "canonical_id": r["canonical_id"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


# =========================================================================
# 4. INTEGRATION WITH KNOWLEDGE GRAPH FEDERATION
# =========================================================================


def integrate_with_kg_federation(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Sync ontology federation state with tools.knowledge_graph.federation.

    Reads the current unified ontology and pushes alignment assertions into
    the kg_graphs metadata via the existing cross-project federation store.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with sync status and stored assertions.
    """
    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)

        # Load alignments from ontology tables
        alignments = conn.execute(
            "SELECT source, target, assertion FROM ontology_alignments"
        ).fetchall()
        assertion_list = [
            {"source": r["source"], "target": r["target"], "assertion": r["assertion"]}
            for r in alignments
        ]

        # Ensure kg_graphs has a graph for the unified ontology
        unified_graph_id = "icdev-core-ontology"
        now = _now()
        meta = json.dumps({
            "graph_type": "ontology",
            "ontology_assertions": assertion_list,
            "source": "ontology_federation.py",
            "federated": True,
        })

        existing = conn.execute(
            "SELECT id FROM kg_graphs WHERE id = %s", (unified_graph_id,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE kg_graphs SET metadata = %s, updated_at = %s WHERE id = %s",
                (meta, now, unified_graph_id),
            )
        else:
            conn.execute(
                "INSERT INTO kg_graphs (id, project_id, name, description, entity_count, edge_count, metadata, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    unified_graph_id,
                    "ontology",
                    "ICDEV Core Ontology",
                    "Unified cross-canvas ontology federation graph",
                    0,
                    0,
                    meta,
                    now,
                    now,
                ),
            )
        conn.commit()

        # Also sync to kg_nodes for each canonical class (so RAG can find them)
        canon_classes = conn.execute(
            "SELECT DISTINCT canonical_id, label FROM ontology_classes"
        ).fetchall()
        node_rows = []
        for r in canon_classes:
            node_id = f"ontology:{r['canonical_id']}"
            node_rows.append((
                node_id,
                unified_graph_id,
                r["label"],
                "ontology_class",
                json.dumps({"canonical_id": r["canonical_id"]}),
                now,
            ))

        conn.execute(
            "DELETE FROM kg_nodes WHERE id LIKE 'ontology:%'",
        )
        conn.executemany(
            "INSERT OR REPLACE INTO kg_nodes (id, graph_id, label, entity_type, properties, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            node_rows,
        )

        # Update entity_count
        conn.execute(
            "UPDATE kg_graphs SET entity_count = %s WHERE id = %s",
            (len(node_rows), unified_graph_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "graph_id": unified_graph_id,
            "alignments_synced": len(assertion_list),
            "ontology_nodes_synced": len(node_rows),
        }
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-canvas Ontology Federation")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build-federation", action="store_true", help="Build unified ontology")
    group.add_argument("--query", metavar="TEXT", help="Heuristic keyword/pattern query (not SPARQL)")
    group.add_argument("--list-domains", action="store_true", help="List ontology domains")
    group.add_argument("--list-classes", action="store_true", help="List ontology classes")
    group.add_argument("--integrate-kg", action="store_true", help="Sync with KG federation")
    group.add_argument("--resolve", metavar="CLASS_ID", help="Resolve equivalents for a class")

    parser.add_argument("--domain", default=None, help="Filter classes by domain")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--db-path", default=None, help="Override database path")
    parser.add_argument("--ttl-dir", default=None,
                        help="Directory of *.ttl files (default: the owning parent's args/ontology)")
    parser.add_argument("--no-builtin", action="store_true",
                        help="Load ONLY the TTL files; skip the IT domain ontologies hard-coded in this module")

    args = parser.parse_args()

    result: Dict[str, Any] = {}

    if args.build_federation:
        result = build_federation(
            db_path=args.db_path,
            ttl_dir=Path(args.ttl_dir) if args.ttl_dir else None,
            include_builtin=not args.no_builtin,
        )
        # Auto-sync to KG federation after build
        if result.get("status") == "ok":
            sync = integrate_with_kg_federation(db_path=args.db_path)
            result["kg_sync"] = sync
    elif args.query:
        result = query_federation(query_text=args.query, db_path=args.db_path)
    elif args.list_domains:
        result = list_domains(db_path=args.db_path)
    elif args.list_classes:
        result = list_classes(domain=args.domain, db_path=args.db_path)
    elif args.integrate_kg:
        result = integrate_with_kg_federation(db_path=args.db_path)
    elif args.resolve:
        conn = _get_db(args.db_path)
        try:
            _ensure_tables(conn)
            cid = _resolve_class_alias(conn, args.resolve)
            eqs = _find_equivalents(conn, cid)
            result = {
                "status": "ok",
                "class_id": args.resolve,
                "resolved_id": cid,
                "equivalents": eqs,
            }
        finally:
            conn.close()

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human_readable(result, args)


def _print_human_readable(result: Dict[str, Any], args) -> None:
    status = result.get("status", "?")
    if status == "error":
        print(f"ERROR: {result.get('message', 'Unknown error')}")
        sys.exit(1)

    if args.build_federation:
        print("\n=== ICDEV Core Ontology Federation Built ===\n")
        print(f"  Classes:          {result.get('classes_total', 0)}")
        print(f"  Properties:       {result.get('properties_total', 0)}")
        print(f"  Alignments:       {result.get('alignments_total', 0)}")
        print(f"  Closure pairs:    {result.get('closure_pairs', 0)}")
        print(f"  Canonical groups: {result.get('canonical_groups', 0)}")
        for g in result.get("canonical_merged_classes", []):
            print(f"    - {g['canonical']}: {g['merged_count']} classes merged")
        kg = result.get("kg_sync", {})
        if kg:
            print(f"\n  KG sync: {kg.get('ontology_nodes_synced', 0)} nodes -> graph {kg.get('graph_id', '?')}")

    elif args.query:
        print(f'\n=== Query: "{args.query}" ===\n')
        print(f"  Patterns: {result.get('patterns_detected', {})}")
        print(f"  Results:  {result.get('results_count', 0)}\n")
        for i, r in enumerate(result.get("results", [])[:20], 1):
            label = r.get("node_label") or r.get("label") or r.get("design_id") or "?"
            print(f"  {i}. {label}")

    elif args.list_domains:
        print("\n=== Ontology Domains ===\n")
        for d in result.get("domains", []):
            print(f"  {d['domain']}: {d['class_count']} classes")

    elif args.list_classes:
        print("\n=== Ontology Classes ===\n")
        for c in result.get("classes", []):
            canon = f" -> {c['canonical_id']}" if c["canonical_id"] != c["id"] else ""
            print(f"  {c['id']} ({c['domain']}) — {c['label']}{canon}")

    elif args.integrate_kg:
        print("\n=== KG Federation Integration ===\n")
        print(f"  Graph:     {result.get('graph_id', '?')}")
        print(f"  Alignments: {result.get('alignments_synced', 0)}")
        print(f"  Nodes:     {result.get('ontology_nodes_synced', 0)}")

    elif args.resolve:
        print(f"\n=== Equivalents for {result.get('class_id', '?')} ===\n")
        print(f"  Resolved: {result.get('resolved_id', '?')}")
        for eq in result.get("equivalents", []):
            print(f"    - {eq}")


if __name__ == "__main__":
    main()
