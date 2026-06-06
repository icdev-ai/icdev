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
            "SELECT * FROM slides_decks ORDER BY created_at DESC LIMIT 100"
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
    from tools.slides.templates import list_deck_templates
    return render_template(
        "slides/new.html",
        deck_types=DECK_TYPES,
        themes=THEMES,
        source_types=SOURCE_TYPES,
        default_theme=DEFAULT_THEME,
        default_deck_type=DEFAULT_DECK_TYPE,
        templates=list_deck_templates(),
        env=os.environ,
    )


def _parse_slide_viz(s: dict) -> dict:
    """Deserialize viz JSON columns into chart/table/diagram/kpis keys."""
    for col, key in (("chart_json", "chart"), ("table_json", "table"),
                     ("diagram_json", "diagram"), ("kpis_json", "kpis"),
                     ("dashboard_json", "dashboard"), ("elements_json", "elements")):
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

    return _render_present(deck, slides_rows)


def _slides_models(slides_rows) -> list:
    """Parse DB slide rows into model dicts (bullets + viz payloads)."""
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
    return slides


def _render_present(deck: dict, slides_rows, readonly: bool = False):
    """Render the interactive presenter view for a deck (shared by /present + /share)."""
    theme = deck.get("theme", DEFAULT_THEME)
    slides = _slides_models(slides_rows)
    from tools.viz.deck_model import build_deck_model
    model = build_deck_model(deck, slides, theme)
    deck_json = json.dumps(model).replace("</", "<\\/")  # safe inline <script> embed
    return render_template("slides/present.html", deck=deck, deck_json=deck_json,
                           colors=model["colors"], readonly=readonly)


@slides_bp.route("/share/<token>")
def share_view(token: str):
    """Public read-only presenter view via share token."""
    if not token or len(token) < 8:
        return "Invalid share link", 404
    conn = _conn()
    try:
        deck = conn.execute("SELECT * FROM slides_decks WHERE share_token = ?", (token,)).fetchone()
        if not deck:
            return "Share link not found", 404
        deck = dict(deck)
        rows = conn.execute(
            "SELECT * FROM slides_slides WHERE deck_id = ? ORDER BY position", (deck["deck_id"],)
        ).fetchall()
    finally:
        conn.close()
    return _render_present(deck, rows, readonly=True)


@slides_bp.route("/api/<int:deck_id>/share", methods=["POST"])
def api_share(deck_id: int):
    """Create (or return existing) a read-only share token for a deck."""
    import secrets
    conn = _conn()
    try:
        row = conn.execute("SELECT share_token FROM slides_decks WHERE deck_id = ?", (deck_id,)).fetchone()
        if not row:
            return jsonify({"error": "deck not found"}), 404
        token = (dict(row).get("share_token") or "").strip()
        if not token:
            token = secrets.token_urlsafe(16)
            conn.execute("UPDATE slides_decks SET share_token = ? WHERE deck_id = ?", (token, deck_id))
            conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "token": token, "url": f"/slides/share/{token}"})


@slides_bp.route("/api/<int:deck_id>/download.pdf")
def api_download_pdf(deck_id: int):
    """Export the deck as a landscape PDF (WYSIWYG with the editor/presenter)."""
    conn = _conn()
    try:
        deck = conn.execute("SELECT * FROM slides_decks WHERE deck_id = ?", (deck_id,)).fetchone()
        if not deck:
            return jsonify({"error": "deck not found"}), 404
        deck = dict(deck)
        rows = conn.execute(
            "SELECT * FROM slides_slides WHERE deck_id = ? ORDER BY position", (deck_id,)
        ).fetchall()
    finally:
        conn.close()
    from tools.viz.deck_model import build_deck_model
    from tools.slides.pdf_export import build_pdf
    theme = deck.get("theme", DEFAULT_THEME)
    model = build_deck_model(deck, _slides_models(rows), theme)
    pdf_bytes = build_pdf(model["slides"], title=deck.get("title", "Deck"), theme=theme)
    safe = "".join(ch for ch in str(deck.get("title", "deck")) if ch.isalnum() or ch in " -_").strip() or "deck"
    from flask import Response
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.pdf"'})


