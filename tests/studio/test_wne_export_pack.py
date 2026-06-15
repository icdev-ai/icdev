"""Unit tests for tools.studio.wne.export_pack_generator — no DB, no LLM, air-gap safe."""
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.studio.wne.export_pack_generator import ExportPackGenerator

YAML_PATH = Path(__file__).parents[2] / "args" / "workflow_templates" / "ai_ml_transformation.yaml"

_EXPECTED_FILES = {
    "exec_brief.md",
    "coa_comparison.md",
    "budget_table.md",
    "roi_analysis.md",
    "slide_outline.md",
    "workflow_summary.json",
}

# Suppress LLM calls — NarrativeGenerator._llm_available triggers LLMRouter()
# init which does Ollama discovery (slow/failing in CI). These tests validate
# the deterministic Jinja2 output, not LLM refinement.
_NO_LLM = patch("tools.studio.wne.narrative_generator.NarrativeGenerator._llm_available", return_value=False)


def test_generate_produces_zip_with_six_files(tmp_path):
    with _NO_LLM:
        zip_path = ExportPackGenerator().generate(YAML_PATH, tmp_path)
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert names == _EXPECTED_FILES


def test_exec_brief_non_empty(tmp_path):
    with _NO_LLM:
        zip_path = ExportPackGenerator().generate(YAML_PATH, tmp_path)
    with zipfile.ZipFile(zip_path) as zf:
        content = zf.read("exec_brief.md").decode("utf-8")
    assert len(content) > 0


def test_workflow_summary_valid_json_with_roi_pct(tmp_path):
    with _NO_LLM:
        zip_path = ExportPackGenerator().generate(YAML_PATH, tmp_path)
    with zipfile.ZipFile(zip_path) as zf:
        data = json.loads(zf.read("workflow_summary.json").decode("utf-8"))
    assert "roi_pct" in data
