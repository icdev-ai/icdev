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
  GET  /slides/templates                     list uploaded .pptx templates
  GET  /slides/templates/<id>                template detail (shape map + fill form)
  POST /slides/api/templates/upload          upload a .pptx template → {template_id, slides}
  POST /slides/api/templates/<id>/fill       fill selected slides → {deck_id}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from typing import Any

from flask import Blueprint, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from tools.slides.constants import (
    DECK_TYPES, SLIDE_TYPES, THEMES, TONES, CITATION_STYLES, OUTPUT_FORMATS,
    SOURCE_TYPES, DEFAULT_THEME, DEFAULT_DECK_TYPE, DEFAULT_TONE,
    DEFAULT_CITATION_STYLE, DEFAULT_OUTPUT_FORMATS,
    AUDIENCE_MODES, AUDIENCE_MODE_HINTS, PITCH_TEMPLATES,
    DECK_READY_STATUSES,
)
from tools.slides.db.init_db import get_connection, init_db
from tools.logging.icdev_logger import get_logger
from tools.db.storage import sql_placeholder
from tools.dashboard.auth import require_role

logger = get_logger(__name__)

# Costly (LLM generation) and destructive (edit/delete/upload) slide operations
# are gated to authoring roles. Global dashboard auth (register_dashboard_auth)
# already requires a logged-in user for every route; require_role stacks a role
# check on top so viewers cannot trigger LLM spend or mutate/delete decks.
# Read-only routes (index/detail/present, status polling, IQE query) and the
# deterministic asset-smoke E2E probe stay open to any authenticated user.
_SLIDES_WRITE_ROLES = ("admin", "pm", "developer")

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


def _ph(conn):
    """Return the SQL placeholder token for the active backend."""
    return sql_placeholder(conn)


def _insert_and_get_id(conn, table: str, columns: list[str], values: tuple, id_column: str) -> int | None:
    """INSERT a row and return its new id, portable across the PG/SQLite canvas backends.

    RETURNING works on both dialects in principle, but the SQLite fallback
    path (StorageCursor over a plain ``?``-placeholder connection) does not
    reliably surface the RETURNING value — use lastrowid there instead.
    """
    from tools.db.storage import is_pg
    ph = _ph(conn)
    col_list = ", ".join(columns)
    val_list = ", ".join([ph] * len(columns))
    if is_pg(conn):
        cur = conn.execute(
            f"INSERT INTO {table} ({col_list}) VALUES ({val_list}) RETURNING {id_column}", values,
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    cur = conn.execute(f"INSERT INTO {table} ({col_list}) VALUES ({val_list})", values)
    return int(cur.lastrowid) if cur.lastrowid is not None else None


def _templates_upload_dir() -> Path:
    from tools.slides.pptx_builder import _OUTPUT_DIR
    return _OUTPUT_DIR.parent / "templates_uploaded"


# ── Page Routes ──────────────────────────────────────────────────────────────

@slides_bp.route("/")
def index():
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT deck_id, title, deck_type, theme, tone, occasion, status, slide_count, "
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
        tones=TONES,
        citation_styles=CITATION_STYLES,
        output_formats=OUTPUT_FORMATS,
        source_types=SOURCE_TYPES,
        default_theme=DEFAULT_THEME,
        default_deck_type=DEFAULT_DECK_TYPE,
        default_tone=DEFAULT_TONE,
        default_citation_style=DEFAULT_CITATION_STYLE,
        default_output_formats=DEFAULT_OUTPUT_FORMATS,
        audience_modes=AUDIENCE_MODES,
        audience_mode_hints=AUDIENCE_MODE_HINTS,
        pitch_templates=PITCH_TEMPLATES,
        env=os.environ,
    )