# ── API Routes ───────────────────────────────────────────────────────────────

@slides_bp.route("/api/<int:deck_id>/rename", methods=["POST"])
def api_rename(deck_id: int):
    """Rename a deck and/or set its tags. Body: {title?, tags?}."""
    data = request.get_json(silent=True) or {}
    sets, params = [], []
    if "title" in data:
        sets.append("title = ?"); params.append(str(data["title"])[:255] or "Untitled")
    if "tags" in data:
        sets.append("tags = ?"); params.append(str(data["tags"])[:300])
    if not sets:
        return jsonify({"error": "nothing to update"}), 400
    conn = _conn()
    try:
        conn.execute(f"UPDATE slides_decks SET {', '.join(sets)} WHERE deck_id = ?", (*params, deck_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@slides_bp.route("/api/<int:deck_id>/duplicate-deck", methods=["POST"])
def api_duplicate_deck(deck_id: int):
    """Clone a deck and all its slides. Returns the new deck_id."""
    conn = _conn()
    try:
        d = conn.execute("SELECT * FROM slides_decks WHERE deck_id = ?", (deck_id,)).fetchone()
        if not d:
            return jsonify({"error": "deck not found"}), 404
        d = dict(d)
        cur = conn.execute(
            "INSERT INTO slides_decks (title, deck_type, theme, status, source_types, slide_count) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING deck_id",
            ((str(d.get("title", "Deck")) + " (copy)")[:255], d.get("deck_type", "custom"),
             d.get("theme", "midnight_executive"), "completed",
             d.get("source_types", "[]"), d.get("slide_count", 0)),
        )
        new_id = int(cur.fetchone()[0])
        rows = conn.execute(
            "SELECT * FROM slides_slides WHERE deck_id = ? ORDER BY position", (deck_id,)
        ).fetchall()
        for r in rows:
            r = dict(r)
            conn.execute(
                "INSERT INTO slides_slides (deck_id, position, slide_type, title, bullets, speaker_notes, "
                "image_path, chart_json, table_json, diagram_json, kpis_json, dashboard_json, elements_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, r.get("position"), r.get("slide_type", "content"), r.get("title", ""),
                 r.get("bullets", "[]"), r.get("speaker_notes", ""), r.get("image_path"),
                 r.get("chart_json"), r.get("table_json"), r.get("diagram_json"),
                 r.get("kpis_json"), r.get("dashboard_json"), r.get("elements_json")),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "deck_id": new_id})


