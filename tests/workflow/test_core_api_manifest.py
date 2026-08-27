# CUI // SP-CTI
"""The shared-core API manifest and the parent gate that reads it (xcore-api-01).

These tests assert PROPERTIES, not today's population. `icdev-core` will gain
modules and symbols; a test pinning "5 exports" or "34 imports" would go red on
the next legitimate change and teach whoever hits it to edit the assertion. What
must hold is that the manifest describes THIS tree, that a symbol the pinned
core does not export is refused, and that every way of failing to measure
reports `warn` rather than `pass`.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from tools.workflow.core_api_manifest import (
    CoreApiError,
    _module_all,
    _module_constants,
    _text_hash,
    collect_surface,
    load_declaration,
    load_manifest,
    manifest_path,
    module_source,
    package_root,
    render_manifest,
    undeclared_core_modules,
    verify,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# The manifest describes this tree
# ---------------------------------------------------------------------------


def test_the_committed_manifest_matches_the_tree():
    """The staleness gate itself.

    This is the assertion that makes re-publishing the package a deliberate
    step: change a core module's public surface without regenerating and this
    goes red on a runner that has checked out neither the package nor the
    sibling parent.
    """
    result = verify()
    assert result["error"] is None, result["error"]
    assert result["drift"] == [], (
        "args/core_api_manifest.json no longer describes icdev/core/. Regenerate with "
        "`python tools/workflow/core_api_manifest.py --write`, then publish the package "
        "and bump pinned_version.\n" + "\n".join(result["drift"])
    )


def test_every_core_source_is_either_exported_or_declared_parent_local():
    """A module added to icdev/core/ and declared nowhere would ship to neither parent."""
    undeclared = undeclared_core_modules()
    assert undeclared == [], (
        "these modules are under the core source root but appear in neither `exports` "
        f"nor `parent_local` in args/core_api.yaml: {undeclared}"
    )


def test_rendering_is_stable():
    """Two renders of an unchanged tree are byte-identical.

    A manifest whose bytes wander produces a diff on every regeneration, and a
    diff nobody can read is a diff nobody checks.
    """
    assert render_manifest() == render_manifest()


def test_the_manifest_on_disk_is_exactly_what_render_produces():
    assert manifest_path().read_text(encoding="utf-8") == render_manifest()


# ---------------------------------------------------------------------------
# The declaration is the authority, not the directory
# ---------------------------------------------------------------------------


def test_shim_is_no_longer_under_icdev_core():
    """xcore-cut-02 moved it, and it HAD to move.

    Once this parent stopped shipping the rest of `icdev/core/`, `icdev.core` resolves to the
    installed distribution and its `__path__` does not include this parent's leftover
    directory -- `icdev.core.shim` would have been unimportable. It still lives in the parent,
    which is what the card requires; only its address changed.
    """
    decl = load_declaration()
    assert "icdev.core.shim" not in decl["exports"]
    assert "icdev.core.shim" not in (decl.get("parent_local") or {})
    assert (REPO_ROOT / "icdev/_shim.py").is_file(), "the shim must still be in the parent"
    assert not (REPO_ROOT / "icdev/core/shim.py").exists()

    from icdev import _shim

    assert hasattr(_shim, "install") and hasattr(_shim, "IcdevToolsAliasFinder")


def test_a_parent_local_entry_needs_a_written_reason():
    """Empty today, and the rule still has to hold for whatever is added next."""
    for module, reason in (load_declaration().get("parent_local") or {}).items():
        assert isinstance(reason, str) and len(reason.strip()) > 20, (
            f"{module} is exempted from the published surface with no usable reason"
        )


@pytest.mark.parametrize("branch", ["main", "master", "HEAD"])
def test_a_branch_is_not_a_legal_pin(tmp_path, monkeypatch, branch):
    """"No floating `main` dependency anywhere" — the card's last line, enforced.

    A branch pin means the manifest describes whatever the core happened to look
    like at install time, and two installs of the same commit can disagree.
    """
    import tools.workflow.core_api_manifest as mod

    decl = tmp_path / "core_api.yaml"
    decl.write_text(
        f"package: icdev-core\npinned_version: {branch}\nresolution: installed\n"
        "exports: [icdev.core]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "declaration_path", lambda: decl)
    with pytest.raises(CoreApiError, match="branch, not a version"):
        mod.load_declaration()


def test_an_absent_declaration_raises_rather_than_defaulting(tmp_path, monkeypatch):
    """Defaulting to "no exports" would fail the parent gate on every core import."""
    import tools.workflow.core_api_manifest as mod

    monkeypatch.setattr(mod, "declaration_path", lambda: tmp_path / "nope.yaml")
    with pytest.raises(CoreApiError):
        mod.load_declaration()


def test_exports_resolve_to_the_installed_distribution_not_this_tree():
    """The whole point of xcore-cut-02, asserted directly.

    `icdev.core.paths` must come from the installed `icdev-core`, NOT from anywhere under this
    repository -- this parent no longer ships those files at all.
    """
    src = module_source("icdev.core.paths")
    assert src.is_file()
    assert not str(src).startswith(str(REPO_ROOT)), (
        f"icdev.core.paths resolved to {src}, inside this parent's tree — the cut did not take"
    )
    assert package_root().name == "core"


def test_a_module_that_cannot_be_located_raises():
    with pytest.raises(CoreApiError):
        module_source("icdev.core.definitely_not_a_module")


def test_this_parent_no_longer_ships_the_core_modules():
    for gone in ("__init__.py", "paths.py", "domain.py", "context.py", "sensitivity.py"):
        assert not (REPO_ROOT / "icdev/core" / gone).exists(), (
            f"icdev/core/{gone} is back in the parent — it would shadow the installed package "
            "on any machine that has both"
        )


def test_the_parents_own_table_manifest_stays():
    """Kept deliberately: a DATA file, not a module.

    `sensitivity._manifest_exempt` reads it from the REPO ROOT by path (not through
    importlib.resources) and `schema_ownership.py --regenerate` writes it there. A leftover
    `icdev/core/` holding only data does not shadow the installed package -- PEP 420 uses a
    namespace portion only when no regular package is found anywhere on the path.
    """
    assert (REPO_ROOT / "icdev/core/schema/tables.yaml").is_file()
    assert not (REPO_ROOT / "icdev/core/__init__.py").exists()


# ---------------------------------------------------------------------------
# Constants are part of the importable surface
# ---------------------------------------------------------------------------


def test_constants_are_captured_because_public_api_does_not_capture_them():
    """The property that motivated _module_constants, asserted rather than assumed.

    `_public_api` reports functions and classes — right for the vendored-copy
    comparison it was built for, and not the whole importable surface. Without
    this, `from icdev.core.domain import BUILTIN_DEFAULT` would be refused as a
    symbol the core does not export.
    """
    from tools.workflow.coherence_checker import _public_api

    source = module_source("icdev.core.domain").read_text(encoding="utf-8")
    constants = _module_constants(source)
    callables = {s.split("(")[0].removeprefix("class ").strip() for s in _public_api(source)}
    assert constants, "icdev/core/domain.py binds public module-level names"
    assert set(constants).isdisjoint(callables), (
        "the two derivations must cover different things, or one of them is redundant"
    )


def test_a_data_file_hash_ignores_line_endings_but_not_content(tmp_path):
    """The committed manifest must mean the same thing on Windows and on a Linux runner.

    `schema/tables.yaml` checks out CRLF under `core.autocrlf` and LF on CI, so hashing raw
    BYTES made the manifest platform-dependent: it was generated on Windows, and every Linux
    job then reported `schema/tables.yaml: content changed` — eight test failures whose real
    cause had nothing to do with what they assert. `_public_api` already avoids exactly this
    for source by comparing an AST (its docstring calls a byte comparison "pure noise"); a
    data file needs the same care one layer lower.
    """
    lf, crlf = tmp_path / "lf.yaml", tmp_path / "crlf.yaml"
    lf.write_bytes(b"rls_exempt:\n  - a\n  - b\n")
    crlf.write_bytes(b"rls_exempt:\r\n  - a\r\n  - b\r\n")
    assert _text_hash(lf) == _text_hash(crlf)

    # A trailing-newline difference is not a content change either.
    no_eol = tmp_path / "noeol.yaml"
    no_eol.write_bytes(b"rls_exempt:\n  - a\n  - b")
    assert _text_hash(lf) == _text_hash(no_eol)

    # ...and a REAL edit still moves it, or the normalisation would hide changes.
    edited = tmp_path / "edited.yaml"
    edited.write_bytes(b"rls_exempt:\n  - a\n  - c\n")
    assert _text_hash(lf) != _text_hash(edited)


def test_dunder_all_absent_is_none_not_empty():
    """None (declared nothing) and [] (declared an empty surface) are different answers."""
    assert _module_all("x = 1\n") is None
    assert _module_all("__all__ = []\n") == []
    assert _module_all("__all__ = ['b', 'a']\n") == ["a", "b"]


# ---------------------------------------------------------------------------
# The parent gate
# ---------------------------------------------------------------------------


@pytest.fixture
def probe(tmp_path):
    """A real file under tools/, because _scan_targets only sees files there.

    Named by pid so two shards cannot collide, and removed in teardown even when
    the test fails.
    """
    directory = REPO_ROOT / f"tools/_core_api_probe_{os.getpid()}"
    directory.mkdir(parents=True, exist_ok=True)

    def write(body: str) -> pathlib.Path:
        path = directory / "probe.py"
        path.write_text(body, encoding="utf-8")
        return path

    try:
        yield write
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _run(path):
    from tools.workflow.coherence_checker import check_core_api

    return check_core_api([path])


def test_a_symbol_the_pinned_core_does_not_export_fails(probe):
    """The card's case."""
    result = _run(probe("from icdev.core.paths import no_such_function\n"))
    assert result.status == "fail"
    assert any("no_such_function" in m for m in result.missing)


