# CUI // SP-CTI
"""IQE Slides collection adapters.

Importing this module registers two collections on the module-level Executor:
  slides.decks  — slide deck records (slides_decks)
  slides.slides — individual slide records (slides_slides)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _canvas_conn():
    """Slides canvas tables live in the canvas DB, not the main ICDEV DB."""
    from tools.slides.db.init_db import get_connection
    return get_connection()


def decks_adapter(conn: Any) -> list[dict]:
    """Return rows from slides_decks."""
    canvas = _canvas_conn()
    try:
        cur = canvas.execute(
            "SELECT deck_id, title, deck_type, theme, tone, occasion, "
            "target_audience, citation_style, output_formats, status, "
            "slide_count, source_types, pptx_path, pdf_path, html_path, "
            "created_at, completed_at "
            "FROM slides_decks ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        canvas.close()


def slides_adapter(conn: Any) -> list[dict]:
    """Return rows from slides_slides joined with deck titles."""
    canvas = _canvas_conn()
    try:
        cur = canvas.execute(
            "SELECT s.slide_id, s.deck_id, d.title as deck_title, "
            "s.position, s.slide_type, s.title, s.bullets, s.speaker_notes, "
            "s.citations, s.image_path, s.created_at "
            "FROM slides_slides s "
            "JOIN slides_decks d ON d.deck_id = s.deck_id "
            "ORDER BY s.deck_id DESC, s.position"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        canvas.close()


register_collection("slides.decks", decks_adapter)
register_collection("slides.slides", slides_adapter)
