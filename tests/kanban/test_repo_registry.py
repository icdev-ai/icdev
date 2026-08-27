# CUI // SP-CTI
"""Tests for repo-aware kanban dispatch resolution (tools/kanban/repo_registry.py).

Default (unmatched id / missing registry) always resolves to ICDev, so an empty
registry is a no-op and existing tasks are byte-unchanged.
"""
from __future__ import annotations

import textwrap

import pytest

from tools.kanban import repo_registry as rr


@pytest.fixture
def registry(tmp_path):
    p = tmp_path / "kanban_external_repos.yaml"
    p.write_text(textwrap.dedent("""
        repos:
          compass:  { base_branch: main,     root_env: TEST_KANBAN_COMPASS }
          idea_lab: { base_branch: develop,  root_env: TEST_KANBAN_IDEALAB }
        prefixes:
          prem-cpmp: compass
          prem-ideal: idea_lab
          prem: compass
    """), encoding="utf-8")
    return p


def test_unmatched_id_is_icdev_default(registry):
    t = rr.resolve_task_repo("ctx-core-01", config_path=registry)
    assert t.is_external is False
    assert t.name == "icdev"
    assert t.base_branch == "main"
    assert t.root is not None and t.dispatchable is True


def test_missing_registry_is_icdev(tmp_path):
    t = rr.resolve_task_repo("prem-cpmp-01", config_path=tmp_path / "nope.yaml")
    assert t.is_external is False and t.name == "icdev"


def test_external_match_unconfigured_root_is_not_dispatchable(registry, monkeypatch):
    monkeypatch.delenv("TEST_KANBAN_COMPASS", raising=False)
    t = rr.resolve_task_repo("prem-cpmp-03", config_path=registry)
    assert t.is_external is True
    assert t.name == "compass"
    assert t.root is None
    assert t.dispatchable is False  # root env unset -> must be skipped, not built in ICDev


def test_external_match_configured_root_is_dispatchable(registry, monkeypatch, tmp_path):
    root = tmp_path / "compass"
    root.mkdir()
    monkeypatch.setenv("TEST_KANBAN_COMPASS", str(root))
    t = rr.resolve_task_repo("prem-cpmp-03", config_path=registry)
    assert t.is_external is True and t.dispatchable is True
    assert str(t.root) == str(root)
    assert t.base_branch == "main"


def test_longest_prefix_wins(registry, monkeypatch):
    monkeypatch.setenv("TEST_KANBAN_IDEALAB", "/x")
    monkeypatch.setenv("TEST_KANBAN_COMPASS", "/y")
    # 'prem-ideal' (idea_lab) must beat the broader 'prem' (compass).
    t = rr.resolve_task_repo("prem-ideal-02", config_path=registry)
    assert t.name == "idea_lab"
    assert t.base_branch == "develop"
    # 'prem-msr' matches only the broad 'prem' -> compass.
    t2 = rr.resolve_task_repo("prem-msr-01", config_path=registry)
    assert t2.name == "compass"


def test_broken_registry_degrades_to_icdev(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("{{{ not yaml", encoding="utf-8")
    t = rr.resolve_task_repo("prem-cpmp-01", config_path=bad)
    assert t.is_external is False and t.name == "icdev"


def test_is_external_task_helper(registry, monkeypatch):
    monkeypatch.delenv("TEST_KANBAN_COMPASS", raising=False)
    assert rr.is_external_task("prem-cpmp-01", config_path=registry) is True
    assert rr.is_external_task("ctx-core-01", config_path=registry) is False


def test_shipped_registry_marks_prem_external():
    # The checked-in args/kanban_external_repos.yaml must classify prem-* streams
    # as external (default resolution, no config_path override).
    for tid in ("prem-cpmp-02", "prem-msr-03", "prem-lcatq-01-d1",
                "prem-recomp-01", "prem-ricoas-01", "prem-ideal-02"):
        assert rr.resolve_task_repo(tid).is_external is True, tid
    # A normal ICDev task stays ICDev.
    assert rr.resolve_task_repo("ctx-core-01").is_external is False


def test_shipped_registry_parks_parent_split_streams(monkeypatch):
    # xit-reg-01: the ICDEV[domain] split registers its two sibling repos BEFORE
    # they exist (the CONCORD precedent). With their root env vars unset an
    # xcore-/xft- task must resolve EXTERNAL and NOT dispatchable, so the
    # dispatcher parks it instead of building core-extraction or trading-system
    # work inside this checkout. xit- genuinely builds here and stays ICDev.
    monkeypatch.delenv("ICDEV_KANBAN_REPO_CORE", raising=False)
    monkeypatch.delenv("ICDEV_KANBAN_REPO_FT", raising=False)
    for tid, repo in (("xcore-boot-01", "icdev_core"), ("xft-ing-01", "icdev_ft"),
                      ("xft-safe-01-d2", "icdev_ft")):
        t = rr.resolve_task_repo(tid)
        assert t.is_external is True, tid
        assert t.name == repo, tid
        assert t.dispatchable is False, tid
    assert rr.resolve_task_repo("xit-decl-01").is_external is False


def test_every_ft_domain_stream_on_the_board_is_external(monkeypatch):
    """The guard above named `xft-`, and the live streams are `ftl-` and `fdx-`.

    THE DEFECT THIS CATCHES (xit-reg-02). `ftl-` -- 53 cards, the ICDEV[FT]
    autonomous-trading loop -- was absent from args/kanban_external_repos.yaml and so
    fell through to the ICDev default: external=False, root=<this checkout>. This repo
    is PUBLIC. Releasing ftl-gate-00, which xit-rm-04 explicitly contemplates, would
    have had the dispatcher build trading work here -- the xit-leak-01 class reached
    through the dispatcher instead of through a commit.

    A prefix-by-prefix assertion could not catch it, because the missing prefix is
    exactly the one nobody thought to list. This reads the SHIPPED registry instead and
    asserts the property for every FT-domain stream, so a new one cannot be added
    without landing here too -- the discipline ked-reg-01 applied to the prem-* streams.

    Registering a stream does NOT enable it: with the root env unset the resolution is
    external + not dispatchable, i.e. PARKED.
    """
    monkeypatch.delenv("ICDEV_KANBAN_REPO_FT", raising=False)
    for stream in ("ftl", "fdx", "xft"):
        t = rr.resolve_task_repo(f"{stream}-zzz-01")
        assert t.is_external is True, f"{stream}- must not build in the public checkout"
        assert t.name == "icdev_ft", f"{stream}- resolved to {t.name}"
        assert t.dispatchable is False, f"{stream}- must be parked while the root is unset"