@slides_bp.route("/<int:deck_id>")
def detail(deck_id: int):
    conn = _conn()
    try:
        deck = conn.execute(
            f"SELECT * FROM slides_decks WHERE deck_id = {_ph(conn)}", (deck_id,)
        ).fetchone()
        if not deck:
            return render_template("slides/index.html", decks=[], error="Deck not found"), 404
        deck = dict(deck)

        slides_rows = conn.execute(
            f"SELECT * FROM slides_slides WHERE deck_id = {_ph(conn)} ORDER BY position",
            (deck_id,),
        ).fetchall()
        slides = []
        for row in slides_rows:
            s = dict(row)
            # Deserialize JSON columns
            for col in ("bullets", "citations"):
                if isinstance(s.get(col), str):
                    try:
                        s[col] = json.loads(s[col])
                    except Exception:
                        s[col] = []
            slides.append(s)
    finally:
        conn.close()

    # Deserialize deck JSON columns for the template
    for col in ("source_types", "output_formats"):
        if isinstance(deck.get(col), str):
            try:
                deck[col] = json.loads(deck[col])
            except Exception:
                deck[col] = []

    # Deserialize new JSON columns for rich slides
    for slide in slides:
        for col in ("three_scene_config", "excalidraw_elements"):
            if isinstance(slide.get(col), str):
                try:
                    import json as _json
                    slide[col] = _json.loads(slide[col])
                except Exception:
                    slide[col] = None

    return render_template("slides/detail.html", deck=deck, slides=slides, themes=THEMES, tones=TONES, slide_types=SLIDE_TYPES)


@slides_bp.route("/<int:deck_id>/present")
def present(deck_id: int):
    """Full-screen web presentation viewer with Three.js / Mermaid / Excalidraw."""
    import json as _json
    conn = _conn()
    try:
        deck = conn.execute(
            f"SELECT * FROM slides_decks WHERE deck_id = {_ph(conn)}", (deck_id,)
        ).fetchone()
        if not deck:
            return render_template("slides/index.html", decks=[], error="Deck not found"), 404
        deck = dict(deck)

        slides_rows = conn.execute(
            f"SELECT * FROM slides_slides WHERE deck_id = {_ph(conn)} ORDER BY position",
            (deck_id,),
        ).fetchall()
        slides = []
        for row in slides_rows:
            s = dict(row)
            for col in ("bullets", "citations"):
                if isinstance(s.get(col), str):
                    try:
                        s[col] = _json.loads(s[col])
                    except Exception:
                        s[col] = []
            for col in ("three_scene_config", "excalidraw_elements"):
                if isinstance(s.get(col), str):
                    try:
                        s[col] = _json.loads(s[col])
                    except Exception:
                        s[col] = None
            slides.append(s)
    finally:
        conn.close()

    for col in ("source_types", "output_formats"):
        if isinstance(deck.get(col), str):
            try:
                deck[col] = _json.loads(deck[col])
            except Exception:
                deck[col] = []

    # Determine slide transition style from tone
    tone = deck.get("tone", "professional")
    transition = "zoom" if tone == "bold" else ("slide" if tone in ("creative", "adventurous") else "fade")

    return render_template(
        "slides/present.html",
        deck=deck,
        slides=slides,
        transition=transition,
        deck_id=deck_id,
    )


# ── Template-Fill Page Routes ────────────────────────────────────────────────

@slides_bp.route("/templates")
def templates_index():
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT template_id, filename, slide_count, uploaded_at "
            "FROM slides_templates ORDER BY uploaded_at DESC LIMIT 50"
        ).fetchall()
        templates = [dict(row) for row in rows]
    except Exception:
        templates = []
    finally:
        conn.close()
    return render_template("slides/templates_index.html", templates=templates)


