"""
Vision LLM image ingestor for architecture diagrams.

Accepts PNG/JPG/SVG bytes, sends to a vision-capable LLM with a
canvas-aware prompt, and returns a normalized graph_json plus a
canvas_type suggestion.
"""

import base64
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Canvas type definitions
# ---------------------------------------------------------------------------

# Canonical canvas keys and their descriptions for prompt context
CANVAS_DESCRIPTIONS: Dict[str, str] = {
    "NDC": "Network Design Canvas — physical/logical network topology with routers, switches, firewalls, VPCs, subnets, and connectivity links",
    "SDC": "Software/Service Design Canvas — microservice architecture map with services, APIs, databases, message queues, and inter-service calls",
    "PDC": "Process Design Canvas — business process / workflow diagram with actors, tasks, decision gates, and data flows",
    "BDC": "Business Design Canvas — business model or capability map with value streams, functions, and organizational units",
    "DDC": "Data Design Canvas — data model or entity-relationship diagram with entities, attributes, and relationships",
    "ODC": "Operations Design Canvas — infrastructure-as-code / deployment topology with cloud resources, containers, and orchestration",
    "IDC": "Integration Design Canvas — system-of-systems or enterprise integration map with integration patterns and adapters",
    "EDA": "Event-Driven Architecture — event mesh with producers, consumers, topics/queues, brokers, and event flows",
    "auto": "Architecture diagram — type unknown; infer the canvas type from the diagram content",
}

# Keywords that indicate each canvas type (used for deterministic fallback)
_CANVAS_KEYWORDS: Dict[str, list] = {
    "NDC": ["router", "switch", "firewall", "subnet", "vpc", "vlan", "cidr", "gateway", "topology", "network"],
    "SDC": ["service", "microservice", "api", "endpoint", "rest", "grpc", "container", "pod", "kubernetes", "docker"],
    "EDA": ["event", "topic", "queue", "broker", "kafka", "pubsub", "producer", "consumer", "stream", "sns", "sqs"],
    "DDC": ["entity", "table", "column", "foreign key", "primary key", "relationship", "schema", "er diagram"],
    "ODC": ["terraform", "ansible", "k8s", "cluster", "node", "deployment", "replica", "ingress", "helm"],
    "PDC": ["task", "workflow", "process", "actor", "lane", "bpmn", "decision", "gateway", "swimlane"],
    "IDC": ["integration", "adapter", "esb", "middleware", "connector", "etl", "pipeline", "transform"],
    "BDC": ["capability", "value stream", "business unit", "department", "function", "stakeholder"],
}

# ---------------------------------------------------------------------------
# Media type detection
# ---------------------------------------------------------------------------

def _detect_media_type(image_bytes: bytes) -> str:
    """Return MIME type based on magic bytes; raise ValueError for unsupported."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    # SVG — XML text; check for common SVG markers
    try:
        head = image_bytes[:512].decode("utf-8", errors="ignore")
        if "<svg" in head or "<?xml" in head:
            return "image/svg+xml"
    except Exception:
        pass
    raise ValueError(f"Unsupported image format (magic bytes: {image_bytes[:4]!r})")


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an expert software architect. Your task is to analyze architecture diagrams and extract a structured graph representation.

Return ONLY valid JSON (no markdown fences, no prose) matching this exact schema:
{
  "canvas_type": "<one of: NDC, SDC, PDC, BDC, DDC, ODC, IDC, EDA>",
  "canvas_type_confidence": <0.0–1.0>,
  "nodes": [
    {
      "id": "<unique string>",
      "label": "<human-readable name>",
      "type": "<node category, e.g. service, database, router, queue, actor, entity>",
      "properties": {}
    }
  ],
  "edges": [
    {
      "source": "<node id>",
      "target": "<node id>",
      "label": "<relationship or protocol, optional>",
      "directed": true
    }
  ],
  "metadata": {
    "title": "<diagram title if visible>",
    "description": "<one-sentence summary>",
    "node_count": <int>,
    "edge_count": <int>
  }
}

Rules:
- Every node must have a unique id (use slugified label if no id is visible).
- Edge source/target must reference existing node ids.
- If canvas_type cannot be determined, use the closest match and set confidence < 0.5.
- Do not invent nodes or edges not visible in the diagram.
- Preserve all labels exactly as shown.
"""


def _build_user_message(image_bytes: bytes, media_type: str, canvas_type: str) -> list:
    """Build the multimodal user message list for the LLM request."""
    canvas_hint = CANVAS_DESCRIPTIONS.get(canvas_type, CANVAS_DESCRIPTIONS["auto"])
    text_part = (
        f"Analyze this architecture diagram and extract its graph structure.\n\n"
        f"Canvas context: {canvas_hint}\n\n"
        f"Return the JSON graph as specified in your system instructions."
    )

    if media_type == "image/svg+xml":
        # SVG is text — send as text content instead of an image block
        svg_text = image_bytes.decode("utf-8", errors="replace")
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"SVG source:\n```xml\n{svg_text}\n```\n\n{text_part}"},
                ],
            }
        ]

    b64_data = base64.b64encode(image_bytes).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                },
                {"type": "text", "text": text_part},
            ],
        }
    ]