@slides_bp.route("/api/<int:deck_id>", methods=["DELETE"])
def api_delete_deck(deck_id: int):
    """Delete a deck and all its slides."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM slides_slides WHERE deck_id = ?", (deck_id,))
        conn.execute("DELETE FROM slides_decks WHERE deck_id = ?", (deck_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@slides_bp.route("/api/templates/new", methods=["POST"])
def api_template_new():
    """Create a new deck from a starter template. Body: {template, theme, title}."""
    data = request.get_json(silent=True) or {}
    from tools.slides import templates as _tpl
    key = data.get("template", "blank")
    theme = data.get("theme", DEFAULT_THEME)
    title = data.get("title") or _tpl.DECK_TEMPLATES.get(key, {}).get("name", "New Deck")
    slides = _tpl.build_from_template(key)
    if not slides:
        return jsonify({"error": "unknown template"}), 400
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO slides_decks (title, deck_type, theme, status, source_types, slide_count) "
            "VALUES (?, 'custom', ?, 'completed', ?, ?) RETURNING deck_id",
            (title, theme, json.dumps(["template:" + key]), len(slides)),
        )
        deck_id = int(cur.fetchone()[0])
        for s in slides:
            _append_slide(conn, deck_id, s)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "deck_id": deck_id})


@slides_bp.route("/api/<int:deck_id>/theme", methods=["POST"])
def api_set_theme(deck_id: int):
    """Switch a deck's theme cohesively. Body: {theme}."""
    data = request.get_json(silent=True) or {}
    theme = data.get("theme")
    if theme not in THEMES:
        return jsonify({"error": "invalid theme"}), 400
    conn = _conn()
    try:
        conn.execute("UPDATE slides_decks SET theme = ? WHERE deck_id = ?", (theme, deck_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "theme": theme})


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
    prompt = data.get("prompt", "")

    from tools.slides.engine import DeckEngine, DeckRequest
    req = DeckRequest(
        title=title,
        deck_type=deck_type,
        theme=theme,
        sources=sources,
        max_slides=max_slides,
        upload_text=upload_text,
        prompt=prompt,
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
    """Re-render and serve the .pptx for download.

    Rebuilds from the CURRENT slides (including freeform ``elements_json`` saved
    by the editor) so the PowerPoint is always WYSIWYG with the editor/presenter.
    """
    conn = _conn()
    try:
        deck = conn.execute(
            "SELECT * FROM slides_decks WHERE deck_id = ? AND status IN ('completed','auto')",
            (deck_id,),
        ).fetchone()
        if not deck:
            return jsonify({"error": "Deck not found or not completed"}), 404
        deck = dict(deck)
        rows = conn.execute(
            "SELECT * FROM slides_slides WHERE deck_id = ? ORDER BY position", (deck_id,)
        ).fetchall()
    finally:
        conn.close()

    theme = deck.get("theme", DEFAULT_THEME)
    title = deck.get("title", "ICDEV Presentation")
    slides = []
    for row in rows:
        s = dict(row)
        if isinstance(s.get("bullets"), str):
            try:
                s["bullets"] = json.loads(s["bullets"])
            except Exception:
                s["bullets"] = []
        _parse_slide_viz(s)
        slides.append(s)

    try:
        from tools.slides import pptx_builder
        pptx_path = pptx_builder.build(slides, theme=theme, title=title)
    except Exception as exc:
        logger.warning("slides: PPTX re-render failed: %s", exc)
        # Fall back to the previously generated file if present.
        pptx_path = deck.get("pptx_path")
        if not pptx_path or not Path(pptx_path).exists():
            return jsonify({"error": f"PPTX render failed: {exc}"}), 500

    filename = f"ICDEV_{title[:40].replace(' ', '_')}.pptx"
    return send_file(
        pptx_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@slides_bp.route("/<int:deck_id>/edit")
def edit(deck_id: int):
    """Freeform WYSIWYG slide editor (drag/resize/layer elements, custom text)."""
    conn = _conn()
    try:
        deck = conn.execute("SELECT * FROM slides_decks WHERE deck_id = ?", (deck_id,)).fetchone()
        if not deck:
            return render_template("slides/index.html", decks=[], error="Deck not found"), 404
        deck = dict(deck)
        rows = conn.execute(
            "SELECT * FROM slides_slides WHERE deck_id = ? ORDER BY position", (deck_id,)
        ).fetchall()
    finally:
        conn.close()

    theme = deck.get("theme", DEFAULT_THEME)
    slides = []
    for row in rows:
        s = dict(row)
        if isinstance(s.get("bullets"), str):
            try:
                s["bullets"] = json.loads(s["bullets"])
            except Exception:
                s["bullets"] = []
        _parse_slide_viz(s)
        slides.append(s)

    from tools.viz.deck_model import build_deck_model
    model = build_deck_model(deck, slides, theme)
    deck_json = json.dumps(model).replace("</", "<\\/")
    return render_template("slides/editor.html", deck=deck, deck_json=deck_json,
                           colors=model["colors"])


@slides_bp.route("/api/<int:deck_id>/elements", methods=["POST"])
def api_save_elements(deck_id: int):
    """Persist freeform per-slide content. Body: {slides:[{slide_id|position,
    elements, title?, speaker_notes?}]}. Targets by slide_id when present."""
    data = request.get_json(silent=True) or {}
    updates = data.get("slides", [])
    if not isinstance(updates, list):
        return jsonify({"error": "slides must be a list"}), 400

    conn = _conn()
    try:
        saved = 0
        for u in updates:
            els = u.get("elements")
            if els is None:
                continue
            sets = ["elements_json = ?"]
            params: list = [json.dumps(els)]
            if "title" in u:
                sets.append("title = ?"); params.append(str(u["title"])[:255])
            if "speaker_notes" in u:
                sets.append("speaker_notes = ?"); params.append(str(u["speaker_notes"]))
            if u.get("slide_id") is not None:
                where, wparam = "slide_id = ?", int(u["slide_id"])
            elif u.get("position") is not None:
                where, wparam = "position = ?", int(u["position"])
            else:
                continue
            conn.execute(
                f"UPDATE slides_slides SET {', '.join(sets)} WHERE deck_id = ? AND {where}",
                (*params, deck_id, wparam),
            )
            saved += 1
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "slides_saved": saved})


