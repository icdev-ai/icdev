# CUI // SP-CTI
"""
Parse PDFs (SSPs, STIGs, architecture review docs) into a normalized graph_json.

Strategy:
  1. Render each PDF page to a PNG via pdf2image.
  2. Pass each rendered page through image_ingestor.ingest_image() with the
     caller-supplied canvas_type hint.
  3. Extract embedded text via pypdf for context.
  4. Merge multi-page results: deduplicate nodes by label, deduplicate edges by
     (source, target, label) tuple.
  5. Pick the dominant canvas_type by confidence-weighted vote across pages.

Public API:
  parse_pdf(file_bytes: bytes, canvas_type: str = 'auto') -> dict

Return schema:
  {
    "source": "pdf",
    "canvas_type": str,               # dominant canvas type across pages
    "canvas_type_confidence": float,
    "nodes": [...],                   # merged, deduplicated nodes
    "edges": [...],                   # merged, deduplicated edges
    "metadata": {
      "title": str,
      "description": str,
      "node_count": int,
      "edge_count": int,
      "page_count": int,
      "text_context": str,            # up to 4000 chars of extracted PDF text
      "pages": [
        {
          "page_number": int,
          "canvas_type": str,
          "canvas_type_confidence": float,
          "node_count": int,
          "edge_count": int,
          "text": str
        }
      ]
    }
  }
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from .image_ingestor import ingest_image  # noqa: E402

# ---------------------------------------------------------------------------
# PDF page rendering (pdf2image / poppler)
# ---------------------------------------------------------------------------

_RENDER_DPI = 150  # 150 DPI balances quality vs token cost for vision LLM


def _render_pdf2image(file_bytes: bytes) -> Optional[List[bytes]]:
    """Render all pages to PNG bytes via pdf2image. Returns None if unavailable."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        return None
    try:
        images = convert_from_bytes(file_bytes, dpi=_RENDER_DPI, fmt="png")
        result = []
        for img in images:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            result.append(buf.getvalue())
        return result
    except Exception:
        return None


def _render_pages(file_bytes: bytes) -> Optional[List[bytes]]:
    """Return list of PNG bytes or None if pdf2image is unavailable."""
    return _render_pdf2image(file_bytes)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_text(file_bytes: bytes) -> Tuple[List[str], str]:
    """Extract per-page text strings and document title via pypdf.

    Returns (page_texts, title). Falls back to ([], '') on any error.
    """
    page_texts: List[str] = []
    title = ""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        meta = reader.metadata or {}
        title = str(meta.get("/Title", "") or "").strip()
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            page_texts.append(text.strip())
    except Exception:
        pass
    return page_texts, title


# ---------------------------------------------------------------------------
# Multi-page merge helpers
# ---------------------------------------------------------------------------

def _merge_graphs(
    page_results: List[Dict[str, Any]],
) -> Tuple[List[dict], List[dict]]:
    """Merge nodes and edges from multiple per-page graph dicts.

    Node deduplication key: normalized label (lowercase, stripped).
    Edge deduplication key: (resolved_source_id, resolved_target_id, label).
    """
    seen_nodes: Dict[str, dict] = {}  # norm_label → canonical node
    id_remap: Dict[str, str] = {}     # page-local id → canonical id
    merged_nodes: List[dict] = []

    seen_edges: set = set()
    merged_edges: List[dict] = []

    for result in page_results:
        for node in result.get("nodes", []):
            raw_id = str(node.get("id", ""))
            label = str(node.get("label", raw_id)).strip()
            norm = label.lower()
            if norm not in seen_nodes:
                canonical_id = raw_id or f"node_{len(seen_nodes)}"
                canonical = {**node, "id": canonical_id}
                seen_nodes[norm] = canonical
                merged_nodes.append(canonical)
                id_remap[raw_id] = canonical_id
            else:
                id_remap[raw_id] = seen_nodes[norm]["id"]

        for edge in result.get("edges", []):
            src = id_remap.get(str(edge.get("source", "")), str(edge.get("source", "")))
            tgt = id_remap.get(str(edge.get("target", "")), str(edge.get("target", "")))
            lbl = str(edge.get("label", "")).strip()
            key = (src, tgt, lbl)
            if key not in seen_edges:
                seen_edges.add(key)
                merged_edges.append({**edge, "source": src, "target": tgt})

    return merged_nodes, merged_edges


