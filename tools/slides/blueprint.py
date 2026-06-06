# CUI // SP-CTI
"""Slide Deck Generator Canvas — Flask Blueprint.

Routes:
  GET  /slides/                  index (deck list + generate button)
  GET  /slides/new               generation wizard
  GET  /slides/<deck_id>         deck detail (slides preview + download)
  POST /api/slides/generate      async generate deck → {deck_id, status}
  POST /api/slides/<id>/revise   revise a single slide → {slide}
  GET  /api/slides/<id>/download serve the .pptx file
  POST /api/slides/<id>/iqe-query IQE natural-language query
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

from tools.slides.constants import (
    DECK_TYPES, THEMES, SOURCE_TYPES, DEFAULT_THEME, DEFAULT_DECK_TYPE
)
from tools.slides.db.init_db import get_connection, init_db
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

slides_bp = Blueprint(
    "slides",
    __name__,
    url_prefix="/slides",
    template_folder="../../tools/dashboard/templates",
)

_INIT_DONE = False


def _ensure_init() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        init_db()
    except Exception as exc:
        logger.warning("slides: DB init error: %s", exc)
    _INIT_DONE = True


@slides_bp.before_request
def _init():
    _ensure_init()


def _conn():
    return get_connection()


# ── Page Routes ──────────────────────────────────────────────────────────────

@slides_bp.route("/")
def index():
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT deck_id, title, deck_type, theme, status, slide_count, "
            "created_at, completed_at FROM slides_decks ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        decks = [dict(row) for row in rows]
    except Exception:
        decks = []
    finally:
        conn.close()

    return render_template(
        "slides/index.html",
        decks=decks,
        deck_types=DECK_TYPES,
        themes=THEMES,
        source_types=SOURCE_TYPES,
    )


@slides_bp.route("/new")
def new_deck():
    return render_template(
        "slides/new.html",
        deck_types=DECK_TYPES,
        themes=THEMES,
        source_types=SOURCE_TYPES,
        default_theme=DEFAULT_THEME,
        default_deck_type=DEFAULT_DECK_TYPE,
        env=os.environ,
    )


def _parse_slide_viz(s: dict) -> dict:
    """Deserialize viz JSON columns into chart/table/diagram/kpis keys."""
    for col, key in (("chart_json", "chart"), ("table_json", "table"),
                     ("diagram_json", "diagram"), ("kpis_json", "kpis"),
                     ("dashboard_json", "dashboard")):
        raw = s.get(col)
        if raw:
            if isinstance(raw, str):
                try:
                    s[key] = json.loads(raw)
                except Exception:
                    s[key] = None
            elif isinstance(raw, dict):
                s[key] = raw
    return s


@slides_bp.route("/<int:deck_id>")
def detail(deck_id: int):
    conn = _conn()
    try:
        deck = conn.execute(
            "SELECT * FROM slides_decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
        if not deck:
            return render_template("slides/index.html", decks=[], error="Deck not found"), 404
        deck = dict(deck)

        slides_rows = conn.execute(
            "SELECT * FROM slides_slides WHERE deck_id = ? ORDER BY position",
            (deck_id,),
        ).fetchall()
        slides = []
        for row in slides_rows:
            s = dict(row)
            # Deserialize bullets JSON
            if isinstance(s.get("bullets"), str):
                try:
                    s["bullets"] = json.loads(s["bullets"])
                except Exception:
                    s["bullets"] = []
            _parse_slide_viz(s)
            slides.append(s)
    finally:
        conn.close()

    return render_template("slides/detail.html", deck=deck, slides=slides)


@slides_bp.route("/<int:deck_id>/present")
def present(deck_id: int):
    """Full-screen, in-browser presenter view (air-gap; charts inline, diagrams via mermaid)."""
    conn = _conn()
    try:
        deck = conn.execute(
            "SELECT * FROM slides_decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
        if not deck:
            return render_template("slides/index.html", decks=[], error="Deck not found"), 404
        deck = dict(deck)
        slides_rows = conn.execute(
            "SELECT * FROM slides_slides WHERE deck_id = ? ORDER BY position", (deck_id,)
        ).fetchall()
    finally:
        conn.close()

    theme = deck.get("theme", DEFAULT_THEME)
    slides = []
    for row in slides_rows:
        s = dict(row)
        if isinstance(s.get("bullets"), str):
            try:
                s["bullets"] = json.loads(s["bullets"])
            except Exception:
                s["bullets"] = []
        _parse_slide_viz(s)
        slides.append(s)

    # Build the interactive deck model (rendered client-side by viz_story.js).
    from tools.viz.deck_model import build_deck_model
    model = build_deck_model(deck, slides, theme)
    deck_json = json.dumps(model).replace("</", "<\\/")  # safe inline <script> embed
    colors = model["colors"]
    return render_template("slides/present.html", deck=deck, deck_json=deck_json, colors=colors)


# ── API Routes ───────────────────────────────────────────────────────────────

@slides_bp.route("/api/generate", methods=["POST"])
def api_generate():
    """Trigger deck generation. Runs synchronously (returns when done)."""
    data = request.get_json(silent=True) or {}
    title = data.get("title", "ICDEV™ Presentation")
    deck_type = data.get("deck_type", DEFAULT_DECK_TYPE)
    theme = data.get("theme", DEFAULT_THEME)
    sources = data.get("sources", ["icdev_capabilities", "canvases", "kanban"])
    max_slides = int(data.get("max_slides", 10))
    upload_text = data.get("upload_text", "")

    from tools.slides.engine import DeckEngine, DeckRequest
    req = DeckRequest(
        title=title,
        deck_type=deck_type,
        theme=theme,
        sources=sources,
        max_slides=max_slides,
        upload_text=upload_text,
    )
    result = DeckEngine().run(req)

    return jsonify({
        "deck_id": result.deck_id,
        "status": result.status,
        "slide_count": len(result.slides),
        "pptx_path": result.pptx_path,
        "error": result.error,
    }), (200 if result.status == "completed" else 500)


@slides_bp.route("/api/<int:deck_id>/revise", methods=["POST"])
def api_revise(deck_id: int):
    """Revise a single slide based on feedback."""
    data = request.get_json(silent=True) or {}
    slide_id = data.get("slide_id")
    feedback = data.get("feedback", "")

    if not slide_id or not feedback:
        return jsonify({"error": "slide_id and feedback required"}), 400

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM slides_slides WHERE slide_id = ? AND deck_id = ?",
            (slide_id, deck_id),
        ).fetchone()
        if not row:
            return jsonify({"error": "Slide not found"}), 404
        slide = dict(row)
        if isinstance(slide.get("bullets"), str):
            try:
                slide["bullets"] = json.loads(slide["bullets"])
            except Exception:
                slide["bullets"] = []
    finally:
        conn.close()

    from tools.slides.content_agent import revise_slide
    revised = revise_slide(slide, feedback, raw_content={})

    # Update DB
    conn = _conn()
    try:
        conn.execute(
            "UPDATE slides_slides SET title=?, bullets=?, speaker_notes=? WHERE slide_id=?",
            (
                revised.get("title", slide.get("title", "")),
                json.dumps(revised.get("bullets", [])),
                revised.get("speaker_notes", ""),
                slide_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"slide": revised})


@slides_bp.route("/api/<int:deck_id>/download")
def api_download(deck_id: int):
    """Serve the .pptx file for download."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT title, pptx_path FROM slides_decks WHERE deck_id = ? AND status = 'completed'",
            (deck_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Deck not found or not completed"}), 404

    title = row["title"] if hasattr(row, "__getitem__") else row[0]
    pptx_path = row["pptx_path"] if hasattr(row, "__getitem__") else row[1]

    if not pptx_path or not Path(pptx_path).exists():
        return jsonify({"error": "PPTX file not found on disk"}), 404

    filename = f"ICDEV_{title[:40].replace(' ', '_')}.pptx"
    return send_file(
        pptx_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@slides_bp.route("/api/image")
def api_image():
    """Serve a generated slide image (PNG) from the slides output dir.

    Fixes the dangling reference in detail.html. Hardened against path
    traversal — only files inside tools/presentations/slides/ are served.
    """
    from tools.slides import pptx_builder

    raw = request.args.get("path", "")
    if not raw:
        return jsonify({"error": "path required"}), 400

    base = Path(pptx_builder._OUTPUT_DIR).resolve()
    try:
        target = Path(raw).resolve()
        target.relative_to(base)  # raises ValueError if outside base
    except (ValueError, OSError):
        return jsonify({"error": "forbidden"}), 403

    if not target.exists() or target.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        return jsonify({"error": "not found"}), 404

    mime = "image/png" if target.suffix.lower() == ".png" else "image/jpeg"
    return send_file(str(target), mimetype=mime)


@slides_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    """IQE natural-language query over slides collections."""
    data = request.get_json(silent=True) or {}
    question = data.get("question", "")
    if not question:
        return jsonify({"error": "question required"}), 400

    try:
        from tools.iqe.dispatcher import dispatch_query
        result = dispatch_query(question, canvas="slides")
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc), "results": []}), 200
