"""aicur-fix-03: a step folder holding only starter/test Python never loads.

Discovery keys a step on its ``stepN_*.md`` frontmatter, so ``stepN_starter.py`` /
``stepN_test.py`` with no lesson beside them were skipped silently. The loader now
warns per such step, and this census pins the set BY NAME so it can only shrink.
"""
from __future__ import annotations

import logging

from apps.forge_academy import content_loader

# Shrink-only. Remove an entry when its lesson is authored (the stale check below
# fails until you do); never add one — write the stepN_*.md instead.
ALLOWED_CODE_ONLY_STEPS = frozenset({
    "tier1/m02-prompt-engineering/steps/step4",  # aicur-fun-04 authors it
    "tier2/m-dataops-03-corrective-rag/steps/step1",
    "tier2/m-dataops-04-capstone/steps/step1",
    "tier2/m-devops-03-gitops/steps/step1",
    "tier2/m-devops-04-capstone/steps/step1",
    "tier2/m-devops-05-localstack-lab/steps/step1",
    "tier2/m-netops-05-gns3-lab/steps/step1",
    "tier2/m-secops-03-evidence-pipeline/steps/step1",
    "tier2/m-secops-04-secops-capstone/steps/step1",
    "tier2/m-swe-03-scaffold/steps/step1",
    "tier2/m-swe-04-capstone/steps/step1",
})


def test_no_new_code_only_step():
    new = set(content_loader.code_only_steps()) - ALLOWED_CODE_ONLY_STEPS
    assert not new, (
        f"step folders with starter/test code but no lesson .md: {sorted(new)} — "
        "author stepN_<name>.md; do not extend the allowlist"
    )


def test_allowlist_has_no_stale_entry():
    stale = ALLOWED_CODE_ONLY_STEPS - set(content_loader.code_only_steps())
    assert not stale, (
        f"these steps now have a lesson .md — remove them from "
        f"ALLOWED_CODE_ONLY_STEPS so the census shrinks: {sorted(stale)}"
    )


def _make_step_dir(root, name):
    steps = root / "tier9" / name / "steps"
    steps.mkdir(parents=True)
    return steps


def test_code_only_step_is_detected_and_warned(tmp_path, monkeypatch, caplog):
    orphan = _make_step_dir(tmp_path, "m-x-01-orphan")
    (orphan / "step1_starter.py").write_text("", encoding="utf-8")
    (orphan / "step1_test.py").write_text("", encoding="utf-8")
    (orphan / "step10_starter.py").write_text("", encoding="utf-8")
    (orphan / "step10_intro.md").write_text("# Ten\n", encoding="utf-8")

    authored = _make_step_dir(tmp_path, "m-x-02-authored")
    (authored / "step1_lab.md").write_text("# Lab\n", encoding="utf-8")
    (authored / "step1_starter.py").write_text("", encoding="utf-8")
    (authored / "helper.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(content_loader, "CONTENT_ROOT", tmp_path)
    assert content_loader.code_only_steps() == ["tier9/m-x-01-orphan/steps/step1"]

    with caplog.at_level(logging.WARNING, logger=content_loader._log.name):
        content_loader.discover_missions()
    warned = [r.getMessage() for r in caplog.records if "no stepN_*.md" in r.getMessage()]
    assert len(warned) == 1
    assert "tier9/m-x-01-orphan/steps/step1" in warned[0]