def _dominant_canvas(
    page_results: List[Dict[str, Any]],
    default: str = "SDC",
) -> Tuple[str, float]:
    """Return (canvas_type, avg_confidence) by confidence-weighted vote."""
    scores: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for result in page_results:
        ctype = result.get("canvas_type", default)
        conf = float(result.get("canvas_type_confidence", 0.0))
        scores[ctype] = scores.get(ctype, 0.0) + conf
        counts[ctype] = counts.get(ctype, 0) + 1
    if not scores:
        return default, 0.0
    best = max(scores, key=lambda k: scores[k])
    return best, round(scores[best] / max(counts[best], 1), 3)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_pdf(file_bytes: bytes, canvas_type: str = "auto") -> dict:
    """Extract diagrams from a PDF and return a merged graph_json.

    Renders each page to PNG via pdf2image, passes each through
    image_ingestor, then merges all per-page results. Embedded text is
    extracted via pypdf and included as metadata.text_context.

    Args:
        file_bytes: Raw PDF bytes.
        canvas_type: Canvas type hint passed to image_ingestor for each page.
                     One of: NDC, SDC, PDC, BDC, DDC, ODC, IDC, EDA, auto.

    Returns:
        Normalized graph dict — see module docstring for full schema.

    Raises:
        ValueError: If file_bytes is empty or not a valid PDF.
    """
    if not file_bytes:
        raise ValueError("file_bytes must be non-empty")
    if file_bytes[:4] != b"%PDF":
        raise ValueError(f"Not a PDF file (magic bytes: {file_bytes[:4]!r})")

    canvas_hint = canvas_type.upper() if canvas_type.lower() != "auto" else "auto"

    page_texts, doc_title = _extract_text(file_bytes)
    rendered = _render_pages(file_bytes)
    page_count = len(rendered) if rendered else len(page_texts)

    if page_count == 0:
        return _text_only_result(canvas_hint, [], "", 0, "PDF has no pages")

    if rendered is None:
        return _text_only_result(
            canvas_hint, page_texts, doc_title, len(page_texts),
            "No image rendering backend available (install pdf2image + poppler). "
            "Text extracted only.",
        )

    # --- Per-page image ingestion ---
    page_results: List[Dict[str, Any]] = []
    pages_meta: List[dict] = []

    for idx, page_png in enumerate(rendered):
        result = ingest_image(page_png, canvas_type=canvas_hint)
        page_results.append(result)
        pages_meta.append({
            "page_number": idx + 1,
            "canvas_type": result.get("canvas_type", "SDC"),
            "canvas_type_confidence": result.get("canvas_type_confidence", 0.0),
            "node_count": len(result.get("nodes", [])),
            "edge_count": len(result.get("edges", [])),
            "text": page_texts[idx] if idx < len(page_texts) else "",
        })

    merged_nodes, merged_edges = _merge_graphs(page_results)
    dom_type, dom_conf = _dominant_canvas(page_results)

    if canvas_hint != "auto":
        dom_type = canvas_hint

    first_meta = page_results[0].get("metadata", {}) if page_results else {}
    title = doc_title or first_meta.get("title", "")
    description = first_meta.get(
        "description", f"PDF diagram extraction — {page_count} page(s)"
    )
    text_context = "\n\n".join(t for t in page_texts if t)[:4000]

    return {
        "source": "pdf",
        "canvas_type": dom_type,
        "canvas_type_confidence": dom_conf,
        "nodes": merged_nodes,
        "edges": merged_edges,
        "metadata": {
            "title": title,
            "description": description,
            "node_count": len(merged_nodes),
            "edge_count": len(merged_edges),
            "page_count": page_count,
            "text_context": text_context,
            "pages": pages_meta,
        },
    }


def _text_only_result(
    canvas_hint: str,
    page_texts: List[str],
    doc_title: str,
    page_count: int,
    description: str,
) -> dict:
    """Build a minimal result when image rendering is unavailable."""
    default_type = canvas_hint if canvas_hint != "auto" else "SDC"
    text_context = "\n\n".join(t for t in page_texts if t)[:4000]
    return {
        "source": "pdf",
        "canvas_type": default_type,
        "canvas_type_confidence": 0.0,
        "nodes": [],
        "edges": [],
        "metadata": {
            "title": doc_title,
            "description": description,
            "node_count": 0,
            "edge_count": 0,
            "page_count": page_count,
            "text_context": text_context,
            "pages": [
                {
                    "page_number": i + 1,
                    "canvas_type": default_type,
                    "canvas_type_confidence": 0.0,
                    "node_count": 0,
                    "edge_count": 0,
                    "text": page_texts[i] if i < len(page_texts) else "",
                }
                for i in range(page_count)
            ],
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    cli = argparse.ArgumentParser(description="Extract diagrams from a PDF file")
    cli.add_argument("pdf", help="Path to PDF file")
    cli.add_argument("--canvas-type", default="auto", help="Canvas type hint (default: auto)")
    cli.add_argument("--json", action="store_true", help="Emit full JSON output")
    args = cli.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Error: {pdf_path} not found", file=sys.stderr)
        sys.exit(1)

    result = parse_pdf(pdf_path.read_bytes(), canvas_type=args.canvas_type)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Canvas type : {result['canvas_type']} (confidence: {result['canvas_type_confidence']:.2f})")
        print(f"Nodes       : {result['metadata']['node_count']}")
        print(f"Edges       : {result['metadata']['edge_count']}")
        print(f"Pages       : {result['metadata']['page_count']}")
        print(f"Title       : {result['metadata']['title']}")
        print(f"Text chars  : {len(result['metadata']['text_context'])}")
