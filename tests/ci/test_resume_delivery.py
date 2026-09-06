# CUI // SP-CTI
"""kpr-watch-13 — a `pr_watcher.resume` row must not mean "a line was written".

THE DEFECT UNDER TEST. `_send_resume` appends one JSONL line to
`.tmp/kanban/messages/<task>.jsonl` and the audit row said `injected resume
context`. Measured 2026-09-06 on the live checkout and PG board: 851 undrained
`sender: pr_watcher` lines on disk against 849 lifetime `pr_watcher.resume`
rows — a drain DELETES the file, so a count on disk that meets or exceeds every
resume ever recorded proves none was drained.

These tests pin the three things that make the row honest, and the two things
that must never happen to it: the verdict is re-derived from the FILESYSTEM, an
absent receipt is `unmeasured` and NEVER `delivered`, and the probe runs BEFORE
the append so a message cannot corroborate itself.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib

import pytest

from tools.ci import resume_delivery as rd


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    """A private message queue, so a test never reads the live board's .tmp."""
    from tools.airgap import hook_compat

    qdir = tmp_path / "messages"
    qdir.mkdir()
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", qdir)
    return qdir


def _enqueue(qdir: pathlib.Path, task_id: str, sender: str = "pr_watcher") -> None:
    with open(qdir / f"{task_id}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(
            {"role": "user", "content": "fix it", "sender": sender,
             "ts": "2026-09-06T00:00:00+00:00"}) + "\n")


# ── the three verdicts ──────────────────────────────────────────────────────


def test_a_pending_message_is_proof_it_was_never_read(queue):
    """The message still being there is PRIMARY evidence and outranks all else."""
    _enqueue(queue, "t-1")
    v = rd.probe_prior_delivery("t-1", had_prior_injection=True)
    assert v.verdict == rd.UNDELIVERED
    assert v.pending == 1
    assert "still unread" in v.detail


def test_a_drain_receipt_is_what_proves_delivery(queue):
    """`delivered` requires positive evidence — a recorded drain, nothing less."""
    rd.record_drain("t-2", [{"sender": "pr_watcher"}, {"sender": "user"}])
    v = rd.probe_prior_delivery("t-2", had_prior_injection=True)
    assert v.verdict == rd.DELIVERED
    # Only the pr_watcher message counts toward a pr_watcher verdict.
    assert v.receipted == 1


def test_first_injection_has_nothing_prior_to_judge(queue):
    v = rd.probe_prior_delivery("t-3", had_prior_injection=False)
    assert v.verdict == rd.UNMEASURED
    assert "first injection" in v.detail


def test_an_empty_queue_with_no_receipt_is_never_delivered(queue):
    """THE ANTI-FABRICATION RULE.

    A `.tmp` sweep and a pre-receipt reader both leave an empty queue and no
    receipt. Reading that as `delivered` would commit this card's own defect —
    a reduction asserting more than its data supports.
    """
    v = rd.probe_prior_delivery("t-4", had_prior_injection=True)
    assert v.verdict == rd.UNMEASURED
    assert v.verdict != rd.DELIVERED
    assert "sweep" in v.detail


def test_an_unreadable_queue_is_unmeasured_not_clean(monkeypatch):
    monkeypatch.setattr(rd, "queue_dir", lambda: None)
    v = rd.probe_prior_delivery("t-5", had_prior_injection=True)
    assert v.verdict == rd.UNMEASURED


# ── the drain now records itself ────────────────────────────────────────────


def test_check_message_queue_records_a_drain_receipt(queue):
    """The reader is what closes the loop — a drain stops being traceless."""
    from tools.airgap import hook_compat

    _enqueue(queue, "t-6")
    _enqueue(queue, "t-6")
    drained = hook_compat.check_message_queue("t-6")
    assert len(drained) == 2
    assert not (queue / "t-6.jsonl").exists()

    receipts = rd.read_receipts("t-6")
    assert receipts and len(receipts) == 1
    assert receipts[0]["senders"]["pr_watcher"] == 2
    # And the verdict flips on that evidence alone.
    assert rd.probe_prior_delivery(
        "t-6", had_prior_injection=True).verdict == rd.DELIVERED


def test_a_receipt_failure_never_breaks_the_drain(queue, monkeypatch):
    """Best-effort by construction: a running agent must still get its message."""
    def boom(*_a, **_k):
        raise RuntimeError("receipt store down")

    monkeypatch.setattr(rd, "record_drain", boom)
    from tools.airgap import hook_compat

    _enqueue(queue, "t-7")
    assert len(hook_compat.check_message_queue("t-7")) == 1


# ── the retrospective the escalation quotes ─────────────────────────────────


def test_unaccounted_injections_are_never_folded_into_delivered(queue):
    """Pre-receipt residue is reported BESIDE `delivered`, never inside it."""
    _enqueue(queue, "t-8")
    s = rd.summarize_delivery("t-8", injections=5)
    assert s["delivered"] == 0
    assert s["undelivered"] == 1
    assert s["unaccounted"] == 4
    assert s["verdict"] == rd.UNDELIVERED


def test_escalation_note_says_none_were_read(queue):
    _enqueue(queue, "t-9")
    note = rd.escalation_note(rd.summarize_delivery("t-9", injections=5))
    assert "NONE of the 5" in note


def test_escalation_note_is_unmeasured_when_the_queue_cannot_be_read(monkeypatch):
    monkeypatch.setattr(rd, "queue_dir", lambda: None)
    note = rd.escalation_note(rd.summarize_delivery("t-10", injections=5))
    assert "unmeasured" in note


