"""Natural Language Query Engine for Network Canvas topology graphs.

Allows users to ask questions like:
  - "Show all paths between Router-A and Server-B"
  - "What happens if Switch-3 goes down?"
  - "How many firewalls are in the topology?"
  - "What devices are directly connected to Core-Router?"

Architecture:
  1. TopologyGraphAdapter  — converts JointJS graph_json → NetworkX graph
  2. QueryClassifier       — detects query intent (path / failure / inventory /
                             neighbour / compliance / general)
  3. Deterministic engines — path BFS, blast-radius, inventory counting, adjacency
  4. LLMQueryEngine        — Ollama fallback for open-ended questions
  5. Public function       — answer_query(topology_id, question, conn) → dict
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import os
import re
from typing import Any

try:
    import networkx as nx

    _HAS_NETWORKX = True
except ImportError:
    nx = None
    _HAS_NETWORKX = False

try:
    import requests

    from tools.http.client import request as _http_request

    _HAS_REQUESTS = True
except ImportError:
    requests = None
    _http_request = None  # type: ignore[assignment]
    _HAS_REQUESTS = False

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.environ.get("OLLAMA_NL_QUERY_MODEL", os.environ.get("OLLAMA_TOPO_MODEL", "qwen3.5:latest"))
_LLM_TIMEOUT = int(os.environ.get("OLLAMA_NL_QUERY_TIMEOUT", "60"))
_MAX_CTX_NODES = int(os.environ.get("NL_QUERY_MAX_NODES", "200"))


# ---------------------------------------------------------------------------
# 1. Topology Graph Adapter
# ---------------------------------------------------------------------------


class TopologyGraphAdapter:
    """Converts a JointJS graph_json blob into a NetworkX undirected graph
    plus convenient look-up structures."""

    def __init__(self, graph_json: dict) -> None:
        self.G = nx.Graph()
        self._nodes: dict[str, dict] = {}  # id → attrs
        self._edges: list[dict] = []
        self._label_index: dict[str, list[str]] = {}  # lower(label) → [ids]
        self._type_index: dict[str, list[str]] = {}  # type → [ids]
        self._parse(graph_json)

    # -- Parsing ---------------------------------------------------------

    def _parse(self, graph_json: dict) -> None:
        cells = graph_json.get("cells") or []
        edge_cells: list[dict] = []

        for cell in cells:
            kind = cell.get("type", "")
            cell_id = cell.get("id", "")

            if "link" in kind.lower() or "edge" in kind.lower():
                edge_cells.append(cell)
                continue
            if not cell_id:
                continue

            label = self._extract_label(cell)
            dev_type = cell.get("deviceType") or cell.get("attrs", {}).get("deviceType", "") or kind

            attrs = {
                "id": cell_id,
                "label": label,
                "type": dev_type,
                "kind": kind,
                "position": cell.get("position", {}),
                "raw": cell,
            }
            self._nodes[cell_id] = attrs
            self.G.add_node(cell_id, **attrs)

            lbl_lower = label.lower()
            self._label_index.setdefault(lbl_lower, []).append(cell_id)
            type_key = dev_type.lower()
            self._type_index.setdefault(type_key, []).append(cell_id)

        for cell in edge_cells:
            src = (cell.get("source") or {}).get("id") or cell.get("source")
            tgt = (cell.get("target") or {}).get("id") or cell.get("target")
            if not src or not tgt:
                continue
            if src not in self._nodes or tgt not in self._nodes:
                continue
            proto = (cell.get("attrs") or {}).get("line", {}).get("strokeDasharray", "") or cell.get("protocol", "")
            bandwidth = cell.get("bandwidth", "")
            edge_attrs = {"protocol": proto, "bandwidth": bandwidth, "raw": cell}
            self.G.add_edge(src, tgt, **edge_attrs)
            self._edges.append({"src": src, "tgt": tgt, **edge_attrs})

    @staticmethod
    def _extract_label(cell: dict) -> str:
        # Try common JointJS label locations
        attrs = cell.get("attrs") or {}
        for key in ("label", "text", "body"):
            val = attrs.get(key)
            if isinstance(val, dict):
                t = val.get("text") or val.get("textWrap", {}).get("text", "")
                if t:
                    return str(t).strip()
        for key in ("label", "name"):
            v = cell.get(key)
            if v:
                return str(v).strip()
        return cell.get("id", "unknown")

    # -- Look-ups --------------------------------------------------------

    def find_node_by_name(self, name: str) -> list[str]:
        """Return node IDs whose label contains *name* (case-insensitive)."""
        name_lower = name.lower()
        # exact first
        if name_lower in self._label_index:
            return self._label_index[name_lower]
        # partial
        return [nid for lbl, ids in self._label_index.items() if name_lower in lbl for nid in ids]

    def nodes_by_type(self, type_fragment: str) -> list[str]:
        """Return node IDs whose device type contains *type_fragment*."""
        frag = type_fragment.lower()
        result: list[str] = []
        for type_key, ids in self._type_index.items():
            if frag in type_key:
                result.extend(ids)
        return result

    def label_of(self, node_id: str) -> str:
        return self._nodes.get(node_id, {}).get("label", node_id)

    def type_of(self, node_id: str) -> str:
        return self._nodes.get(node_id, {}).get("type", "unknown")

    def neighbors_of(self, node_id: str) -> list[str]:
        return list(self.G.neighbors(node_id))

    def compact_text(self) -> str:
        """Compact human-readable serialization for LLM context window."""
        lines: list[str] = []
        shown = 0
        for nid, attrs in self._nodes.items():
            if shown >= _MAX_CTX_NODES:
                lines.append(f"... ({len(self._nodes) - shown} more nodes truncated)")
                break
            lines.append(f"  NODE {attrs['label']} [type={attrs['type']}]")
            shown += 1
        lines.append("")
        for i, e in enumerate(self._edges[:300]):
            src_lbl = self.label_of(e["src"])
            tgt_lbl = self.label_of(e["tgt"])
            proto = e.get("protocol", "")
            bw = e.get("bandwidth", "")
            extra = " ".join(filter(None, [proto, bw]))
            lines.append(f"  LINK {src_lbl} <-> {tgt_lbl}" + (f" [{extra}]" if extra else ""))
        return "\n".join(lines)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)


# ---------------------------------------------------------------------------
# 2. Query Classifier
# ---------------------------------------------------------------------------

_PATH_PATTERNS = [
    r"\b(path|route|routes|hop|hops|reachab|reach|between|from .+ to)\b",
]
_FAILURE_PATTERNS = [
    r"\b(if|when|what.?if|what happen|goes? down|fail|failover|outage|lose|lost|"
    r"remove|disconnect|impact|blast.?radius|affect)\b",
]
_NEIGHBOR_PATTERNS = [
    r"\b(connect\w*|neighbor|neighbour|adjacent|directly|attached|link)\b",
]
_INVENTORY_PATTERNS = [
    r"\b(how many|count|list|all|show|display|inventory|total|number of)\b",
]
_COMPLIANCE_PATTERNS = [
    r"\b(cat1|cat2|cat3|stig|fips|finding|complian|audit|nist|cmmc|fisma)\b",
]
_CLOUD_PROP_PATTERNS = [
    r"\b(bfd|resilien|tier|sla|bandwidth|vif|macsec|encrypt|egress|"
    r"ingress|flow.?log|failover.?time|detection.?time)\b",
]
_CSP_EQUIV_PATTERNS = [
    r"\b(equivalent|equival|same.?as|analog|counterpart|"
    r"what.?is.?the.?(aws|azure|gcp|oci|ibm))\b",
]
_COST_PATTERNS = [
    r"\b(cost|price|pricing|expensive|cheap|egress.?cost|data.?transfer.?cost)\b",
]

# Network Infrastructure Intelligence patterns — dispatched to network_query_router
_NII_PATTERNS = [
    r"\b(redundan\w*|resilien\w*|SPOF|single\s*point\s*of\s*failure)\b",
    r"\b(EOL|end\s*of\s*life|end\s*of\s*support|EOS)\b",
    r"\b(blast\s*radius)\b",
    r"\b(capacity|VDI|add\s*\d+\s*(user|seat))\b",
    r"\b(vendor\s*(risk|concentrat)|supply\s*chain\s*risk)\b",
    r"\b(circuit\s*(contract|expir|renew))\b",
    r"\b(config\s*drift|golden\s*config|self[-\s]?provis|ansible\s*push)\b",
    r"\b(SLA|availab\w*|MTBF|MTTR|uptime|downtime)\b.*\b(path|device|network)\b",
    r"\b(TCO|\d+[-\s]year\s*(cost|project|forecast))\b",
    r"\b(latency|RTT|round\s*trip)\b.*\b(path|device|network)\b",
]


def classify_query(question: str) -> str:
    q = question.lower()
    # Check NII patterns first — delegate to network_intelligence engine
    if any(re.search(p, q) for p in _NII_PATTERNS):
        return "network_intelligence"
    if any(re.search(p, q) for p in _PATH_PATTERNS):
        return "path"
    if any(re.search(p, q) for p in _FAILURE_PATTERNS):
        return "failure"
    if any(re.search(p, q) for p in _CSP_EQUIV_PATTERNS):
        return "csp_equivalence"
    if any(re.search(p, q) for p in _CLOUD_PROP_PATTERNS):
        return "cloud_properties"
    if any(re.search(p, q) for p in _COST_PATTERNS):
        return "cost"
    if any(re.search(p, q) for p in _NEIGHBOR_PATTERNS):
        return "neighbor"
    if any(re.search(p, q) for p in _INVENTORY_PATTERNS):
        return "inventory"
    if any(re.search(p, q) for p in _COMPLIANCE_PATTERNS):
        return "compliance"
    return "general"


# ---------------------------------------------------------------------------
# 3. Deterministic Query Engines
# ---------------------------------------------------------------------------


def _extract_device_names_from_question(question: str, graph: TopologyGraphAdapter) -> list[str]:
    """Heuristic: try to match topology labels mentioned in the question."""
    found: list[str] = []
    q_lower = question.lower()
    for lbl in graph._label_index:
        if lbl in q_lower:
            found.extend(graph._label_index[lbl])
    # Also try camel-case fragments: Router-A, Switch-3, etc.
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]+", question)
    for tok in tokens:
        hits = graph.find_node_by_name(tok)
        for h in hits:
            if h not in found:
                found.append(h)
    return list(dict.fromkeys(found))  # deduplicate, preserve order


def _path_query(question: str, graph: TopologyGraphAdapter) -> dict[str, Any]:
    """Find all simple paths between two named devices."""
    nodes = _extract_device_names_from_question(question, graph)
    if len(nodes) < 2:
        return {
            "answer": (
                "Could not identify two distinct devices in your question. "
                "Please use labels that appear in the topology."
            ),
            "data": [],
            "engine": "path",
        }
    src, tgt = nodes[0], nodes[1]
    src_lbl = graph.label_of(src)
    tgt_lbl = graph.label_of(tgt)

    try:
        paths = list(nx.all_simple_paths(graph.G, src, tgt, cutoff=8))
    except nx.NetworkXNoPath:
        paths = []
    except nx.NodeNotFound as exc:
        return {"answer": f"Node not found: {exc}", "data": [], "engine": "path"}

    if not paths:
        return {
            "answer": (
                f"No path found between **{src_lbl}** and **{tgt_lbl}**. They may be in disconnected network segments."
            ),
            "data": [],
            "engine": "path",
        }

    # shortest path
    shortest = min(paths, key=len)
    shortest_labels = [graph.label_of(n) for n in shortest]

    path_descriptions = []
    for i, path in enumerate(paths[:5], 1):
        hops = [graph.label_of(n) for n in path]
        path_descriptions.append(f"Path {i} ({len(path) - 1} hops): " + " → ".join(hops))

    answer_parts = [
        f"Found **{len(paths)}** path(s) between **{src_lbl}** and **{tgt_lbl}**.",
        "",
        f"**Shortest path ({len(shortest) - 1} hops):** " + " → ".join(shortest_labels),
    ]
    if len(paths) > 1:
        answer_parts.append("")
        answer_parts.append("**All paths (up to 5):**")
        answer_parts.extend(path_descriptions)

    return {
        "answer": "\n".join(answer_parts),
        "data": {
            "source": src_lbl,
            "target": tgt_lbl,
            "path_count": len(paths),
            "shortest_path": shortest_labels,
            "shortest_hops": len(shortest) - 1,
            "all_paths": [[graph.label_of(n) for n in p] for p in paths[:10]],
        },
        "engine": "path",
    }


def _failure_query(question: str, graph: TopologyGraphAdapter) -> dict[str, Any]:
    """Blast-radius analysis: what happens if a device fails?"""
    nodes = _extract_device_names_from_question(question, graph)
    if not nodes:
        return {
            "answer": ("Could not identify a device in your question. Please name a device from the topology."),
            "data": {},
            "engine": "failure",
        }
    failed_id = nodes[0]
    failed_lbl = graph.label_of(failed_id)

    # Connectivity before failure
    components_before = nx.number_connected_components(graph.G)

    # Remove the node
    G_after = graph.G.copy()
    G_after.remove_node(failed_id)

    components_after = nx.number_connected_components(G_after)
    direct_neighbors = graph.neighbors_of(failed_id)
    neighbor_labels = [graph.label_of(n) for n in direct_neighbors]

    # Nodes that become unreachable from the largest remaining component
    if G_after.number_of_nodes() == 0:
        isolated = []
    else:
        largest_cc = max(nx.connected_components(G_after), key=len)
        isolated = [graph.label_of(n) for n in G_after.nodes() if n not in largest_cc]

    # Articulation points check
    is_articulation = failed_id in set(nx.articulation_points(graph.G))

    # Cloud-managed HA services that are NOT realistic failure scenarios
    _CLOUD_HA_NOTES = {
        "aws-tgw": "TGW runs on Hyperplane (distributed, 99.99% SLA) — data-plane failure is extremely unlikely",
        "aws-dx-gw": "DX Gateway is a global config overlay, not a data-plane device — cannot fail independently",
        "aws-cloudwan": "Cloud WAN uses distributed infrastructure — 99.99% SLA",
        "az-vwan": "Virtual WAN hub is zone-redundant and Microsoft-managed — failure is rare",
        "gcp-ncc": "Network Connectivity Center is Google-managed — inherently HA",
        "oci-drg": "DRG v2 is Oracle-managed and HA by default",
        "ibm-tg": "IBM Transit Gateway is managed infrastructure",
    }
    failed_type = graph.G.nodes[failed_id].get("type", "") if failed_id in graph.G else ""
    cloud_ha_note = _CLOUD_HA_NOTES.get(failed_type)

    answer_parts = [
        f"**Failure analysis for: {failed_lbl}**",
        "",
    ]

    if cloud_ha_note:
        answer_parts.append(f"ℹ **Cloud-managed HA:** {cloud_ha_note}")
        answer_parts.append("")

    if is_articulation:
        answer_parts.append(
            f"⚠ **Critical node** — removing **{failed_lbl}** will partition "
            f"the network (segments: {components_before} → {components_after})."
        )
        if cloud_ha_note:
            answer_parts.append(
                "  → However, this is a cloud-managed service with provider HA — actual risk is very low."
            )
    else:
        answer_parts.append(
            f"✓ **{failed_lbl}** is NOT a critical bridge — the rest of the "
            f"topology remains connected after its failure."
        )

    if direct_neighbors:
        answer_parts += [
            "",
            f"**Directly affected devices ({len(direct_neighbors)}):**",
            ", ".join(neighbor_labels),
        ]
    if isolated:
        answer_parts += [
            "",
            f"**Isolated / unreachable devices ({len(isolated)}):**",
            ", ".join(isolated[:20]),
        ]

    return {
        "answer": "\n".join(answer_parts),
        "data": {
            "failed_device": failed_lbl,
            "is_critical": is_articulation,
            "direct_neighbors": neighbor_labels,
            "segments_before": components_before,
            "segments_after": components_after,
            "isolated_devices": isolated,
        },
        "engine": "failure",
    }


def _neighbor_query(question: str, graph: TopologyGraphAdapter) -> dict[str, Any]:
    """Return directly connected neighbors of a named device."""
    nodes = _extract_device_names_from_question(question, graph)
    if not nodes:
        return {
            "answer": "Could not identify a device. Please name a device from the topology.",
            "data": {},
            "engine": "neighbor",
        }
    node_id = nodes[0]
    node_lbl = graph.label_of(node_id)
    neighbors = graph.neighbors_of(node_id)
    neighbor_labels = [graph.label_of(n) for n in neighbors]

    if not neighbors:
        answer = f"**{node_lbl}** has no connections in this topology."
    else:
        answer = f"**{node_lbl}** is directly connected to **{len(neighbors)}** device(s):\n\n" + "\n".join(
            f"- {lbl} ({graph.type_of(n)})" for n, lbl in zip(neighbors, neighbor_labels)
        )
    return {
        "answer": answer,
        "data": {"device": node_lbl, "neighbors": neighbor_labels, "count": len(neighbors)},
        "engine": "neighbor",
    }


def _inventory_query(question: str, graph: TopologyGraphAdapter) -> dict[str, Any]:
    """Count or list devices by type."""
    q = question.lower()

    # Detect device type from question
    type_keywords = {
        "router": "router",
        "switch": "switch",
        "firewall": "firewall",
        "server": "server",
        "host": "host",
        "workstation": "workstation",
        "load balancer": "load.balancer",
        "cloud": "cloud",
        "vpn": "vpn",
        "encryptor": "encryptor",
        "siem": "siem",
        "ap": "access.point",
        "access point": "access.point",
        "camera": "camera",
        "phone": "phone",
        "iot": "iot",
        # Cloud networking types
        "vpc": "vpc",
        "vnet": "vnet",
        "vcn": "vcn",
        "transit gateway": "tgw",
        "tgw": "tgw",
        "direct connect": "dx",
        "expressroute": "er",
        "interconnect": "ic",
        "fastconnect": "fc",
        "dx gateway": "dx-gw",
        "privatelink": "privatelink",
        "private link": "privatelink",
        "private endpoint": "privatelink",
        "drg": "drg",
        "vwan": "vwan",
        "virtual wan": "vwan",
        "shield": "shield",
        "waf": "waf",
        "flow log": "flowlog",
        "nsg": "nsg",
        "cloud armor": "armor",
        "global accelerator": "ga",
    }

    matched_type: str | None = None
    for kw, frag in type_keywords.items():
        if kw in q:
            matched_type = frag
            break

    if matched_type:
        found_ids = graph.nodes_by_type(matched_type)
        labels = [graph.label_of(n) for n in found_ids]
        answer_parts = [
            f"Found **{len(found_ids)}** {matched_type.replace('.', ' ')} device(s) in this topology:",
        ]
        if labels:
            answer_parts.append("\n".join(f"- {lbl}" for lbl in labels[:50]))
        return {
            "answer": "\n".join(answer_parts),
            "data": {"type": matched_type, "count": len(found_ids), "devices": labels},
            "engine": "inventory",
        }

    # General inventory summary
    type_counts: dict[str, int] = {}
    for nid in graph.G.nodes():
        t = graph.type_of(nid).lower()
        type_counts[t] = type_counts.get(t, 0) + 1

    top = sorted(type_counts.items(), key=lambda x: -x[1])[:15]
    summary_lines = [f"- **{t}**: {c}" for t, c in top]
    answer = (
        f"**Topology Inventory Summary**\n\n"
        f"Total nodes: {graph.node_count}, Total links: {graph.edge_count}\n\n"
        "**By device type:**\n" + "\n".join(summary_lines)
    )
    return {
        "answer": answer,
        "data": {"total_nodes": graph.node_count, "total_edges": graph.edge_count, "by_type": dict(top)},
        "engine": "inventory",
    }


def _compliance_query(
    question: str, graph: TopologyGraphAdapter, conn: Any, topology_id: str | None = None
) -> dict[str, Any]:
    """Pull compliance findings from the DB and answer compliance questions."""
    q = question.lower()

    if not topology_id:
        return {
            "answer": (
                "Compliance data is available only when querying a specific topology. Please open a topology and retry."
            ),
            "data": {},
            "engine": "compliance",
        }

    try:
        rows = conn.execute(
            "SELECT severity, rule_id, description, status FROM nc_compliance_findings "
            "WHERE topology_id=? ORDER BY "
            "CASE severity WHEN 'CAT1' THEN 1 WHEN 'CAT2' THEN 2 ELSE 3 END",
            (topology_id,),
        ).fetchall()
    except Exception:
        rows = []

    if not rows:
        return {
            "answer": "No compliance findings recorded for this topology. Run a compliance audit first.",
            "data": {},
            "engine": "compliance",
        }

    # Filter by severity if mentioned
    sev_filter = None
    if "cat1" in q:
        sev_filter = "CAT1"
    elif "cat2" in q:
        sev_filter = "CAT2"
    elif "cat3" in q:
        sev_filter = "CAT3"

    displayed = [r for r in rows if (not sev_filter or r[0] == sev_filter)]
    counts = {s: sum(1 for r in rows if r[0] == s) for s in ("CAT1", "CAT2", "CAT3")}

    lines = [
        f"**Compliance Findings** (CAT1: {counts.get('CAT1', 0)}, "
        f"CAT2: {counts.get('CAT2', 0)}, "
        f"CAT3: {counts.get('CAT3', 0)})\n"
    ]
    if sev_filter:
        lines.append(f"Showing only **{sev_filter}** findings:\n")
    for row in displayed[:20]:
        sev, rule_id, desc, status = row[0], row[1], row[2], row[3]
        lines.append(f"- [{sev}] {rule_id}: {desc} *(status: {status})*")

    return {
        "answer": "\n".join(lines),
        "data": {"finding_counts": counts, "total": len(rows), "filtered": len(displayed)},
        "engine": "compliance",
    }


# ---------------------------------------------------------------------------
# 4. LLM Query Engine (Ollama)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a network topology analyst assistant.
You are given a compact representation of a network topology (nodes and links).
Answer the user's question concisely and accurately based on this topology.
Use markdown formatting. Be specific — reference device names from the topology.
If you cannot answer from the topology data, say so clearly.
Do NOT invent devices or links that are not listed."""


