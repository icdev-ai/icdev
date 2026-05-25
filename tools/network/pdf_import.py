# [CUI // SP-CTI]
"""ICDEV™ NDC — PDF import for network diagrams.

Two-tier extraction strategy:

1. **Vector path** (``_import_pdf_vector``): uses ``pdfplumber`` to parse
   rectangles + lines + text as native PDF primitives. Works out of the
   box for Visio/drawio/Lucidchart "Export to PDF" — no vision LLM, no
   OCR, deterministic, coords preserved. This is the #1 real-world use
   case and the one that was previously ignored.

2. **Raster path** (``rasterize_pdf_pages``): uses ``pypdfium2`` (or
   ``pdf2image`` as legacy fallback) to render pages to PNG for
   vision-LLM / OCR consumption. Called by ``network_ingester`` when the
   vector path returns no usable nodes (e.g., scanned PDFs).

Multi-page is first-class in both paths: every page is traversed, node
IDs are page-scoped, and nodes carry a ``page`` field when not on page 0.

Public API:
- ``import_pdf(path) -> {nodes, edges, _pages, _errors}``
- ``rasterize_pdf_pages(path, dpi=200) -> list[Path]``  (PNG paths)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import math
import tempfile
from pathlib import Path

logger = get_logger(__name__)


# ── Public entry points ────────────────────────────────────────────────


def import_pdf(file_path: str, *, spatial_tolerance: float = 18.0) -> dict:
    """Parse a PDF diagram into a graph dict via the vector path.

    Returns ``{nodes, edges, _pages, _errors}``. ``nodes`` and ``edges``
    are empty if the PDF has no extractable vector shapes (e.g., a scan);
    callers should then fall back to the raster/vision pipeline.

    Args:
        file_path: Path to a .pdf file.
        spatial_tolerance: Pixels of slack when matching line endpoints
            to rectangle centers (default 8.0). Tune up for hand-drawn
            PDFs where endpoints don't land exactly on shape centers.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        logger.info("pdfplumber not installed; vector PDF extraction unavailable")
        return {"nodes": [], "edges": [], "_pages": 0,
                "_errors": ["pdfplumber not installed"]}

    return _import_pdf_vector(file_path, pdfplumber, spatial_tolerance)