@slides_bp.route("/templates/<int:template_id>")
def template_detail(template_id: int):
    conn = _conn()
    try:
        row = conn.execute(
            f"SELECT * FROM slides_templates WHERE template_id = {_ph(conn)}", (template_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return render_template("slides/templates_index.html", templates=[], error="Template not found"), 404

    template = dict(row)
    shape_map = template.get("shape_map_json")
    if isinstance(shape_map, str):
        try:
            shape_map = json.loads(shape_map)
        except Exception:
            shape_map = {"slide_count": 0, "slides": []}
    template["shape_map"] = shape_map or {"slide_count": 0, "slides": []}

    return render_template("slides/template_detail.html", template=template)


# ── API Routes ───────────────────────────────────────────────────────────────

@slides_bp.route("/api/generate", methods=["POST"])
@require_role(*_SLIDES_WRITE_ROLES)
def api_generate():
    """Trigger deck generation asynchronously. Returns deck_id immediately; poll /api/<id>/status."""
    data = request.get_json(silent=True) or {}
    title = data.get("title", "ICDEV™ Presentation")
    deck_type = data.get("deck_type", DEFAULT_DECK_TYPE)
    theme = data.get("theme", DEFAULT_THEME)
    tone = data.get("tone", DEFAULT_TONE)
    occasion = data.get("occasion", "")
    target_audience = data.get("target_audience", "")
    citation_style = data.get("citation_style", DEFAULT_CITATION_STYLE)
    output_formats = data.get("output_formats", list(DEFAULT_OUTPUT_FORMATS))
    sources = data.get("sources", ["icdev_capabilities", "canvases", "kanban"])
    max_slides = int(data.get("max_slides", 10))
    upload_text = data.get("upload_text", "")
    enable_rich_diagrams = bool(data.get("enable_rich_diagrams", False))
    audience_mode = data.get("audience_mode") or None
    output_language = data.get("output_language", "English") or "English"

    from tools.slides.engine import DeckEngine, DeckRequest
    req = DeckRequest(
        title=title,
        deck_type=deck_type,
        theme=theme,
        tone=tone,
        occasion=occasion,
        target_audience=target_audience,
        citation_style=citation_style,
        output_formats=output_formats,
        sources=sources,
        max_slides=max_slides,
        upload_text=upload_text,
        enable_rich_diagrams=enable_rich_diagrams,
        audience_mode=audience_mode,
        output_language=output_language,
    )
    deck_id = DeckEngine().run_async(req)

    return jsonify({
        "deck_id": deck_id,
        "status": "running",
    }), 202


@slides_bp.route("/api/<int:deck_id>/status", methods=["GET"])
def api_deck_status(deck_id: int):
    """Poll generation progress. Returns {status, phase_label, done, slide_count}."""
    conn = _conn()
    try:
        row = conn.execute(
            f"SELECT status, slide_count FROM slides_decks WHERE deck_id = {_ph(conn)}",
            (deck_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Deck not found"}), 404

    row = dict(row)
    status = row.get("status", "running")
    slide_count = row.get("slide_count") or 0

    _PHASE_LABELS: dict[str, str] = {
        "running":    "Preparing…",
        "gathering":  "Gathering data from sources…",
        "planning":   "Planning slide outline…",
        "generating": "Writing slide content…",
        "graphics":   "Generating images…",
        "building":   "Building PPTX & exports…",
        "completed":  "Complete!",
        "degraded":   "Complete — degraded (partial fallback content)",
        "template":   "Complete — template (canned outline, LLM unavailable)",
        "failed":     "Generation failed",
        "auto":       "Complete (auto-generated)",
    }
    label = _PHASE_LABELS.get(status, status.replace("_", " ").title())
    done = status in ("completed", "degraded", "template", "auto", "failed")

    return jsonify({
        "deck_id": deck_id,
        "status": status,
        "phase_label": label,
        "done": done,
        "slide_count": slide_count,
        "degraded": status in ("degraded", "template"),
        "error": status == "failed",
    })


@slides_bp.route("/api/<int:deck_id>/revise", methods=["POST"])
@require_role(*_SLIDES_WRITE_ROLES)
def api_revise(deck_id: int):
    """Revise a single slide based on feedback."""
    data = request.get_json(silent=True) or {}
    slide_id = data.get("slide_id")
    feedback = data.get("feedback", "")
    new_tone = data.get("tone")
    new_theme = data.get("theme")

    if not slide_id:
        return jsonify({"error": "slide_id required"}), 400
    if not feedback and not (new_tone or new_theme):
        return jsonify({"error": "feedback, tone, or theme required"}), 400

    conn = _conn()
    try:
        row = conn.execute(
            f"SELECT * FROM slides_slides WHERE slide_id = {_ph(conn)} AND deck_id = {_ph(conn)}",
            (slide_id, deck_id),
        ).fetchone()
        if not row:
            return jsonify({"error": "Slide not found"}), 404
        slide = dict(row)
        for col in ("bullets", "citations"):
            if isinstance(slide.get(col), str):
                try:
                    slide[col] = json.loads(slide[col])
                except Exception:
                    slide[col] = []
    finally:
        conn.close()

    tone = new_tone or slide.get("tone", "professional")
    extra = ""
    if new_tone or new_theme:
        extra = f" (switch to {tone} tone and {new_theme or 'current'} theme)"

    from tools.slides.content_agent import revise_slide
    revised = revise_slide(slide, feedback + extra, raw_content={}, tone=tone)

    # Update DB
    conn = _conn()
    try:
        conn.execute(
            f"UPDATE slides_slides SET title={_ph(conn)}, bullets={_ph(conn)}, speaker_notes={_ph(conn)}, citations={_ph(conn)} WHERE slide_id={_ph(conn)}",
            (
                revised.get("title", slide.get("title", "")),
                json.dumps(revised.get("bullets", [])),
                revised.get("speaker_notes", ""),
                json.dumps(revised.get("citations", [])),
                slide_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"slide": revised})


def _serve_file(deck_id: int, column: str, mime: str, ext: str) -> Any:
    conn = _conn()
    try:
        # Degraded/template decks are honestly flagged but still downloadable —
        # gate on any ready status, not just plain "completed".
        ready = ", ".join(f"'{s}'" for s in DECK_READY_STATUSES)
        row = conn.execute(
            f"SELECT title, {column} FROM slides_decks "
            f"WHERE deck_id = {_ph(conn)} AND status IN ({ready})",
            (deck_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Deck not found or not completed"}), 404

    title = row["title"] if hasattr(row, "__getitem__") else row[0]
    path = row[column] if hasattr(row, "__getitem__") else row[1]

    if not path or not Path(path).exists():
        return jsonify({"error": f"{ext.upper()} file not found on disk"}), 404

    filename = f"ICDEV_{title[:40].replace(' ', '_')}.{ext}"
    return send_file(path, as_attachment=True, download_name=filename, mimetype=mime)


@slides_bp.route("/api/<int:deck_id>/download")
def api_download(deck_id: int):
    """Serve the .pptx file for download."""
    return _serve_file(
        deck_id, "pptx_path",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
    )


@slides_bp.route("/api/<int:deck_id>/download/pdf")
def api_download_pdf(deck_id: int):
    """Serve the .pdf file for download."""
    return _serve_file(deck_id, "pdf_path", "application/pdf", "pdf")


@slides_bp.route("/api/<int:deck_id>/download/html")
def api_download_html(deck_id: int):
    """Serve the .html file for download."""
    return _serve_file(deck_id, "html_path", "text/html", "html")


@slides_bp.route("/api/<int:deck_id>/regenerate-slide", methods=["POST"])
@require_role(*_SLIDES_WRITE_ROLES)
def api_regenerate_slide(deck_id: int):
    """Regenerate a single slide with optional tone/theme change."""
    data = request.get_json(silent=True) or {}
    slide_id = data.get("slide_id")
    feedback = data.get("feedback", "")
    new_tone = data.get("tone")
    new_theme = data.get("theme")

    if not slide_id:
        return jsonify({"error": "slide_id required"}), 400

    conn = _conn()
    try:
        row = conn.execute(
            f"SELECT * FROM slides_slides WHERE slide_id = {_ph(conn)} AND deck_id = {_ph(conn)}",
            (slide_id, deck_id),
        ).fetchone()
        if not row:
            return jsonify({"error": "Slide not found"}), 404
        slide = dict(row)
        for col in ("bullets", "citations"):
            if isinstance(slide.get(col), str):
                try:
                    slide[col] = json.loads(slide[col])
                except Exception:
                    slide[col] = []
    finally:
        conn.close()

    from tools.slides import content_agent
    tone = new_tone or slide.get("tone", "professional")
    if feedback:
        extra = f" (switch to {tone} tone and {new_theme or 'current'} theme)" if (new_tone or new_theme) else ""
        revised = content_agent.revise_slide(
            slide, feedback + extra, raw_content={}, tone=tone
        )
    else:
        revised = content_agent._generate_one(
            title=slide.get("title", ""),
            position=slide.get("position", 1),
            raw_content={},
            tone=tone,
        )

    # Update DB
    conn = _conn()
    try:
        conn.execute(
            f"UPDATE slides_slides SET title={_ph(conn)}, bullets={_ph(conn)}, speaker_notes={_ph(conn)}, citations={_ph(conn)} WHERE slide_id={_ph(conn)}",
            (
                revised.get("title", slide.get("title", "")),
                json.dumps(revised.get("bullets", [])),
                revised.get("speaker_notes", ""),
                json.dumps(revised.get("citations", [])),
                slide_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"slide": revised})


@slides_bp.route("/api/<int:deck_id>/slides/<int:slide_id>", methods=["PUT"])
@require_role(*_SLIDES_WRITE_ROLES)
def api_update_slide(deck_id: int, slide_id: int):
    """Inline slide editor — update a slide's content, type, and speaker notes."""
    data = request.get_json(silent=True) or {}

    conn = _conn()
    try:
        row = conn.execute(
            f"SELECT * FROM slides_slides WHERE slide_id = {_ph(conn)} AND deck_id = {_ph(conn)}",
            (slide_id, deck_id),
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Slide not found"}), 404
    finally:
        conn.close()

    # Build update payload
    title = data.get("title", "")
    slide_type = data.get("slide_type", "content")
    position = data.get("position")
    speaker_notes = data.get("speaker_notes", "")

    # Serialize bullets / table dict / other JSON content
    bullets_raw = data.get("bullets", [])
    if isinstance(bullets_raw, (dict, list)):
        bullets_json = json.dumps(bullets_raw)
    else:
        bullets_json = json.dumps([])

    mermaid_code = data.get("mermaid_code") or None
    three_scene_config = data.get("three_scene_config")
    excalidraw_elements = data.get("excalidraw_elements")

    three_json = json.dumps(three_scene_config) if three_scene_config is not None else None
    exc_json = json.dumps(excalidraw_elements) if excalidraw_elements is not None else None

    conn = _conn()
    try:
        ph = _ph(conn)
        sets = [
            f"title={ph}",
            f"slide_type={ph}",
            f"bullets={ph}",
            f"speaker_notes={ph}",
            f"mermaid_code={ph}",
            f"three_scene_config={ph}",
            f"excalidraw_elements={ph}",
        ]
        params = [
            title, slide_type, bullets_json, speaker_notes,
            mermaid_code, three_json, exc_json,
        ]
        if position is not None:
            sets.append(f"position={ph}")
            params.append(int(position))
        params.append(slide_id)
        conn.execute(
            f"UPDATE slides_slides SET {', '.join(sets)} WHERE slide_id={ph}",
            params,
        )
        conn.commit()
    except Exception as exc:
        logger.exception("api_update_slide failed")
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        conn.close()

    return jsonify({"ok": True, "slide_id": slide_id, "deck_id": deck_id})


@slides_bp.route("/api/<int:deck_id>/slides/<int:slide_id>", methods=["DELETE"])
@require_role(*_SLIDES_WRITE_ROLES)
def api_delete_slide(deck_id: int, slide_id: int):
    """Delete a single slide from a deck."""
    conn = _conn()
    try:
        row = conn.execute(
            f"SELECT slide_id FROM slides_slides WHERE slide_id = {_ph(conn)} AND deck_id = {_ph(conn)}",
            (slide_id, deck_id),
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Slide not found"}), 404
        conn.execute(
            f"DELETE FROM slides_slides WHERE slide_id = {_ph(conn)}",
            (slide_id,),
        )
        # Update slide_count on deck
        conn.execute(
            f"UPDATE slides_decks SET slide_count = (SELECT COUNT(*) FROM slides_slides WHERE deck_id = {_ph(conn)}) WHERE deck_id = {_ph(conn)}",
            (deck_id, deck_id),
        )
        conn.commit()
    except Exception as exc:
        logger.exception("api_delete_slide failed")
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        conn.close()
    return jsonify({"ok": True, "slide_id": slide_id, "deck_id": deck_id})


@slides_bp.route("/api/asset-smoke", methods=["POST"])
def api_asset_smoke():
    """Lightweight smoke test for the ICDEV-native asset generator.

    Exercises the shared AssetGenerator (used by GraphicsGenerator) with the
    deterministic slides_svg provider so E2E tests can verify the media pipeline
    end-to-end without GPU/cloud keys or LLM calls.
    """
    data = request.get_json(silent=True) or {}
    title = data.get("title", "Smoke Test Slide")
    bullets = data.get("bullets", [])
    theme = data.get("theme", "midnight_executive")

    try:
        from tools.viz.asset_generator import generate_for_slide
        from pathlib import Path
        from tempfile import gettempdir

        output_path = str(Path(gettempdir()) / f"slides_asset_smoke_{int(__import__('time').time())}.svg")
        result = generate_for_slide(
            title=title,
            bullets=bullets,
            theme=theme,
            output_path=output_path,
            preferred_providers=["slides_svg"],
        )
        return jsonify(result)
    except Exception as exc:
        logger.exception("asset-smoke failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@slides_bp.route("/api/templates/upload", methods=["POST"])
@require_role(*_SLIDES_WRITE_ROLES)
def api_upload_template():
    """Upload a .pptx template, inspect its shape map, and persist it.

    Returns {template_id, slide_count, slides: [...]}.
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "file required"}), 400

    filename = secure_filename(f.filename)
    if not filename.lower().endswith(".pptx"):
        return jsonify({"error": "only .pptx files are supported"}), 400

    upload_dir = _templates_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = upload_dir / f"{ts}_{filename}"
    f.save(str(dest))

    try:
        from tools.slides import template_fill
        info = template_fill.inspect_template(str(dest))
    except Exception as exc:
        logger.exception("api_upload_template: inspect_template failed")
        dest.unlink(missing_ok=True)
        return jsonify({"error": f"could not parse .pptx: {exc}"}), 400

    conn = _conn()
    try:
        template_id = _insert_and_get_id(
            conn, "slides_templates",
            ["filename", "path", "slide_count", "shape_map_json"],
            (filename, str(dest), info["slide_count"], json.dumps(info)),
            "template_id",
        )
        conn.commit()
    except Exception as exc:
        logger.exception("api_upload_template: DB insert failed")
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()

    return jsonify({"template_id": template_id, "slide_count": info["slide_count"], "slides": info["slides"]}), 201


@slides_bp.route("/api/templates/<int:template_id>/fill", methods=["POST"])
@require_role(*_SLIDES_WRITE_ROLES)
def api_fill_template(template_id: int):
    """Fill selected slides of an uploaded template and register the result as a deck."""
    data = request.get_json(silent=True) or {}
    selections = data.get("selections") or []
    if not selections:
        return jsonify({"error": "selections required"}), 400

    conn = _conn()
    try:
        row = conn.execute(
            f"SELECT filename, path FROM slides_templates WHERE template_id = {_ph(conn)}",
            (template_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "template not found"}), 404
    row = dict(row)
    pptx_path = row["path"]
    if not pptx_path or not Path(pptx_path).exists():
        return jsonify({"error": "uploaded template file missing on disk"}), 404

    try:
        from tools.slides import template_fill
        out_path = template_fill.fill_and_export(pptx_path, selections)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("api_fill_template: fill_and_export failed")
        return jsonify({"error": str(exc)}), 500

    title = f"{Path(row['filename']).stem} (filled)"
    conn = _conn()
    try:
        deck_id = _insert_and_get_id(
            conn, "slides_decks",
            ["title", "deck_type", "status", "pptx_path", "slide_count", "source_types"],
            (title, "template_fill", "completed", out_path, len(selections), json.dumps(["template_fill"])),
            "deck_id",
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"deck_id": deck_id, "pptx_path": out_path}), 201


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
