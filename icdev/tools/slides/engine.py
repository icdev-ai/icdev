# CUI // SP-CTI
"""Slide Deck Engine — main orchestration DAG.

Pipeline:
  1. Gather  — pull from selected source connectors in parallel
  2. Plan    — LLM outlines slide titles (Orchestrator)
  3. Generate — parallel LLM content per slide (ContentAgent)
  4. Graphics — per-slide image generation (GraphicsGenerator)
  5. Build   — python-pptx assembly (PptxBuilder)
  6. Persist — save to DB + return DeckResult

Usage:
  from tools.slides.engine import DeckEngine, DeckRequest
  result = DeckEngine().run(DeckRequest(title="ICDEV Overview", sources=["canvases", "kanban"]))
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from tools.slides.constants import (
    DEFAULT_THEME, DEFAULT_DECK_TYPE, DEFAULT_MAX_SLIDES,
    DEFAULT_TONE, DEFAULT_CITATION_STYLE, DEFAULT_OUTPUT_FORMATS,
    MIN_SLIDES,
)


@dataclass
class DeckRequest:
    title: str = "ICDEV™ Presentation"
    deck_type: str = DEFAULT_DECK_TYPE
    theme: str = DEFAULT_THEME
    tone: str = DEFAULT_TONE
    occasion: str = ""
    target_audience: str = ""
    citation_style: str = DEFAULT_CITATION_STYLE
    output_formats: list[str] = field(default_factory=lambda: list(DEFAULT_OUTPUT_FORMATS))
    sources: list[str] = field(default_factory=lambda: ["icdev_capabilities", "canvases", "kanban"])
    max_slides: int = DEFAULT_MAX_SLIDES
    min_slides: int = MIN_SLIDES
    upload_text: str = ""
    upload_file_path: str = ""
    enable_graphics: bool = True
    enable_rich_diagrams: bool = False
    audience_mode: str | None = None
    output_language: str = "English"


@dataclass
class DeckResult:
    deck_id: int | None
    title: str
    pptx_path: str
    slides: list[dict]
    theme: str
    source_types: list[str]
    status: str = "completed"
    error: str | None = None
    pdf_path: str = ""
    html_path: str = ""
    tone: str = DEFAULT_TONE


class DeckEngine:
    """Main orchestration engine for slide deck generation."""

    def run(self, req: DeckRequest) -> DeckResult:
        """Run the full generation pipeline synchronously."""
        deck_id = self._create_deck_record(req)
        return self._run_phases(req, deck_id)

    def run_async(self, req: DeckRequest) -> int | None:
        """Create the deck record and start generation in a background thread.

        Returns deck_id immediately so the caller can poll /api/<deck_id>/status.
        """
        import threading
        deck_id = self._create_deck_record(req)
        t = threading.Thread(target=self._run_phases, args=(req, deck_id), daemon=True)
        t.start()
        return deck_id

    def _set_phase(self, deck_id: int | None, phase: str) -> None:
        """Update the deck status column to a phase label for progress polling."""
        if deck_id is None:
            return
        try:
            from tools.slides.db.init_db import get_connection
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE slides_decks SET status=%s WHERE deck_id=%s",
                    (phase, deck_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _run_phases(self, req: DeckRequest, deck_id: int | None) -> DeckResult:
        """Internal: run all phases with phase status updates."""
        pdf_path = ""
        html_path = ""

        try:
            # Phase 1: Gather
            self._set_phase(deck_id, "gathering")
            raw = self._gather(req)

            # Research degradation signal (general decks): the connector reports
            # degraded=True when it could not produce real research and refused to
            # fabricate a summary/citations. Absent research must be surfaced.
            research_degraded = bool((raw.get("research") or {}).get("degraded", False))

            # Phase 2: Plan outline
            self._set_phase(deck_id, "planning")
            from tools.slides import orchestrator
            _outline_result = orchestrator.plan_outline(
                raw_content=raw,
                deck_title=req.title,
                deck_type=req.deck_type,
                tone=req.tone,
                occasion=req.occasion,
                target_audience=req.target_audience,
                min_slides=req.min_slides,
                max_slides=req.max_slides,
                enable_rich_diagrams=req.enable_rich_diagrams,
                audience_mode=req.audience_mode,
                output_language=req.output_language,
                return_provenance=True,
            )
            # return_provenance=True yields (titles, used_fallback). Tolerate a
            # plain-list return (e.g. patched mocks) by treating it as non-fallback.
            if isinstance(_outline_result, tuple):
                outline, outline_fallback = _outline_result
            else:
                outline, outline_fallback = _outline_result, False

            # Phase 3: Generate content (parallel)
            self._set_phase(deck_id, "generating")
            from tools.slides import content_agent
            slides = content_agent.generate_all(
                outline, raw,
                tone=req.tone,
                citation_style=req.citation_style,
                enable_rich_diagrams=req.enable_rich_diagrams,
                theme=req.theme,
                output_language=req.output_language,
            )

            # Phase 4: Graphics (parallel, optional)
            if req.enable_graphics and os.environ.get("SLIDES_IMAGE_ENABLED", "true").lower() in ("true", "1", "yes"):
                self._set_phase(deck_id, "graphics")
                slides = self._generate_graphics(slides, theme=req.theme, tone=req.tone)

            # Phase 5: Build PPTX
            self._set_phase(deck_id, "building")
            from tools.slides import pptx_builder
            pptx_path = ""
            if "pptx" in req.output_formats:
                pptx_path = pptx_builder.build(slides, theme=req.theme, title=req.title)

            # Phase 5b: Build PDF
            if "pdf" in req.output_formats:
                try:
                    from tools.slides import export_pdf
                    pdf_path = export_pdf.build_pdf(slides, theme=req.theme, title=req.title)
                except Exception as pdf_exc:
                    self._audit(deck_id, "pdf_export_warning", {"error": str(pdf_exc)})

            # Phase 5c: Build HTML
            if "html" in req.output_formats:
                try:
                    from tools.slides import export_html
                    html_path = export_html.build_html(
                        slides, theme=req.theme, title=req.title,
                        deck_meta={
                            "occasion": req.occasion,
                            "tone": req.tone,
                            "target_audience": req.target_audience,
                        },
                        enable_rich_diagrams=req.enable_rich_diagrams,
                    )
                except Exception as html_exc:
                    self._audit(deck_id, "html_export_warning", {"error": str(html_exc)})

            # Determine honest final status. A deck that shipped canned/fallback
            # content must never report a plain "completed" (wave honesty
            # standard, cf. Oracle available:false, FathomDesk simulated:true):
            #   template  — the outline itself is a static fallback (whole deck
            #               structure is canned; LLM planner was unavailable)
            #   degraded  — outline is real but some slides fell back to canned
            #               content, or requested research was unavailable
            #   completed — real LLM content end-to-end
            slide_fallbacks = sum(
                1 for s in slides if s.get("provenance") == "fallback"
            )
            if outline_fallback:
                final_status = "template"
            elif slide_fallbacks or research_degraded:
                final_status = "degraded"
            else:
                final_status = "completed"

            # Phase 6: Persist
            self._update_deck_record(
                deck_id, slides, pptx_path, final_status,
                pdf_path=pdf_path, html_path=html_path,
            )
            self._audit(deck_id, final_status, {
                "slide_count": len(slides),
                "pptx_path": pptx_path,
                "pdf_path": pdf_path,
                "html_path": html_path,
                "outline_fallback": outline_fallback,
                "slide_fallbacks": slide_fallbacks,
                "research_degraded": research_degraded,
            })

            return DeckResult(
                deck_id=deck_id,
                title=req.title,
                pptx_path=pptx_path,
                slides=slides,
                theme=req.theme,
                source_types=req.sources,
                status=final_status,
                pdf_path=pdf_path,
                html_path=html_path,
                tone=req.tone,
            )

        except Exception as exc:
            self._update_deck_record(deck_id, [], "", "failed", error=str(exc))
            self._audit(deck_id, "failed", {"error": str(exc)})
            return DeckResult(
                deck_id=deck_id,
                title=req.title,
                pptx_path="",
                slides=[],
                theme=req.theme,
                source_types=req.sources,
                status="failed",
                error=str(exc),
                tone=req.tone,
            )

    def run_demo(self) -> DeckResult:
        """Demo run using kanban + canvases sources and matplotlib graphics."""
        return self.run(DeckRequest(
            title="ICDEV™ Platform Overview",
            sources=["icdev_capabilities", "canvases", "kanban"],
            enable_graphics=True,
        ))

    # ── Phase 1: Gather ───────────────────────────────────────────────────────

    def _gather(self, req: DeckRequest) -> dict[str, Any]:
        """Pull from all selected source connectors in parallel."""
        raw: dict[str, Any] = {}

        # General presentations use AI research instead of ICDEV internal sources
        if req.deck_type == "general_presentation":
            from tools.slides import research_connector
            raw["research"] = research_connector.research_topic(
                topic=req.title,
                occasion=req.occasion or None,
                target_audience=req.target_audience or None,
                citation_style=req.citation_style,
            )

        # Handle upload sources
        if req.upload_text:
            from tools.slides import input_parser
            raw["upload"] = input_parser.parse_text(req.upload_text)

        if req.upload_file_path:
            from tools.slides import input_parser
            raw["upload"] = input_parser.parse_file(req.upload_file_path)

        # Gather ICDEV native sources in parallel (skipped for general decks)
        if req.deck_type != "general_presentation":
            source_map: dict[str, Any] = {
                "icdev_capabilities": lambda: __import__("tools.slides.sources.icdev_capabilities", fromlist=["gather"]).gather(),
                "canvases":           lambda: __import__("tools.slides.sources.canvases", fromlist=["gather"]).gather(),
                "child_apps":         lambda: __import__("tools.slides.sources.child_apps", fromlist=["gather"]).gather(),
                "kanban":             lambda: __import__("tools.slides.sources.kanban", fromlist=["gather"]).gather(),
                "genesis":            lambda: __import__("tools.slides.sources.genesis", fromlist=["gather"]).gather(),
            }

            active_sources = [s for s in req.sources if s in source_map]
            if not active_sources and not raw:
                active_sources = ["icdev_capabilities", "canvases"]

            with ThreadPoolExecutor(max_workers=min(4, len(active_sources))) as pool:
                futures = {pool.submit(source_map[s]): s for s in active_sources}
                for future in as_completed(futures):
                    source_name = futures[future]
                    try:
                        raw[source_name] = future.result()
                    except Exception:
                        pass

        return raw

    # ── Phase 4: Graphics ─────────────────────────────────────────────────────

    def _generate_graphics(self, slides: list[dict], theme: str = "", tone: str = "") -> list[dict]:
        """Generate images for content slides in parallel."""
        from tools.slides.graphics_generator import GraphicsGenerator
        gen = GraphicsGenerator()

        def _gen_one(slide_data: dict) -> dict:
            slide_type = slide_data.get("slide_type", "content")
            if slide_type in ("title", "outro", "mermaid_diagram", "three_animation",
                              "excalidraw_sketch", "card_grid", "svg_art"):
                return slide_data
            title = slide_data.get("title", "")
            bullets = slide_data.get("bullets", [])
            visual_ctx = slide_data.get("visual_context", "")
            try:
                img_path = gen.generate(title, bullets, visual_ctx, theme=theme, tone=tone)
                if img_path:
                    slide_data = dict(slide_data, image_path=img_path)
            except Exception:
                pass
            return slide_data

        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(_gen_one, slides))

    # ── DB Persistence ────────────────────────────────────────────────────────

    def _create_deck_record(self, req: DeckRequest) -> int | None:
        try:
            from tools.slides.db.init_db import get_connection, init_db
            init_db()
            conn = get_connection()
            try:
                source_types_json = json.dumps(req.sources)
                output_formats_json = json.dumps(req.output_formats or list(DEFAULT_OUTPUT_FORMATS))
                cur = conn.execute(
                    "INSERT INTO slides_decks "
                    "(title, deck_type, theme, tone, occasion, target_audience, citation_style, "
                    "output_formats, status, source_types, enable_rich_diagrams, audience_mode) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, %s) RETURNING deck_id",
                    (
                        req.title, req.deck_type, req.theme, req.tone,
                        req.occasion, req.target_audience, req.citation_style,
                        output_formats_json, source_types_json,
                        bool(req.enable_rich_diagrams),
                        req.audience_mode,
                    ),
                )
                row = cur.fetchone()
                conn.commit()
                return int(row[0]) if row else None
            finally:
                conn.close()
        except Exception as e:
            import traceback
            print(f"[slides] _create_deck_record error: {e}\n{traceback.format_exc()}")
            return None

    def _update_deck_record(
        self, deck_id: int | None, slides: list[dict], pptx_path: str,
        status: str, error: str | None = None,
        pdf_path: str = "", html_path: str = "",
    ) -> None:
        if deck_id is None:
            return
        try:
            from tools.slides.db.init_db import get_connection
            conn = get_connection()
            try:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE slides_decks SET status=%s, slide_count=%s, pptx_path=%s, "
                    "pdf_path=%s, html_path=%s, error_message=%s, completed_at=%s WHERE deck_id=%s",
                    (status, len(slides), pptx_path, pdf_path, html_path, error, now, deck_id),
                )
                # Persist slides
                for i, slide_data in enumerate(slides):
                    three_cfg = slide_data.get("three_scene_config")
                    exc_elems = slide_data.get("excalidraw_elements")
                    conn.execute(
                        "INSERT INTO slides_slides "
                        "(deck_id, position, slide_type, title, bullets, speaker_notes, citations, "
                        "image_path, image_prompt, mermaid_code, three_scene_config, excalidraw_elements, "
                        "provenance) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            deck_id, i + 1,
                            slide_data.get("slide_type", "content"),
                            slide_data.get("title", "")[:255],
                            json.dumps(slide_data.get("bullets", [])),
                            slide_data.get("speaker_notes", ""),
                            json.dumps(slide_data.get("citations", [])),
                            slide_data.get("image_path"),
                            slide_data.get("image_prompt"),
                            slide_data.get("mermaid_code"),
                            json.dumps(three_cfg) if three_cfg is not None else None,
                            json.dumps(exc_elems) if exc_elems is not None else None,
                            slide_data.get("provenance", "llm"),
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _audit(self, deck_id: int | None, action: str, details: dict) -> None:
        if deck_id is None:
            return
        try:
            from tools.slides.db.init_db import get_connection
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT INTO slides_audit (deck_id, action, details) VALUES (%s, %s, %s)",
                    (deck_id, action, json.dumps(details)),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