def test_a_module_the_pinned_core_does_not_export_fails(probe):
    result = _run(probe("from icdev.core.nonexistent import whatever\n"))
    assert result.status == "fail"
    assert any("icdev.core.nonexistent" in m for m in result.missing)


def test_an_exported_function_passes(probe):
    result = _run(probe("from icdev.core.paths import repo_root\n"))
    assert result.status == "pass", result.missing


def test_an_exported_constant_passes(probe):
    """The false positive `_module_constants` exists to prevent, end to end."""
    result = _run(probe("from icdev.core.domain import BUILTIN_DEFAULT\n"))
    assert result.status == "pass", result.missing


def test_a_submodule_imported_from_the_package_passes(probe):
    """`from icdev.core import paths` binds a MODULE; no callable in paths.py names it."""
    result = _run(probe("from icdev.core import paths\n"))
    assert result.status == "pass", result.missing


def test_a_parent_local_import_is_reported_and_never_failed(probe, monkeypatch):
    """A parent-local module is REPORTED, never failed — the code path, kept guarded.

    `parent_local` is empty since xcore-cut-02 (shim moved to `icdev/_shim.py`), so this uses
    a synthetic declaration. Without it the branch would be dead code that still ships, and
    the next module a parent keeps back would land on an unexercised path.
    """
    import tools.workflow.core_api_manifest as mod

    patched = dict(mod.load_manifest(), parent_local=["icdev.core.kept_back"])
    monkeypatch.setattr(mod, "load_manifest", lambda: patched)

    result = _run(probe("from icdev.core import kept_back\n"))
    assert result.status == "pass", result.missing
    assert any("parent-local" in line for line in result.actual)
    assert any("icdev.core.kept_back" in line for line in result.actual), (
        "the note must name the symbol that matched, not the module it was imported from"
    )


