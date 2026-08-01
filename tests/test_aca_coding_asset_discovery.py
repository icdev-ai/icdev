# CUI // SP-CTI
"""Authored starter/test code must be discovered, not just markdown.

fga-wire-02 ingested Tier-1 prose and is legitimately done, but content discovery
globs `*.md` only (content_loader.discover_steps). The Python step assets sitting
beside those markdown files were therefore never attached: every m01 step was
step_type='watch' with starter_code_path='' and test_code_path='', while
step1_starter.py and step1_test.py sat unused in the same directory.

124 asset files across 60 mission directories are affected, including all ten
Tier-1 missions — the entire onboarding path — and all seven Tier-3 missions. That
is why Tier 1 advertised CODING and delivered reading with an "Understood" button,
and why only 10 of 212 steps had any verification at all.

test_discovery_finds_every_authored_markdown_file asserted markdown coverage and
had no counterpart for assets, which is how this stayed invisible. These are that
counterpart.

Promotion rule under test: a sibling TEST promotes the step to 'coding', because a
test is what makes it gradeable (aca-int-02 refuses to credit a coding step with no
stored test — promoting without one would manufacture an ungradeable exercise). A
starter alone is attached for the editor but does not change the step type.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from apps.forge_academy.content_loader import CONTENT_ROOT, discover_steps


@pytest.fixture(scope="module")
def discovered():
    return discover_steps()


def _all_steps(discovered):
    for slug, steps in discovered.items():
        for st in steps:
            yield slug, st


# Exercises whose starter/test exist but whose PROSE was never authored. Discovery
# keys on markdown frontmatter, so there is nothing to attach these to — and
# attaching them anyway would produce a coding step with no problem statement,
# which is not shippable content. This is an authoring gap, deliberately distinct
# from the discovery bug aca-hon-05 fixed.
#
# m02-prompt-engineering has prose for steps 1-2 only; its step4 exercise has no
# lesson. The ten tier2 directories below contain NO .md file at all — they are
# also exactly the directories that appear to have "no catalog entry", which is
# explained by this and not by a discovery defect.
#
# The set is asserted EXACTLY so a new orphan fails the build instead of quietly
# joining the pile. Removing an entry (by authoring the lesson) is the fix.
_ASSETS_AWAITING_PROSE = {
    "tier1/m02-prompt-engineering/steps/step4_starter.py",
    "tier1/m02-prompt-engineering/steps/step4_test.py",
    "tier2/m-dataops-03-corrective-rag/steps/step1_starter.py",
    "tier2/m-dataops-03-corrective-rag/steps/step1_test.py",
    "tier2/m-dataops-04-capstone/steps/step1_starter.py",
    "tier2/m-dataops-04-capstone/steps/step1_test.py",
    "tier2/m-devops-03-gitops/steps/step1_starter.py",
    "tier2/m-devops-03-gitops/steps/step1_test.py",
    "tier2/m-devops-04-capstone/steps/step1_starter.py",
    "tier2/m-devops-04-capstone/steps/step1_test.py",
    "tier2/m-devops-05-localstack-lab/steps/step1_starter.py",
    "tier2/m-devops-05-localstack-lab/steps/step1_test.py",
    "tier2/m-netops-05-gns3-lab/steps/step1_starter.py",
    "tier2/m-netops-05-gns3-lab/steps/step1_test.py",
    "tier2/m-secops-03-evidence-pipeline/steps/step1_starter.py",
    "tier2/m-secops-03-evidence-pipeline/steps/step1_test.py",
    "tier2/m-secops-04-secops-capstone/steps/step1_starter.py",
    "tier2/m-secops-04-secops-capstone/steps/step1_test.py",
    "tier2/m-swe-03-scaffold/steps/step1_starter.py",
    "tier2/m-swe-03-scaffold/steps/step1_test.py",
    "tier2/m-swe-04-capstone/steps/step1_starter.py",
    "tier2/m-swe-04-capstone/steps/step1_test.py",
}


# ---------------------------------------------------------------------------
# The reverse-direction test: every authored asset must be reachable
# ---------------------------------------------------------------------------

def test_every_authored_test_asset_is_attached_to_a_step(discovered):
    """The assertion whose absence let 124 files rot unnoticed."""
    on_disk = {
        p.relative_to(CONTENT_ROOT).as_posix()
        for p in CONTENT_ROOT.rglob("step*_test.py")
    }
    assert on_disk, "fixture guard: expected authored test assets on disk"

    attached = {
        st.get("test_code_path")
        for _, st in _all_steps(discovered)
        if st.get("test_code_path")
    }
    orphaned = sorted(on_disk - attached - _ASSETS_AWAITING_PROSE)
    assert not orphaned, (
        f"{len(orphaned)} authored test files are attached to no step, so the "
        f"exercise is unreachable: {orphaned[:10]}"
    )


def test_every_authored_starter_asset_is_attached_to_a_step(discovered):
    on_disk = {
        p.relative_to(CONTENT_ROOT).as_posix()
        for p in CONTENT_ROOT.rglob("step*_starter.py")
    }
    attached = {
        st.get("starter_code_path")
        for _, st in _all_steps(discovered)
        if st.get("starter_code_path")
    }
    orphaned = sorted(on_disk - attached - _ASSETS_AWAITING_PROSE)
    assert not orphaned, f"{len(orphaned)} authored starter files unreachable: {orphaned[:10]}"


def test_the_awaiting_prose_list_is_exact(discovered):
    """Pin the authoring backlog so it can shrink but never silently grow."""
    on_disk = {
        p.relative_to(CONTENT_ROOT).as_posix()
        for p in CONTENT_ROOT.rglob("step*_starter.py")
    } | {
        p.relative_to(CONTENT_ROOT).as_posix()
        for p in CONTENT_ROOT.rglob("step*_test.py")
    }
    attached = {
        st[k]
        for _, st in _all_steps(discovered)
        for k in ("test_code_path", "starter_code_path")
        if st.get(k)
    }
    actual = on_disk - attached
    unexpected = sorted(actual - _ASSETS_AWAITING_PROSE)
    stale = sorted(_ASSETS_AWAITING_PROSE - actual)
    assert not unexpected, f"new unreachable assets — author the lesson: {unexpected}"
    assert not stale, (
        f"these now attach; remove them from _ASSETS_AWAITING_PROSE: {stale}"
    )


# ---------------------------------------------------------------------------
# Promotion rule
# ---------------------------------------------------------------------------

def test_a_step_with_a_sibling_test_is_a_coding_step(discovered):
    graded = [
        (slug, st) for slug, st in _all_steps(discovered)
        if st.get("test_code_path")
    ]
    assert graded, "expected discovery to attach graded coding steps"
    for slug, st in graded:
        assert st["step_type"] == "coding", (
            f"{slug} step {st['step_num']} has a test but is {st['step_type']!r}"
        )


def test_no_coding_step_is_promoted_without_a_test(discovered):
    """aca-int-02 refuses to credit an untested coding step — never manufacture one."""
    for slug, st in _all_steps(discovered):
        if st["step_type"] == "coding":
            assert st.get("test_code_path"), (
                f"{slug} step {st['step_num']} is 'coding' with no stored test, so it "
                "can never be completed for credit"
            )


def test_attached_asset_paths_exist_on_disk(discovered):
    for slug, st in _all_steps(discovered):
        for key in ("test_code_path", "starter_code_path"):
            rel = st.get(key)
            if rel:
                assert (CONTENT_ROOT / rel).is_file(), f"{slug}: {key} -> missing {rel}"


def test_assets_pair_with_their_own_step_number(discovered):
    """step3_test.py must attach to step 3, never to step 1."""
    for slug, st in _all_steps(discovered):
        rel = st.get("test_code_path")
        if rel:
            assert Path(rel).name.startswith(f"step{st['step_num']}_"), (
                f"{slug}: step {st['step_num']} attached to {rel}"
            )


# ---------------------------------------------------------------------------
# Tier 1 — the specific regression that made the front door ungradeable
# ---------------------------------------------------------------------------

def test_tier1_onboarding_missions_have_gradeable_steps(discovered):
    """m01-m10 advertised CODING and had zero verifiable steps between them."""
    tier1 = [f"m{n:02d}-" for n in range(1, 11)]
    graded_missions = {
        slug for slug, st in _all_steps(discovered) if st.get("test_code_path")
    }
    missing = [
        prefix for prefix in tier1
        if not any(s.startswith(prefix) for s in graded_missions)
    ]
    assert not missing, f"Tier-1 missions still with no gradeable step: {missing}"


def test_m01_step1_is_wired_to_its_authored_exercise(discovered):
    steps = discovered.get("m01-llm-fundamentals") or []
    assert steps, "m01 must be discovered"
    step1 = next((s for s in steps if s["step_num"] == 1), None)
    assert step1 is not None
    assert step1["step_type"] == "coding"
    assert step1["test_code_path"].endswith("step1_test.py")
    assert step1["starter_code_path"].endswith("step1_starter.py")


# ---------------------------------------------------------------------------
# Markdown discovery must not regress (the fga-wire-05 guarantee)
# ---------------------------------------------------------------------------

def test_markdown_coverage_is_unchanged(discovered):
    on_disk = {
        p.relative_to(CONTENT_ROOT).as_posix() for p in CONTENT_ROOT.rglob("*.md")
    }
    found = {st["content_path"] for _, st in _all_steps(discovered)}
    # Every discovered step still points at a real markdown file.
    assert found <= on_disk
    assert len(found) > 100, "markdown discovery regressed"
