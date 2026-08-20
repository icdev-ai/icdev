# CUI // SP-CTI
"""A module with no in-repo caller BY DESIGN needs a gate, not a comment (ctx-reach-02).

``tools/cortex/client.py`` is 542 lines and 23 public methods that nothing in
ICDEV imports. That is deliberate — the only ICDEV process that could call it IS
the Cortex server it talks to, and it reaches the same operations in-process
through ``tools/cortex/api.py`` — but "deliberate" and "dead" are
indistinguishable from inside the repo, which is exactly the shape
``check_capability_liveness`` exists to catch and could not see here (a vendored
SDK is not one of the seven telemetry-backed classes it measures).

So the third state is declared in ``args/external_only_surfaces.yaml``, and the
declaration carries obligations instead of a budget. These tests pin BOTH
directions, because a suppression list that only ever passes is the failure mode:

  * the real, shipped declaration passes (test 1) — no fixture can prove that;
  * each obligation independently FAILS when broken (tests 2-7);
  * a production importer appearing is a failure whose remedy is to DELETE the
    entry, not widen it (test 5) — an exemption outliving its justification is
    the same defect in a new costume;
  * the config carries no numeric budget at all (test 8).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.awareness.capability_consumption import PROBES as CAPABILITY_CLASSES
from tools.workflow import coherence_checker as cc


# ---------------------------------------------------------------------------
# 1. The shipped declaration
# ---------------------------------------------------------------------------


def test_the_real_declaration_passes():
    """The committed tree satisfies every obligation it declares.

    This is the test that makes the rest meaningful: the fixtures below prove
    the check CAN fail, but only this one proves the repo is actually in the
    state the decision record claims.
    """
    result = cc.check_external_only_surfaces()
    assert result.status == "pass", (
        "external-only obligations are not satisfied: " + "; ".join(result.missing)
    )


def test_cortex_client_is_the_declared_surface():
    entries = cc._external_only_config().get("surfaces") or []
    paths = {str(e.get("path")) for e in entries if isinstance(e, dict)}
    assert "tools/cortex/client.py" in paths


def test_the_decision_doc_exists_and_the_docstring_names_it():
    """A reader who opens client.py must find the answer IN client.py."""
    doc = "docs/design/ctx-reach-02-cortex-client-external-only.md"
    assert (cc.PROJECT_ROOT / doc).is_file()
    for module in ("tools/cortex/client.py", "icdev/tools/cortex/client.py"):
        text = (cc.PROJECT_ROOT / module).read_text(encoding="utf-8")
        assert doc in text, f"{module} does not point a reader at {doc}"


def test_the_client_has_no_production_importers_in_either_namespace():
    """Measured, not asserted — and across both import spellings.

    ``tools.cortex.client`` and ``icdev.tools.cortex.client`` reach the same
    module (the root ``tools/`` package is a shim), so a scan that knew only one
    spelling would read half the repo as empty.
    """
    assert cc._production_importers("tools/cortex/client.py") == []
    names = cc._module_dotted_names("tools/cortex/client.py")
    assert names == {"tools.cortex.client", "icdev.tools.cortex.client"}


def test_the_clients_only_behavioural_test_is_gated():
    """An external-only surface whose tests CI never runs is unverified.

    This was the concrete defect ctx-reach-02 found: tests/cortex/test_client.py
    was the client's only behavioural coverage and it lived in the ungated
    backlog. Pinned here as well as in the check so that removing it from
    core.txt fails loudly in two places.
    """
    from tools.ci import gated_test_list

    core = set(gated_test_list.resolve("core", cc.PROJECT_ROOT))
    backlog = set(
        gated_test_list.parse(
            (cc.PROJECT_ROOT / "args" / "ci_test_backlog.txt").read_text(encoding="utf-8")
        )
    )
    assert "tests/cortex/test_client.py" in core
    assert "tests/cortex/test_client.py" not in backlog


# ---------------------------------------------------------------------------
# 2. Each obligation fails independently
# ---------------------------------------------------------------------------


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A throwaway repo root with one satisfied external-only declaration.

    Returns a callable that rewrites the declaration and re-runs the check, so
    each test below breaks exactly one obligation and reads the verdict.
    """
    root = tmp_path
    (root / "args" / "ci_test_files").mkdir(parents=True)
    (root / "tools" / "widget").mkdir(parents=True)
    (root / "docs" / "design").mkdir(parents=True)
    (root / "tests").mkdir()

    (root / "tools" / "widget" / "sdk.py").write_text(
        '"""A vendored SDK. See docs/design/widget.md."""\n', encoding="utf-8"
    )
    (root / "docs" / "design" / "widget.md").write_text("# why\n", encoding="utf-8")
    (root / "tests" / "test_widget.py").write_text(
        "from tools.widget.sdk import x  # noqa\n", encoding="utf-8"
    )
    (root / "args" / "ci_test_files" / "core.txt").write_text(
        "# a comment\ntests/test_widget.py\n", encoding="utf-8"
    )
    (root / "args" / "ci_test_backlog.txt").write_text("tests/test_other.py\n", encoding="utf-8")

    monkeypatch.setattr(cc, "PROJECT_ROOT", root)
    monkeypatch.setattr(cc, "vendor_parity_sources", lambda: ["tools/widget/sdk.py"])

    def run(**overrides):
        entry = {
            "path": "tools/widget/sdk.py",
            "decision": "docs/design/widget.md",
            "docstring_must_reference": "docs/design/widget.md",
            "max_in_repo_importers": 0,
            "must_be_vendor_parity_source": True,
            "gated_tests": ["tests/test_widget.py"],
        }
        entry.update(overrides)
        monkeypatch.setattr(cc, "_external_only_config", lambda: {"surfaces": [entry]})
        return cc.check_external_only_surfaces()

    run.root = root
    return run


