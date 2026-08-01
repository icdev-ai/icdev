# CUI // SP-CTI
"""DWO-MEM — canvas resolution for Studio workflow executors.

Regression cover for the silent-default defect: `resolve_canvas()` used to
return 'ddc' when no strategy resolved, so an AADC/AIMC/MDC/OHC/QDC run with no
artifacts yet wrote its output into the DDC artifact directory and reported
success. See docs/spikes/dwo-00-superplane-workflow-adaptation.md (gap MEM).
"""
from __future__ import annotations

import importlib
import json
import uuid

import pytest

from tools.db.storage import get_connection
from tools.studio.executors import _base
from tools.studio.executors._base import (
    CanvasResolutionError,
    known_canvas_slugs,
    resolve_canvas,
)
from tools.studio.init_db import init_studio_tables


@pytest.fixture(autouse=True)
def _studio_schema():
    init_studio_tables()


def _seed_run(*, template_yaml: str = "", workflow_name: str = "Test Workflow") -> str:
    """Insert a workflow + run pair and return the run_id."""
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflows (workflow_id, name, template_yaml) "
            "VALUES (%s, %s, %s)",
            (workflow_id, workflow_name, template_yaml),
        )
        conn.execute(
            "INSERT INTO studio_workflow_runs (run_id, workflow_id, workflow_name, status) "
            "VALUES (%s, %s, %s, 'running')",
            (run_id, workflow_id, workflow_name),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def _seed_iac_step(run_id: str, artifact_path: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflow_run_steps "
            "(step_run_id, run_id, step_id, step_name, tool, status, stdout) "
            "VALUES (%s, %s, 'gen', 'Generate IaC', '', 'success', %s)",
            (
                f"sr-{uuid.uuid4().hex[:12]}",
                run_id,
                json.dumps({"artifacts": [{"name": "main.tf", "path": artifact_path, "type": "tf"}]}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── Priority order ─────────────────────────────────────────────────────────

def test_explicit_hint_wins():
    run_id = _seed_run(template_yaml="canvas: ndc\nsteps: []\n")
    assert resolve_canvas(run_id, "AADC") == "aadc"


def test_template_canvas_key_is_authoritative():
    """The template declaration beats both artifacts and the workflow name."""
    run_id = _seed_run(template_yaml="canvas: aimc\nsteps: []\n", workflow_name="DDC Legacy Name")
    _seed_iac_step(run_id, "data/studio_artifacts/ddc/main.tf")
    assert resolve_canvas(run_id) == "aimc"


def test_falls_back_to_artifact_path():
    run_id = _seed_run(template_yaml="steps: []\n", workflow_name="Unnamed")
    _seed_iac_step(run_id, "data/studio_artifacts/odc/main.tf")
    assert resolve_canvas(run_id) == "odc"


def test_falls_back_to_workflow_name_prefix():
    run_id = _seed_run(template_yaml="steps: []\n", workflow_name="MDC Cutover Workflow")
    assert resolve_canvas(run_id) == "mdc"


# ── The defect ─────────────────────────────────────────────────────────────

def test_unresolvable_run_raises_instead_of_defaulting_to_ddc():
    """The regression: this used to silently return 'ddc'."""
    run_id = _seed_run(template_yaml="steps: []\n", workflow_name="Nameless Workflow")
    with pytest.raises(CanvasResolutionError) as exc:
        resolve_canvas(run_id)
    assert "ddc" not in str(exc.value).split("data/studio_artifacts")[0].lower()
    assert "--canvas" in str(exc.value)


def test_no_run_id_and_no_hint_raises():
    with pytest.raises(CanvasResolutionError):
        resolve_canvas()


def test_aadc_workflow_no_longer_resolves_to_ddc():
    """AADC/AIMC/MDC/OHC/QDC were absent from the old hardcoded prefix tuple.

    Before the fix, an AADC run whose artifacts had not been produced yet
    resolved to 'ddc'. It must now resolve to 'aadc' via the template key.
    """
    run_id = _seed_run(template_yaml="canvas: aadc\nsteps: []\n", workflow_name="AADC Teardown")
    assert resolve_canvas(run_id) == "aadc"


# ── Registry-derived slug list ─────────────────────────────────────────────

def test_known_slugs_come_from_the_component_registry():
    slugs = known_canvas_slugs()
    # Canvases registered in args/component_registry.yaml but missing from the
    # old hardcoded tuple.
    assert "aadc" in slugs
    assert "ndc" in slugs
    assert "idc" in slugs


def test_known_slugs_are_longest_first():
    slugs = known_canvas_slugs()
    lengths = [len(s) for s in slugs]
    assert lengths == sorted(lengths, reverse=True)


def test_known_slugs_fall_back_when_registry_unavailable(monkeypatch):
    """Air-gap safety: an unreadable registry must not break resolution."""
    # Patch through importlib, not `import tools.x as` — the back-compat shim
    # resolves the two forms differently (CLAUDE.md, canonical/legacy namespace).
    reg = importlib.import_module("tools.config.component_registry")

    def _boom(*_a, **_kw):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(reg, "get_registry", _boom)
    slugs = known_canvas_slugs()
    assert set(slugs) == set(_base._FALLBACK_CANVAS_SLUGS)
