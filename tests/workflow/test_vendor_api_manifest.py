# CUI // SP-CTI
"""Vendored API drift must fail INSIDE this repo, with no external checkout.

``coherence_checker.py::check_vendor_parity`` compares canonical modules against
their vendored copies in the standalone repos, computes the right answer, and
cannot enforce: those copies live in SEPARATE PRIVATE repositories that ICDEV's
CI never checks out, and the check SKIPS a consumer whose path is absent. So
``drift`` stays empty and the gate returns pass — verified even with ``--gate``
and the source in ``--changed-files``.

That is repo topology, not an OS problem: ``/home/me/standalone`` skips exactly
as ``C:/AI/standalone`` does. Consequence measured 2026-08-13 —
``CortexClient.reason()`` and ``.agent()`` were added 2026-08-09 and both
vendored copies were still missing them, with ``last_synced`` reading
2026-08-02.

The committed manifest closes that: it cannot prove the consumers are in sync
(nothing inside this repo can), but it makes the moment the contract CHANGES
impossible to miss, which is the moment re-vendoring is owed.

The load-bearing test here is
``test_it_still_fails_with_no_standalone_checkout`` — the exact configuration in
which the original gate is powerless.
"""
from __future__ import annotations

import importlib
import pathlib

import pytest

vam = importlib.import_module("tools.workflow.vendor_api_manifest")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_every_declared_vendored_source_has_a_committed_manifest():
    sources = vam.declared_sources()
    assert sources, "args/vendor_parity.yaml declares no vendored sources"

    missing = [s for s in sources if not vam.manifest_path_for(s).is_file()]
    assert not missing, (
        f"no committed API manifest for {missing}. Generate with: "
        "python -m tools.workflow.vendor_api_manifest --write"
    )


def test_the_manifests_are_current():
    """The gate itself: source changed without the manifest being regenerated."""
    result = vam.check()
    assert result["ok"], (
        "vendored API manifest is stale — the vendored copies are now owed a "
        f"re-sync: {result['drift'] or result['missing_source']}. Regenerate: "
        "python -m tools.workflow.vendor_api_manifest --write"
    )


def test_it_still_fails_with_no_standalone_checkout(monkeypatch, tmp_path):
    """THE point of this manifest.

    check_vendor_parity returns pass here because both consumers are absent.
    This must not: it reads only files inside this repository.
    """
    monkeypatch.setenv("ICDEV_STANDALONE_ROOT", str(tmp_path / "definitely-absent"))

    source = vam.declared_sources()[0]
    original = (REPO_ROOT / source).read_text(encoding="utf-8")
    monkeypatch.setattr(
        vam, "render", lambda s: "# CUI // SP-CTI\n\nDrifted.method(self)\n"
    )

    result = vam.check()

    assert not result["ok"], (
        "manifest check passed while the canonical API had changed and no "
        "consumer repo was present — this is exactly the blind spot it exists "
        "to cover"
    )
    assert (REPO_ROOT / source).read_text(encoding="utf-8") == original


def test_the_manifest_uses_the_same_api_definition_as_the_parity_check():
    """Two implementations of "the public API" is how the drift went unnoticed."""
    from tools.workflow.coherence_checker import _public_api as checker_impl

    source = vam.declared_sources()[0]
    text = (REPO_ROOT / source).read_text(encoding="utf-8")

    assert vam._public_api(text) == checker_impl(text)


def test_a_manifest_records_the_methods_that_actually_drifted():
    """Regression pin for the measured case: reason() and agent()."""
    path = vam.manifest_path_for("tools/cortex/client.py")
    body = path.read_text(encoding="utf-8")

    assert "CortexClient.reason(" in body
    assert "CortexClient.agent(" in body


def test_vendor_parity_yaml_does_not_hardcode_a_machine_specific_default():
    """ICDEV is OS-agnostic; a Windows path as a shipped default is not.

    This does not fix the gate (repo topology does that — see the module
    docstring), it just stops the config asserting something untrue everywhere
    except one workstation.
    """
    import yaml

    data = yaml.safe_load(
        (REPO_ROOT / "args" / "vendor_parity.yaml").read_text(encoding="utf-8")
    ) or {}

    # Asserted over PARSED VALUES, not the raw text: the file documents the old
    # default in prose to explain why it was removed, and a substring check
    # would match that comment and be unable to pass.
    defaults = data.get("path_defaults") or {}
    offenders = {
        k: v for k, v in defaults.items()
        if isinstance(v, str) and (":" in v[:3] or v.startswith("\\\\") or v.startswith("/home/"))
    }
    assert not offenders, (
        f"machine-specific absolute path shipped as a default: {offenders}. "
        "ICDEV is OS-agnostic; set it via the environment instead."
    )


def test_write_is_idempotent(tmp_path, monkeypatch):
    before = {s: vam.manifest_path_for(s).read_text(encoding="utf-8")
              for s in vam.declared_sources()}
    vam.write()
    after = {s: vam.manifest_path_for(s).read_text(encoding="utf-8")
             for s in vam.declared_sources()}
    assert before == after, "--write is not idempotent; CI would flap"


@pytest.mark.parametrize("source", vam.declared_sources())
def test_manifest_path_is_deterministic_and_inside_args(source):
    p = vam.manifest_path_for(source)
    assert p == vam.manifest_path_for(source)
    assert vam.MANIFEST_DIR in p.parents
