# CUI // SP-CTI
"""Unit tests for Slide Deck Generator engine.

Tests the core pipeline components without requiring LLM calls or DB connections.
Uses mocks for LLM router and DB.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# ── Constants ─────────────────────────────────────────────────────────────────

class TestSlidesConstants:
    def test_deck_types_not_empty(self):
        from tools.slides.constants import DECK_TYPES
        assert len(DECK_TYPES) >= 5

    def test_themes_not_empty(self):
        from tools.slides.constants import THEMES
        assert len(THEMES) == 8
        assert "midnight_executive" in THEMES
        assert "fun_fiesta" in THEMES

    def test_check_constraints_are_strings(self):
        from tools.slides.constants import CHECK_DECK_TYPE, CHECK_DECK_STATUS, CHECK_THEME
        assert "midnight_executive" in CHECK_THEME
        assert "completed" in CHECK_DECK_STATUS
        assert "executive_overview" in CHECK_DECK_TYPE

    def test_palette_keys_present(self):
        from tools.slides.constants import THEME_PALETTES
        for palette in THEME_PALETTES.values():
            assert "bg" in palette
            assert "accent" in palette
            assert "text" in palette


# ── Orchestrator Parser ────────────────────────────────────────────────────────

class TestOrchestratorParser:
    def test_direct_json_array(self):
        from tools.slides.orchestrator import _parse_titles
        titles = _parse_titles('["Slide One", "Slide Two", "Slide Three"]')
        assert titles == ["Slide One", "Slide Two", "Slide Three"]

    def test_json_object_with_titles_key(self):
        from tools.slides.orchestrator import _parse_titles
        titles = _parse_titles('{"titles": ["Alpha", "Beta", "Gamma"]}')
        assert titles == ["Alpha", "Beta", "Gamma"]

    def test_regex_bracket_extraction(self):
        from tools.slides.orchestrator import _parse_titles
        raw = 'Here are the slides: ["Title A", "Title B", "Title C"] — done.'
        titles = _parse_titles(raw)
        assert "Title A" in titles

    def test_line_heuristic_fallback(self):
        from tools.slides.orchestrator import _parse_titles
        raw = "1. Introduction to AI\n2. Platform Overview\n3. Compliance Summary"
        titles = _parse_titles(raw)
        assert len(titles) >= 2

    def test_static_fallback_returns_min_slides(self):
        from tools.slides.orchestrator import _static_outline
        outline = _static_outline("executive_overview", "ICDEV™ Test", min_slides=5)
        assert len(outline) >= 5


# ── Content Agent Parser ──────────────────────────────────────────────────────

class TestContentAgentParser:
    def test_parse_valid_json(self):
        from tools.slides.content_agent import _parse_slide
        raw = json.dumps({
            "title": "Platform Overview",
            "bullets": ["Bullet 1", "Bullet 2", "Bullet 3"],
            "speaker_notes": "This slide covers the platform.",
            "visual_context": "Navy background with gold accents.",
            "slide_type": "content",
        })
        result = _parse_slide(raw, "Platform Overview")
        assert result["title"] == "Platform Overview"
        assert len(result["bullets"]) == 3
        assert result["slide_type"] == "content"

    def test_parse_json_in_markdown_block(self):
        from tools.slides.content_agent import _parse_slide
        raw = '```json\n{"title": "Test", "bullets": ["A", "B"], "speaker_notes": "Notes."}\n```'
        result = _parse_slide(raw, "Test")
        assert result["title"] == "Test"

    def test_fallback_heuristic(self):
        from tools.slides.content_agent import _parse_slide
        raw = "- AI-powered compliance automation\n- Real-time monitoring\n- Zero-trust enforcement"
        result = _parse_slide(raw, "Compliance Overview")
        assert len(result["bullets"]) >= 1
        assert result["title"] == "Compliance Overview"

    def test_title_slide_generation(self):
        from tools.slides.content_agent import _generate_one
        result = _generate_one("ICDEV™ Platform Overview", 1, {}, is_title_slide=True)
        assert result["slide_type"] == "title"
        assert result["bullets"] == []

    def test_outro_slide_generation(self):
        from tools.slides.content_agent import _generate_one
        result = _generate_one("Thank You", 10, {}, is_outro=True)
        assert result["slide_type"] == "outro"
        assert len(result["bullets"]) > 0


# ── Research Connector ──────────────────────────────────────────────────────────

class TestResearchConnector:
    def test_research_airgap_returns_summary(self):
        from unittest.mock import patch
        from tools.slides.research_connector import research_topic
        with patch(
            "tools.slides.research_connector._llm_research",
            return_value={"summary": "Air-gap summary.", "sources": [{"title": "KG Node", "url": "/kg/1"}]},
        ):
            result = research_topic(
                "sustainable aviation", occasion="keynote", target_audience="analysts", airgap=True
            )
        assert result["summary"] == "Air-gap summary."
        assert result["sources"]
        assert result["citation_style"] == "inline_links"

    def test_format_citations_inline_links(self):
        from tools.slides.research_connector import format_citations
        sources = [{"title": "Example", "url": "https://example.com"}]
        citations = format_citations(sources, "inline_links")
        assert "Example" in citations[0]
        assert "https://example.com" in citations[0]

    def test_inline_citations_short(self):
        from tools.slides.research_connector import inline_citations
        sources = [{"title": "Example", "url": "https://example.com"}]
        assert inline_citations(sources) == ["[1](https://example.com)"]


# ── Source Connectors ─────────────────────────────────────────────────────────

class TestCanvasSource:
    def test_gather_returns_dict_with_canvases(self):
        from tools.slides.sources.canvases import gather
        result = gather()
        assert result["source"] == "canvases"
        assert "canvases" in result
        assert "summary" in result
        assert isinstance(result["total_active"], int)


class TestIcdevCapabilitiesSource:
    def test_gather_returns_domains(self):
        from tools.slides.sources.icdev_capabilities import gather
        result = gather()
        assert result["source"] == "icdev_capabilities"
        assert len(result["domains"]) >= 4
        assert "summary" in result


class TestChildAppsSource:
    def test_gather_returns_apps_with_fallback(self):
        from tools.slides.sources.child_apps import gather
        result = gather()
        assert result["source"] == "child_apps"
        assert result["total_apps"] >= 1
        assert "summary" in result


# ── PPTX Builder ─────────────────────────────────────────────────────────────

class TestPptxBuilder:
    def test_build_creates_file(self, tmp_path):
        """Build a minimal deck and verify .pptx file is created."""
        from tools.slides import pptx_builder
        from unittest.mock import patch

        slides = [
            {
                "title": "ICDEV™ Test Presentation",
                "slide_type": "title",
                "bullets": [],
                "speaker_notes": "Opening slide.",
                "image_path": None,
            },
            {
                "title": "Platform Capabilities",
                "slide_type": "content",
                "bullets": ["Canvas system", "Genesis daemon", "LLM Router"],
                "speaker_notes": "Core capabilities overview.",
                "image_path": None,
            },
            {
                "title": "Thank You",
                "slide_type": "outro",
                "bullets": ["Get in touch", "Schedule a demo"],
                "speaker_notes": "Closing remarks.",
                "image_path": None,
            },
        ]

        with patch.object(pptx_builder, "_OUTPUT_DIR", tmp_path):
            path = pptx_builder.build(slides, theme="midnight_executive", title="Test")

        assert Path(path).exists()
        assert Path(path).suffix == ".pptx"
        assert Path(path).stat().st_size > 1000


class TestExports:
    def test_export_pdf_creates_file(self, tmp_path):
        from tools.slides import export_pdf
        slides = [
            {"title": "Title", "slide_type": "title", "bullets": [], "speaker_notes": ""},
            {"title": "Content", "slide_type": "content", "bullets": ["A", "B"], "speaker_notes": "Notes."},
        ]
        path = export_pdf.build_pdf(slides, theme="fun_fiesta", title="PDF Test", output_dir=tmp_path)
        assert Path(path).exists()
        assert Path(path).suffix == ".pdf"
        assert Path(path).stat().st_size > 100

    def test_export_html_creates_file(self, tmp_path):
        from tools.slides import export_html
        slides = [
            {"title": "Title", "slide_type": "title", "bullets": [], "speaker_notes": ""},
            {"title": "Content", "slide_type": "content", "bullets": ["A", "B"], "speaker_notes": "Notes."},
        ]
        path = export_html.build_html(
            slides, theme="creative_aurora", title="HTML Test", output_dir=tmp_path,
            deck_meta={"occasion": "demo", "tone": "visionary", "target_audience": "execs"},
        )
        assert Path(path).exists()
        assert Path(path).suffix == ".html"
        html_text = Path(path).read_text(encoding="utf-8")
        assert " visionary " in html_text or 'tone&quot;' in html_text or "execs" in html_text


# ── Engine (mocked) ───────────────────────────────────────────────────────────

class TestDeckEngine:
    def test_run_demo_returns_result(self, tmp_path):
        """Run the demo with all external calls mocked."""
        from tools.slides.engine import DeckEngine, DeckRequest
        from tools.slides import pptx_builder

        mock_outline = [
            "ICDEV™ Platform Overview",
            "Design Canvases",
            "Thank You",
        ]
        mock_slides = [
            {"title": "ICDEV™ Platform Overview", "slide_type": "title", "bullets": [], "speaker_notes": "", "visual_context": ""},
            {"title": "Design Canvases", "slide_type": "content", "bullets": ["Canvas system"], "speaker_notes": "Notes.", "visual_context": ""},
            {"title": "Thank You", "slide_type": "outro", "bullets": ["Get in touch"], "speaker_notes": "Closing.", "visual_context": ""},
        ]

        with (
            patch("tools.slides.orchestrator.plan_outline", return_value=mock_outline),
            patch("tools.slides.content_agent.generate_all", return_value=mock_slides),
            patch.object(pptx_builder, "_OUTPUT_DIR", tmp_path),
            patch.object(DeckEngine, "_create_deck_record", return_value=1),
            patch.object(DeckEngine, "_update_deck_record"),
            patch.object(DeckEngine, "_audit"),
        ):
            engine = DeckEngine()
            req = DeckRequest(
                title="Test Deck",
                sources=["canvases"],
                enable_graphics=False,
            )
            result = engine.run(req)

        assert result.status == "completed"
        assert len(result.slides) == 3
        assert result.pptx_path.endswith(".pptx")


# ── Graphics Generator ──────────────────────────────────────────────────────────

class TestGraphicsGenerator:
    def test_matplotlib_fallback_creates_png(self, tmp_path):
        from tools.slides.graphics_generator import GraphicsGenerator
        gen = GraphicsGenerator(output_dir=tmp_path)
        path = gen._matplotlib_fallback("Test Slide", ["Bullet one", "Bullet two"], theme="midnight_executive")
        assert path is not None
        assert Path(path).exists()
        assert Path(path).suffix == ".png"

    def test_save_image_wraps_raw_rgba_bytes(self, tmp_path):
        from tools.slides.graphics_generator import GraphicsGenerator
        gen = GraphicsGenerator(output_dir=tmp_path)
        # Fake RGBA bytes (not a PNG)
        from PIL import Image as PILImage
        from io import BytesIO
        img = PILImage.new("RGBA", (8, 8), (255, 0, 0, 255))
        buf = BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        path = gen._save_image(png_bytes, "RGBATest")
        assert Path(path).exists()
        assert Path(path).read_bytes().startswith(b"\x89PNG")

    def test_provider_list_includes_new_backends(self):
        from tools.slides.constants import IMAGE_PROVIDERS
        assert "gpt_image_2" in IMAGE_PROVIDERS
        assert "imagen_4" in IMAGE_PROVIDERS
        assert "matplotlib" in IMAGE_PROVIDERS

    def test_gpt_image_2_request_shape(self, tmp_path):
        """Verify GPT-Image-2 request matches OpenAI Images API shape."""
        from tools.slides.graphics_generator import GraphicsGenerator
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            gen = GraphicsGenerator(output_dir=tmp_path)
            gen._provider = "gpt_image_2"
            captured: dict = {}
            class _FakeResp:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def read(self): return json.dumps({"data": [{"b64_json": "aGVsbG8="}]}).encode()
            def _fake_urlopen(req, **_kw):
                captured["data"] = json.loads(req.data)
                captured["headers"] = dict(req.header_items())
                return _FakeResp()
            with patch("urllib.request.urlopen", _fake_urlopen):
                bytes_out = gen._call_image_api("a professional diagram", "Title")
            assert bytes_out == b"hello"
            assert captured["data"]["model"] == "gpt-image-2"
            assert captured["data"]["size"] == "1024x576"
            assert captured["data"]["response_format"] == "b64_json"
            assert captured["data"]["quality"] == "standard"
            assert "Authorization" in captured["headers"]

    def test_imagen_4_no_key_returns_none(self, tmp_path):
        from tools.slides.graphics_generator import GraphicsGenerator
        with patch.dict(os.environ, {"GOOGLE_API_KEY": ""}, clear=False):
            gen = GraphicsGenerator(output_dir=tmp_path)
            gen._provider = "imagen_4"
            assert gen._call_image_api("prompt", "Title") is None