def _llm_query(question: str, graph: TopologyGraphAdapter, topology_name: str = "") -> dict[str, Any]:
    """Use Ollama to answer open-ended questions about the topology."""
    topo_text = graph.compact_text()
    user_content = f"Topology: {topology_name or 'unnamed'}\n\n{topo_text}\n\nQuestion: {question}"
    payload = {
        "model": _OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 512,
            "think": False,
        },
    }
    if not _HAS_REQUESTS:
        return {
            "answer": "LLM query requires the 'requests' package. Install it with: pip install requests",
            "data": {},
            "engine": "llm_unavailable",
        }
    try:
        resp = _http_request(
            "POST",
            f"{_OLLAMA_HOST}/api/chat",
            json=payload,
            timeout=_LLM_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        answer = (data.get("message") or {}).get("content", "").strip()
        # Strip <think> blocks (qwen3.5 reasoning)
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
        return {"answer": answer, "data": {}, "engine": "llm", "model": _OLLAMA_MODEL}
    except requests.exceptions.ConnectionError:
        return {
            "answer": (
                "Local LLM (Ollama) is not running. "
                "Start Ollama and retry, or rephrase your question to ask about "
                "specific paths, neighbors, failures, or inventory counts."
            ),
            "data": {},
            "engine": "llm_unavailable",
        }
    except Exception as exc:
        logger.warning("LLM query error: %s", exc)
        return {
            "answer": f"LLM query failed: {exc}",
            "data": {},
            "engine": "llm_error",
        }


# ---------------------------------------------------------------------------
# 4b. Cloud-Aware Query Engines
# ---------------------------------------------------------------------------


def _cloud_properties_query(question: str, graph: TopologyGraphAdapter) -> dict[str, Any]:
    """Answer questions about cloud properties (BFD, resiliency, flow logs)."""
    q = question.lower()
    answer_parts = ["**Cloud Properties Analysis**", ""]

    # BFD check
    if "bfd" in q:
        bfd_nodes = []
        no_bfd_nodes = []
        dx_types = {"aws-dx", "aws-dx-gw", "az-er", "az-er-global", "gcp-ic", "oci-fc", "ibm-dl"}
        for nid, data in graph.G.nodes(data=True):
            ntype = data.get("type", "")
            if ntype in dx_types:
                raw = data.get("raw", {})
                cfg = raw.get("config", {})
                if cfg.get("bfd") or cfg.get("bfd_enabled"):
                    bfd_nodes.append(graph.label_of(nid))
                else:
                    no_bfd_nodes.append(graph.label_of(nid))
        if bfd_nodes:
            answer_parts.append(f"**BFD enabled** on: {', '.join(bfd_nodes)}")
        if no_bfd_nodes:
            answer_parts.append(f"**BFD NOT enabled** on: {', '.join(no_bfd_nodes)} (failover ~90s vs <1s with BFD)")
        if not bfd_nodes and not no_bfd_nodes:
            answer_parts.append("No dedicated interconnect (DX/ER/IC/FC) nodes found.")
        return {
            "answer": "\n".join(answer_parts),
            "data": {
                "bfd_enabled": bfd_nodes,
                "bfd_missing": no_bfd_nodes,
            },
            "engine": "cloud_properties",
        }

    # Flow logs
    if "flow" in q and "log" in q:
        vpc_types = {"aws-vpc", "az-vnet", "gcp-vpc", "oci-vcn", "ibm-vpc"}
        with_logs = []
        without_logs = []
        for nid, data in graph.G.nodes(data=True):
            ntype = data.get("type", "")
            if ntype in vpc_types:
                raw = data.get("raw", {})
                cfg = raw.get("config", {})
                if cfg.get("flow_logs") or cfg.get("flow_logs_enabled"):
                    with_logs.append(graph.label_of(nid))
                else:
                    without_logs.append(graph.label_of(nid))
        answer_parts.append(f"Flow logs enabled: {len(with_logs)}, missing: {len(without_logs)}")
        if without_logs:
            answer_parts.append(f"Missing on: {', '.join(without_logs)}")
        return {
            "answer": "\n".join(answer_parts),
            "data": {
                "with_logs": with_logs,
                "without_logs": without_logs,
            },
            "engine": "cloud_properties",
        }

    # Resiliency tier
    if "resilien" in q or "tier" in q or "sla" in q:
        try:
            from tools.network.cloud_architecture import (
                score_hybrid_resiliency,
            )

            raw_nodes = [graph.G.nodes[n].get("raw", {}) for n in graph.G.nodes()]
            raw_edges = []
            for u, v, data in graph.G.edges(data=True):
                raw_edges.append(data.get("raw", {"source": u, "target": v}))
            res = score_hybrid_resiliency(raw_nodes, raw_edges)
            answer_parts.append(f"**Resiliency Tier:** {res['tier_label']} (SLA: {res['sla']})")
            answer_parts.append(f"DX connections: {res['dedicated_connections']}, locations: {res['unique_locations']}")
            answer_parts.append(
                f"VPN backup: {'Yes' if res['has_vpn_backup'] else 'No'}, BFD: {'Yes' if res['bfd_enabled'] else 'No'}"
            )
            answer_parts.append(f"Est. failover: {res['estimated_failover_sec']}s")
            for r in res.get("recommendations", []):
                answer_parts.append(f"  - {r}")
        except Exception:
            answer_parts.append("Could not compute resiliency tier.")
        return {"answer": "\n".join(answer_parts), "data": {}, "engine": "cloud_properties"}

    answer_parts.append("Supported queries: BFD status, flow logs, resiliency tier, SLA")
    return {"answer": "\n".join(answer_parts), "data": {}, "engine": "cloud_properties"}


def _csp_equivalence_query(question: str) -> dict[str, Any]:
    """Answer CSP equivalence questions using constants data."""
    try:
        from tools.network.constants import CSP_EQUIVALENCE
    except ImportError:
        return {"answer": "CSP equivalence data not available.", "data": {}, "engine": "csp_equivalence"}

    q = question.lower()
    # Try to match a service name
    _SVC_KEYWORDS = {
        "direct connect": "dedicated_interconnect",
        "dx": "dedicated_interconnect",
        "expressroute": "dedicated_interconnect",
        "interconnect": "dedicated_interconnect",
        "fastconnect": "dedicated_interconnect",
        "vpn": "site_to_site_vpn",
        "transit gateway": "transit_hub",
        "tgw": "transit_hub",
        "vwan": "transit_hub",
        "virtual wan": "transit_hub",
        "privatelink": "private_endpoint",
        "private link": "private_endpoint",
        "private service connect": "private_endpoint",
        "cloud wan": "global_wan",
        "vpc": "virtual_network",
        "vnet": "virtual_network",
        "vcn": "virtual_network",
        "firewall": "network_firewall",
        "ddos": "ddos_protection",
        "shield": "ddos_protection",
        "flow log": "flow_logs",
        "load balanc": "global_load_balancing",
        "global accelerator": "global_load_balancing",
        "outpost": "hybrid_edge",
        "satellite": "hybrid_edge",
    }
    matched_key = None
    for kw, key in _SVC_KEYWORDS.items():
        if kw in q:
            matched_key = key
            break

    if not matched_key:
        # List all available services
        services = [f"- {k}: {v.get('description', '')}" for k, v in CSP_EQUIVALENCE.items()]
        return {
            "answer": ("**Available CSP equivalence mappings:**\n" + "\n".join(services)),
            "data": {"services": list(CSP_EQUIVALENCE.keys())},
            "engine": "csp_equivalence",
        }

    mapping = CSP_EQUIVALENCE.get(matched_key, {})
    parts = [f"**{mapping.get('description', matched_key)}**", ""]
    for csp in ("aws", "azure", "gcp", "oci", "ibm"):
        info = mapping.get(csp, {})
        svc = info.get("service", "—")
        parity = info.get("parity", "baseline" if csp == "aws" else "?")
        note = info.get("note", "")
        line = f"- **{csp.upper()}:** {svc} (parity: {parity})"
        if note:
            line += f" — {note}"
        parts.append(line)

    return {
        "answer": "\n".join(parts),
        "data": {"service_key": matched_key, "mapping": mapping},
        "engine": "csp_equivalence",
    }


def _cost_query(question: str) -> dict[str, Any]:
    """Answer egress cost comparison questions."""
    try:
        from tools.network.cloud_architecture import compare_egress_costs
    except ImportError:
        return {"answer": "Cost comparison not available.", "data": {}, "engine": "cost"}

    # Extract GB amount if mentioned
    import re as _re

    gb_match = _re.search(r"(\d+)\s*(gb|tb|terabyte|gigabyte)", question.lower())
    gb = 1000.0
    if gb_match:
        val = float(gb_match.group(1))
        unit = gb_match.group(2)
        if unit in ("tb", "terabyte"):
            val *= 1000
        gb = val

    results = compare_egress_costs(gb, "internet")
    parts = [f"**Egress cost comparison ({gb:.0f} GB/month, internet):**", ""]
    for r in results:
        parts.append(
            f"- **{r['source_csp'].upper()}:** "
            f"${r['monthly_cost_usd']:.2f}/mo "
            f"(${r['rate_per_gb']}/GB)" + (f" — {r['note']}" if r.get("note") else "")
        )
    return {"answer": "\n".join(parts), "data": {"results": results}, "engine": "cost"}


# ---------------------------------------------------------------------------
# 5. Public API
# ---------------------------------------------------------------------------


def answer_query(topology_id: str, question: str, conn: Any) -> dict[str, Any]:
    """Answer a natural language question about a topology.

    Args:
        topology_id: Primary key in the topologies table.
        question:    Free-form natural language question.
        conn:        Open SQLite connection to the network canvas DB.

    Returns:
        dict with keys: answer (str), data (dict), engine (str),
        intent (str), query_id (str)
    """
    import uuid
    import datetime

    question = (question or "").strip()
    if not question:
        return {"answer": "Please enter a question.", "data": {}, "engine": "none", "intent": "none", "query_id": ""}

    if not _HAS_NETWORKX:
        return {
            "answer": "Natural language query requires the 'networkx' package. Install it with: pip install networkx",
            "data": {},
            "engine": "none",
            "intent": "none",
            "query_id": "",
        }

    # Load topology from DB
    row = conn.execute(
        "SELECT name, graph_json FROM topologies WHERE id=?",
        (topology_id,),
    ).fetchone()
    if not row:
        return {"answer": "Topology not found.", "data": {}, "engine": "none", "intent": "none", "query_id": ""}

    topo_name = row[0] or topology_id
    try:
        graph_json = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    except (json.JSONDecodeError, TypeError):
        return {
            "answer": "Topology data is corrupt or empty.",
            "data": {},
            "engine": "none",
            "intent": "none",
            "query_id": "",
        }

    graph = TopologyGraphAdapter(graph_json)

    if graph.node_count == 0:
        return {
            "answer": "This topology has no devices yet. Add some nodes first.",
            "data": {},
            "engine": "none",
            "intent": "empty",
            "query_id": "",
        }

    # Classify and route
    intent = classify_query(question)

    if intent == "network_intelligence":
        try:
            from tools.network.network_query_router import route_query as _nii_route
            nii_result = _nii_route(topology_id, question)
            analysis = nii_result.get("analysis", {})
            result = {
                "answer": analysis.get("result_summary", json.dumps(analysis, indent=2, default=str)[:500]),
                "data": analysis,
                "engine": f"network_intelligence:{nii_result.get('intent', 'unknown')}",
            }
        except Exception as exc:
            logger.warning("NII query routing failed: %s", exc)
            result = _llm_query(question, graph, topo_name)
    elif intent == "path":
        result = _path_query(question, graph)
    elif intent == "failure":
        result = _failure_query(question, graph)
    elif intent == "neighbor":
        result = _neighbor_query(question, graph)
    elif intent == "inventory":
        result = _inventory_query(question, graph)
    elif intent == "compliance":
        result = _compliance_query(question, graph, conn, topology_id)
    elif intent == "cloud_properties":
        result = _cloud_properties_query(question, graph)
    elif intent == "csp_equivalence":
        result = _csp_equivalence_query(question)
    elif intent == "cost":
        result = _cost_query(question)
    else:
        result = _llm_query(question, graph, topo_name)

    result["intent"] = intent
    result["topology_name"] = topo_name
    result["node_count"] = graph.node_count
    result["edge_count"] = graph.edge_count

    # Persist query log
    query_id = str(uuid.uuid4())
    result["query_id"] = query_id
    try:
        conn.execute(
            "INSERT INTO nc_query_log (id, topology_id, question, intent, answer, engine, ts) VALUES (?,?,?,?,?,?,?)",
            (
                query_id,
                topology_id,
                question,
                intent,
                result.get("answer", ""),
                result.get("engine", ""),
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("Could not persist query log: %s", exc)

    return result
