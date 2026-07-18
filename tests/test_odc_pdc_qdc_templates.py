# CUI // SP-CTI
"""Tests verifying odc/pdc/qdc canvas templates, mirrors, IQE, and registry entries."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]


# ── Template existence ─────────────────────────────────────────────────────────

def test_odc_template_exists():
    tpl = REPO / "tools/dashboard/templates/observability_canvas/index.html"
    assert tpl.exists(), f"odc template missing: {tpl}"


def test_pdc_template_exists():
    tpl = REPO / "tools/dashboard/templates/pipeline/index.html"
    assert tpl.exists(), f"pdc template missing: {tpl}"


def test_qdc_template_exists():
    tpl = REPO / "tools/dashboard/templates/qdc_canvas/index.html"
    assert tpl.exists(), f"qdc template missing: {tpl}"


# ── icdev/ mirror existence ────────────────────────────────────────────────────

def test_odc_mirror_exists():
    tpl = REPO / "icdev/tools/dashboard/templates/observability_canvas/index.html"
    assert tpl.exists(), f"odc icdev mirror missing: {tpl}"


def test_pdc_mirror_exists():
    tpl = REPO / "icdev/tools/dashboard/templates/pipeline/index.html"
    assert tpl.exists(), f"pdc icdev mirror missing: {tpl}"


def test_qdc_mirror_exists():
    tpl = REPO / "icdev/tools/dashboard/templates/qdc_canvas/index.html"
    assert tpl.exists(), f"qdc icdev mirror missing: {tpl}"


# ── IQE adapters ──────────────────────────────────────────────────────────────

def test_odc_iqe_adapter_exists():
    adapter = REPO / "tools/iqe/adapters/observability.py"
    assert adapter.exists(), f"odc IQE adapter missing: {adapter}"


def test_pdc_iqe_adapter_exists():
    adapter = REPO / "tools/iqe/adapters/pipeline.py"
    assert adapter.exists(), f"pdc IQE adapter missing: {adapter}"


def test_qdc_iqe_adapter_exists():
    adapter = REPO / "tools/iqe/adapters/qdc.py"
    assert adapter.exists(), f"qdc IQE adapter missing: {adapter}"


# ── Seed queries ───────────────────────────────────────────────────────────────

def test_odc_seed_queries_exist():
    d = REPO / "context/iqe/queries/observability"
    assert d.exists(), f"odc seed query dir missing: {d}"
    files = list(d.glob("*.iqe"))
    assert len(files) >= 3, f"odc needs ≥3 seed queries, found {len(files)}"


def test_pdc_seed_queries_exist():
    d = REPO / "context/iqe/queries/pipeline"
    assert d.exists(), f"pdc seed query dir missing: {d}"
    files = list(d.glob("*.iqe"))
    assert len(files) >= 3, f"pdc needs ≥3 seed queries, found {len(files)}"


def test_qdc_seed_queries_exist():
    d = REPO / "context/iqe/queries/qdc"
    assert d.exists(), f"qdc seed query dir missing: {d}"
    files = list(d.glob("*.iqe"))
    assert len(files) >= 3, f"qdc needs ≥3 seed queries, found {len(files)}"


# ── Registry completeness.template accuracy ────────────────────────────────────

def _load_registry():
    import yaml
    with open(REPO / "args/component_registry.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_component(registry, key):
    return next((c for c in registry.get("components", []) if c.get("key") == key), None)


def test_odc_registry_template_correct():
    reg = _load_registry()
    c = _get_component(reg, "odc")
    assert c is not None, "odc not found in registry"
    declared = REPO / c["completeness"]["template"]
    assert declared.exists(), f"odc completeness.template {declared} does not exist"


def test_pdc_registry_template_correct():
    reg = _load_registry()
    c = _get_component(reg, "pdc")
    assert c is not None, "pdc not found in registry"
    declared = REPO / c["completeness"]["template"]
    assert declared.exists(), f"pdc completeness.template {declared} does not exist"


def test_qdc_registry_template_correct():
    reg = _load_registry()
    c = _get_component(reg, "qdc")
    assert c is not None, "qdc not found in registry"
    declared = REPO / c["completeness"]["template"]
    assert declared.exists(), f"qdc completeness.template {declared} does not exist"


# ── Registry seed_queries path accuracy ───────────────────────────────────────

def test_qdc_registry_seed_queries_correct():
    reg = _load_registry()
    c = _get_component(reg, "qdc")
    assert c is not None
    sq_path = REPO / c["completeness"]["seed_queries"]
    assert sq_path.exists(), f"qdc seed_queries path {sq_path} does not exist"


# ── pdx-ops-02: reachability — nav links to previously orphaned pdc pages ──────

_PDC_INDEX_REL = "tools/dashboard/templates/pipeline/index.html"
_PDC_INDEX_MIRROR_REL = "icdev/tools/dashboard/templates/pipeline/index.html"
_PDC_NAV_LINKS = [
    "/devops/runbooks",
    "/devops/sops",
    "/devops/twin",
    "/devops/ask",
]


def test_pdc_index_has_reachability_nav_links():
    """index.html must link the four otherwise-orphaned pdc pages (page-gate item 7)."""
    html = (REPO / _PDC_INDEX_REL).read_text(encoding="utf-8")
    for href in _PDC_NAV_LINKS:
        assert f'href="{href}"' in html, f"pdc index missing nav link to {href}"


def test_pdc_index_mirror_has_reachability_nav_links():
    """The icdev/ mirror must carry the same nav links (byte-identical mirror)."""
    html = (REPO / _PDC_INDEX_MIRROR_REL).read_text(encoding="utf-8")
    for href in _PDC_NAV_LINKS:
        assert f'href="{href}"' in html, f"pdc index mirror missing nav link to {href}"


def test_pdc_index_mirror_byte_identical():
    src = (REPO / _PDC_INDEX_REL).read_bytes()
    mirror = (REPO / _PDC_INDEX_MIRROR_REL).read_bytes()
    assert src == mirror, "pdc index.html and its icdev/ mirror diverge"


def test_pdc_runbooks_extend_base_navy_theme():
    """runbooks.html + runbook_detail.html must extend base.html (navy theme), not be
    standalone GitHub-dark HTML documents."""
    for rel in (
        "tools/dashboard/templates/pipeline/runbooks.html",
        "tools/dashboard/templates/pipeline/runbook_detail.html",
    ):
        html = (REPO / rel).read_text(encoding="utf-8")
        assert '{% extends "base.html" %}' in html, f"{rel} must extend base.html"
        assert "<!DOCTYPE html>" not in html, f"{rel} must not be a standalone document"
        assert "#0d1117" not in html, f"{rel} must not keep the GitHub-dark palette"


def test_pdc_runbooks_mirror_byte_identical():
    for rel in ("runbooks.html", "runbook_detail.html"):
        src = (REPO / "tools/dashboard/templates/pipeline" / rel).read_bytes()
        mirror = (REPO / "icdev/tools/dashboard/templates/pipeline" / rel).read_bytes()
        assert src == mirror, f"pipeline/{rel} and its icdev/ mirror diverge"