# ── the board-wide survey ───────────────────────────────────────────────────


def test_survey_proves_never_drained_from_the_inequality(queue):
    for i in range(3):
        _enqueue(queue, f"t-s{i}")
    s = rd.survey(get_connection=lambda: _FakeConn(2))
    assert s.pending_messages == 3
    assert s.recorded_resumes == 2
    assert s.never_drained() is True
    assert s.state == rd.UNDELIVERED


def test_survey_never_claims_drained_from_a_shortfall_alone(queue):
    """Fewer on disk than recorded is NOT proof anything was read.

    A worktree with its own empty `.tmp`, or a swept scratch dir, produces
    exactly that shortfall. `never_drained` is None there — never False.
    """
    _enqueue(queue, "t-s9")
    s = rd.survey(get_connection=lambda: _FakeConn(400))
    assert s.never_drained() is None
    assert s.state == rd.UNMEASURED


def test_survey_is_unmeasured_on_a_deployment_with_no_history(queue):
    s = rd.survey(get_connection=lambda: _FakeConn(0))
    assert s.state == rd.UNMEASURED
    assert s.never_drained() is None


def test_survey_reports_unmeasured_when_the_board_is_unreachable(queue):
    def boom():
        raise RuntimeError("no board")

    _enqueue(queue, "t-s10")
    s = rd.survey(get_connection=boom)
    assert s.recorded_resumes is None
    assert s.never_drained() is None
    assert s.state == rd.UNMEASURED


class _FakeConn:
    def __init__(self, n):
        self._n = n

    def execute(self, *_a, **_k):
        n = self._n
        return type("C", (), {"fetchone": staticmethod(lambda: {"n": n})})()

    def close(self):
        pass


# ── what this module must NOT do ────────────────────────────────────────────


def test_the_module_has_no_actuator():
    """No dispatch, no subprocess, no threshold write.

    The card forbids "fixing" this by raising `max_resume_cycles_per_task` or
    lowering `RESUME_COOLDOWN_SECONDS` — more undelivered messages is not more
    attempts. Read the AST rather than the behaviour: a behavioural test over
    today's call sites would still pass the day somebody adds one.
    """
    src = pathlib.Path(inspect.getfile(rd)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"subprocess", "shutil"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name.split(".")[0] for a in getattr(node, "names", [])]
            mod = (getattr(node, "module", None) or "").split(".")[0]
            assert not (set(names) & banned), f"forbidden import: {names}"
            assert mod not in banned, f"forbidden import: {mod}"

    # The thresholds may be DISCUSSED (the docstring explains why they are not
    # the repair) and must never be READ or WRITTEN. Identifiers and config-key
    # literals only — docstrings are excluded, so prose stays free.
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef))
        and n.body and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
        and isinstance(n.body[0].value.value, str)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id != "RESUME_COOLDOWN_SECONDS", "threshold referenced"
        if isinstance(node, ast.Attribute):
            assert node.attr != "RESUME_COOLDOWN_SECONDS", "threshold referenced"
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            assert node.value != "max_resume_cycles_per_task", (
                "the resume budget must not be read here — more undelivered "
                "messages is not more attempts"
            )
        # It READS the queue; only the reader drains it.
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"unlink", "rmtree", "remove"}, (
                f"this module must never delete anything: .{node.attr}()"
            )


def test_the_probe_runs_before_the_append():
    """A message must not corroborate itself.

    Probing after `queue_message` would count the line just written and report
    `undelivered` on the first injection of every task forever — a constant
    wearing the name of a measurement. Asserted as SOURCE ORDER, because the
    two calls both succeed either way and no behavioural test can see it.
    """
    from tools.ci import pr_watcher as pw

    src = inspect.getsource(pw.PRWatcher.poll_once)
    probe = src.index("_probe_prior_delivery")
    send = src.index("_send_resume", probe - 4000 if probe > 4000 else 0)
    assert probe < src.index("_send_resume", probe), (
        "the delivery probe must run BEFORE the resume is enqueued"
    )
    assert send is not None


def test_the_resume_row_no_longer_claims_delivery():
    """`injected resume context` was a sentence about a file write."""
    from tools.ci import pr_watcher as pw

    src = inspect.getsource(pw.PRWatcher.poll_once)
    assert 'reason="injected resume context"' not in src
    assert "delivery=delivery.verdict" in src


def test_the_escalation_states_how_many_were_delivered():
    from tools.ci import pr_watcher as pw

    src = inspect.getsource(pw.PRWatcher.poll_once)
    cap = src.index("resume cap reached")
    head = src.rindex("if cycle >= max_cycles", 0, cap)
    block = src[head:cap]
    assert "_summarize_resume_delivery" in block, (
        "the escalation must measure delivery before declaring N attempts spent"
    )
    assert "escalation_note" in src[head:head + 4000]
    assert "final_attempt_grace_seconds" in src[head:head + 4000], (
        "the final attempt's grace is a decision and must be recorded, "
        "not left as an accident of branch order"
    )


def test_the_watcher_action_carries_the_verdict():
    from tools.ci.pr_watcher import WatcherAction

    a = WatcherAction(task_id="t", pr_url="u", classification="c", action="resume")
    assert a.delivery == ""
    assert a.final_attempt_grace_seconds is None
    assert set(rd.VERDICTS) == {rd.DELIVERED, rd.UNDELIVERED, rd.UNMEASURED}
