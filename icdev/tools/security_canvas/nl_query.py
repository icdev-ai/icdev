#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Natural Language Query Engine for the SDC Compliance Knowledge Graph.

Allows users to ask questions like:
  - "What NIST controls address spoofing threats?"
  - "Which NIST controls does a firewall implement?"
  - "Does AC-2 satisfy FedRAMP Moderate?"
  - "What's the gap for CMMC Level 2?"
  - "How does a KMS relate to information disclosure?"
  - "What threats lead to elevation of privilege?"

Architecture:
  1. SDCComplianceGraph — loads sdc-compliance-kg from DB into memory
  2. classify_query()   — intent detection via regex patterns
  3. Deterministic handlers — graph traversal (no LLM required)
  4. LLMQueryEngine     — governed LLMRouter fallback (via llm_adapter) for
                          open-ended questions
  5. answer_query()     — public API: (question, design_id=None) → dict

Usage:
    python tools/security_canvas/nl_query.py --query "What controls address spoofing?" --json
    python tools/security_canvas/nl_query.py --build --json
    python tools/security_canvas/nl_query.py --query "..." --design-id <id> --json
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.security_canvas.nl_query")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# STRIDE metadata (kept local to avoid circular imports)
_STRIDE_LABELS = {
    "S": "Spoofing",
    "T": "Tampering",
    "R": "Repudiation",
    "I": "Information Disclosure",
    "D": "Denial of Service",
    "E": "Elevation of Privilege",
}

_SDC_CTRL_LABELS = {
    "ctrl-firewall": "Firewall / WAF",
    "ctrl-idp": "IdP / MFA",
    "ctrl-kms": "KMS / HSM",
    "ctrl-siem": "SIEM / SOC",
    "ctrl-ids": "IDS / IPS",
    "ctrl-pam": "PAM",
    "ctrl-scanner": "Vulnerability Scanner",
    "ctrl-encryption": "Encryptor",
}

_SDC_THREAT_LABELS = {
    "threat-actor": "Threat Actor",
    "threat-malware": "Malware",
    "threat-phishing": "Phishing",
    "threat-exploit": "Exploit",
    "threat-dos": "DoS / DDoS",
    "threat-supply": "Supply Chain Attack",
    "threat-insider": "Insider Threat",
}

_FRAMEWORK_ALIASES: Dict[str, List[str]] = {
    "fedramp": ["fedramp_moderate", "fedramp_high"],
    "fedramp moderate": ["fedramp_moderate"],
    "fedramp high": ["fedramp_high"],
    "fedramp_moderate": ["fedramp_moderate"],
    "fedramp_high": ["fedramp_high"],
    "cmmc": ["cmmc_level_2", "cmmc_level_3"],
    "cmmc level 2": ["cmmc_level_2"],
    "cmmc level 3": ["cmmc_level_3"],
    "cmmc_level_2": ["cmmc_level_2"],
    "cmmc_level_3": ["cmmc_level_3"],
    "nist 800-171": ["nist_800_171"],
    "nist_800_171": ["nist_800_171"],
    "800-171": ["nist_800_171"],
    "il4": ["il4_required"],
    "il5": ["il5_required"],
    "il6": ["il6_required"],
}

_FRAMEWORK_LABELS = {
    "fedramp_moderate": "FedRAMP Moderate",
    "fedramp_high": "FedRAMP High",
    "nist_800_171": "NIST 800-171",
    "cmmc_level_2": "CMMC Level 2",
    "cmmc_level_3": "CMMC Level 3",
    "il4_required": "DoD IL4",
    "il5_required": "DoD IL5",
    "il6_required": "DoD IL6",
}


# ---------------------------------------------------------------------------
# 1. SDC Compliance Graph (in-memory representation for traversal)
# ---------------------------------------------------------------------------