def test_an_import_of_a_module_no_longer_under_icdev_core_fails(probe):
    """The regression this card could have shipped.

    `from icdev.core import shim` resolved for as long as the parent carried its own
    `icdev/core/`. After the cut it cannot, and the gate must say so here rather than let it
    reach an ImportError at dashboard start-up.
    """
    result = _run(probe("from icdev.core import shim\n"))
    assert result.status == "fail"
    assert any("shim" in m for m in result.missing)


def test_a_diff_with_no_python_file_cannot_add_a_core_import():
    from tools.workflow.coherence_checker import check_core_api

    result = check_core_api([pathlib.Path("README.md")])
    assert result.status == "pass"


# ---------------------------------------------------------------------------
# Every way of not measuring reports warn, never pass
# ---------------------------------------------------------------------------


def test_an_unreadable_manifest_warns_rather_than_passing(monkeypatch, probe):
    """"did not run" must never read as "found nothing"."""
    import tools.workflow.core_api_manifest as mod

    monkeypatch.setattr(mod, "load_manifest", lambda: None)
    result = _run(probe("from icdev.core.paths import repo_root\n"))
    assert result.status == "warn"
    assert "NOT verified" in result.message


def test_a_broken_declaration_warns_rather_than_passing(monkeypatch, probe):
    import tools.workflow.core_api_manifest as mod

    def _boom():
        raise CoreApiError("args/core_api.yaml declares no exports")

    monkeypatch.setattr(mod, "load_declaration", _boom)
    result = _run(probe("from icdev.core.paths import repo_root\n"))
    assert result.status == "warn"