def rasterize_pdf_pages(
    file_path: str,
    *,
    dpi: int = 200,
    max_pages: int | None = None,
) -> list[Path]:
    """Render PDF pages to PNG files for vision/OCR fallback.

    Prefers ``pypdfium2`` (pure-Python wheel, bundles PDFium, air-gap
    safe) and falls back to ``pdf2image``+Poppler. Returns a list of
    PNG paths in a temp directory; caller is responsible for cleanup.

    Args:
        file_path: Path to a .pdf file.
        dpi: Render resolution. 200 is a good default; 300+ helps for
            small text or scanned originals.
        max_pages: Cap the number of pages rendered (None = all).
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="ni_pdf_"))
    out: list[Path] = []

    # Preferred: pypdfium2
    try:
        import pypdfium2 as pdfium  # type: ignore

        pdf = pdfium.PdfDocument(file_path)
        n_pages = len(pdf)
        if max_pages is not None:
            n_pages = min(n_pages, max_pages)
        scale = dpi / 72.0  # PDF default is 72 DPI
        for i in range(n_pages):
            page = pdf[i]
            pil_image = page.render(scale=scale).to_pil()
            png_path = tmp_dir / f"page_{i + 1:03d}.png"
            pil_image.save(str(png_path), "PNG")
            out.append(png_path)
        pdf.close()
        return out
    except ImportError:
        pass
    except Exception as e:
        logger.warning("pypdfium2 render failed (%s); trying pdf2image", e)

    # Legacy: pdf2image (requires poppler binary)
    try:
        from pdf2image import convert_from_path  # type: ignore

        kwargs = {"dpi": dpi}
        if max_pages is not None:
            kwargs["last_page"] = max_pages
        images = convert_from_path(file_path, **kwargs)
        for i, img in enumerate(images):
            png_path = tmp_dir / f"page_{i + 1:03d}.png"
            img.save(str(png_path), "PNG")
            out.append(png_path)
        return out
    except ImportError:
        logger.info("Neither pypdfium2 nor pdf2image available; cannot rasterize PDF")
        return []
    except Exception as e:
        logger.warning("pdf2image render failed: %s", e)
        return []


# ── Vector extraction ──────────────────────────────────────────────────


def _import_pdf_vector(
    file_path: str,
    pdfplumber_mod,
    spatial_tolerance: float,
) -> dict:
    """Vector path: rects → nodes, lines → edges."""
    nodes: list[dict] = []
    edges: list[dict] = []
    errors: list[str] = []
    page_count = 0

    try:
        with pdfplumber_mod.open(file_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                page_count += 1
                try:
                    _parse_page(
                        page, page_idx, nodes, edges, errors,
                        spatial_tolerance=spatial_tolerance,
                    )
                except Exception as e:
                    errors.append(f"page {page_idx}: {e}")
    except Exception as e:
        errors.append(f"pdf open: {e}")

    result: dict = {"nodes": nodes, "edges": edges, "_pages": page_count}
    if errors:
        result["_errors"] = errors
    return result


def _parse_page(
    page,
    page_idx: int,
    nodes: list,
    edges: list,
    errors: list,
    *,
    spatial_tolerance: float,
) -> None:
    """Extract rectangles as nodes and lines/curves as edges from a page."""
    # Rectangles → candidate node shapes
    rects = list(page.rects or [])
    # pdfplumber exposes "lines" for straight lines; "curves" covers poly/bezier
    lines = list(page.lines or [])
    curves = list(page.curves or [])
    chars = list(page.chars or [])

    # ── Nodes ──
    # Filter: drop page-border rects (too large) and slivers (too small)
    page_area = max(1.0, (page.width or 1.0) * (page.height or 1.0))
    node_candidates = []
    for r in rects:
        w = float(r.get("width", 0) or 0)
        h = float(r.get("height", 0) or 0)
        area = w * h
        if area <= 0:
            continue
        # Page borders / backgrounds: skip anything > 70% of page
        if area > page_area * 0.70:
            continue
        # Slivers: skip anything with either dim < 6 pt
        if w < 6 or h < 6:
            continue
        node_candidates.append(r)

    # Group overlapping rects (Visio exports sometimes emit a fill rect +
    # a stroke rect at the same coords — collapse to one node)
    merged = _merge_overlapping_rects(node_candidates)

    rect_to_node: dict[int, str] = {}  # index in merged -> node_id
    for i, r in enumerate(merged):
        x0 = float(r["x0"])
        top = float(r["top"])
        w = float(r["width"])
        h = float(r["height"])
        cx = x0 + w / 2
        cy = top + h / 2

        label = _text_inside(chars, x0, top, x0 + w, top + h)
        if not label:
            label = f"Shape-p{page_idx}-{i}"

        node_id = f"pdf-p{page_idx}-{i}"
        node = {
            "id": node_id,
            "label": label,
            "type": "imported",
            "x": round(x0),
            "y": round(top),
            "width": round(w),
            "height": round(h),
        }
        if page_idx > 0:
            node["page"] = page_idx
        nodes.append(node)
        rect_to_node[i] = node_id
        # Stash center for line endpoint matching
        merged[i]["_cx"] = cx
        merged[i]["_cy"] = cy
        merged[i]["_node_id"] = node_id

    if not merged:
        return  # no shapes — skip edge work entirely

    # ── Edges ──
    # Build edge segments from lines + straight curves.
    # pdfplumber lines expose (x0, top, x1, bottom) as the bbox, not the
    # endpoints — for diagonal lines the actual endpoints could be
    # (x0,top)→(x1,bottom) OR (x0,bottom)→(x1,top). We emit both and let
    # the edge-dedup step collapse duplicates; the wrong diagonal won't
    # match any rects and is silently dropped.
    segments: list[tuple[float, float, float, float]] = []
    for ln in lines:
        try:
            x0 = float(ln["x0"])
            x1 = float(ln["x1"])
            top = float(ln["top"])
            bot = float(ln["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        segments.append((x0, top, x1, bot))
        if abs(top - bot) > 1 and abs(x0 - x1) > 1:
            # Diagonal — also emit the opposite diagonal
            segments.append((x0, bot, x1, top))
    for cv in curves:
        pts = cv.get("pts") or []
        if len(pts) < 2:
            continue
        try:
            # Per-segment polyline endpoints
            for j in range(len(pts) - 1):
                x0, y0 = pts[j]
                x1, y1 = pts[j + 1]
                segments.append((float(x0), float(y0), float(x1), float(y1)))
            # Also emit the full chord (first→last point) so that bezier
            # arrows whose intermediate points sit far from rect borders
            # still produce a single src→dst edge.
            if len(pts) > 2:
                segments.append((
                    float(pts[0][0]), float(pts[0][1]),
                    float(pts[-1][0]), float(pts[-1][1]),
                ))
        except (TypeError, ValueError):
            continue

    # Chars NOT inside any node rect — candidates for connector labels
    free_chars = [
        ch for ch in chars
        if not _point_in_any_rect(merged, _char_center(ch))
    ]

    seen_edges: set = set()
    for seg_idx, (x0, y0, x1, y1) in enumerate(segments):
        # Skip extremely short segments (decorative ticks, arrow heads, etc.)
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 20:
            continue

        src_idx = _nearest_rect(merged, x0, y0, spatial_tolerance)
        dst_idx = _nearest_rect(merged, x1, y1, spatial_tolerance)
        if src_idx is None or dst_idx is None or src_idx == dst_idx:
            continue

        src_id = merged[src_idx]["_node_id"]
        dst_id = merged[dst_idx]["_node_id"]
        # Dedupe undirected duplicates (two-segment arrows end up identical)
        key = tuple(sorted([src_id, dst_id]))
        if key in seen_edges:
            continue
        seen_edges.add(key)

        # Edge label: text near the segment midpoint (interface refs like
        # "Gi0/1", "ge-0/0/24", or VLAN/speed annotations).
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        label = _text_near_point(free_chars, mx, my, radius=24)

        edges.append({
            "id": f"pdf-p{page_idx}-e-{seg_idx}",
            "source": src_id,
            "target": dst_id,
            "label": label,
        })


def _merge_overlapping_rects(rects: list[dict]) -> list[dict]:
    """Collapse rects whose centers are within each other's bounds.

    Visio and drawio often emit two rects per shape (fill + stroke). The
    fill is typically slightly inset from the stroke. Without dedup, each
    shape doubles up.
    """
    if not rects:
        return []
    out: list[dict] = []
    for r in rects:
        try:
            x0 = float(r.get("x0", 0))
            top = float(r.get("top", 0))
            w = float(r.get("width", 0))
            h = float(r.get("height", 0))
        except (TypeError, ValueError):
            continue
        cx, cy = x0 + w / 2, top + h / 2
        absorbed = False
        for existing in out:
            ex0 = float(existing["x0"])
            etop = float(existing["top"])
            ew = float(existing["width"])
            eh = float(existing["height"])
            if ex0 <= cx <= ex0 + ew and etop <= cy <= etop + eh:
                # Keep the larger of the two (stroke rect usually wins)
                if w * h > ew * eh:
                    existing["x0"] = x0
                    existing["top"] = top
                    existing["width"] = w
                    existing["height"] = h
                absorbed = True
                break
        if not absorbed:
            out.append({"x0": x0, "top": top, "width": w, "height": h})
    return out


def _text_inside(chars: list, x0: float, top: float, x1: float, bot: float) -> str:
    """Return text whose character centers fall inside the rect.

    Reads chars in PDF order (pdfplumber emits them left-to-right,
    top-to-bottom for most generators). Inserts a space between chars
    separated by a large X gap — crude but adequate for shape labels.
    """
    pieces: list[str] = []
    prev_x1: float | None = None
    prev_top: float | None = None
    for ch in chars:
        try:
            cx0 = float(ch.get("x0", 0))
            cx1 = float(ch.get("x1", 0))
            ctop = float(ch.get("top", 0))
            cbot = float(ch.get("bottom", 0))
        except (TypeError, ValueError):
            continue
        ccx = (cx0 + cx1) / 2
        ccy = (ctop + cbot) / 2
        if not (x0 <= ccx <= x1 and top <= ccy <= bot):
            continue
        text = ch.get("text", "")
        if prev_top is not None and abs(ctop - prev_top) > 4:
            pieces.append(" ")
        elif prev_x1 is not None and cx0 - prev_x1 > 2:
            pieces.append(" ")
        pieces.append(text)
        prev_x1 = cx1
        prev_top = ctop
    return "".join(pieces).strip()


def _char_center(ch: dict) -> tuple[float, float]:
    try:
        return (
            (float(ch.get("x0", 0)) + float(ch.get("x1", 0))) / 2,
            (float(ch.get("top", 0)) + float(ch.get("bottom", 0))) / 2,
        )
    except (TypeError, ValueError):
        return (0.0, 0.0)


def _point_in_any_rect(merged: list[dict], pt: tuple[float, float]) -> bool:
    px, py = pt
    for r in merged:
        x0 = float(r["x0"])
        top = float(r["top"])
        if x0 <= px <= x0 + float(r["width"]) and top <= py <= top + float(r["height"]):
            return True
    return False


def _text_near_point(
    chars: list,
    mx: float,
    my: float,
    *,
    radius: float,
) -> str:
    """Concatenate chars whose center is within ``radius`` of (mx, my).

    Used to capture connector labels (interface names, speed/VLAN tags)
    that float beside an edge line. Sorts by Y then X to read naturally.
    """
    near = []
    for ch in chars:
        cx, cy = _char_center(ch)
        if math.hypot(cx - mx, cy - my) <= radius:
            near.append((cy, cx, ch.get("text", "")))
    if not near:
        return ""
    near.sort()
    pieces: list[str] = []
    prev_y: float | None = None
    prev_x: float | None = None
    for y, x, t in near:
        if prev_y is not None and abs(y - prev_y) > 4:
            pieces.append(" ")
        elif prev_x is not None and x - prev_x > 2:
            pieces.append(" ")
        pieces.append(t)
        prev_y, prev_x = y, x
    return "".join(pieces).strip()


def _nearest_rect(
    merged: list[dict],
    x: float,
    y: float,
    tolerance: float,
) -> int | None:
    """Return index of the rect whose edge is closest to (x, y), if within
    ``tolerance`` padding. Endpoints usually land on the rect border, not
    the center, so we use edge-distance, not center-distance."""
    best_idx: int | None = None
    best_dist = float("inf")
    for i, r in enumerate(merged):
        x0 = float(r["x0"])
        top = float(r["top"])
        w = float(r["width"])
        h = float(r["height"])
        x1 = x0 + w
        bot = top + h
        # Clamp (x,y) to the rect; distance is then to the clamp point
        cx = min(max(x, x0), x1)
        cy = min(max(y, top), bot)
        d = math.hypot(x - cx, y - cy)
        if d < best_dist:
            best_dist = d
            best_idx = i
    if best_idx is not None and best_dist <= tolerance:
        return best_idx
    return None
