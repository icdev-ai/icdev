# CUI // SP-CTI
"""Diagram Refinement Engine — TFW REFINE mode.

Conversational loop:
  1. User sends /refine <mermaid_diagram>
  2. Engine parses the diagram, asks canvas-specific clarifying questions (phase 1)
  3. User answers → engine applies refinements, asks follow-up questions (phase 2)
  4. User answers → engine emits final refined Mermaid, clears session

Canvas question banks:
  NDC : encryption strategy · BCAP requirements · microsegmentation scope
  SDC : circuit breaker config · authentication scheme · API versioning
  EDA : ordering guarantees · DLQ handling · consumer group isolation

Public API:
  has_active_refine_session(session_id) -> bool
  start_refine(raw_diagram, canvas_type, session_id) -> dict
  continue_refine(session_id, user_text, canvas_type) -> dict
  extract_mermaid_from_message(text) -> str | None
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Canvas aliases (non-profiled canvases → closest question bank)
# ---------------------------------------------------------------------------

_CANVAS_ALIAS: dict[str, str] = {
    "ddc": "eda",
    "bdc": "sdc",
    "pdc": "eda",
    "odc": "ndc",
    "idc": "ndc",
    "qdc": "sdc",
    "mdc": "ndc",
}

_CANVAS_DISPLAY: dict[str, str] = {
    "ndc": "Network Design Canvas",
    "sdc": "Security Design Canvas",
    "eda": "Event-Driven Architecture",
    "ddc": "Data Design Canvas",
    "bdc": "Boundary Design Canvas",
    "pdc": "Pipeline Design Canvas",
    "odc": "Observability Design Canvas",
    "idc": "Infrastructure Design Canvas",
    "qdc": "Quality Design Canvas",
    "mdc": "Migration Design Canvas",
}


def _resolve_canvas(canvas_type: str) -> str:
    ct = canvas_type.lower().strip()
    return _CANVAS_ALIAS.get(ct, ct)


# ---------------------------------------------------------------------------
# Question banks — phase 1 (core) and phase 2 (secondary)
# ---------------------------------------------------------------------------

_QUESTIONS: dict[str, list[dict[str, Any]]] = {
    "ndc": [
        {
            "key": "encryption",
            "phase": 1,
            "text": (
                "**1. Encryption strategy** for inter-zone links?\n"
                "   > Options: `IPSec`, `mTLS`, `TLS 1.3`, `MACsec`, `none`"
            ),
        },
        {
            "key": "bcap",
            "phase": 1,
            "text": (
                "**2. BCAP** (Boundary Crossing Access Point) requirements?\n"
                "   > Options: `DISA BCAP`, `ZTNA gateway`, `VLAN separation`, `none`"
            ),
        },
        {
            "key": "microseg",
            "phase": 1,
            "text": (
                "**3. Microsegmentation** scope?\n"
                "   > Options: `host-level`, `zone-level`, `workload-level`, `none`"
            ),
        },
        {
            "key": "ha",
            "phase": 2,
            "text": (
                "**1. High availability** topology?\n"
                "   > Options: `active-active`, `active-standby`, `none`"
            ),
        },
        {
            "key": "mgmt_plane",
            "phase": 2,
            "text": (
                "**2. Management plane separation**?\n"
                "   > Options: `OOB VLAN`, `in-band`, `isolated VLAN`, `none`"
            ),
        },
    ],
    "sdc": [
        {
            "key": "circuit_breaker",
            "phase": 1,
            "text": (
                "**1. Circuit breaker** configuration?\n"
                "   > Options: `aggressive` (5 s / 3 failures), `standard` (30 s / 5 failures), "
                "`lenient` (60 s / 10 failures), `none`"
            ),
        },
        {
            "key": "auth_scheme",
            "phase": 1,
            "text": (
                "**2. Authentication scheme**?\n"
                "   > Options: `SAML`, `OAuth2/OIDC`, `mTLS/SPIFFE`, `Kerberos`, `API key`"
            ),
        },
        {
            "key": "api_versioning",
            "phase": 1,
            "text": (
                "**3. API versioning** strategy?\n"
                "   > Options: `URL path` (/v1/v2), `Accept header`, `query param`, `none`"
            ),
        },
        {
            "key": "service_mesh",
            "phase": 2,
            "text": (
                "**1. Service mesh** in use?\n"
                "   > Options: `Istio`, `Linkerd`, `Cilium`, `none`"
            ),
        },
        {
            "key": "zt_level",
            "phase": 2,
            "text": (
                "**2. Zero Trust enforcement** level?\n"
                "   > Options: `identity-only`, `device+identity`, `micro-segmentation`, `full ZTA`"
            ),
        },
    ],
    "eda": [
        {
            "key": "ordering",
            "phase": 1,
            "text": (
                "**1. Ordering guarantees** required?\n"
                "   > Options: `total order`, `partition order`, `per-key order`, `none`"
            ),
        },
        {
            "key": "dlq",
            "phase": 1,
            "text": (
                "**2. Dead Letter Queue (DLQ)** handling?\n"
                "   > Options: `retry 3x then DLQ`, `immediate DLQ`, `skip and log`, `none`"
            ),
        },
        {
            "key": "consumer_group",
            "phase": 1,
            "text": (
                "**3. Consumer group isolation** pattern?\n"
                "   > Options: `dedicated per service`, `shared group`, `fan-out broadcast`, `single consumer`"
            ),
        },
        {
            "key": "delivery",
            "phase": 2,
            "text": (
                "**1. Message delivery** semantics?\n"
                "   > Options: `exactly-once`, `at-least-once`, `at-most-once`"
            ),
        },
        {
            "key": "schema",
            "phase": 2,
            "text": (
                "**2. Schema evolution** strategy?\n"
                "   > Options: `backward compatible`, `forward compatible`, `full compatible`, `none`"
            ),
        },
    ],
}


def _get_questions(canvas_type: str, phase: int) -> list[dict[str, Any]]:
    resolved = _resolve_canvas(canvas_type)
    all_qs = _QUESTIONS.get(resolved, _QUESTIONS["ndc"])
    return [q for q in all_qs if q["phase"] == phase]


def _format_questions(questions: list[dict[str, Any]]) -> str:
    return "\n\n".join(q["text"] for q in questions)


# ---------------------------------------------------------------------------
# Answer parsing
# ---------------------------------------------------------------------------

_NUMBERED_SPLIT_RE = re.compile(r"(?:^|\n)\s*\d+\s*[.)]\s*", re.MULTILINE)

_KEYWORD_MAPS: dict[str, list[tuple[re.Pattern, str]]] = {
    "encryption": [
        (re.compile(r"\bipsec\b", re.I), "IPSec"),
        (re.compile(r"\bmtls\b|\bmutual.?tls\b", re.I), "mTLS"),
        (re.compile(r"\btls.?1\.?3\b", re.I), "TLS 1.3"),
        (re.compile(r"\bmacsec\b", re.I), "MACsec"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "bcap": [
        (re.compile(r"\bdisa\b", re.I), "DISA BCAP"),
        (re.compile(r"\bztna\b", re.I), "ZTNA gateway"),
        (re.compile(r"\bvlan\b", re.I), "VLAN separation"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "microseg": [
        (re.compile(r"\bhost\b", re.I), "host-level"),
        (re.compile(r"\bzone\b", re.I), "zone-level"),
        (re.compile(r"\bworkload\b", re.I), "workload-level"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "ha": [
        (re.compile(r"\bactive.?active\b", re.I), "active-active"),
        (re.compile(r"\bactive.?standby\b|\bactive.?passive\b", re.I), "active-standby"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "mgmt_plane": [
        (re.compile(r"\boob\b|\bout.?of.?band\b", re.I), "OOB VLAN"),
        (re.compile(r"\bin.?band\b", re.I), "in-band"),
        (re.compile(r"\bisolated\b", re.I), "isolated VLAN"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "circuit_breaker": [
        (re.compile(r"\baggressive\b|\b5\s*s\b", re.I), "aggressive"),
        (re.compile(r"\bstandard\b|\b30\s*s\b", re.I), "standard"),
        (re.compile(r"\blenient\b|\b60\s*s\b", re.I), "lenient"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "auth_scheme": [
        (re.compile(r"\bsaml\b", re.I), "SAML"),
        (re.compile(r"\boauth\b|\boidc\b", re.I), "OAuth2/OIDC"),
        (re.compile(r"\bmtls\b|\bspiffe\b", re.I), "mTLS/SPIFFE"),
        (re.compile(r"\bkerberos\b", re.I), "Kerberos"),
        (re.compile(r"\bapi.?key\b", re.I), "API key"),
    ],
    "api_versioning": [
        (re.compile(r"\burl\b|\bpath\b|\bv1\b|\bv2\b", re.I), "URL path"),
        (re.compile(r"\baccept\b|\bheader\b", re.I), "Accept header"),
        (re.compile(r"\bquery\b|\bparam\b", re.I), "query param"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "service_mesh": [
        (re.compile(r"\bistio\b", re.I), "Istio"),
        (re.compile(r"\blinkerd\b", re.I), "Linkerd"),
        (re.compile(r"\bcilium\b", re.I), "Cilium"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "zt_level": [
        (re.compile(r"\bidentity.?only\b", re.I), "identity-only"),
        (re.compile(r"\bdevice\b", re.I), "device+identity"),
        (re.compile(r"\bmicro.?seg\b", re.I), "micro-segmentation"),
        (re.compile(r"\bfull\b|\bzta\b", re.I), "full ZTA"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "ordering": [
        (re.compile(r"\btotal\b", re.I), "total order"),
        (re.compile(r"\bpartition\b", re.I), "partition order"),
        (re.compile(r"\bper.?key\b|\bkey\b", re.I), "per-key order"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "dlq": [
        (re.compile(r"\bretry\b|\b3x\b|\b3 times\b", re.I), "retry 3x then DLQ"),
        (re.compile(r"\bimmediate\b", re.I), "immediate DLQ"),
        (re.compile(r"\bskip\b|\blog\b", re.I), "skip and log"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
    "consumer_group": [
        (re.compile(r"\bdedicated\b|\bper.?service\b", re.I), "dedicated per service"),
        (re.compile(r"\bshared\b", re.I), "shared group"),
        (re.compile(r"\bfan.?out\b|\bbroadcast\b", re.I), "fan-out broadcast"),
        (re.compile(r"\bsingle\b", re.I), "single consumer"),
    ],
    "delivery": [
        (re.compile(r"\bexactly.?once\b", re.I), "exactly-once"),
        (re.compile(r"\bat.?least.?once\b", re.I), "at-least-once"),
        (re.compile(r"\bat.?most.?once\b", re.I), "at-most-once"),
    ],
    "schema": [
        (re.compile(r"\bbackward\b", re.I), "backward compatible"),
        (re.compile(r"\bforward\b", re.I), "forward compatible"),
        (re.compile(r"\bfull\b", re.I), "full compatible"),
        (re.compile(r"\bnone\b|\bno\b", re.I), "none"),
    ],
}


def _keyword_match(key: str, text: str) -> str | None:
    for pat, label in _KEYWORD_MAPS.get(key, []):
        if pat.search(text):
            return label
    return None


def _parse_answers(user_text: str, questions: list[dict[str, Any]]) -> dict[str, str]:
    """Extract one answer per question from numbered or keyword-matched user text."""
    answers: dict[str, str] = {}

    # Try splitting on numbered markers: "1. " / "1) "
    raw_parts = _NUMBERED_SPLIT_RE.split(user_text.strip())
    parts = [p.strip() for p in raw_parts if p.strip()]

    if len(parts) >= len(questions):
        for i, q in enumerate(questions):
            answers[q["key"]] = parts[i]
        return answers

    # Fall back to keyword matching per question key
    for q in questions:
        matched = _keyword_match(q["key"], user_text)
        answers[q["key"]] = matched if matched is not None else user_text.strip()

    return answers


# ---------------------------------------------------------------------------
# Mermaid extraction from user messages
# ---------------------------------------------------------------------------

_MERMAID_FENCE_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_GENERIC_FENCE_RE = re.compile(r"```\s*\n(.*?)```", re.DOTALL)
_MERMAID_START_RE = re.compile(
    r"^(flowchart|graph|sequenceDiagram|classDiagram|erDiagram)\s", re.IGNORECASE
)


def extract_mermaid_from_message(text: str) -> str | None:
    """Extract Mermaid source from a fenced code block or bare graph/flowchart text."""
    m = _MERMAID_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _GENERIC_FENCE_RE.search(text)
    if m:
        candidate = m.group(1).strip()
        if _MERMAID_START_RE.match(candidate):
            return candidate
    stripped = text.strip()
    if _MERMAID_START_RE.match(stripped):
        return stripped
    return None


# ---------------------------------------------------------------------------
# Mermaid enhancement helpers
# ---------------------------------------------------------------------------

def _extract_direction(mermaid_src: str) -> str:
    first = mermaid_src.strip().split("\n")[0].strip().upper()
    for d in ("LR", "TD", "RL", "BT", "TB"):
        if d in first:
            return d
    return "LR"


_ARROW_NO_LABEL_RE = re.compile(r"(-->\s*)([^|])")


def _add_edge_label(line: str, label: str) -> str:
    """Insert *label* on a Mermaid edge that has no pipe-label yet."""
    if "|" in line and re.search(r"-->\s*\|", line):
        return line  # already has a label
    return _ARROW_NO_LABEL_RE.sub(rf"-->|{label}| \2", line, count=1)


def _build_refined_mermaid(
    original_mermaid: str,
    canvas_type: str,
    answers: dict[str, str],
) -> str:
    """Dispatch to canvas-specific refinement builder."""
    resolved = _resolve_canvas(canvas_type)
    if resolved == "sdc":
        return _refine_sdc(original_mermaid, answers)
    if resolved == "eda":
        return _refine_eda(original_mermaid, answers)
    return _refine_ndc(original_mermaid, answers)


# ---------------------------------------------------------------------------
# NDC refinement
# ---------------------------------------------------------------------------

def _refine_ndc(original: str, answers: dict[str, str]) -> str:
    direction = _extract_direction(original)
    encryption = answers.get("encryption", "none")
    bcap = answers.get("bcap", "none")
    microseg = answers.get("microseg", "none")
    ha = answers.get("ha", "none")
    mgmt = answers.get("mgmt_plane", "none")

    new_classdefs: list[str] = []
    new_nodes: list[str] = []
    body_lines: list[str] = []

    for i, raw_line in enumerate(original.split("\n")):
        stripped = raw_line.strip()
        if i == 0:
            continue  # skip header — re-added below
        if stripped.startswith("classDef") or stripped.startswith("class "):
            continue  # strip existing class defs; we'll add our own
        if stripped.startswith("%%"):
            body_lines.append(f"    {stripped}" if not raw_line.startswith("    ") else raw_line)
            continue
        if "-->" in stripped and encryption.lower() not in ("none", "no", ""):
            enhanced = _add_edge_label(stripped, encryption)
            body_lines.append(f"    {enhanced}")
        else:
            body_lines.append(raw_line if raw_line.startswith("    ") else f"    {stripped}")

    if encryption.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef encrypted fill:#e3f2fd,stroke:#1565c0,color:#0d47a1"
        )
    if bcap.lower() not in ("none", "no", ""):
        bcap_display = "DISA BCAP" if "disa" in bcap.lower() else bcap.strip()
        new_classdefs.append(
            "    classDef bcap fill:#5C4EE5,stroke:#3A2D9E,color:#fff,stroke-width:2px"
        )
        new_nodes.append(f'    BCAP["🔒 {bcap_display}\\nBoundary Gateway"]:::bcap')
    if microseg.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef zone fill:#f3e5f5,stroke:#7b1fa2,color:#4a148c"
        )
        new_nodes.append(f'    MICROSEG["🔐 Microseg: {microseg}"]:::zone')
    if ha.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef ha fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20"
        )
        new_nodes.append(f'    HA["🔄 HA: {ha}"]:::ha')
    if mgmt.lower() not in ("none", "no", "in-band", ""):
        new_classdefs.append(
            "    classDef mgmt fill:#fff9c4,stroke:#f57f17,color:#e65100"
        )
        new_nodes.append(f'    MGMT["⚙️ Mgmt Plane: {mgmt}"]:::mgmt')

    result = [f"graph {direction}"]
    result.extend(new_classdefs)
    result.extend(ln for ln in body_lines if ln.strip())
    result.extend(new_nodes)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# SDC refinement
# ---------------------------------------------------------------------------

def _refine_sdc(original: str, answers: dict[str, str]) -> str:
    direction = _extract_direction(original)
    cb = answers.get("circuit_breaker", "none")
    auth = answers.get("auth_scheme", "none")
    versioning = answers.get("api_versioning", "none")
    mesh = answers.get("service_mesh", "none")
    zt = answers.get("zt_level", "none")

    new_classdefs: list[str] = []
    new_nodes: list[str] = []
    body_lines: list[str] = []

    for i, raw_line in enumerate(original.split("\n")):
        stripped = raw_line.strip()
        if i == 0:
            continue
        if stripped.startswith("classDef") or stripped.startswith("class "):
            continue
        if stripped.startswith("%%"):
            body_lines.append(f"    {stripped}" if not raw_line.startswith("    ") else raw_line)
            continue
        if "-->" in stripped and auth.lower() not in ("none", "no", ""):
            enhanced = _add_edge_label(stripped, auth)
            body_lines.append(f"    {enhanced}")
        else:
            body_lines.append(raw_line if raw_line.startswith("    ") else f"    {stripped}")

    if auth.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef authed fill:#fce4ec,stroke:#880e4f,color:#4a0019"
        )
    if cb.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef cb fill:#FF9800,stroke:#e65100,color:#fff,stroke-width:2px"
        )
        if "aggressive" in cb.lower():
            cb_cfg = "5 s / 3 failures"
        elif "lenient" in cb.lower():
            cb_cfg = "60 s / 10 failures"
        else:
            cb_cfg = "30 s / 5 failures"
        new_nodes.append(f'    CB["⚡ Circuit Breaker\\n({cb_cfg})"]:::cb')
    if versioning.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef ver fill:#e8eaf6,stroke:#3949ab,color:#1a237e"
        )
        new_nodes.append(f'    VER["📋 API Versioning: {versioning}"]:::ver')
    if mesh.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef mesh fill:#e8f5e9,stroke:#1b5e20,color:#1b5e20"
        )
        new_nodes.append(f'    MESH["🕸️ Service Mesh: {mesh}\\nControl Plane"]:::mesh')
    if zt.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef zt fill:#fff3e0,stroke:#e65100,color:#bf360c"
        )
        new_nodes.append(f'    ZT["🛡️ Zero Trust: {zt}"]:::zt')

    result = [f"graph {direction}"]
    result.extend(new_classdefs)
    result.extend(ln for ln in body_lines if ln.strip())
    result.extend(new_nodes)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# EDA refinement
# ---------------------------------------------------------------------------

def _refine_eda(original: str, answers: dict[str, str]) -> str:
    direction = _extract_direction(original)
    ordering = answers.get("ordering", "none")
    dlq = answers.get("dlq", "none")
    cg = answers.get("consumer_group", "none")
    delivery = answers.get("delivery", "none")
    schema = answers.get("schema", "none")

    new_classdefs: list[str] = []
    new_nodes: list[str] = []
    body_lines: list[str] = []

    for i, raw_line in enumerate(original.split("\n")):
        stripped = raw_line.strip()
        if i == 0:
            continue
        if stripped.startswith("classDef") or stripped.startswith("class "):
            continue
        if stripped.startswith("%%"):
            body_lines.append(f"    {stripped}" if not raw_line.startswith("    ") else raw_line)
            continue
        if "-->" in stripped and ordering.lower() not in ("none", "no", ""):
            enhanced = _add_edge_label(stripped, ordering)
            body_lines.append(f"    {enhanced}")
        else:
            body_lines.append(raw_line if raw_line.startswith("    ") else f"    {stripped}")

    if ordering.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef ordered fill:#e8eaf6,stroke:#1a237e,color:#0d1b60"
        )
    if dlq.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef dlq fill:#f44336,stroke:#b71c1c,color:#fff,stroke-width:2px"
        )
        new_nodes.append('    DLQ["☠️ Dead Letter Queue"]:::dlq')
        if "retry" in dlq.lower():
            dlq_trigger = "after 3 retries"
        elif "immediate" in dlq.lower():
            dlq_trigger = "on first failure"
        else:
            dlq_trigger = "on failure"
        new_nodes.append(f"    CG -->|{dlq_trigger}| DLQ")
    if cg.lower() not in ("none", "no", "single consumer", ""):
        new_classdefs.append(
            "    classDef cg fill:#0078D4,stroke:#004a8e,color:#fff"
        )
        if "dedicated" in cg.lower():
            cg_display = "Dedicated per Service"
        elif "fan-out" in cg.lower() or "broadcast" in cg.lower():
            cg_display = "Fan-out Broadcast"
        else:
            cg_display = cg.strip()
        new_nodes.append(f'    CG["👥 Consumer Group\\n({cg_display})"]:::cg')
    if delivery.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef delivery fill:#e0f2f1,stroke:#004d40,color:#00251a"
        )
        new_nodes.append(f'    DELIVERY["📨 Delivery: {delivery}"]:::delivery')
    if schema.lower() not in ("none", "no", ""):
        new_classdefs.append(
            "    classDef schema fill:#fafafa,stroke:#424242,color:#212121"
        )
        new_nodes.append(f'    SCHEMA["📜 Schema: {schema}"]:::schema')

    result = [f"graph {direction}"]
    result.extend(new_classdefs)
    result.extend(ln for ln in body_lines if ln.strip())
    result.extend(new_nodes)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Applied-refinement summary
# ---------------------------------------------------------------------------

def _summarize_answers(answers: dict[str, str], canvas_type: str) -> str:
    resolved = _resolve_canvas(canvas_type)
    all_qs = _QUESTIONS.get(resolved, _QUESTIONS["ndc"])
    key_to_title = {
        q["key"]: re.sub(r"\*+|\d+\.\s*", "", q["text"].split("\n")[0]).strip().rstrip("?").strip()
        for q in all_qs
    }
    lines = []
    for key, val in answers.items():
        title = key_to_title.get(key, key.replace("_", " ").title())
        lines.append(f"  • {title}: **{val}**")
    return "\n".join(lines) if lines else "  (no refinements collected)"


# ---------------------------------------------------------------------------
# In-memory session state
# ---------------------------------------------------------------------------

_SESSIONS: dict[str, dict[str, Any]] = {}


def has_active_refine_session(session_id: str) -> bool:
    return session_id in _SESSIONS


def _get_session(session_id: str) -> dict[str, Any] | None:
    return _SESSIONS.get(session_id)


def _set_session(session_id: str, state: dict[str, Any]) -> None:
    _SESSIONS[session_id] = state


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_refine(
    raw_diagram: str,
    canvas_type: str,
    session_id: str,
) -> dict[str, Any]:
    """Start a refine session from a pasted Mermaid diagram.

    Parses the diagram, stores session state, and returns the first batch
    of canvas-specific clarifying questions plus the original diagram.
    """
    canvas_display = _CANVAS_DISPLAY.get(_resolve_canvas(canvas_type), canvas_type.upper())

    try:
        from tools.simulation.parsers.mermaid_parser import parse_mermaid
        graph_json = parse_mermaid(raw_diagram)
    except Exception:
        graph_json = {"nodes": [], "edges": []}

    questions_p1 = _get_questions(canvas_type, 1)

    _set_session(session_id, {
        "canvas_type": canvas_type,
        "phase": 1,
        "original_mermaid": raw_diagram,
        "graph_json": graph_json,
        "answers": {},
        "questions_asked": [q["key"] for q in questions_p1],
    })

    node_count = len(graph_json.get("nodes", []))
    edge_count = len(graph_json.get("edges", []))
    q_text = _format_questions(questions_p1)
    diagram_fence = f"```mermaid\n{raw_diagram}\n```"

    reply = (
        f"[REFINE — {canvas_display}]\n\n"
        f"Diagram parsed: {node_count} node{'s' if node_count != 1 else ''}, "
        f"{edge_count} edge{'s' if edge_count != 1 else ''}.\n\n"
        "Please answer the following questions to refine your diagram:\n\n"
        f"{q_text}\n\n"
        "_Reply with numbered answers, e.g. `1. mTLS  2. DISA BCAP  3. zone-level`_\n"
        "_Or type `done` to finalize with the diagram as-is._"
    )

    return {
        "reply": reply,
        "mode": "refine",
        "phase": 1,
        "diagram_mermaid": diagram_fence,
        "is_complete": False,
        "questions": [q["key"] for q in questions_p1],
    }


def continue_refine(
    session_id: str,
    user_text: str,
    canvas_type: str,
) -> dict[str, Any]:
    """Process user's answers and advance the refine conversation.

    Phase 1 answers → apply primary refinements, ask phase 2 questions.
    Phase 2 (or 'done') → apply all refinements, emit final diagram.
    """
    state = _get_session(session_id)

    if state is None:
        return {
            "reply": (
                "[REFINE] No active refine session found. "
                "Start one with `/refine` followed by your Mermaid diagram in a code block.\n\n"
                "```\n/refine\n```mermaid\ngraph LR\n    A --> B\n```\n```"
            ),
            "mode": "refine",
            "phase": 0,
            "diagram_mermaid": None,
            "is_complete": False,
        }

    resolved_ct = state["canvas_type"]
    current_phase = state["phase"]
    canvas_display = _CANVAS_DISPLAY.get(_resolve_canvas(resolved_ct), resolved_ct.upper())
    original_mermaid = state["original_mermaid"]

    # If user says "done" skip to final
    if user_text.strip().lower() in ("done", "finalize", "finish", "complete"):
        all_answers = state["answers"]
        refined = _build_refined_mermaid(original_mermaid, resolved_ct, all_answers)
        _SESSIONS.pop(session_id, None)
        diagram_fence = f"```mermaid\n{refined}\n```"
        applied = _summarize_answers(all_answers, resolved_ct)
        reply = (
            f"[REFINE — {canvas_display}] Complete\n\n"
            f"Applied {len(all_answers)} refinement(s):\n{applied}\n\n"
            "Your diagram is ready. Use `/troubleshoot` to analyze fault paths "
            "or `/explain` for a guided walkthrough."
        )
        return {
            "reply": reply,
            "mode": "refine",
            "phase": "complete",
            "diagram_mermaid": diagram_fence,
            "is_complete": True,
        }

    questions_this_phase = _get_questions(resolved_ct, current_phase)
    new_answers = _parse_answers(user_text, questions_this_phase)
    state["answers"].update(new_answers)
    all_answers = state["answers"]

    # Apply all collected answers so far to produce an intermediate diagram
    refined_mermaid = _build_refined_mermaid(original_mermaid, resolved_ct, all_answers)
    applied_summary = _summarize_answers(new_answers, resolved_ct)

    if current_phase == 1:
        questions_p2 = _get_questions(resolved_ct, 2)
        state["phase"] = 2
        state["questions_asked"] += [q["key"] for q in questions_p2]
        _set_session(session_id, state)

        q_text = _format_questions(questions_p2)
        diagram_fence = f"```mermaid\n{refined_mermaid}\n```"

        reply = (
            f"[REFINE — {canvas_display}]\n\n"
            f"Applied {len(new_answers)} primary refinement(s):\n{applied_summary}\n\n"
            "Diagram updated. A few secondary questions:\n\n"
            f"{q_text}\n\n"
            "_Reply with numbered answers, or type `done` to finalize._"
        )

        return {
            "reply": reply,
            "mode": "refine",
            "phase": 2,
            "diagram_mermaid": diagram_fence,
            "is_complete": False,
        }

    # Phase 2+ — finalize
    _SESSIONS.pop(session_id, None)
    diagram_fence = f"```mermaid\n{refined_mermaid}\n```"
    all_applied = _summarize_answers(all_answers, resolved_ct)

    reply = (
        f"[REFINE — {canvas_display}] Complete\n\n"
        f"All {len(all_answers)} refinement(s) applied:\n{all_applied}\n\n"
        "Your diagram is ready. Use `/troubleshoot` to analyze fault paths "
        "or `/explain` for a guided walkthrough."
    )

    return {
        "reply": reply,
        "mode": "refine",
        "phase": "complete",
        "diagram_mermaid": diagram_fence,
        "is_complete": True,
    }