def _renumber(conn, deck_id: int) -> None:
    conn.execute(
        "UPDATE slides_decks SET slide_count = "
        "(SELECT COUNT(*) FROM slides_slides WHERE deck_id = ?) WHERE deck_id = ?",
        (deck_id, deck_id),
    )


@slides_bp.route("/api/<int:deck_id>/slides/add", methods=["POST"])
def api_slide_add(deck_id: int):
    """Insert a blank content slide at the end. Returns its slide_id + position."""
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "New Slide"))[:255]
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), 0) FROM slides_slides WHERE deck_id = ?", (deck_id,)
        ).fetchone()
        pos = int((row[0] if row else 0) or 0) + 1
        cur = conn.execute(
            "INSERT INTO slides_slides (deck_id, position, slide_type, title, bullets, "
            "speaker_notes, elements_json) VALUES (?, ?, 'content', ?, '[]', '', '[]') RETURNING slide_id",
            (deck_id, pos, title),
        )
        sid = int(cur.fetchone()[0])
        _renumber(conn, deck_id)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "slide_id": sid, "position": pos})


@slides_bp.route("/api/<int:deck_id>/slides/<int:slide_id>/duplicate", methods=["POST"])
def api_slide_duplicate(deck_id: int, slide_id: int):
    """Duplicate a slide (all columns) to the end. Returns the new slide_id."""
    conn = _conn()
    try:
        src = conn.execute(
            "SELECT * FROM slides_slides WHERE slide_id = ? AND deck_id = ?", (slide_id, deck_id)
        ).fetchone()
        if not src:
            return jsonify({"error": "slide not found"}), 404
        src = dict(src)
        row = conn.execute(
            "SELECT COALESCE(MAX(position), 0) FROM slides_slides WHERE deck_id = ?", (deck_id,)
        ).fetchone()
        pos = int((row[0] if row else 0) or 0) + 1
        cur = conn.execute(
            "INSERT INTO slides_slides (deck_id, position, slide_type, title, bullets, speaker_notes, "
            "image_path, chart_json, table_json, diagram_json, kpis_json, dashboard_json, elements_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING slide_id",
            (deck_id, pos, src.get("slide_type", "content"),
             (str(src.get("title", "")) + " (copy)")[:255],
             src.get("bullets", "[]"), src.get("speaker_notes", ""), src.get("image_path"),
             src.get("chart_json"), src.get("table_json"), src.get("diagram_json"),
             src.get("kpis_json"), src.get("dashboard_json"), src.get("elements_json")),
        )
        sid = int(cur.fetchone()[0])
        _renumber(conn, deck_id)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "slide_id": sid, "position": pos})


