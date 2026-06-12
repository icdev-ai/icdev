#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Intelligence Brief Generator — Jinja2 template engine for intelligence products.

Supported brief types:
  sitrep     — Situation Report (threat posture, key events, SIO top-3, recs)
  iir        — Intelligence Information Report (single source, reliability/credibility tags)
  warnord    — Warning Order (OPORD-lite: task org, situation, mission, execution, C2)
  assessment — Intelligence Assessment (key judgment, confidence, reasoning, gaps)

Usage:
    gen = BriefGenerator()
    md = gen.generate("sitrep", context_dict)
    pdf_bytes_or_none = gen.export_pdf(md)   # None if weasyprint unavailable
    record = gen.save(brief_type, title, md, sio_confidence=0.82)
"""
import importlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, Undefined

from tools.db.storage import get_connection

_TEMPLATES_DIR = Path(__file__).parent / "templates"

BRIEF_TYPES = ("sitrep", "iir", "warnord", "assessment", "war_council")


class _LenientUndefined(Undefined):
    """Return empty string for missing variables instead of raising."""
    def __str__(self):
        return ""
    def __iter__(self):
        return iter([])
    def __bool__(self):
        return False


def _make_env() -> Environment:
    env = Environment(  # nosec B701 — templates are .md.j2 Markdown; autoescape=True corrupts output
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=_LenientUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals["enumerate"] = enumerate
    return env


class BriefGenerator:
    """Generate and persist intelligence products from Jinja2 templates."""

    def __init__(self):
        self._env = _make_env()

    def generate(self, brief_type: str, context_dict: dict) -> str:
        """Render a brief template to a markdown string.

        Args:
            brief_type: One of 'sitrep', 'iir', 'warnord', 'assessment'.
            context_dict: Template variables merged with auto-injected defaults.

        Returns:
            Rendered markdown string.

        Raises:
            ValueError: If brief_type is not recognised.
        """
        if brief_type not in BRIEF_TYPES:
            raise ValueError(
                f"Unknown brief_type '{brief_type}'. Must be one of: {BRIEF_TYPES}"
            )
        template = self._env.get_template(f"{brief_type}.md.j2")
        ctx = {
            "brief_id": context_dict.get("brief_id", str(uuid.uuid4())),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "classification": "CUI // SP-CTI",
        }
        ctx.update(context_dict)
        return template.render(**ctx)

    def export_pdf(self, markdown_content: str) -> bytes | None:
        """Convert markdown to PDF via weasyprint + markdown lib.

        Returns bytes on success, None if dependencies are unavailable.
        """
        try:
            md_mod = importlib.import_module("markdown")
            weasyprint = importlib.import_module("weasyprint")
        except ImportError:
            return None
        html_body = md_mod.markdown(
            markdown_content, extensions=["tables", "fenced_code"]
        )
        full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: 'DejaVu Sans', sans-serif; font-size: 11pt; margin: 2cm; }}
  h1 {{ font-size: 16pt; border-bottom: 2px solid #333; padding-bottom: 4px; }}
  h2 {{ font-size: 13pt; color: #1a1a2e; margin-top: 1.4em; }}
  h3 {{ font-size: 11pt; color: #444; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.8em 0; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
  th {{ background: #f0f0f0; font-weight: bold; }}
  blockquote {{ border-left: 4px solid #e94560; padding: 6px 12px;
                 background: #fff5f5; margin: 0.6em 0; }}
  code {{ background: #f4f4f4; padding: 2px 4px; border-radius: 3px; }}
  strong:first-child {{ color: #8b0000; }}
</style>
</head><body>{html_body}</body></html>"""
        doc = weasyprint.HTML(string=full_html)
        return doc.write_pdf()

    def save(
        self,
        brief_type: str,
        title: str,
        content_md: str,
        sio_confidence: float | None = None,
    ) -> dict:
        """Persist a generated brief to sg_intelligence_briefs.

        Returns the saved record as a dict.
        """
        brief_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO sg_intelligence_briefs
                   (id, brief_type, title, content_md, sio_confidence,
                    analyst_reviewed, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (brief_id, brief_type, title, content_md, sio_confidence, now),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "id": brief_id,
            "brief_type": brief_type,
            "title": title,
            "content_md": content_md,
            "sio_confidence": sio_confidence,
            "analyst_reviewed": 0,
            "created_at": now,
        }

    def generate_and_save(
        self,
        brief_type: str,
        title: str,
        context_dict: dict,
        sio_confidence: float | None = None,
    ) -> dict:
        """Convenience: generate markdown then persist it.

        Injects the brief_id into context before rendering so templates can
        reference it for report numbers.
        """
        brief_id = str(uuid.uuid4())
        ctx = {"brief_id": brief_id}
        ctx.update(context_dict)
        content_md = self.generate(brief_type, ctx)
        return self.save(
            brief_type=brief_type,
            title=title,
            content_md=content_md,
            sio_confidence=sio_confidence,
        )
