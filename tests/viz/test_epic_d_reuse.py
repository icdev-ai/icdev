# CUI // SP-CTI
"""VIZ Epic D — PDF reports + canvas exports reuse the Viz Kernel."""
from __future__ import annotations


_NODES = [
    {"id": "a", "label": "Planner", "type": "service", "description": "plans"},
    {"id": "b", "label": "Executor", "type": "service", "description": "executes"},
    {"id": "c", "label": "Store", "type": "database", "description": "state"},
]
_EDGES = [
    {"source": "a", "target": "b", "label": "dispatch"},
    {"source": "b", "target": "c", "relationship": "writes"},
]


def test_agentic_pdf_embeds_kernel_diagram():
    from tools.agentic_ai_canvas.export_pdf import generate_pdf

    pdf = generate_pdf("design-1", _NODES, _EDGES, classification="CUI")
    assert pdf[:5] == b"%PDF-"          # valid PDF
    # A topology image was embedded → PDF carries an image XObject.
    assert b"/Image" in pdf or b"/XObject" in pdf
    assert len(pdf) > 3000


def test_agentic_pdf_no_nodes_still_builds():
    from tools.agentic_ai_canvas.export_pdf import generate_pdf
    pdf = generate_pdf("empty", [], [], classification="CUI")
    assert pdf[:5] == b"%PDF-"          # degrades gracefully, no diagram


# NOTE: Excalidraw diagram export (tools.canvas.export_utils.export_excalidraw)
# doesn't exist on this branch and is out of scope for the BI Dashboard canvas.
