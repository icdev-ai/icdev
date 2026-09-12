# CUI // SP-CTI
"""kpr-watch-19 — nothing drained the queue on the executor that actually runs.

THE DEFECT UNDER TEST, in two halves that have to be fixed together.

**No consumer.** The only caller of `hook_compat.check_message_queue` is
`tools/genesis/reflexes/kanban.py`, inside the text-only LLMRouter executor
loop. Measured 2026-09-12: 4,059 of 4,070 tasks on the board (99.73%) are
dispatched `claude_cli`, 11 `ollama_local` — so the one drain site is
unreachable for the executor that runs essentially every task. Board-wide:
**911 undrained `pr_watcher` messages across 212 queue files, 0 receipts.**

**A per-checkout path shared by two processes with different checkouts.**
`MESSAGE_QUEUE_DIR` was `BASE_DIR/.tmp/kanban/messages` with `BASE_DIR` a
self-root (xit-decl-03), so a worker resolving it from its worktree addressed a
directory the watcher never writes to. Measured the same day on task
`aca-hyg-06-d4-d3`: the main checkout said `undelivered — 5 pr_watcher
message(s) still unread`, and the SAME command in a worktree said `unmeasured —
queue empty`. A worker diagnosing its own undelivered resume was told there was
nothing pending. This is the defect `tools/kanban/build_mode.py::_main_checkout`
already fixed for the Manual Build flag, in the same shape.

Fixing only the consumer still misses: the hook runs inside the worktree.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from tools.airgap import hook_compat
from tools.ci import resume_delivery as rd
from tools.hooks import resume_inbox


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
POST_TOOL_USE = REPO_ROOT / ".claude" / "hooks" / "post_tool_use.py"


# -- fixtures ---------------------------------------------------------------


def _fake_checkout(tmp_path: pathlib.Path):
    """A main checkout with a real `.git` dir and one linked worktree.

    The worktree's `.git` is a FILE reading `gitdir: <main>/.git/worktrees/<n>`,
    which is exactly how git spells a linked worktree on disk and what the
    resolver reads.
    """
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "wt-1").mkdir(parents=True)
    wt = tmp_path / "wt-1"
    wt.mkdir()
    (wt / ".git").write_text(
        "gitdir: {}\n".format((main / ".git" / "worktrees" / "wt-1").as_posix()),
        encoding="utf-8",
    )
    return main, wt


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    """A private queue, so a test never reads or drains the live board's .tmp."""
    qdir = tmp_path / "messages"
    qdir.mkdir()
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", qdir)
    return qdir


def _enqueue(qdir, task_id, content, sender="pr_watcher"):
    with open(qdir / (task_id + ".jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "role": "user", "content": content, "sender": sender,
            "ts": "2026-09-12T00:00:00+00:00",
        }) + "\n")


