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
    MIN_SLIDES,
)


@dataclass
class DeckRequest:
    title: str = "ICDEV™ Presentation"
    deck_type: str = DEFAULT_DECK_TYPE
    theme: str = DEFAULT_THEME
    sources: list[str] = field(default_factory=lambda: ["icdev_capabilities", "canvases", "kanban"])
    max_slides: int = DEFAULT_MAX_SLIDES
    min_slides: int = MIN_SLIDES
    upload_text: str = ""
    upload_file_path: str = ""
    enable_graphics: bool = True


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


class DeckEngine:
    """Main orchestration engine for slide deck generation."""

    def run(self, req: DeckRequest) -> DeckResult:
        """Run the full generation pipeline."""
        deck_id = self._create_deck_record(req)

        try:
            # Phase 1: Gather
            raw = self._gather(req)

            # Data-story fast path: an uploaded CSV/JSON dataset becomes an
            # interactive dashboard + auto Story-Point deck (no LLM narrative).
            dataset = raw.get("__dataset__")
            if dataset:
                return self._run_dataset_story(deck_id, req, dataset)

            # Phase 2: Plan outline
            from tools.slides import orchestrator
            outline = orchestrator.plan_outline(
                raw_content=raw,
                deck_title=req.title,
                deck_type=req.deck_type,
                min_slides=req.min_slides,
                max_slides=req.max_slides,
            )

            # Phase 3: Generate content (parallel)
            from tools.slides import content_agent
            slides = content_agent.generate_all(outline, raw)

            # Phase 3.5: deterministic data-driven viz slides (real numbers, no LLM)
            from tools.slides import viz_mapper
            data_slides = viz_mapper.build_data_slides(raw)
            if data_slides:
                if slides and slides[-1].get("slide_type") == "outro":
                    slides = slides[:-1] + data_slides + [slides[-1]]
                else:
                    slides = slides + data_slides

            # Phase 4: Graphics (parallel, optional)
            if req.enable_graphics and os.environ.get("SLIDES_IMAGE_ENABLED", "true").lower() in ("true", "1", "yes"):
                slides = self._generate_graphics(slides)

            # Phase 5: Build PPTX
            from tools.slides import pptx_builder
            pptx_path = pptx_builder.build(slides, theme=req.theme, title=req.title)

            # Phase 6: Persist
            self._update_deck_record(deck_id, slides, pptx_path, "completed")
            self._audit(deck_id, "completed", {"slide_count": len(slides), "pptx_path": pptx_path})

            return DeckResult(
                deck_id=deck_id,
                title=req.title,
                pptx_path=pptx_path,
                slides=slides,
                theme=req.theme,
                source_types=req.sources,
                status="completed",
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
            )

    def _run_dataset_story(self, deck_id, req: "DeckRequest", dataset: dict) -> "DeckResult":
        """Build an interactive dashboard + auto Story-Point deck from a dataset."""
        from tools.viz.story_builder import build_dataset_slides
        from tools.slides import pptx_builder

        slides = (
            [{"slide_type": "title", "title": req.title, "bullets": [],
              "speaker_notes": f"Data story over {dataset.get('name', 'the dataset')}."}]
            + build_dataset_slides(dataset)
            + [{"slide_type": "outro", "title": "Explore the Data",
                "bullets": ["Open the live dashboard", "Filter and drill down"],
                "speaker_notes": "The dashboard is fully interactive in the presenter."}]
        )
        try:
            pptx_path = pptx_builder.build(slides, theme=req.theme, title=req.title)
        except Exception:
            pptx_path = ""
        self._update_deck_record(deck_id, slides, pptx_path, "completed")
        self._audit(deck_id, "completed", {"slide_count": len(slides), "mode": "dataset_story"})
        return DeckResult(
            deck_id=deck_id, title=req.title, pptx_path=pptx_path, slides=slides,
            theme=req.theme, source_types=["dataset"], status="completed",
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

        # Handle upload sources
        if req.upload_text:
            from tools.slides import input_parser
            raw["upload"] = input_parser.parse_text(req.upload_text)

        if req.upload_file_path:
            from tools.slides import input_parser
            raw["upload"] = input_parser.parse_file(req.upload_file_path)

        # Detect a tabular dataset upload (CSV/JSON) → data-story path.
        if req.upload_text or req.upload_file_path:
            try:
                from tools.viz.dataset import parse_dataset
                ds = parse_dataset(text=req.upload_text or None,
                                   path=req.upload_file_path or None,
                                   name=(req.title or "Dataset"))
                # Only treat as a dataset when it's genuinely tabular.
                if ds and len(ds["columns"]) >= 2 and len(ds["rows"]) >= 2:
                    raw["__dataset__"] = ds
            except Exception:
                pass

        # Gather ICDEV native sources in parallel
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

    def _generate_graphics(self, slides: list[dict]) -> list[dict]:
        """Generate images for content slides in parallel."""
        from tools.slides.graphics_generator import GraphicsGenerator
        gen = GraphicsGenerator()

        def _gen_one(slide_data: dict) -> dict:
            slide_type = slide_data.get("slide_type", "content")
            if slide_type in ("title", "outro"):
                return slide_data
            # Data-driven viz slides render their own chart/table/diagram — no image.
            if any(slide_data.get(k) for k in ("chart", "table", "diagram", "kpis", "dashboard")):
                return slide_data
            title = slide_data.get("title", "")
            bullets = slide_data.get("bullets", [])
            visual_ctx = slide_data.get("visual_context", "")
            try:
                img_path = gen.generate(title, bullets, visual_ctx)
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
                cur = conn.execute(
                    "INSERT INTO slides_decks (title, deck_type, theme, status, source_types) "
                    "VALUES (?, ?, ?, 'running', ?) RETURNING deck_id",
                    (req.title, req.deck_type, req.theme, source_types_json),
                )
                row = cur.fetchone()
                conn.commit()
                return int(row[0]) if row else None
            finally:
                conn.close()
        except Exception:
            return None

    def _update_deck_record(
        self, deck_id: int | None, slides: list[dict], pptx_path: str,
        status: str, error: str | None = None
    ) -> None:
        if deck_id is None:
            return
        try:
            from tools.slides.db.init_db import get_connection
            conn = get_connection()
            try:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE slides_decks SET status=?, slide_count=?, pptx_path=?, "
                    "error_message=?, completed_at=? WHERE deck_id=?",
                    (status, len(slides), pptx_path, error, now, deck_id),
                )
                # Persist slides (incl. viz payloads — VIZ Epic B)
                def _vz(sd, key):
                    val = sd.get(key)
                    return json.dumps(val) if val else None

                for i, slide_data in enumerate(slides):
                    conn.execute(
                        "INSERT INTO slides_slides "
                        "(deck_id, position, slide_type, title, bullets, speaker_notes, "
                        "image_path, image_prompt, chart_json, table_json, diagram_json, kpis_json, dashboard_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            deck_id, i + 1,
                            slide_data.get("slide_type", "content"),
                            slide_data.get("title", "")[:255],
                            json.dumps(slide_data.get("bullets", [])),
                            slide_data.get("speaker_notes", ""),
                            slide_data.get("image_path"),
                            slide_data.get("image_prompt"),
                            _vz(slide_data, "chart"),
                            _vz(slide_data, "table"),
                            _vz(slide_data, "diagram"),
                            _vz(slide_data, "kpis"),
                            _vz(slide_data, "dashboard"),
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
                    "INSERT INTO slides_audit (deck_id, action, details) VALUES (?, ?, ?)",
                    (deck_id, action, json.dumps(details)),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
