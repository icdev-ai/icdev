# CUI // SP-CTI
"""Core-epic closeout tests for the Cortex public API surface (ctx-core-04).

Locks the package facade contract: every ``__all__`` export resolves in both
namespaces (``tools.cortex`` shim and ``icdev.tools.cortex`` canonical), the
two package trees stay byte-identical mirrors, and the module registration
checklist artifacts (manifest shard + index line) exist and cover every
cortex module.
"""
import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

CORTEX_MODULES = ["__init__.py", "api.py", "config.py", "governance.py", "schemas.py", "search_service.py"]


# ---------------------------------------------------------------------------
# __all__ export contract
# ---------------------------------------------------------------------------

def test_all_exports_resolve_in_shim_namespace():
    pkg = importlib.import_module("tools.cortex")
    missing = [name for name in pkg.__all__ if not hasattr(pkg, name)]
    assert not missing, f"tools.cortex.__all__ names missing attributes: {missing}"


def test_all_exports_resolve_in_canonical_namespace():
    pkg = importlib.import_module("icdev.tools.cortex")
    missing = [name for name in pkg.__all__ if not hasattr(pkg, name)]
    assert not missing, f"icdev.tools.cortex.__all__ names missing attributes: {missing}"


def test_all_identical_across_namespaces():
    shim = importlib.import_module("tools.cortex")
    canonical = importlib.import_module("icdev.tools.cortex")
    assert list(shim.__all__) == list(canonical.__all__)


def test_all_is_sorted_and_unique():
    pkg = importlib.import_module("tools.cortex")
    exports = list(pkg.__all__)
    assert exports == sorted(set(exports)), "__all__ must stay sorted and duplicate-free"


def test_facade_callables_exported():
    """The 5 shipped facade entry points are reachable from the package root."""
    pkg = importlib.import_module("tools.cortex")
    for name in ("complete", "classify", "extract", "search", "classify_route"):
        assert callable(getattr(pkg, name)), f"tools.cortex.{name} is not callable"


# ---------------------------------------------------------------------------
# Mirror parity: tools/cortex/ <-> icdev/tools/cortex/
# ---------------------------------------------------------------------------

def test_cortex_package_mirrored_byte_identical():
    diffs = []
    for module in CORTEX_MODULES:
        shim_path = REPO_ROOT / "tools" / "cortex" / module
        canonical_path = REPO_ROOT / "icdev" / "tools" / "cortex" / module
        assert shim_path.exists(), f"missing {shim_path}"
        assert canonical_path.exists(), f"missing mirror {canonical_path}"
        if shim_path.read_bytes() != canonical_path.read_bytes():
            diffs.append(module)
    assert not diffs, f"tools/cortex and icdev/tools/cortex diverge: {diffs}"


def test_no_stray_modules_outside_mirror_list():
    """A new cortex module must be added to CORTEX_MODULES (and the manifest shard)."""
    for tree in (REPO_ROOT / "tools" / "cortex", REPO_ROOT / "icdev" / "tools" / "cortex"):
        found = sorted(p.name for p in tree.glob("*.py"))
        assert found == sorted(CORTEX_MODULES), f"unexpected module set in {tree}: {found}"


def test_cortex_config_yaml_mirrored():
    shim = REPO_ROOT / "args" / "cortex_config.yaml"
    canonical = REPO_ROOT / "icdev" / "args" / "cortex_config.yaml"
    assert shim.exists() and canonical.exists()
    assert shim.read_bytes() == canonical.read_bytes()


# ---------------------------------------------------------------------------
# Registration checklist: manifest shard + index
# ---------------------------------------------------------------------------

def test_manifest_shard_exists_and_covers_all_modules():
    shard = REPO_ROOT / "tools" / "manifest" / "cortex.md"
    assert shard.exists(), "tools/manifest/cortex.md shard missing"
    text = shard.read_text(encoding="utf-8")
    uncovered = [m for m in CORTEX_MODULES if f"tools/cortex/{m}" not in text]
    assert not uncovered, f"manifest shard does not document: {uncovered}"


def test_manifest_index_links_cortex_shard():
    index = (REPO_ROOT / "tools" / "manifest.md").read_text(encoding="utf-8")
    assert "manifest/cortex.md" in index, "tools/manifest.md missing cortex shard index line"
