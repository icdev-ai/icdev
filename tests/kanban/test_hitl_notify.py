# CUI // SP-CTI
"""The HITL notifier must reach a human who is not looking at the dashboard.

Everything recoverable is retried automatically. This is the residue — a task
whose rebase and resume budgets are both spent — so the signal has to survive a
backgrounded terminal, a headless box with no notification daemon, and a Windows
console that cannot encode a box-drawing character.
"""
from __future__ import annotations

import io

from tools.kanban import hitl_notify as hn


class _Conn:
    def __init__(self, rows, raises=False):
        self._rows = rows
        self.raises = raises

    def execute(self, sql, params=None):
        if self.raises:
            raise RuntimeError("db down")
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        pass


def _row(task):
    return {"id": 1, "source": f"pr_watcher:hitl:{task}", "title": f"{task} needs a human",
            "description": "resume cap reached (5/5).", "created_at": "2026-08-09"}


class _Runner:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        return type("P", (), {"returncode": self.returncode, "stdout": "", "stderr": ""})()


# ── the queue ───────────────────────────────────────────────────────────────
def test_only_hitl_sources_are_returned():
    conn = _Conn([_row("sbx-gov-02")])
    items = hn.pending(get_conn=lambda: conn)
    assert [i["task_id"] for i in items] == ["sbx-gov-02"]


def test_a_db_failure_is_silence_not_a_crash():
    """The notifier runs inside other people's jobs; it must never be the thing
    that fails them."""
    assert hn.pending(get_conn=lambda: _Conn([], raises=True)) == []


# ── terminal ────────────────────────────────────────────────────────────────
def test_nothing_pending_prints_nothing():
    assert hn.render_terminal([]) == ""


def test_the_bell_is_present_because_a_silent_print_is_invisible():
    out = hn.render_terminal([{"task_id": "t", "description": "d"}])
    assert out.startswith("\a")
    assert hn.render_terminal([{"task_id": "t", "description": "d"}], bell=False)[0] != "\a"


def test_the_banner_is_ascii_only():
    """A Windows console defaults to cp1252, where a box-drawing character raises
    UnicodeEncodeError — which would make the NOTIFIER the thing that breaks."""
    out = hn.render_terminal([{"task_id": "sbx-gov-02", "description": "x"}], bell=False)
    assert all(ord(c) < 128 for c in out), [c for c in out if ord(c) > 127]


# ── OS toast ────────────────────────────────────────────────────────────────
def test_the_toast_command_is_argv_and_never_a_shell():
    argv = hn._toast_argv("title", "body")
    assert isinstance(argv, list) and argv
    runner = _Runner()
    hn.desktop_toast("t", "b", runner=runner)
    _, kw = runner.calls[0]
    assert kw.get("shell") is False, "a shell here would be a command-injection seam"


def test_toast_text_travels_by_env_not_string_interpolation():
    """A task id containing a quote must not be able to break the command."""
    runner = _Runner()
    hn.desktop_toast("ICDEV", "sbx-'; rm -rf /", runner=runner)
    _, kw = runner.calls[0]
    assert kw["env"]["ICDEV_TOAST_BODY"] == "sbx-'; rm -rf /"


def test_a_platform_with_no_notifier_is_not_an_error():
    """Headless CI, air-gapped images and locked-down PowerShell are all normal."""
    def boom(*a, **k):
        raise OSError("no notify-send")
    assert hn.desktop_toast("t", "b", runner=boom) is False


# ── end to end ──────────────────────────────────────────────────────────────
def test_notify_returns_the_count_so_callers_can_use_it_as_an_exit_code():
    conn = _Conn([_row("a"), _row("b")])
    buf = io.StringIO()
    n = hn.notify(get_conn=lambda: conn, stream=buf, toast=False)
    assert n == 2
    assert "NEED A HUMAN" in buf.getvalue()


def test_notify_is_a_noop_when_the_queue_is_empty():
    buf = io.StringIO()
    assert hn.notify(get_conn=lambda: _Conn([]), stream=buf, toast=False) == 0
    assert buf.getvalue() == ""
