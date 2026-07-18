# CUI // SP-CTI
"""Tests for PMC, CCC, and DSOC canvas template completeness."""
import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


# ── Template existence ────────────────────────────────────────────────────────

def test_pmc_index_template_exists():
    assert (REPO / "tools/dashboard/templates/pmc_canvas/index.html").exists()


def test_ccc_index_template_exists():
    assert (REPO / "tools/dashboard/templates/ccc_canvas/index.html").exists()


def test_dsoc_index_template_exists():
    assert (REPO / "tools/dashboard/templates/dsoc_canvas/index.html").exists()


# ── icdev mirror existence ────────────────────────────────────────────────────

def test_pmc_icdev_mirror_exists():
    assert (REPO / "icdev/tools/dashboard/templates/pmc_canvas/index.html").exists()


def test_ccc_icdev_mirror_exists():
    assert (REPO / "icdev/tools/dashboard/templates/ccc_canvas/index.html").exists()


def test_dsoc_icdev_mirror_exists():
    assert (REPO / "icdev/tools/dashboard/templates/dsoc_canvas/index.html").exists()


# ── Blueprint imports ─────────────────────────────────────────────────────────

def test_pmc_blueprint_importable():
    mod = importlib.import_module("tools.pmc_canvas.blueprint")
    assert callable(getattr(mod, "create_pmc_blueprint", None))


def test_ccc_blueprint_importable():
    mod = importlib.import_module("tools.ccc_canvas.blueprint")
    assert callable(getattr(mod, "create_ccc_blueprint", None))


def test_dsoc_blueprint_importable():
    mod = importlib.import_module("tools.dsoc_canvas.blueprint")
    assert callable(getattr(mod, "create_dsoc_blueprint", None))


# ── IQE adapters ─────────────────────────────────────────────────────────────

def test_pmc_iqe_adapter_exists():
    assert (REPO / "tools/iqe/adapters/pmc.py").exists()


def test_ccc_iqe_adapter_exists():
    assert (REPO / "tools/iqe/adapters/ccc.py").exists()


def test_dsoc_iqe_adapter_exists():
    assert (REPO / "tools/iqe/adapters/dsoc.py").exists()


# ── Seed queries ──────────────────────────────────────────────────────────────
#
# cvx-nav-02: the loaded seed queries live in the `<key>_canvas/*.iqe` dirs —
# those are what the dashboard /iqe loader globs and what the template dir is
# named. The bare `<key>/seed.yaml` dirs were an unread duplicate (no runtime
# consumer; every collection they named is already covered by a `.iqe` file) and
# were removed. These tests now assert the loaded dir is populated AND that the
# orphan bare dir is gone, so the drift cannot silently reappear.

def _count_seed_queries(canvas_key: str) -> int:
    """Count loaded .iqe seed queries in the canvas's `<key>_canvas/` dir."""
    seed_dir = REPO / f"context/iqe/queries/{canvas_key}_canvas"
    if not seed_dir.is_dir():
        return 0
    return len(list(seed_dir.glob("*.iqe")))


def test_pmc_seed_queries_exist():
    assert (REPO / "context/iqe/queries/pmc_canvas").is_dir()
    assert not (REPO / "context/iqe/queries/pmc").exists(), \
        "orphan bare pmc/ seed dir must stay removed (cvx-nav-02)"


def test_pmc_seed_queries_min_three():
    assert _count_seed_queries("pmc") >= 3


def test_ccc_seed_queries_exist():
    assert (REPO / "context/iqe/queries/ccc_canvas").is_dir()
    assert not (REPO / "context/iqe/queries/ccc").exists(), \
        "orphan bare ccc/ seed dir must stay removed (cvx-nav-02)"


def test_ccc_seed_queries_min_three():
    assert _count_seed_queries("ccc") >= 3


def test_dsoc_seed_queries_exist():
    assert (REPO / "context/iqe/queries/dsoc_canvas").is_dir()
    assert not (REPO / "context/iqe/queries/dsoc").exists(), \
        "orphan bare dsoc/ seed dir must stay removed (cvx-nav-02)"


def test_dsoc_seed_queries_min_three():
    assert _count_seed_queries("dsoc") >= 3


# ── Constants ─────────────────────────────────────────────────────────────────

def test_pmc_constants_non_empty():
    mod = importlib.import_module("tools.pmc_canvas.constants")
    attrs = [a for a in dir(mod) if not a.startswith("_")]
    assert len(attrs) > 0


def test_ccc_constants_non_empty():
    mod = importlib.import_module("tools.ccc_canvas.constants")
    attrs = [a for a in dir(mod) if not a.startswith("_")]
    assert len(attrs) > 0


def test_dsoc_constants_non_empty():
    mod = importlib.import_module("tools.dsoc_canvas.constants")
    attrs = [a for a in dir(mod) if not a.startswith("_")]
    assert len(attrs) > 0


# ── Registry completeness.template points to existing file ────────────────────

def test_pmc_registry_template_exists():
    assert (REPO / "tools/dashboard/templates/pmc_canvas/index.html").exists(), \
        "Registry completeness.template pmc_canvas/index.html must exist on disk"


def test_ccc_registry_template_exists():
    assert (REPO / "tools/dashboard/templates/ccc_canvas/index.html").exists(), \
        "Registry completeness.template ccc_canvas/index.html must exist on disk"


def test_dsoc_registry_template_exists():
    assert (REPO / "tools/dashboard/templates/dsoc_canvas/index.html").exists(), \
        "Registry completeness.template dsoc_canvas/index.html must exist on disk"