def test_a_satisfied_fixture_declaration_passes(sandbox):
    """Guards the fixture itself — a broken sandbox would make every failure
    test below pass for the wrong reason."""
    assert sandbox().status == "pass"


def test_a_missing_decision_doc_fails(sandbox):
    result = sandbox(decision="docs/design/nope.md")
    assert result.status == "fail"
    assert any("does not exist" in m for m in result.missing)


def test_a_docstring_that_stops_naming_the_doc_fails(sandbox):
    (sandbox.root / "tools" / "widget" / "sdk.py").write_text(
        '"""A vendored SDK."""\n', encoding="utf-8"
    )
    result = sandbox()
    assert result.status == "fail"
    assert any("docstring does not reference" in m for m in result.missing)


def test_a_production_importer_fails_and_says_to_delete_the_entry(sandbox):
    """The stale-declaration case. The remedy is removal, not a wider budget."""
    (sandbox.root / "tools" / "consumer.py").write_text(
        "from tools.widget.sdk import thing\n", encoding="utf-8"
    )
    result = sandbox()
    assert result.status == "fail"
    assert any("DELETE the" in m and "tools/consumer.py" in m for m in result.missing)


def test_the_icdev_namespace_spelling_also_counts_as_an_importer(sandbox):
    """Importing via the mirror is still importing — a consumer must not be able
    to hide behind the shim namespace."""
    (sandbox.root / "tools" / "consumer.py").write_text(
        "import icdev.tools.widget.sdk\n", encoding="utf-8"
    )
    assert sandbox().status == "fail"


@pytest.mark.parametrize(
    "statement",
    [
        "from tools.widget.sdk import thing",
        "from tools.widget import sdk",  # checking node.module alone misses this
        "from tools.widget import sdk as s",
        "import tools.widget.sdk",
        "import tools.widget.sdk as s",
        "from icdev.tools.widget import sdk",
    ],
)
def test_every_import_spelling_is_detected(sandbox, statement):
    """An importer must not be able to hide behind a different spelling.

    ``from tools.widget import sdk`` binds the module just as surely as
    ``from tools.widget.sdk import thing``, but it leaves ``node.module`` one
    segment short — the gap this parametrisation exists to keep closed.
    """
    (sandbox.root / "tools" / "consumer.py").write_text(statement + "\n", encoding="utf-8")
    result = sandbox()
    assert result.status == "fail", f"{statement!r} was not detected as an importer"
    assert any("tools/consumer.py" in m for m in result.missing)