class SDCComplianceGraph:
    """In-memory representation of the sdc-compliance-kg for fast traversal.

    Loads nodes and edges from the kg_graphs/kg_nodes/kg_edges tables.
    Provides BFS traversal, label-based lookup, and type-based filtering.
    """

    def __init__(
        self,
        conn: Any,
        graph_id: str,
    ) -> None:
        self._graph_id = graph_id
        self._nodes: Dict[str, Dict[str, Any]] = {}  # id → {label, entity_type, properties}
        self._adj: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        # label_index: lowercase(label) → [node_ids]
        self._label_index: Dict[str, List[str]] = defaultdict(list)
        # type_index: entity_type → [node_ids]
        self._type_index: Dict[str, List[str]] = defaultdict(list)
        self._load(conn)

    def _load(self, conn: Any) -> None:
        # Load nodes
        rows = conn.execute(
            "SELECT id, label, entity_type, properties FROM kg_nodes WHERE graph_id = %s",
            (self._graph_id,),
        ).fetchall()
        for row in rows:
            d = dict(row)
            props = json.loads(d.get("properties") or "{}")
            nid = d["id"]
            self._nodes[nid] = {
                "id": nid,
                "label": d["label"],
                "entity_type": d["entity_type"],
                "properties": props,
            }
            self._label_index[d["label"].lower()].append(nid)
            self._type_index[d["entity_type"]].append(nid)

        # Load edges (store as bidirectional)
        edge_rows = conn.execute(
            "SELECT source_id, target_id, relationship, weight, properties FROM kg_edges WHERE graph_id = %s",
            (self._graph_id,),
        ).fetchall()
        for row in edge_rows:
            d = dict(row)
            src, tgt, rel, wt = d["source_id"], d["target_id"], d["relationship"], d["weight"]
            self._adj[src].append({"target": tgt, "rel": rel, "weight": wt, "direction": "out"})
            self._adj[tgt].append({"target": src, "rel": rel, "weight": wt, "direction": "in"})

    # -- Look-ups ------------------------------------------------------------

    def find_nodes(
        self,
        fragment: str,
        entity_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return nodes whose label contains fragment (case-insensitive)."""
        frag = fragment.lower()
        results = []
        seen: Set[str] = set()
        for lbl, nids in self._label_index.items():
            if frag in lbl:
                for nid in nids:
                    if nid not in seen:
                        n = self._nodes[nid]
                        if entity_type is None or n["entity_type"] == entity_type:
                            results.append(n)
                        seen.add(nid)
        return results

    def nodes_by_type(self, entity_type: str) -> List[Dict[str, Any]]:
        return [self._nodes[nid] for nid in self._type_index.get(entity_type, []) if nid in self._nodes]

    def node(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self._nodes.get(node_id)

    def neighbors(
        self,
        node_id: str,
        rel_filter: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return neighbor nodes with edge metadata."""
        results = []
        for nbr in self._adj.get(node_id, []):
            if rel_filter and nbr["rel"] != rel_filter:
                continue
            if direction and nbr["direction"] != direction:
                continue
            target = self._nodes.get(nbr["target"])
            if target:
                results.append({**target, "_rel": nbr["rel"], "_weight": nbr["weight"]})
        return results

    def bfs_to_type(
        self,
        source_id: str,
        target_type: str,
        max_depth: int = 5,
    ) -> List[Dict[str, Any]]:
        """BFS from source_id; collect all reachable nodes of target_type."""
        visited: Set[str] = {source_id}
        queue: deque = deque([(source_id, 0)])
        found: List[Dict[str, Any]] = []
        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for nbr in self._adj.get(current_id, []):
                nid = nbr["target"]
                if nid in visited:
                    continue
                visited.add(nid)
                n = self._nodes.get(nid)
                if not n:
                    continue
                if n["entity_type"] == target_type:
                    found.append({**n, "_via": nbr["rel"]})
                queue.append((nid, depth + 1))
        return found

    def bfs_path(
        self,
        source_id: str,
        target_ids: Set[str],
        max_depth: int = 6,
    ) -> Optional[tuple]:
        """BFS shortest path from source to any target. Returns (path_ids, edges) or None."""
        visited: Set[str] = {source_id}
        queue: deque = deque([(source_id, [source_id], [])])
        while queue:
            curr, path, edges = queue.popleft()
            if len(path) > max_depth:
                continue
            if curr in target_ids:
                return path, edges
            for nbr in self._adj.get(curr, []):
                nid = nbr["target"]
                if nid not in visited:
                    visited.add(nid)
                    queue.append(
                        (
                            nid,
                            path + [nid],
                            edges + [{"from": curr, "to": nid, "rel": nbr["rel"], "weight": nbr["weight"]}],
                        )
                    )
        return None

    def compact_context(self, max_nodes: int = 80) -> str:
        """Compact human-readable description for LLM context."""
        lines: List[str] = []
        # Group by type
        for etype in ["stride_category", "sdc_control_type", "sdc_threat_type", "framework", "nist_family"]:
            nodes = self.nodes_by_type(etype)
            if not nodes:
                continue
            lines.append(f"\n## {etype.replace('_', ' ').title()} nodes ({len(nodes)}):")
            for n in nodes[:20]:
                lines.append(f"  - {n['label']}")

        ctrl_nodes = self.nodes_by_type("nist_control")
        lines.append(f"\n## NIST controls: {len(ctrl_nodes)} total")
        return "\n".join(lines)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(v) for v in self._adj.values()) // 2


# ---------------------------------------------------------------------------
# Graph loader (singleton per session)
# ---------------------------------------------------------------------------

_GRAPH_CACHE: Optional[SDCComplianceGraph] = None


