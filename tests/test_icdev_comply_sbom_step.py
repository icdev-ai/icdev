#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-cov-01 — the CI comply workflow's SBOM step actually calls the generator.

`tools/ci/workflows/icdev_comply.py` is one of the ~25 live call sites the sbx
gap analysis names, but its SBOM step called
``generate_sbom(project_dir=str(PROJECT_ROOT))`` — a keyword the function has
never had — and then called ``.get()`` on the ``str`` it returns. Both raise into
the surrounding ``except Exception``, so the step logged "SBOM generation failed"
and moved on. Every sibling step in the same function passes ``project_id``.

These tests pin the contract in both directions: the call has to succeed against
the real signature, and a caller that reintroduces the old keyword has to fail
loudly rather than be swallowed.
"""

import importlib
import inspect
import logging

import pytest

from tools.compliance import sbom_generator


@pytest.fixture
def comply():
    return importlib.import_module("tools.ci.workflows.icdev_comply")


@pytest.fixture
def stub_other_artifact_steps(monkeypatch):
    """Neuter the SSP/POAM/STIG/CUI steps so only the SBOM step is under test.

    Each step imports lazily inside its own ``try``, so patching the source
    module attribute is what the import picks up.
    """
    from tools.compliance import cui_marker, poam_generator, ssp_generator, stig_checker

    monkeypatch.setattr(ssp_generator, "generate_ssp", lambda *a, **k: {"path": ""}, raising=False)
    monkeypatch.setattr(poam_generator, "generate_poam", lambda *a, **k: {"path": ""}, raising=False)
    monkeypatch.setattr(stig_checker, "check_stig", lambda *a, **k: {"findings": {}}, raising=False)
    monkeypatch.setattr(
        cui_marker, "verify_cui_markings", lambda *a, **k: {"files_checked": 0, "missing_markings": 0}, raising=False
    )


def test_generate_sbom_has_no_project_dir_parameter():
    """The keyword the old call site used does not exist and never did."""
    params = inspect.signature(sbom_generator.generate_sbom).parameters
    assert "project_dir" not in params
    assert "project_id" in params


def test_the_sbom_step_calls_the_generator_with_the_project_id(
    comply, monkeypatch, stub_other_artifact_steps
):
    calls = []

    def fake_generate_sbom(project_id, **kwargs):
        calls.append((project_id, kwargs))
        return "/artifacts/sbom_issue-42.cdx.json"

    monkeypatch.setattr(sbom_generator, "generate_sbom", fake_generate_sbom)

    results = comply.run_compliance_artifacts("run-1", "42", logging.getLogger("test"))

    assert calls == [("issue-42", {})], "the SBOM step did not pass the project id positionally"
    assert results["sbom"]["status"] == "generated"
    assert results["sbom"]["path"] == "/artifacts/sbom_issue-42.cdx.json"
    assert not [e for e in results["errors"] if e.startswith("SBOM:")]


def test_a_failing_generator_is_reported_rather_than_hidden(
    comply, monkeypatch, stub_other_artifact_steps
):
    """The handler must still exist — it just must not be the normal path."""

    def exploding_generate_sbom(project_id, **kwargs):
        raise RuntimeError("no such project")

    monkeypatch.setattr(sbom_generator, "generate_sbom", exploding_generate_sbom)

    results = comply.run_compliance_artifacts("run-1", "42", logging.getLogger("test"))

    assert results["sbom"]["status"] == "skipped"
    assert any(e.startswith("SBOM:") for e in results["errors"])