# ---------------------------------------------------------------------------
# Deterministic fallback (no LLM)
# ---------------------------------------------------------------------------

def _guess_canvas_type(image_bytes: bytes) -> str:
    """Heuristic canvas type guess from raw bytes (keyword scan)."""
    try:
        text = image_bytes.decode("utf-8", errors="ignore").lower()
    except Exception:
        return "SDC"  # safe default
    scores: Dict[str, int] = {}
    for ctype, keywords in _CANVAS_KEYWORDS.items():
        scores[ctype] = sum(1 for kw in keywords if kw in text)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "SDC"


def _empty_graph(canvas_type: str, error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "canvas_type": canvas_type,
        "canvas_type_confidence": 0.0,
        "nodes": [],
        "edges": [],
        "metadata": {
            "title": "",
            "description": error or "Could not extract graph — no LLM available",
            "node_count": 0,
            "edge_count": 0,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_image(image_bytes: bytes, canvas_type: str = "auto") -> Dict[str, Any]:
    """Send an architecture diagram image to a vision LLM and return a graph.

    Args:
        image_bytes: Raw PNG, JPG, or SVG bytes.
        canvas_type: Hint for the LLM ('auto' = infer from content).
                     One of NDC, SDC, PDC, BDC, DDC, ODC, IDC, EDA, auto.

    Returns:
        dict with keys: canvas_type, canvas_type_confidence, nodes, edges, metadata.
        On LLM failure returns an empty graph with error metadata.
    """
    if not image_bytes:
        raise ValueError("image_bytes must be non-empty")

    canvas_type = canvas_type.upper() if canvas_type.lower() != "auto" else "auto"
    if canvas_type not in CANVAS_DESCRIPTIONS:
        raise ValueError(f"Unknown canvas_type '{canvas_type}'. Valid: {list(CANVAS_DESCRIPTIONS)}")

    try:
        media_type = _detect_media_type(image_bytes)
    except ValueError as exc:
        return _empty_graph(
            _guess_canvas_type(image_bytes) if canvas_type == "auto" else canvas_type,
            str(exc),
        )

    # Try LLM path
    try:
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        router = get_router()
        messages = _build_user_message(image_bytes, media_type, canvas_type)

        request = LLMRequest(
            messages=messages,
            system_prompt=_SYSTEM_PROMPT,
            model="diagram_extraction",  # resolved via llm_config.yaml routing
            max_tokens=4096,
            temperature=0.1,
        )

        response = router.invoke("diagram_extraction", request)
        raw = response.content.strip()

        # Strip markdown fences if the model added them anyway
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        graph = json.loads(raw)

        # Normalize: ensure required keys exist
        graph.setdefault("canvas_type", canvas_type if canvas_type != "auto" else "SDC")
        graph.setdefault("canvas_type_confidence", 0.5)
        graph.setdefault("nodes", [])
        graph.setdefault("edges", [])
        graph.setdefault("metadata", {})
        graph["metadata"].setdefault("node_count", len(graph["nodes"]))
        graph["metadata"].setdefault("edge_count", len(graph["edges"]))

        # Override canvas_type if caller specified a non-auto value
        if canvas_type != "auto":
            graph["canvas_type"] = canvas_type

        return graph

    except ImportError:
        # LLM subsystem not installed — deterministic fallback
        guessed = _guess_canvas_type(image_bytes) if canvas_type == "auto" else canvas_type
        return _empty_graph(guessed, "LLM subsystem not available (ImportError)")

    except Exception as exc:  # noqa: BLE001
        # LLM call failed or JSON parse error — return minimal graph
        guessed = _guess_canvas_type(image_bytes) if canvas_type == "auto" else canvas_type
        return _empty_graph(guessed, f"LLM extraction failed: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest architecture diagram image via vision LLM")
    parser.add_argument("image", help="Path to PNG/JPG/SVG file")
    parser.add_argument("--canvas-type", default="auto", help="Canvas type hint (default: auto)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"Error: {image_path} not found", file=sys.stderr)
        sys.exit(1)

    result = ingest_image(image_path.read_bytes(), canvas_type=args.canvas_type)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Canvas type : {result['canvas_type']} (confidence: {result['canvas_type_confidence']:.2f})")
        print(f"Nodes       : {len(result['nodes'])}")
        print(f"Edges       : {len(result['edges'])}")
        print(f"Title       : {result['metadata'].get('title', '')}")
        print(f"Description : {result['metadata'].get('description', '')}")