def _get_graph(conn: Any) -> Optional[SDCComplianceGraph]:
    """Load sdc-compliance-kg from DB. Returns None if not yet built."""
    global _GRAPH_CACHE
    if _GRAPH_CACHE is not None:
        return _GRAPH_CACHE

    row = conn.execute(
        "SELECT id FROM kg_graphs WHERE name = 'sdc-compliance-kg' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None

    graph_id = dict(row)["id"]
    _GRAPH_CACHE = SDCComplianceGraph(conn, graph_id)
    return _GRAPH_CACHE


def _ensure_graph(conn: Any) -> Optional[SDCComplianceGraph]:
    """Load or auto-build the KG, then return it."""
    from tools.security_canvas.compliance_kg import build_sdc_kg

    row = conn.execute("SELECT id FROM kg_graphs WHERE name = 'sdc-compliance-kg' LIMIT 1").fetchone()
    if not row:
        logger.info("sdc-compliance-kg not found — building now...")
        build_sdc_kg()
    return _get_graph(conn)


# ---------------------------------------------------------------------------
# 2. Query Classifier
# ---------------------------------------------------------------------------

_STRIDE_PATTERNS = [
    r"\b(stride|spoofing|tampering|repudiation|information.?disclosure|"
    r"denial.?of.?service|elevation.?of.?privilege)\b",
]
_CONTROL_IMPLEMENTS_PATTERNS = [
    r"\b(firewall|waf|kms|hsm|siem|soc|idp|mfa|ids|ips|pam|"
    r"vulnerability.?scanner|encryptor|implement|provide|cover|"
    r"ctrl-|what.+(does|do).+(ctrl|control|tool))\b",
]
_FRAMEWORK_PATTERNS = [
    r"\b(fedramp|cmmc|il4|il5|il6|nist.?800.?171|800-171|satisfy|satisfies|"
    r"framework|require|required.?for|authorized|ato)\b",
]
_THREAT_PATTERNS = [
    r"\b(malware|phishing|exploit|supply.?chain|insider.?threat|"
    r"threat.?actor|dos|ddos|ransomware|threat)\b",
]
_GAP_PATTERNS = [
    r"\b(gap|miss|lack|need|not.?cover|incomplete|what.+(is|are).+missing|"
    r"not.+implement|what.+need)\b",
]
_PATH_PATTERNS = [
    r"\b(path|relate|relation|connect|how.+connect|link|bridge|"
    r"between|from.+to)\b",
]
_CROSSWALK_PATTERNS = [
    r"\b([A-Z]{2}-\d+)\b",  # NIST control ID + framework mention
]
_INVENTORY_PATTERNS = [
    r"\b(how.?many|list.?all|show.?all|all.+(nist|control|framework|stride|"
    r"threat)|count|total|inventory)\b",
]


def classify_query(question: str) -> str:
    """Classify the query intent.

    Returns one of:
        stride_to_nist      — STRIDE category → NIST controls
        control_implements  — SDC control type → NIST controls
        framework_crosswalk — NIST control ↔ framework satisfaction
        threat_coverage     — SDC threat type → STRIDE → NIST
        gap_analysis        — missing controls for a framework
        path_query          — BFS path between two nodes
        inventory           — list/count nodes by type
        general             — LLM fallback
    """
    q = question.lower()

    # Detect NIST control ID pattern early for crosswalk
    ctrl_match = re.search(r"\b([a-z]{2}-\d+)\b", q)
    fw_mention = any(re.search(p, q) for p in [r"\b(fedramp|cmmc|il4|il5|il6|800-171)\b"])
    if ctrl_match and fw_mention:
        return "framework_crosswalk"

    if any(re.search(p, q) for p in _GAP_PATTERNS):
        return "gap_analysis"
    if any(re.search(p, q) for p in _STRIDE_PATTERNS):
        return "stride_to_nist"
    if any(re.search(p, q) for p in _THREAT_PATTERNS):
        return "threat_coverage"
    if any(re.search(p, q) for p in _CONTROL_IMPLEMENTS_PATTERNS):
        return "control_implements"
    if any(re.search(p, q) for p in _FRAMEWORK_PATTERNS):
        return "framework_crosswalk"
    if any(re.search(p, q) for p in _PATH_PATTERNS):
        return "path_query"
    if any(re.search(p, q) for p in _INVENTORY_PATTERNS):
        return "inventory"
    return "general"


# ---------------------------------------------------------------------------
# 3. Deterministic Query Handlers
# ---------------------------------------------------------------------------


def _extract_stride_code(question: str) -> Optional[str]:
    """Extract STRIDE code or name from question."""
    q = question.lower()
    stride_kw = {
        "spoofing": "S",
        "tamper": "T",
        "repudiation": "R",
        "information disclosure": "I",
        "info disclosure": "I",
        "denial of service": "D",
        "dos": "D",
        "ddos": "D",
        "elevation of privilege": "E",
        "privilege escalation": "E",
    }
    for kw, code in stride_kw.items():
        if kw in q:
            return code
    # Single-letter: "STRIDE category S" or just " S "
    m = re.search(r"\bstride:?\s*([strideSTRIDE])\b", question)
    if m:
        return m.group(1).upper()
    return None


def _extract_sdc_ctrl_type(question: str) -> Optional[str]:
    """Extract SDC control type from question."""
    q = question.lower()
    kw_map = {
        "firewall": "ctrl-firewall",
        "waf": "ctrl-firewall",
        "idp": "ctrl-idp",
        "mfa": "ctrl-idp",
        "identity provider": "ctrl-idp",
        "kms": "ctrl-kms",
        "hsm": "ctrl-kms",
        "key management": "ctrl-kms",
        "siem": "ctrl-siem",
        "soc": "ctrl-siem",
        "ids": "ctrl-ids",
        "ips": "ctrl-ids",
        "intrusion": "ctrl-ids",
        "pam": "ctrl-pam",
        "privileged access": "ctrl-pam",
        "vulnerability scanner": "ctrl-scanner",
        "scanner": "ctrl-scanner",
        "encrypt": "ctrl-encryption",
        "encryptor": "ctrl-encryption",
    }
    for kw, ctrl in kw_map.items():
        if kw in q:
            return ctrl
    return None


def _extract_sdc_threat_type(question: str) -> Optional[str]:
    """Extract SDC threat type from question."""
    q = question.lower()
    kw_map = {
        "malware": "threat-malware",
        "ransomware": "threat-malware",
        "virus": "threat-malware",
        "phishing": "threat-phishing",
        "social engineering": "threat-phishing",
        "exploit": "threat-exploit",
        "zero-day": "threat-exploit",
        "zero day": "threat-exploit",
        "supply chain": "threat-supply",
        "insider": "threat-insider",
        "threat actor": "threat-actor",
        "dos": "threat-dos",
        "ddos": "threat-dos",
        "denial of service": "threat-dos",
    }
    for kw, ttype in kw_map.items():
        if kw in q:
            return ttype
    return None


def _extract_framework(question: str) -> Optional[str]:
    """Extract framework name from question."""
    q = question.lower()
    # Order matters — check more specific first
    checks = [
        ("fedramp high", "fedramp_high"),
        ("fedramp moderate", "fedramp_moderate"),
        ("fedramp mod", "fedramp_moderate"),
        ("fedramp", "fedramp_moderate"),
        ("cmmc level 2", "cmmc_level_2"),
        ("cmmc level 3", "cmmc_level_3"),
        ("cmmc l2", "cmmc_level_2"),
        ("cmmc l3", "cmmc_level_3"),
        ("cmmc", "cmmc_level_2"),
        ("nist 800-171", "nist_800_171"),
        ("800-171", "nist_800_171"),
        ("il6", "il6_required"),
        ("il5", "il5_required"),
        ("il4", "il4_required"),
    ]
    for kw, fw_key in checks:
        if kw in q:
            return fw_key
    return None


def _handle_stride_to_nist(
    question: str,
    graph: SDCComplianceGraph,
) -> Dict[str, Any]:
    """Handle: What NIST controls address <STRIDE category>?"""
    code = _extract_stride_code(question)

    if not code:
        # Return all STRIDE categories
        results = []
        for stride_node in graph.nodes_by_type("stride_category"):
            ctrl_neighbors = graph.neighbors(stride_node["id"], rel_filter="maps_to", direction="out")
            results.append(
                {
                    "stride": stride_node["properties"].get("name", ""),
                    "code": stride_node["properties"].get("code", ""),
                    "nist_controls": [
                        {
                            "control_id": n["properties"].get("control_id", ""),
                            "title": n["properties"].get("title", ""),
                            "family": n["properties"].get("family", ""),
                        }
                        for n in ctrl_neighbors
                    ],
                }
            )
        return {
            "intent": "stride_to_nist",
            "answer": "NIST 800-53 controls mapped to each STRIDE category:",
            "stride_categories": results,
        }

    # Find specific STRIDE node
    stride_nodes = graph.find_nodes(f"STRIDE:{_STRIDE_LABELS.get(code, code)}", entity_type="stride_category")
    if not stride_nodes:
        # Try by code in properties
        for n in graph.nodes_by_type("stride_category"):
            if n["properties"].get("code") == code:
                stride_nodes = [n]
                break

    if not stride_nodes:
        return {
            "intent": "stride_to_nist",
            "answer": f"STRIDE category '{code}' not found in graph.",
            "error": f"Unknown STRIDE code: {code}",
        }

    stride_node = stride_nodes[0]
    ctrl_neighbors = graph.neighbors(stride_node["id"], rel_filter="maps_to", direction="out")
    nist_controls = [
        {
            "control_id": n["properties"].get("control_id", ""),
            "title": n["properties"].get("title", ""),
            "family": n["properties"].get("family", ""),
            "priority": n["properties"].get("priority", ""),
        }
        for n in ctrl_neighbors
    ]

    # Also get framework coverage via BFS
    fw_reach = graph.bfs_to_type(stride_node["id"], "framework", max_depth=3)
    frameworks = list({n["label"] for n in fw_reach})

    stride_name = stride_node["properties"].get("name", code)
    answer = (
        f"The STRIDE category '{stride_name}' maps to "
        f"{len(nist_controls)} NIST 800-53 controls: "
        f"{', '.join(c['control_id'] for c in nist_controls)}. "
        f"These controls satisfy: {', '.join(frameworks[:5])}."
        if frameworks
        else f"The STRIDE category '{stride_name}' maps to "
        f"{len(nist_controls)} NIST 800-53 controls: "
        f"{', '.join(c['control_id'] for c in nist_controls)}."
    )

    return {
        "intent": "stride_to_nist",
        "stride": {"code": code, "name": stride_name, "description": stride_node["properties"].get("description", "")},
        "nist_controls": nist_controls,
        "framework_reach": frameworks,
        "answer": answer,
    }


def _handle_control_implements(
    question: str,
    graph: SDCComplianceGraph,
) -> Dict[str, Any]:
    """Handle: What NIST controls does a <SDC control type> implement?"""
    ctrl_type = _extract_sdc_ctrl_type(question)

    if not ctrl_type:
        # List all SDC control types and their NIST control counts
        results = []
        for sdc_node in graph.nodes_by_type("sdc_control_type"):
            nist_nbrs = graph.neighbors(sdc_node["id"], rel_filter="implements", direction="out")
            results.append(
                {
                    "sdc_type": sdc_node["properties"].get("type", ""),
                    "label": sdc_node["properties"].get("label", ""),
                    "nist_control_count": len(nist_nbrs),
                    "nist_controls": [n["properties"].get("control_id", "") for n in nist_nbrs],
                }
            )
        return {
            "intent": "control_implements",
            "answer": "SDC palette controls and their NIST 800-53 mappings:",
            "sdc_controls": results,
        }

    sdc_nodes = graph.find_nodes(ctrl_type, entity_type="sdc_control_type")
    if not sdc_nodes:
        for n in graph.nodes_by_type("sdc_control_type"):
            if n["properties"].get("type") == ctrl_type:
                sdc_nodes = [n]
                break

    if not sdc_nodes:
        return {
            "intent": "control_implements",
            "answer": f"SDC control type '{ctrl_type}' not found in graph.",
            "error": f"Unknown SDC control type: {ctrl_type}",
        }

    sdc_node = sdc_nodes[0]
    nist_nbrs = graph.neighbors(sdc_node["id"], rel_filter="implements", direction="out")
    nist_controls = [
        {
            "control_id": n["properties"].get("control_id", ""),
            "title": n["properties"].get("title", ""),
            "family": n["properties"].get("family", ""),
            "priority": n["properties"].get("priority", ""),
        }
        for n in nist_nbrs
    ]

    # BFS to frameworks
    fw_reach = graph.bfs_to_type(sdc_node["id"], "framework", max_depth=3)
    frameworks = list({n["label"] for n in fw_reach})

    ctrl_label = sdc_node["properties"].get("label", ctrl_type)
    answer = (
        f"A '{ctrl_label}' implements {len(nist_controls)} NIST 800-53 controls: "
        f"{', '.join(c['control_id'] for c in nist_controls)}. "
        f"Together these satisfy requirements in: {', '.join(frameworks[:6])}."
        if frameworks
        else f"A '{ctrl_label}' implements {len(nist_controls)} NIST 800-53 controls: "
        f"{', '.join(c['control_id'] for c in nist_controls)}."
    )

    return {
        "intent": "control_implements",
        "sdc_control": {"type": ctrl_type, "label": ctrl_label},
        "nist_controls": nist_controls,
        "framework_reach": frameworks,
        "answer": answer,
    }


def _handle_framework_crosswalk(
    question: str,
    graph: SDCComplianceGraph,
) -> Dict[str, Any]:
    """Handle: Does <control> satisfy <framework>? / List controls for <framework>."""
    fw_key = _extract_framework(question)

    # Check if a specific NIST control ID is mentioned
    ctrl_match = re.search(r"\b([A-Z]{2}-\d+)\b", question.upper())
    ctrl_id = ctrl_match.group(1) if ctrl_match else None

    if ctrl_id and fw_key:
        # Point query: does AC-2 satisfy FedRAMP?
        ctrl_nodes = graph.find_nodes(ctrl_id, entity_type="nist_control")
        if not ctrl_nodes:
            return {
                "intent": "framework_crosswalk",
                "answer": f"NIST control '{ctrl_id}' not found in graph.",
            }
        ctrl_node = ctrl_nodes[0]
        fw_label = _FRAMEWORK_LABELS.get(fw_key, fw_key)

        # Check direct satisfies edge
        fw_nbrs = graph.neighbors(ctrl_node["id"], rel_filter="satisfies", direction="out")
        satisfies = any(n["properties"].get("key") == fw_key or fw_key in n["label"].lower() for n in fw_nbrs)

        answer = (
            f"Yes — NIST control {ctrl_id} satisfies {fw_label}."
            if satisfies
            else f"No — NIST control {ctrl_id} does not directly satisfy {fw_label}."
        )
        return {
            "intent": "framework_crosswalk",
            "control": ctrl_id,
            "framework": fw_label,
            "satisfies": satisfies,
            "answer": answer,
            "control_details": {
                "title": ctrl_node["properties"].get("title", ""),
                "priority": ctrl_node["properties"].get("priority", ""),
                "family": ctrl_node["properties"].get("family", ""),
            },
        }

    if fw_key:
        # List all controls satisfying this framework
        fw_label = _FRAMEWORK_LABELS.get(fw_key, fw_key)
        fw_nodes = graph.find_nodes(fw_label, entity_type="framework")
        if not fw_nodes:
            return {
                "intent": "framework_crosswalk",
                "answer": f"Framework '{fw_key}' not found in graph.",
            }
        fw_node = fw_nodes[0]

        # Controls → framework via satisfies (inbound to framework)
        ctrl_nbrs = graph.neighbors(fw_node["id"], rel_filter="satisfies")
        ctrl_nbrs = [n for n in ctrl_nbrs if n["entity_type"] == "nist_control"]

        # Group by family
        by_family: Dict[str, List[Dict]] = defaultdict(list)
        for n in ctrl_nbrs:
            fam = n["properties"].get("family", "??")
            by_family[fam].append(
                {
                    "control_id": n["properties"].get("control_id", ""),
                    "title": n["properties"].get("title", ""),
                    "priority": n["properties"].get("priority", ""),
                }
            )

        coverage = [
            {"family": fam, "controls": sorted(ctrls, key=lambda c: c["control_id"])}
            for fam, ctrls in sorted(by_family.items())
        ]

        return {
            "intent": "framework_crosswalk",
            "framework": fw_label,
            "total_controls": len(ctrl_nbrs),
            "family_count": len(by_family),
            "coverage_by_family": coverage,
            "answer": (
                f"{fw_label} requires {len(ctrl_nbrs)} NIST 800-53 controls across {len(by_family)} control families."
            ),
        }

    # No framework extracted — describe all frameworks
    fw_nodes = graph.nodes_by_type("framework")
    fw_summary = []
    for fw_node in fw_nodes:
        ctrl_nbrs = [
            n for n in graph.neighbors(fw_node["id"], rel_filter="satisfies") if n["entity_type"] == "nist_control"
        ]
        fw_summary.append({"framework": fw_node["label"], "control_count": len(ctrl_nbrs)})

    return {
        "intent": "framework_crosswalk",
        "answer": "Compliance frameworks in the SDC knowledge graph:",
        "frameworks": sorted(fw_summary, key=lambda f: f["control_count"], reverse=True),
    }


def _handle_threat_coverage(
    question: str,
    graph: SDCComplianceGraph,
) -> Dict[str, Any]:
    """Handle: What controls address <threat type>?"""
    threat_type = _extract_sdc_threat_type(question)

    if not threat_type:
        # List all threat types and their STRIDE codes
        results = []
        for tn in graph.nodes_by_type("sdc_threat_type"):
            stride_nbrs = graph.neighbors(tn["id"], rel_filter="represents", direction="out")
            results.append(
                {
                    "threat": tn["properties"].get("label", ""),
                    "type": tn["properties"].get("type", ""),
                    "stride_categories": [n["properties"].get("name", "") for n in stride_nbrs],
                }
            )
        return {
            "intent": "threat_coverage",
            "answer": "SDC threat types and their STRIDE category mappings:",
            "threats": results,
        }

    # Find threat node
    threat_nodes = graph.find_nodes(
        _SDC_THREAT_LABELS.get(threat_type, threat_type),
        entity_type="sdc_threat_type",
    )
    if not threat_nodes:
        for n in graph.nodes_by_type("sdc_threat_type"):
            if n["properties"].get("type") == threat_type:
                threat_nodes = [n]
                break

    if not threat_nodes:
        return {
            "intent": "threat_coverage",
            "answer": f"SDC threat type '{threat_type}' not found.",
        }

    threat_node = threat_nodes[0]
    # STRIDE categories this threat represents
    stride_nbrs = graph.neighbors(threat_node["id"], rel_filter="represents", direction="out")
    stride_names = [n["properties"].get("name", "") for n in stride_nbrs]

    # NIST controls reachable via represents → maps_to
    nist_via_stride: Dict[str, Dict] = {}
    for sn in stride_nbrs:
        ctrl_nbrs = graph.neighbors(sn["id"], rel_filter="maps_to", direction="out")
        for cn in ctrl_nbrs:
            ctrl_id = cn["properties"].get("control_id", "")
            if ctrl_id and ctrl_id not in nist_via_stride:
                nist_via_stride[ctrl_id] = {
                    "control_id": ctrl_id,
                    "title": cn["properties"].get("title", ""),
                    "family": cn["properties"].get("family", ""),
                    "priority": cn["properties"].get("priority", ""),
                }

    threat_label = threat_node["properties"].get("label", threat_type)
    nist_list = sorted(nist_via_stride.values(), key=lambda c: c["control_id"])

    answer = (
        f"The '{threat_label}' threat maps to STRIDE categories: "
        f"{', '.join(stride_names)}. "
        f"Mitigating NIST 800-53 controls: "
        f"{', '.join(c['control_id'] for c in nist_list[:8])}."
    )

    return {
        "intent": "threat_coverage",
        "threat": {"type": threat_type, "label": threat_label},
        "stride_categories": stride_names,
        "nist_controls": nist_list,
        "answer": answer,
    }


def _handle_gap_analysis(
    question: str,
    graph: SDCComplianceGraph,
) -> Dict[str, Any]:
    """Handle: What controls are missing for <framework>?"""
    fw_key = _extract_framework(question)
    if not fw_key:
        return {
            "intent": "gap_analysis",
            "answer": (
                "Please specify a framework for gap analysis (e.g., 'FedRAMP Moderate', 'CMMC Level 2', 'IL5')."
            ),
        }

    fw_label = _FRAMEWORK_LABELS.get(fw_key, fw_key)
    fw_nodes = graph.find_nodes(fw_label, entity_type="framework")
    if not fw_nodes:
        return {"intent": "gap_analysis", "answer": f"Framework '{fw_label}' not found in graph."}

    fw_node = fw_nodes[0]
    required_ctrls = [
        n for n in graph.neighbors(fw_node["id"], rel_filter="satisfies") if n["entity_type"] == "nist_control"
    ]

    # Group by family; compute which families are covered by SDC control types
    covered_families: Set[str] = set()
    for sdc_node in graph.nodes_by_type("sdc_control_type"):
        for nist_nbr in graph.neighbors(sdc_node["id"], rel_filter="implements", direction="out"):
            fam = nist_nbr["properties"].get("family", "")
            if fam:
                covered_families.add(fam)

    by_family: Dict[str, Dict] = {}
    for n in required_ctrls:
        fam = n["properties"].get("family", "??")
        if fam not in by_family:
            by_family[fam] = {"family": fam, "controls": [], "covered": fam in covered_families}
        by_family[fam]["controls"].append(
            {
                "control_id": n["properties"].get("control_id", ""),
                "title": n["properties"].get("title", ""),
                "priority": n["properties"].get("priority", ""),
            }
        )

    covered_count = sum(1 for f in by_family.values() if f["covered"])
    gap_count = len(by_family) - covered_count
    uncovered = [f for f in by_family.values() if not f["covered"]]
    covered = [f for f in by_family.values() if f["covered"]]

    answer = (
        f"Gap analysis for {fw_label}: "
        f"{len(required_ctrls)} controls required across {len(by_family)} families. "
        f"{covered_count} families have SDC controls implementing them. "
        f"{gap_count} families have no direct SDC mapping: "
        f"{', '.join(f['family'] for f in uncovered[:8])}."
    )

    return {
        "intent": "gap_analysis",
        "framework": fw_label,
        "total_required_controls": len(required_ctrls),
        "total_families": len(by_family),
        "covered_families": covered_count,
        "gap_families": gap_count,
        "gaps": [
            {"family": f["family"], "sample_controls": [c["control_id"] for c in f["controls"][:3]]} for f in uncovered
        ],
        "covered": [{"family": f["family"], "control_count": len(f["controls"])} for f in covered],
        "answer": answer,
    }


def _handle_path_query(
    question: str,
    graph: SDCComplianceGraph,
) -> Dict[str, Any]:
    """Handle: How does <A> relate to <B>?"""
    ctrl_type = _extract_sdc_ctrl_type(question)
    threat_type = _extract_sdc_threat_type(question)
    stride_code = _extract_stride_code(question)
    fw_key = _extract_framework(question)
    ctrl_match = re.search(r"\b([A-Z]{2}-\d+)\b", question.upper())
    ctrl_id = ctrl_match.group(1) if ctrl_match else None

    # Determine source node
    source_node: Optional[Dict] = None
    if ctrl_type:
        candidates = graph.find_nodes(ctrl_type, entity_type="sdc_control_type")
        if not candidates:
            for n in graph.nodes_by_type("sdc_control_type"):
                if n["properties"].get("type") == ctrl_type:
                    candidates = [n]
                    break
        if candidates:
            source_node = candidates[0]
    elif threat_type:
        candidates = graph.find_nodes(
            _SDC_THREAT_LABELS.get(threat_type, threat_type),
            entity_type="sdc_threat_type",
        )
        if candidates:
            source_node = candidates[0]
    elif stride_code:
        candidates = graph.find_nodes(
            f"STRIDE:{_STRIDE_LABELS.get(stride_code, stride_code)}",
            entity_type="stride_category",
        )
        if candidates:
            source_node = candidates[0]
    elif ctrl_id:
        candidates = graph.find_nodes(ctrl_id, entity_type="nist_control")
        if candidates:
            source_node = candidates[0]

    # Determine target node(s)
    target_ids: Set[str] = set()
    if fw_key:
        fw_label = _FRAMEWORK_LABELS.get(fw_key, fw_key)
        fw_nodes = graph.find_nodes(fw_label, entity_type="framework")
        target_ids = {n["id"] for n in fw_nodes}

    if not source_node or not target_ids:
        return {
            "intent": "path_query",
            "answer": (
                "Could not identify source and target nodes from your question. "
                "Try: 'How does ctrl-firewall relate to FedRAMP?' or "
                "'What's the path from phishing to CMMC?'"
            ),
        }

    result = graph.bfs_path(source_node["id"], target_ids)
    if not result:
        return {
            "intent": "path_query",
            "source": source_node["label"],
            "answer": (f"No path found between '{source_node['label']}' and the target in the SDC compliance graph."),
        }

    path_ids, path_edges = result
    path_nodes = [
        {
            "label": graph.node(nid)["label"] if graph.node(nid) else nid,
            "type": graph.node(nid)["entity_type"] if graph.node(nid) else "?",
        }
        for nid in path_ids
    ]
    # Build human-readable path description
    path_str = (
        " → ".join(f"{path_nodes[i]['label']} --{path_edges[i]['rel']}--> " for i in range(len(path_edges)))
        + path_nodes[-1]["label"]
        if path_edges
        else path_nodes[0]["label"]
    )

    return {
        "intent": "path_query",
        "source": source_node["label"],
        "path_length": len(path_ids),
        "path_nodes": path_nodes,
        "path_edges": path_edges,
        "answer": (f"Path ({len(path_ids)} hops): {path_str}"),
    }


def _handle_inventory(
    question: str,
    graph: SDCComplianceGraph,
) -> Dict[str, Any]:
    """Handle: How many / list all <node type>."""
    results: Dict[str, int] = {
        "nist_controls": len(graph.nodes_by_type("nist_control")),
        "nist_families": len(graph.nodes_by_type("nist_family")),
        "stride_categories": len(graph.nodes_by_type("stride_category")),
        "sdc_control_types": len(graph.nodes_by_type("sdc_control_type")),
        "sdc_threat_types": len(graph.nodes_by_type("sdc_threat_type")),
        "frameworks": len(graph.nodes_by_type("framework")),
    }
    total = sum(results.values())
    answer = (
        f"SDC Compliance KG contains {total} nodes: "
        + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in results.items())
        + f". Total edges: {graph.edge_count}."
    )
    return {
        "intent": "inventory",
        "node_counts": results,
        "total_nodes": total,
        "total_edges": graph.edge_count,
        "answer": answer,
    }