def _load_hook():
    spec = importlib.util.spec_from_file_location("_kpr19_pth", POST_TOOL_USE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- half one: the queue is ONE directory, whoever asks ---------------------


def test_a_worktree_resolves_the_main_checkouts_queue_not_its_own(tmp_path):
    """The whole point: the watcher and a worktree-resident worker agree.

    `message_queue_dir` answers for an ANCHOR rather than reading a module
    constant, because that is what makes the worktree case testable at all.
    """
    main, wt = _fake_checkout(tmp_path)
    assert hook_compat.message_queue_dir(wt) == main / ".tmp" / "kanban" / "messages"
    assert hook_compat.message_queue_dir(wt) == hook_compat.message_queue_dir(main)


def test_a_plain_checkout_still_answers_for_itself(tmp_path):
    """No `.git` file to read: the anchor IS the checkout. Never raises."""
    plain = tmp_path / "plain"
    (plain / ".git").mkdir(parents=True)
    assert hook_compat.message_queue_dir(plain) == plain / ".tmp" / "kanban" / "messages"


def test_an_operator_can_relocate_the_queue(tmp_path, monkeypatch):
    """One env var, honoured by every reader, because they share one resolver."""
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("ICDEV_MESSAGE_QUEUE_DIR", str(elsewhere))
    assert hook_compat.message_queue_dir(tmp_path / "anything") == elsewhere


def test_the_worktree_and_the_main_checkout_report_the_same_verdict(
    tmp_path, monkeypatch,
):
    """AC2, as a test: today the worktree says `unmeasured — queue empty`.

    The verdict is read through `resume_delivery`, which imports the queue
    location from `hook_compat` and never respells it — so pinning the resolver
    pins the verdict.
    """
    main, wt = _fake_checkout(tmp_path)
    qdir = hook_compat.message_queue_dir(main)
    qdir.mkdir(parents=True)
    _enqueue(qdir, "t-same", "rebase your branch")

    verdicts = []
    for anchor in (main, wt):
        monkeypatch.setattr(
            hook_compat, "MESSAGE_QUEUE_DIR", hook_compat.message_queue_dir(anchor))
        verdicts.append(rd.probe_prior_delivery("t-same", had_prior_injection=True))

    assert verdicts[0].verdict == verdicts[1].verdict == rd.UNDELIVERED
    assert verdicts[0].pending == verdicts[1].pending == 1


# -- half two: something on the claude_cli path actually READS it -----------


def test_deliver_drains_the_resume_and_hands_back_its_words(queue):
    """A drain that does not return the CONTENT is this card's own defect."""
    _enqueue(queue, "t-read", "your PR #1 conflicts on args/ci_test_files/core.txt")
    out = resume_inbox.deliver("t-read", source="post_tool_use")

    assert out.delivered == 1
    assert "your PR #1 conflicts on args/ci_test_files/core.txt" in out.text
    assert "pr_watcher" in out.text
    # Drained means drained: the file is gone, so it is not re-delivered.
    assert not (queue / "t-read.jsonl").exists()


def test_delivery_is_what_makes_resume_delivery_say_delivered(queue):
    """AC1 end to end: undelivered -> deliver() -> delivered, with a receipt."""
    _enqueue(queue, "t-ac1", "rebase onto origin/main")
    before = rd.probe_prior_delivery("t-ac1", had_prior_injection=True)
    assert before.verdict == rd.UNDELIVERED

    resume_inbox.deliver("t-ac1", source="post_tool_use")

    after = rd.probe_prior_delivery("t-ac1", had_prior_injection=True)
    assert after.verdict == rd.DELIVERED
    assert after.receipted == 1
    assert rd.read_receipts("t-ac1")


def test_a_message_from_anyone_else_is_delivered_too(queue):
    """The queue is the mid-run channel (OPT-62), not a pr_watcher private line."""
    _enqueue(queue, "t-user", "stop and ask me first", sender="user")
    out = resume_inbox.deliver("t-user", source="session_start")
    assert out.delivered == 1
    assert "stop and ask me first" in out.text


def test_an_empty_queue_costs_nothing_and_says_nothing(queue):
    """The overwhelmingly common case: no file, no text, no receipt."""
    out = resume_inbox.deliver("t-none", source="post_tool_use")
    assert out.delivered == 0
    assert out.text == ""
    assert rd.read_receipts("t-none") == []


def test_no_task_id_is_a_no_op(queue):
    """A session nobody dispatched has no inbox, and must not guess one."""
    assert resume_inbox.deliver("", source="post_tool_use").delivered == 0
    assert resume_inbox.deliver(None, source="post_tool_use").delivered == 0


def test_delivery_never_raises_and_never_swallows_the_message(queue, monkeypatch):
    """A broken inbox must not break the tool call it rides on.

    But a FAILED drain must report 0 delivered — never a receipt for words
    nobody got, which would be kpr-watch-13's defect wearing this card's name.
    """
    def boom(_task_id):
        raise OSError("queue on fire")

    monkeypatch.setattr(hook_compat, "check_message_queue", boom)
    out = resume_inbox.deliver("t-boom", source="post_tool_use")
    assert out.delivered == 0
    assert out.text == ""
    assert out.error


# -- the two spellings of the queue path must agree -------------------------


def test_the_hooks_cheap_spelling_agrees_with_hook_compat(tmp_path):
    """`post_tool_use.py` re-spells the path with stdlib only, on purpose.

    It fires on EVERY tool call and `import tools.*` costs ~137ms (measured
    2026-09-12), so the hook may not import the authority to find out whether
    there is anything to do. The cost of that is a second spelling, and the
    mitigation is this test — the same bargain the `_CAPTURE_TOOLS` literal in
    that file already strikes.
    """
    hook = _load_hook()
    main, wt = _fake_checkout(tmp_path)
    for anchor in (main, wt):
        assert hook._queue_file("t-x", anchor=anchor) == (
            hook_compat.message_queue_dir(anchor) / "t-x.jsonl")


def test_the_hook_asks_the_inbox_for_the_running_task_only(queue, monkeypatch):
    """The dispatch tag is the identity — never the cwd, never a guess."""
    hook = _load_hook()
    monkeypatch.setenv("ICDEV_DISPATCH_TASK_ID", "t-hook")
    monkeypatch.setattr(hook, "_queue_dir_for_cwd", lambda: queue)
    _enqueue(queue, "t-hook", "the PR is red on Lint")
    _enqueue(queue, "t-other", "not yours")

    text = hook.drain_resume_inbox()
    assert "the PR is red on Lint" in text
    assert "not yours" not in text
    assert (queue / "t-other.jsonl").exists()


def test_the_hook_is_silent_when_there_is_nothing_to_deliver(queue, monkeypatch):
    hook = _load_hook()
    monkeypatch.setenv("ICDEV_DISPATCH_TASK_ID", "t-quiet")
    monkeypatch.setattr(hook, "_queue_dir_for_cwd", lambda: queue)
    assert hook.drain_resume_inbox() == ""


# -- the window that pays for pr_watcher traffic: the NEXT session ----------


def _load_session_start():
    path = REPO_ROOT / ".claude" / "hooks" / "session_start.py"
    spec = importlib.util.spec_from_file_location("_kpr19_ss", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_session_start_prints_the_inbox_as_context(queue, monkeypatch, capsys):
    """Every pr_watcher.resume is enqueued POST-run, so this is the real window.

    The watcher only resumes a task whose PR is already open: for
    qa-fail-5cacee65f1d03c8c the PR appeared at 22:45:33Z and the resume landed
    19 minutes later, by which time the session was gone. A mid-run drain alone
    would still deliver 0% of that traffic.
    """
    ss = _load_session_start()
    monkeypatch.setenv("ICDEV_DISPATCH_TASK_ID", "t-next")
    monkeypatch.delenv("ICDEV_RESUME_INBOX", raising=False)
    _enqueue(queue, "t-next", "CI is red on Security Scan")

    ss.deliver_resume_inbox()
    assert "CI is red on Security Scan" in capsys.readouterr().out


def test_the_inbox_survives_the_context_blocks_kill_switch(queue, monkeypatch, capsys):
    """`ICDEV_SESSION_START_HOOK=0` quietens the memory index, not a resume.

    Losing a resume because someone turned down a context index is exactly the
    silent-miss class this card exists to end, so the inbox has its own switch.
    """
    ss = _load_session_start()
    monkeypatch.setenv("ICDEV_DISPATCH_TASK_ID", "t-sw")
    monkeypatch.setenv("ICDEV_SESSION_START_HOOK", "0")
    monkeypatch.delenv("ICDEV_RESUME_INBOX", raising=False)
    _enqueue(queue, "t-sw", "rebase me")

    ss.deliver_resume_inbox()
    assert "rebase me" in capsys.readouterr().out


def test_the_inbox_has_its_own_kill_switch(queue, monkeypatch, capsys):
    ss = _load_session_start()
    monkeypatch.setenv("ICDEV_DISPATCH_TASK_ID", "t-off")
    monkeypatch.setenv("ICDEV_RESUME_INBOX", "0")
    _enqueue(queue, "t-off", "you should not see this")

    ss.deliver_resume_inbox()
    assert capsys.readouterr().out == ""
    # Stood down means NOT DRAINED -- a switch that ate the message would be
    # worse than the defect, because the queue would then read `delivered`.
    assert (queue / "t-off.jsonl").exists()


def test_the_inbox_defaults_to_the_dispatched_task(queue, monkeypatch):
    """`deliver()` with no id reads the dispatch tag -- the identity, not cwd."""
    monkeypatch.setenv("ICDEV_DISPATCH_TASK_ID", "t-default")
    _enqueue(queue, "t-default", "from the tag")
    out = resume_inbox.deliver(source="session_start")
    assert out.task_id == "t-default"
    assert "from the tag" in out.text
