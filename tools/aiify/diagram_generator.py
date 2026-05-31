# CUI // SP-CTI
"""AI-ify Canvas — target-architecture Mermaid diagram generator.

Deterministic (no LLM). Given a scan's opportunities, renders a Mermaid
flowchart depicting the *target* architecture: each current module flows into a
proposed AI component, grouped into three layers — GenAI, Classic ML, and
Agentic AI — with the recommended model attached. Embedded into the generated
PRD as a fenced mermaid block and rendered inline in the wizard via mermaid.js.
"""
from __future__ import annotations

import pathlib

_PARADIGM_LABEL = {
    "llm_generation": "LLM Generation",
    "nlp_extractor": "NLP Extractor",
    "embedding_search": "Embedding Search",
    "ml_classifier": "ML Classifier",
    "anomaly_detection": "Anomaly Detection",
    "decision_agent": "Decision Agent",
    "agentic_trigger": "Agentic Trigger",
}

_PARADIGM_LAYER = {
    "llm_generation": "GenAI",
    "nlp_extractor": "GenAI",
    "embedding_search": "GenAI",
    "ml_classifier": "ML",
    "anomaly_detection": "ML",
    "decision_agent": "Agentic",
    "agentic_trigger": "Agentic",
}

_LAYER_TITLE = {
    "GenAI": "GenAI Layer",
    "ML": "Classic ML Layer",
    "Agentic": "Agentic AI Layer",
}

_PARADIGM_MODEL = {
    "ml_classifier": "claude-haiku-4-5",
    "nlp_extractor": "claude-haiku-4-5",
    "llm_generation": "claude-sonnet-4-6",
    "agentic_trigger": "claude-sonnet-4-6",
    "anomaly_detection": "claude-haiku-4-5",
    "embedding_search": "claude-haiku-4-5",
    "decision_agent": "claude-opus-4-7",
}


def _san(text: str) -> str:
    """Escape a label for a Mermaid quoted string."""
    return str(text).replace('"', "'").replace("\n", " ").strip()[:48] or "?"


def _short_path(path: str) -> str:
    """Last two path segments — keeps node labels readable."""
    parts = pathlib.PurePosixPath(str(path).replace("\\", "/")).parts
    return "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else str(path))


def build_architecture_mermaid(scan: dict, opps: list[dict], title: str | None = None, *, max_opps: int = 25) -> str:
    """Return Mermaid flowchart source (no fences) for the target architecture.

    Args:
        scan:    Scan dict with project info.
        opps:    Opportunities to diagram.
        title:   Optional diagram title.
        max_opps: Cap the diagram to the top-N opportunities by composite score
                  so Mermaid.js can render without choking on hundreds of nodes.
    """
    proj = _san(scan.get("project_summary") or scan.get("input_ref") or "Existing System")
    if not opps:
        return (f'flowchart TD\n  ROOT["{proj}"]\n'
                f'  NONE["No AI-augmentable modules identified"]\n  ROOT --> NONE')

    # Sort by composite_score descending and slice so the diagram stays readable
    sorted_opps = sorted(opps, key=lambda o: float(o.get("composite_score", 0) or 0), reverse=True)
    truncated = sorted_opps[:max_opps]
    total = len(sorted_opps)
    shown = len(truncated)

    lines: list[str] = ["flowchart TD"]
    if title:
        lines.append(f"  %% {_san(title)}")
    lines.append(f'  ROOT["{proj}"]')

    layers: dict[str, dict] = {"GenAI": {}, "ML": {}, "Agentic": {}}
    module_nodes: list[tuple[str, str, str]] = []
    models: dict[str, str] = {}
    paradigm_model: dict[str, str] = {}

    for i, opp in enumerate(truncated):
        paradigm = opp.get("ai_paradigm") or "llm_generation"
        layer = _PARADIGM_LAYER.get(paradigm, "GenAI")
        p_node = f"P_{paradigm}"
        layers[layer][p_node] = _PARADIGM_LABEL.get(paradigm, paradigm)
        model = opp.get("il_recommended_model") or _PARADIGM_MODEL.get(paradigm, "claude-sonnet-4-6")
        m_node = "M_" + model.replace(".", "_").replace("-", "_").replace(":", "_")
        models[m_node] = model
        paradigm_model[p_node] = m_node
        mod_node = f"SRC{i}"
        module_nodes.append((mod_node, _short_path(opp.get("module_path", f"module{i}")), p_node))

    lines.append('  subgraph CUR["Current System"]')
    for mod_node, label, _ in module_nodes:
        lines.append(f'    {mod_node}["{label}"]')
    lines.append("  end")
    lines.append("  ROOT --> CUR")

    for layer in ("GenAI", "ML", "Agentic"):
        bucket = layers[layer]
        if not bucket:
            continue
        lines.append(f'  subgraph {layer}["{_LAYER_TITLE[layer]}"]')
        for p_node, p_label in bucket.items():
            lines.append(f'    {p_node}["{p_label}"]')
        lines.append("  end")

    for mod_node, _, p_node in module_nodes:
        lines.append(f"  {mod_node} -->|augment| {p_node}")

    lines.append('  subgraph MODELS["Model Routing (IL-aware)"]')
    for m_node, m_label in models.items():
        lines.append(f'    {m_node}["{_san(m_label)}"]')
    lines.append("  end")
    seen = set()
    for p_node, m_node in paradigm_model.items():
        if (p_node, m_node) not in seen:
            seen.add((p_node, m_node))
            lines.append(f"  {p_node} --> {m_node}")

    # Truncation note so users know this is a subset
    if shown < total:
        lines.append(f'  NOTE["… and {total - shown} more module(s)"]')
        lines.append("  ROOT -.-> NOTE")

    lines.append("  classDef genai fill:#1e3a5f,stroke:#6ee7b7,color:#fff;")
    lines.append("  classDef ml fill:#3f2d5c,stroke:#c4b5fd,color:#fff;")
    lines.append("  classDef agentic fill:#5c3a2d,stroke:#fdba74,color:#fff;")
    for layer, css in (("GenAI", "genai"), ("ML", "ml"), ("Agentic", "agentic")):
        bucket = list(layers[layer])
        if bucket:
            lines.append(f"  class {','.join(bucket)} {css};")

    return "\n".join(lines)


def build_architecture_mermaid_block(scan: dict, opps: list[dict], title: str | None = None, *, max_opps: int = 25) -> str:
    """Same as build_architecture_mermaid but wrapped in a fenced code block."""
    return "```mermaid\n" + build_architecture_mermaid(scan, opps, title, max_opps=max_opps) + "\n```"