# ---------------------------------------------------------------------------
# 4. LLM Fallback
# ---------------------------------------------------------------------------


_LLM_SYSTEM_PROMPT = (
    "You are a NIST 800-53 and SDC compliance expert. Answer questions about the "
    "Security Design Canvas compliance graph concisely and accurately, citing "
    "specific NIST controls or STRIDE categories."
)


def _llm_query(question: str, graph: SDCComplianceGraph) -> Dict[str, Any]:
    """Fallback to the governed LLM router for open-ended questions.

    Routes through ``tools.security_canvas.llm_adapter.generate`` (LLMRouter
    function ``security_canvas``) — no direct provider calls or model IDs here.
    When the adapter returns ``None`` (router/provider unavailable, redaction
    refusal, budget block, etc.) we degrade to the deterministic guidance
    message, exactly as the previous provider-unreachable path did.
    """
    context = graph.compact_context()
    prompt = (
        "Answer the following question about the Security Design Canvas compliance graph.\n\n"
        f"GRAPH CONTEXT:\n{context}\n\n"
        f"QUESTION: {question}\n\n"
        "Provide a concise, accurate answer citing specific NIST controls or STRIDE categories."
    )

    from tools.security_canvas import llm_adapter

    answer = llm_adapter.generate(
        prompt,
        purpose="nl_query",
        system=_LLM_SYSTEM_PROMPT,
        max_tokens=512,
    )

    if answer:
        return {
            "intent": "general",
            "answer": answer.strip(),
            "llm_used": True,
        }

    # Adapter returned None → LLM unavailable. Same deterministic fallback the
    # module used when the provider was unreachable.
    return {
        "intent": "general",
        "answer": (
            "LLM unavailable. "
            "Try a more specific question using STRIDE codes (S/T/R/I/D/E), "
            "SDC control types (ctrl-firewall, ctrl-kms, etc.), "
            "or framework names (fedramp, cmmc, il5)."
        ),
        "llm_used": False,
    }


