# CUI // SP-CTI
"""A timed-out scan must show evidence, not just a long runtime.

Scan-only tasks are read-only: no git commits, and Claude CLI's
``--output-format text`` writes stdout only at exit, so a killed run yields
nothing. Some allowance is needed or a scan that really finished is retried
forever. The allowance used to be ``elapsed > task_budget * 0.9``.

That test could never fail. The block containing it is reached ONLY from
``if elapsed > task_budget``, so ``elapsed > task_budget * 0.9`` is a tautology
and the effective rule was "any ``task_type='test'`` whose description mentions
pytest/coherence/companion is DONE when it times out" — no evidence at all.

It fired on hgx-vv-01 (2026-08-09), HGX's end-to-end verification task: killed
at 3641s of a 3600s budget, marked done, zero output, nothing on a branch, and
the card read 38/38 on a proof that did not exist.

The replacement is the scan's own result artifact in ``.tmp/`` — written by the
command itself, and surviving the kill that destroys stdout.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

kanban = pytest.importorskip("tools.genesis.reflexes.kanban")


@pytest.fixture
def tmp_base(tmp_path, monkeypatch):
    """Point BASE_DIR at a throwaway tree with an empty .tmp/."""
    (tmp_path / ".tmp").mkdir()
    monkeypatch.setattr(kanban, "BASE_DIR", tmp_path)
    return tmp_path


def _write_artifact(base: Path, name: str) -> Path:
    p = base / ".tmp" / name
    with p.open("w", encoding="utf-8", newline="") as fh:
        fh.write('{"status": "pass"}\n')
    return p


# --- the tautology itself ----------------------------------------------------


def test_the_old_budget_test_could_never_fail():
    """Pin the reasoning, so nobody reintroduces `elapsed > budget * 0.9`.

    The acceptance block is guarded by `elapsed > task_budget`. Any further
    comparison against a FRACTION of the budget is implied by that guard and
    therefore decides nothing.
    """
    budget = 3600
    for elapsed in (budget + 1, 3641, budget * 2, budget * 10):
        assert elapsed > budget, "precondition: block only runs past the budget"
        assert elapsed > budget * 0.9, (
            "the old condition is implied by the guard — it can never reject, "
            "which is why it accepted hgx-vv-01"
        )


# --- the evidence helper -----------------------------------------------------


def test_no_artifact_means_no_evidence(tmp_base):
    assert kanban._scan_result_artifact("hgx-vv-01") is None


def test_artifact_matching_the_task_id_is_found(tmp_base):
    _write_artifact(tmp_base, "codelens-hgx-vv-01-2026.json")
    found = kanban._scan_result_artifact("hgx-vv-01")
    assert found is not None
    assert found.name == "codelens-hgx-vv-01-2026.json"


@pytest.mark.parametrize(
    "task_id,artifact",
    [
        ("efa-E-gate-1-codelens", "codelens-efa-E-gate-1-2026.json"),
        ("diag-970d-coherence", "codelens-diag-970d-x.json"),
        ("some-task-e2e", "codelens-some-task-y.json"),
        ("thing-scan", "codelens-thing-z.json"),
    ],
)
def test_suffix_stripped_ids_still_match(tmp_base, task_id, artifact):
    """The id-suffix strip is preserved from the original inline lookup."""
    _write_artifact(tmp_base, artifact)
    assert kanban._scan_result_artifact(task_id) is not None


def test_another_tasks_artifact_does_not_count(tmp_base):
    """Evidence must belong to THIS task — the hgx-vv-01 failure shape."""
    _write_artifact(tmp_base, "codelens-some-other-task-2026.json")
    assert kanban._scan_result_artifact("hgx-vv-01") is None


def test_missing_tmp_dir_is_absence_of_proof_not_proof(tmp_path, monkeypatch):
    """No .tmp/ at all must read as 'no evidence', never as an error or a pass."""
    monkeypatch.setattr(kanban, "BASE_DIR", tmp_path / "nonexistent")
    assert kanban._scan_result_artifact("hgx-vv-01") is None


# --- the acceptance decision the scheduler makes -----------------------------
#
# The timeout branch is deep inside the scheduler loop, so these assert the
# decision it now computes: artifact present -> accept, absent -> do not.


def _would_accept(task_id: str, task_type: str, description: str) -> bool:
    """Mirror of the acceptance condition at the timeout site."""
    kw = ["pytest", "codelens", "coherence", "companion", "report pass/fail", "behave"]
    if not (task_type == "test" and any(k in description.lower() for k in kw)):
        return False
    return kanban._scan_result_artifact(task_id) is not None


def test_hgx_vv_01_shape_is_rejected(tmp_base):
    """Killed past budget, description mentions pytest, nothing on disk."""
    assert _would_accept(
        "hgx-vv-01", "test", "run pytest and coherence_checker for the proof"
    ) is False, "a timed-out scan with no artifact must not be marked done"


def test_genuine_scan_that_wrote_its_report_is_still_accepted(tmp_base):
    """The legitimate case the allowance exists for must keep working."""
    _write_artifact(tmp_base, "codelens-efa-gate-1-2026.json")
    assert _would_accept(
        "efa-gate-1", "test", "run codelens.py and report pass/fail"
    ) is True


def test_non_scan_task_is_never_accepted_this_way(tmp_base):
    """An artifact must not launder a task that is not a scan at all."""
    _write_artifact(tmp_base, "codelens-build-thing-2026.json")
    assert _would_accept("build-thing", "feature", "implement the widget") is False
