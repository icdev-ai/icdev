# CUI // SP-CTI
"""Unit tests for the ACF Oracle-style deterministic concept verifiers (acf-ada-03).

Each verifier is exercised against hermetic mock fixtures (a temp manifest dir, a
fake app.py, a fake init_icdev_db.py) so the tests never depend on the live repo
state. Covers the five acceptance criteria:

  1. oracle_verifiers.py exists with all three functions
  2. a concept matching an existing manifest entry FAILS concept_not_in_manifest
  3. a concept with a new route missing from app.py FAILS route_not_listed
  4. a concept with a table missing from init_icdev_db.py FAILS orphan_db_table
  5. all verifiers run in < 100 ms
"""
import time

import pytest

from tools.foundry import oracle_verifiers as ov


# --------------------------------------------------------------------------
# Fixtures — mock manifest / app.py / init_icdev_db.py
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_caches():
    ov.clear_caches()
    yield
    ov.clear_caches()


@pytest.fixture
def manifest_dir(tmp_path):
    d = tmp_path / "manifest"
    d.mkdir()
    (d / "security.md").write_text(
        "# Security Tools\n\n"
        "| Tool | Path | Description |\n"
        "|------|------|-------------|\n"
        "| Smart Contract Auditor | tools/sca/engine.py | Audits smart contracts "
        "for reentrancy and overflow vulnerabilities |\n"
        "| Bandit Scanner | tools/sec/bandit.py | Runs static analysis for Python "
        "security defects |\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def app_path(tmp_path):
    p = tmp_path / "app.py"
    p.write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n\n"
        "_CANVAS_DEFS = [\n"
        '    ("dic", "ICDEV_DIC_ENABLED", "tools.document_intelligence.blueprint", "dic_bp"),\n'
        '    ("slides", "ICDEV_SLIDES_ENABLED", "tools.slides.blueprint", "slides_bp"),\n'
        "]\n\n"
        '@app.route("/existing-canvas")\n'
        "def existing_canvas():\n"
        "    return 'ok'\n\n"
        '@bp.route("/api/foo/items", methods=["POST"])\n'
        "def foo_items():\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def init_path(tmp_path):
    p = tmp_path / "init_icdev_db.py"
    p.write_text(
        'SCHEMA = """\n'
        "CREATE TABLE existing_items (id TEXT PRIMARY KEY, name TEXT);\n"
        "CREATE TABLE IF NOT EXISTS existing_events (id TEXT PRIMARY KEY, ts TEXT);\n"
        '"""\n',
        encoding="utf-8",
    )
    return p


# --------------------------------------------------------------------------
# Acceptance 1 — module exists with all three functions
# --------------------------------------------------------------------------
def test_module_exposes_all_three_verifiers():
    assert callable(ov.concept_not_in_manifest)
    assert callable(ov.route_not_listed)
    assert callable(ov.orphan_db_table)


def test_verdict_shape(manifest_dir):
    r = ov.concept_not_in_manifest(
        {"name": "Quantum Tea Brewer", "proposed_capability": "brew tea via entanglement"},
        manifest_dir=manifest_dir,
    )
    assert set(r) == {"lens", "passed", "confidence", "detail"}
    assert isinstance(r["passed"], bool)
    assert 0.0 <= r["confidence"] <= 1.0
    assert isinstance(r["detail"], str) and r["detail"]


# --------------------------------------------------------------------------
# Acceptance 2 — manifest-duplicate concept fails concept_not_in_manifest
# --------------------------------------------------------------------------
def test_concept_matching_manifest_entry_fails(manifest_dir):
    concept = {
        "name": "Smart Contract Auditor",
        "proposed_capability": "audit smart contracts for reentrancy vulnerabilities",
    }
    r = ov.concept_not_in_manifest(concept, manifest_dir=manifest_dir)
    assert r["passed"] is False
    assert r["lens"] == "concept_not_in_manifest"
    assert "Smart Contract Auditor" in r["detail"]


def test_net_new_concept_passes_manifest(manifest_dir):
    concept = {
        "name": "Quantum Tea Brewer",
        "proposed_capability": "brew loose-leaf tea using quantum entanglement timers",
    }
    r = ov.concept_not_in_manifest(concept, manifest_dir=manifest_dir)
    assert r["passed"] is True


def test_empty_manifest_dir_passes(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = ov.concept_not_in_manifest(
        {"name": "X", "proposed_capability": "do something"}, manifest_dir=empty
    )
    assert r["passed"] is True
    assert r["confidence"] == 0.0


# --------------------------------------------------------------------------
# Acceptance 3 — concept with route missing from app.py fails route_not_listed
# --------------------------------------------------------------------------
def test_concept_with_missing_route_fails(app_path):
    concept = {
        "canvas_contract": {
            "slug": "brand-new-canvas",
            "routes": [
                {"method": "GET", "path": "/brand-new-canvas", "kind": "page"},
            ],
        }
    }
    r = ov.route_not_listed(concept, app_path=app_path)
    assert r["passed"] is False
    assert r["lens"] == "route_not_listed"
    assert "/brand-new-canvas" in r["detail"]


def test_concept_with_registered_explicit_route_passes(app_path):
    concept = {
        "canvas_contract": {
            "slug": "existing-canvas",
            "routes": [{"method": "GET", "path": "/existing-canvas", "kind": "page"}],
        }
    }
    r = ov.route_not_listed(concept, app_path=app_path)
    assert r["passed"] is True


def test_concept_with_canvas_def_key_route_passes(app_path):
    # "dic" is registered via _CANVAS_DEFS, so /dic counts as listed.
    concept = {
        "canvas_contract": {
            "slug": "dic",
            "routes": [{"method": "GET", "path": "/dic", "kind": "page"}],
        }
    }
    r = ov.route_not_listed(concept, app_path=app_path)
    assert r["passed"] is True


# --------------------------------------------------------------------------
# Acceptance 4 — concept with table missing from init_icdev_db.py fails
# --------------------------------------------------------------------------
def test_concept_with_orphan_table_fails(init_path):
    concept = {
        "canvas_contract": {
            "tables": [
                {"name": "brandnew_items"},
                {"name": "brandnew_events"},
            ]
        }
    }
    r = ov.orphan_db_table(concept, init_path=init_path)
    assert r["passed"] is False
    assert r["lens"] == "orphan_db_table"
    assert "brandnew_items" in r["detail"]


def test_concept_with_existing_tables_passes(init_path):
    concept = {
        "canvas_contract": {
            "tables": [
                {"name": "existing_items"},
                {"name": "existing_events"},
            ]
        }
    }
    r = ov.orphan_db_table(concept, init_path=init_path)
    assert r["passed"] is True


def test_partial_orphan_fails_with_fractional_confidence(init_path):
    concept = {
        "canvas_contract": {
            "tables": [
                {"name": "existing_items"},  # present
                {"name": "missing_one"},     # orphan
            ]
        }
    }
    r = ov.orphan_db_table(concept, init_path=init_path)
    assert r["passed"] is False
    assert r["confidence"] == pytest.approx(0.5)


# --------------------------------------------------------------------------
# run-all convenience
# --------------------------------------------------------------------------
def test_verify_concept_runs_all_three(manifest_dir, app_path, init_path):
    concept = {
        "name": "Quantum Tea Brewer",
        "proposed_capability": "brew tea via quantum timers",
        "canvas_contract": {
            "slug": "qtb",
            "routes": [{"method": "GET", "path": "/qtb", "kind": "page"}],
            "tables": [{"name": "qtb_items"}],
        },
    }
    results = ov.verify_concept(
        concept, manifest_dir=manifest_dir, app_path=app_path, init_path=init_path
    )
    assert [r["lens"] for r in results] == [
        "concept_not_in_manifest",
        "route_not_listed",
        "orphan_db_table",
    ]
    # net-new capability passes manifest; brand-new route + table fail.
    assert results[0]["passed"] is True
    assert results[1]["passed"] is False
    assert results[2]["passed"] is False


# --------------------------------------------------------------------------
# Acceptance 5 — all verifiers run in < 100 ms
# --------------------------------------------------------------------------
def test_all_verifiers_under_100ms(manifest_dir, app_path, init_path):
    concept = {
        "name": "Smart Contract Auditor",
        "proposed_capability": "audit smart contracts for vulnerabilities",
        "canvas_contract": {
            "slug": "brand-new-canvas",
            "routes": [{"method": "GET", "path": "/brand-new-canvas", "kind": "page"}],
            "tables": [{"name": "brandnew_items"}],
        },
    }
    ov.clear_caches()  # measure cold (uncached) cost
    start = time.perf_counter()
    ov.verify_concept(
        concept, manifest_dir=manifest_dir, app_path=app_path, init_path=init_path
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    assert elapsed_ms < 100.0, f"verifiers took {elapsed_ms:.1f} ms (>= 100 ms)"