# ---------------------------------------------------------------------------
# 5. Public API
# ---------------------------------------------------------------------------


def answer_query(
    question: str,
    design_id: Optional[str] = None,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Answer a natural language question about the SDC compliance graph.

    Args:
        question: Natural language question.
        design_id: Optional SDC design ID for design-specific context (unused
                   in deterministic handlers; passed to LLM context if provided).
        conn: Optional existing DB connection. If None, opens a new one.

    Returns:
        Dict with 'answer' (str), 'intent' (str), and handler-specific fields.
    """
    global _GRAPH_CACHE
    _GRAPH_CACHE = None  # Invalidate cache per call (supports test isolation)

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
        conn.row_factory = __import__("sqlite3").Row
        conn.execute("PRAGMA journal_mode=WAL")

    try:
        # Ensure KG tables exist
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kg_graphs (
                id TEXT PRIMARY KEY, project_id TEXT, name TEXT NOT NULL,
                description TEXT, entity_count INTEGER DEFAULT 0,
                edge_count INTEGER DEFAULT 0, metadata TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS kg_nodes (
                id TEXT PRIMARY KEY, graph_id TEXT, label TEXT NOT NULL,
                entity_type TEXT NOT NULL, properties TEXT DEFAULT '{}',
                embedding BLOB, centrality REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS kg_edges (
                id TEXT PRIMARY KEY, graph_id TEXT, source_id TEXT,
                target_id TEXT, relationship TEXT NOT NULL,
                weight REAL DEFAULT 1.0, properties TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        graph = _ensure_graph(conn)
        if not graph:
            return {
                "intent": "error",
                "answer": "SDC Compliance KG is not available. Run --build first.",
                "error": "Graph not built",
            }

        intent = classify_query(question)
        logger.debug("Query intent: %s — %s", intent, question)

        if intent == "stride_to_nist":
            result = _handle_stride_to_nist(question, graph)
        elif intent == "control_implements":
            result = _handle_control_implements(question, graph)
        elif intent == "framework_crosswalk":
            result = _handle_framework_crosswalk(question, graph)
        elif intent == "threat_coverage":
            result = _handle_threat_coverage(question, graph)
        elif intent == "gap_analysis":
            result = _handle_gap_analysis(question, graph)
        elif intent == "path_query":
            result = _handle_path_query(question, graph)
        elif intent == "inventory":
            result = _handle_inventory(question, graph)
        else:
            result = _llm_query(question, graph)

        result["intent"] = result.get("intent", intent)
        if design_id:
            result["design_id"] = design_id
        return result

    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="SDC Compliance Knowledge Graph — Natural Language Query Engine",
    )
    parser.add_argument("--query", "-q", default=None, help="Natural language question to answer")
    parser.add_argument("--build", action="store_true", help="Build/rebuild the sdc-compliance-kg and exit")
    parser.add_argument("--design-id", default=None, help="Optional SDC design ID for context")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--db-path", default=None)

    args = parser.parse_args()

    if args.build:
        from tools.security_canvas.compliance_kg import build_sdc_kg

        db_path = Path(args.db_path) if args.db_path else None
        result = build_sdc_kg(db_path=db_path)
        print(json.dumps(result, indent=2, default=str))
        return

    if not args.query:
        parser.print_help()
        return

    result = answer_query(args.query, design_id=args.design_id)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