@slides_bp.route("/api/<int:deck_id>/slides/<int:slide_id>", methods=["DELETE"])
def api_slide_delete(deck_id: int, slide_id: int):
    """Delete a slide."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM slides_slides WHERE slide_id = ? AND deck_id = ?", (slide_id, deck_id))
        _renumber(conn, deck_id)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@slides_bp.route("/api/<int:deck_id>/slides/reorder", methods=["POST"])
def api_slide_reorder(deck_id: int):
    """Set slide positions from an ordered list of slide_ids. Body: {slide_ids:[...]}."""
    data = request.get_json(silent=True) or {}
    ids = data.get("slide_ids", [])
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "slide_ids list required"}), 400
    conn = _conn()
    try:
        for i, sid in enumerate(ids):
            conn.execute(
                "UPDATE slides_slides SET position = ? WHERE slide_id = ? AND deck_id = ?",
                (i + 1, int(sid), deck_id),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "count": len(ids)})


@slides_bp.route("/api/<int:deck_id>/upload-image", methods=["POST"])
def api_upload_image(deck_id: int):
    """Accept an image upload for the editor; store it and return a serve URL."""
    import hashlib
    from urllib.parse import quote

    from tools.slides import pptx_builder

    f = request.files.get("image")
    if f is None or not f.filename:
        return jsonify({"error": "no image"}), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return jsonify({"error": "unsupported image type"}), 400

    data = f.read()
    if len(data) > 12 * 1024 * 1024:  # 12 MB cap
        return jsonify({"error": "image too large (max 12MB)"}), 400

    uploads = Path(pptx_builder._OUTPUT_DIR) / "images" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    name = f"deck{deck_id}_{hashlib.sha256(data).hexdigest()[:16]}{ext}"
    target = uploads / name
    try:
        target.write_bytes(data)
    except OSError as exc:
        return jsonify({"error": f"write failed: {exc}"}), 500

    return jsonify({"url": "/slides/api/image?path=" + quote(str(target))})


def _append_slide(conn, deck_id: int, slide: dict) -> int:
    """Insert a slide at the next position and refresh slide_count. Returns position."""
    row = conn.execute(
        "SELECT COALESCE(MAX(position), 0) FROM slides_slides WHERE deck_id = ?", (deck_id,)
    ).fetchone()
    pos = int((row[0] if row else 0) or 0) + 1

    def _vz(k):
        v = slide.get(k)
        return json.dumps(v) if v else None

    conn.execute(
        "INSERT INTO slides_slides (deck_id, position, slide_type, title, bullets, speaker_notes, "
        "image_path, chart_json, table_json, diagram_json, kpis_json, dashboard_json, elements_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (deck_id, pos, slide.get("slide_type", "content"), str(slide.get("title", ""))[:255],
         json.dumps(slide.get("bullets", [])), slide.get("speaker_notes", ""),
         slide.get("image_path"), _vz("chart"), _vz("table"), _vz("diagram"),
         _vz("kpis"), _vz("dashboard"), _vz("elements")),
    )
    conn.execute(
        "UPDATE slides_decks SET slide_count = "
        "(SELECT COUNT(*) FROM slides_slides WHERE deck_id = ?) WHERE deck_id = ?",
        (deck_id, deck_id),
    )
    return pos


def _store_data_url_image(deck_id: int, data_url: str) -> str | None:
    """Decode a data: URL image, store it, return a serve URL (or None)."""
    import base64
    import hashlib
    from urllib.parse import quote
    from tools.slides import pptx_builder

    if not data_url or "," not in data_url:
        return None
    header, b64 = data_url.split(",", 1)
    ext = ".png"
    if "image/jpeg" in header:
        ext = ".jpg"
    elif "image/webp" in header:
        ext = ".webp"
    elif "image/svg" in header:
        ext = ".svg"
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None
    if ext == ".svg":  # rasterization not available server-side; store as-is is unsafe to embed → reject
        return None
    uploads = Path(pptx_builder._OUTPUT_DIR) / "images" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    target = uploads / f"cap{deck_id}_{hashlib.sha256(raw).hexdigest()[:16]}{ext}"
    try:
        target.write_bytes(raw)
    except OSError:
        return None
    return "/slides/api/image?path=" + quote(str(target))


@slides_bp.route("/<int:deck_id>/add-from-canvas")
def add_from_canvas(deck_id: int):
    """Picker: list all canvas designs to add into this deck (native diagram slides)."""
    from tools.slides.canvas_bridge import list_designs
    try:
        designs = list_designs()
    except Exception as exc:
        logger.warning("slides: canvas enumeration failed: %s", exc)
        designs = []
    return render_template("slides/add_from_canvas.html", deck_id=deck_id, designs=designs)


@slides_bp.route("/api/<int:deck_id>/capture", methods=["POST"])
def api_capture(deck_id: int):
    """Add a captured artifact to the deck as a new slide.

    Body (one of):
      {canvas_key, design_id}      → native pull of a saved canvas design
      {graph_json, title}          → native diagram from client-sent graph
      {kind:'image', image_data, title}  → image fallback (data URL)
      {kind:'chart', chart, title} → native chart spec
    """
    data = request.get_json(silent=True) or {}
    from tools.slides import canvas_bridge
    from tools.viz import elements as _elements

    slide = None
    if data.get("canvas_key") and data.get("design_id") is not None:
        slide = canvas_bridge.design_to_slide(data["canvas_key"], data["design_id"])
        if slide is None:
            return jsonify({"error": "design not found or empty"}), 404
    elif data.get("graph_json"):
        slide = canvas_bridge.graph_to_diagram_slide(
            data["graph_json"], data.get("title", "Canvas Design"))
    elif data.get("kind") == "chart" and data.get("chart"):
        slide = {"slide_type": "data", "title": data.get("title", "Chart"),
                 "speaker_notes": "", "chart": data["chart"]}
        slide["elements"] = _elements.elements_to_dicts(_elements.auto_layout(slide))
    elif data.get("kind") == "image" and data.get("image_data"):
        url = _store_data_url_image(deck_id, data["image_data"])
        if not url:
            return jsonify({"error": "could not store image"}), 400
        el = _elements.Element("image", 0.08, 0.16, 0.84, 0.74, z=0, payload={"src": url})
        title_el = _elements.Element("text", 0.04, 0.04, 0.92, 0.1, z=1,
                                     payload={"text": data.get("title", "Captured Graphic")},
                                     style={"fontSize": 26, "bold": True, "color": "#C8A951"})
        slide = {"slide_type": "content", "title": data.get("title", "Captured Graphic"),
                 "speaker_notes": "", "image_path": None,
                 "elements": _elements.elements_to_dicts([title_el, el])}
    else:
        return jsonify({"error": "unsupported capture payload"}), 400

    conn = _conn()
    try:
        pos = _append_slide(conn, deck_id, slide)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "position": pos})


@slides_bp.route("/api/aggregate-canvases", methods=["POST"])
def api_aggregate_canvases():
    """Auto-build a new deck aggregating every canvas design as diagram slides."""
    from tools.slides import canvas_bridge
    from tools.slides import pptx_builder

    data = request.get_json(silent=True) or {}
    theme = data.get("theme", DEFAULT_THEME)
    title = data.get("title", "Canvas Designs Overview")
    slides = canvas_bridge.build_overview_slides(theme=theme)

    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO slides_decks (title, deck_type, theme, status, source_types, slide_count) "
            "VALUES (?, 'executive_overview', ?, 'completed', ?, ?) RETURNING deck_id",
            (title, theme, json.dumps(["canvas"]), len(slides)),
        )
        deck_id = int(cur.fetchone()[0])
        for s in slides:
            _append_slide(conn, deck_id, s)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()

    try:
        pptx_builder.build(slides, theme=theme, title=title)
    except Exception:
        pass
    return jsonify({"ok": True, "deck_id": deck_id, "slides": len(slides)})


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

    _mimes = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp"}
    suffix = target.suffix.lower()
    if not target.exists() or suffix not in _mimes:
        return jsonify({"error": "not found"}), 404
    return send_file(str(target), mimetype=_mimes[suffix])


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