def test_a_lookalike_import_is_not_a_false_positive(sandbox):
    """The cheap text prefilter must not turn into a substring match."""
    (sandbox.root / "tools" / "consumer.py").write_text(
        "from tools.widget.sdk_helpers import sdk  # not the surface\n"
        "from other.widget.sdk import thing\n",
        encoding="utf-8",
    )
    assert sandbox().status == "pass"


def test_a_test_importer_does_not_count_as_a_consumer(sandbox):
    """Tests are the evidence the surface works, not consumers of it — counting
    them would make the declaration trivially self-satisfying."""
    assert sandbox().status == "pass"  # tests/test_widget.py imports it and is ignored


def test_a_surface_that_is_not_vendor_pinned_fails(sandbox, monkeypatch):
    monkeypatch.setattr(cc, "vendor_parity_sources", lambda: [])
    result = sandbox()
    assert result.status == "fail"
    assert any("vendor_parity" in m for m in result.missing)


def test_an_ungated_test_fails(sandbox):
    (sandbox.root / "args" / "ci_test_files" / "core.txt").write_text(
        "# nothing gated\n", encoding="utf-8"
    )
    result = sandbox()
    assert result.status == "fail"
    assert any("core.txt" in m for m in result.missing)


def test_a_test_in_both_lists_fails(sandbox):
    (sandbox.root / "args" / "ci_test_backlog.txt").write_text(
        "tests/test_widget.py\n", encoding="utf-8"
    )
    result = sandbox()
    assert result.status == "fail"
    assert any("BOTH" in m for m in result.missing)


def test_a_declared_surface_that_does_not_exist_fails(sandbox):
    result = sandbox(path="tools/widget/gone.py")
    assert result.status == "fail"
    assert any("does not exist" in m for m in result.missing)


# ---------------------------------------------------------------------------
# 3. The config may never become a budget
# ---------------------------------------------------------------------------


def test_the_config_carries_no_numeric_budget():
    """``max_in_repo_importers`` is the only number allowed, and only as 0.

    Every other gate in this repo that acquired a count acquired a backlog with
    it (args/liveness_gate.yaml, args/model_id_gate.yaml). This file must stay a
    list of obligations: the cheap path has to be deleting the module or wiring
    it up, never raising a number.
    """
    for entry in cc._external_only_config().get("surfaces") or []:
        for key, value in entry.items():
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            assert key == "max_in_repo_importers" and value == 0, (
                f"{key}={value} is a budget; external-only declarations carry "
                "obligations, not counts"
            )


def test_the_check_is_registered_and_runs_in_both_tiers():
    assert cc.CHECK_REGISTRY["external_only_surfaces"] is cc.check_external_only_surfaces
    assert "external_only_surfaces" not in cc.HEAVY_CHECKS
    assert "external_only_surfaces" in cc.select_checks("fast", [Path("tools/cortex/client.py")])
    assert "external_only_surfaces" in cc.select_checks("full")


def test_no_liveness_budget_was_raised_for_this():
    """The decision explicitly did NOT buy its way through an existing gate.

    args/liveness_gate.yaml is a ratchet; this task must not have touched it.
    Recorded as a test because "we added a check instead of raising a budget" is
    the load-bearing claim of the decision record, and prose does not hold.
    """
    import yaml

    gate = yaml.safe_load(
        (cc.PROJECT_ROOT / "args" / "liveness_gate.yaml").read_text(encoding="utf-8")
    )
    grandfathered = gate.get("grandfathered") or {}
    # A key naming a DECLARED capability class is a measurement class, not a
    # module exemption, and this scan must not confuse the two. `cortex_backend`
    # and `cortex_facade` (cef-ci-01) count Cortex RUNGS and VERBS through
    # cortex_audit; `tools/cortex/client.py` is a vendored SDK surface and can
    # never be a capability class, so dropping the declared classes narrows the
    # substring proxy back onto exactly what this test has always meant.
    grandfathered = {
        key: value for key, value in grandfathered.items()
        if key not in CAPABILITY_CLASSES
    }
    assert not any("cortex" in key or "client" in key for key in grandfathered), (
        "tools/cortex/client.py should be governed by check_external_only_surfaces, "
        "not by a liveness budget"
    )