def test_a_tree_scan_that_finds_no_core_import_warns(monkeypatch):
    """A scan with nothing to check has verified nothing — that is not a clean bill."""
    import tools.workflow.coherence_checker as cc

    monkeypatch.setattr(cc, "_core_imports", lambda paths: [])
    monkeypatch.setattr(cc, "_scan_targets", lambda changed, subdir: [])
    result = cc.check_core_api(None)
    assert result.status == "warn"
    assert "unmeasured" in result.message


# ---------------------------------------------------------------------------
# One derivation of "the public API", not two
# ---------------------------------------------------------------------------


def test_the_manifest_reuses_public_api_and_does_not_reimplement_it():
    """Structural: a second opinion about the public API is how the two drift apart.

    Read from source rather than by importing, because an import proves only
    that the name resolves — not that the module has no private copy alongside.
    """
    source = (REPO_ROOT / "tools/workflow/core_api_manifest.py").read_text(encoding="utf-8")
    assert "from tools.workflow.coherence_checker import PROJECT_ROOT, _public_api" in source
    assert "def _public_api" not in source, (
        "core_api_manifest must not define its own _public_api — it is shared with "
        "check_vendor_parity and a second copy can disagree with the first"
    )


def test_the_check_is_registered():
    from tools.workflow.coherence_checker import CHECK_REGISTRY, HEAVY_CHECKS

    assert "core_api" in CHECK_REGISTRY
    assert "core_api" in HEAVY_CHECKS, (
        "a ~25s tree scan must be diff-triggered in the fast tier"
    )


def test_the_manifest_is_valid_json_with_the_fields_the_gate_reads():
    manifest = load_manifest()
    assert manifest is not None
    for key in ("package", "pinned_version", "modules", "parent_local", "surface_hash"):
        assert key in manifest, f"the parent gate reads {key}"
    for module, entry in manifest["modules"].items():
        assert {"path", "symbols", "constants", "signature_hash"} <= set(entry), module
    # the on-disk file is what the gate reads; prove it parses as committed
    json.loads(manifest_path().read_text(encoding="utf-8"))


def test_collect_surface_refuses_a_declared_export_that_cannot_be_located():
    """A declaration naming a module that does not exist must fail, not silently skip."""
    decl = {
        "package": "icdev-core",
        "pinned_version": "0.2.0",
        "resolution": "installed",
        "exports": ["icdev.core.does_not_exist"],
    }
    with pytest.raises(CoreApiError, match="could not be located"):
        collect_surface(decl)


def test_a_source_tree_resolution_is_refused(tmp_path, monkeypatch):
    """`resolution` may only be `installed` — there is no core source tree here any more."""
    import tools.workflow.core_api_manifest as mod

    decl = tmp_path / "core_api.yaml"
    decl.write_text(
        "package: icdev-core\npinned_version: 0.2.0\nresolution: tree\n"
        "exports: [icdev.core]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "declaration_path", lambda: decl)
    with pytest.raises(CoreApiError, match="only supported value"):
        mod.load_declaration()
