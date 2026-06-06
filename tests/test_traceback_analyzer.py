# CUI // SP-CTI
"""Unit tests for tools/workflow/traceback_analyzer.py.

Covers: standard CPython tracebacks, chained ("During handling of...")
tracebacks, pytest long-style frames + summary line, pytest "E   " exception
lines, in_repo / external-frame classification, deepest-in-repo suspect
selection, and the never-raise empty-result contract.
"""
from __future__ import annotations

from pathlib import Path

from tools.workflow.traceback_analyzer import (
    Traceback,
    parse_traceback,
    primary_suspect,
)

REPO = Path("C:/AI/ICDev")


# ---------------------------------------------------------------------------
# Standard CPython traceback
# ---------------------------------------------------------------------------

def test_standard_traceback_basic():
    text = (
        "Traceback (most recent call last):\n"
        '  File "/app/main.py", line 10, in <module>\n'
        "    run()\n"
        '  File "/app/worker.py", line 42, in run\n'
        '    raise ValueError("boom")\n'
        "ValueError: boom\n"
    )
    tb = parse_traceback(text, repo_root=Path("/app"))
    assert tb.exception_type == "ValueError"
    assert tb.exception_message == "boom"
    assert len(tb.frames) == 2
    assert tb.frames[0].function == "<module>"
    assert tb.frames[1].file == "/app/worker.py"
    assert tb.frames[1].line == 42
    assert tb.frames[1].code_line == 'raise ValueError("boom")'


def test_exception_without_message():
    text = (
        "Traceback (most recent call last):\n"
        '  File "/app/main.py", line 5, in <module>\n'
        "    loop()\n"
        "KeyboardInterrupt\n"
    )
    tb = parse_traceback(text, repo_root=Path("/app"))
    assert tb.exception_type == "KeyboardInterrupt"
    assert tb.exception_message == ""


def test_primary_suspect_deepest_in_repo(tmp_path):
    # External frame is deepest, but suspect must be the deepest in-repo one.
    text = (
        "Traceback (most recent call last):\n"
        f'  File "{tmp_path}/app/main.py", line 10, in <module>\n'
        "    run()\n"
        f'  File "{tmp_path}/app/worker.py", line 42, in run\n'
        "    json.loads(x)\n"
        '  File "/usr/lib/python3.11/json/__init__.py", line 346, in loads\n'
        "    return _default_decoder.decode(s)\n"
        "json.decoder.JSONDecodeError: Expecting value\n"
    )
    tb = parse_traceback(text, repo_root=tmp_path)
    assert tb.exception_type == "json.decoder.JSONDecodeError"
    # last frame is stdlib -> not the suspect
    assert tb.frames[-1].in_repo is False
    assert primary_suspect(tb) == f"{tmp_path}/app/worker.py:42"


# ---------------------------------------------------------------------------
# Chained tracebacks
# ---------------------------------------------------------------------------

def test_chained_traceback_reports_final_exception():
    text = (
        "Traceback (most recent call last):\n"
        '  File "/app/a.py", line 1, in f\n'
        "    g()\n"
        "ValueError: first\n"
        "\n"
        "During handling of the above exception, another exception occurred:\n"
        "\n"
        "Traceback (most recent call last):\n"
        '  File "/app/b.py", line 2, in h\n'
        "    raise RuntimeError('second')\n"
        "RuntimeError: second\n"
    )
    tb = parse_traceback(text, repo_root=Path("/app"))
    # final propagated exception wins
    assert tb.exception_type == "RuntimeError"
    assert tb.exception_message == "second"
    # frames from both segments are concatenated; deepest in-repo is b.py
    assert len(tb.frames) == 2
    assert primary_suspect(tb) == "/app/b.py:2"


# ---------------------------------------------------------------------------
# pytest formats
# ---------------------------------------------------------------------------

def test_pytest_long_style_frames_and_summary():
    text = (
        "    def test_add():\n"
        ">       assert add(1, 2) == 4\n"
        "tests/test_math.py:5: in test_add\n"
        "    assert add(1, 2) == 4\n"
        "tests/test_math.py:5: AssertionError\n"
    )
    tb = parse_traceback(text, repo_root=REPO)
    assert tb.exception_type == "AssertionError"
    assert any(f.file == "tests/test_math.py" and f.line == 5 for f in tb.frames)
    # relative pytest path -> in_repo
    assert tb.frames[-1].in_repo is True
    assert primary_suspect(tb) == "tests/test_math.py:5"


def test_pytest_e_prefixed_exception_line():
    text = (
        "tests/test_x.py:12: in test_thing\n"
        "    do_it()\n"
        "tests/test_x.py:30: in do_it\n"
        "    raise TypeError('nope')\n"
        "E   TypeError: nope\n"
    )
    tb = parse_traceback(text, repo_root=REPO)
    assert tb.exception_type == "TypeError"
    assert tb.exception_message == "nope"
    assert primary_suspect(tb) == "tests/test_x.py:30"


# ---------------------------------------------------------------------------
# in_repo classification
# ---------------------------------------------------------------------------

def test_site_packages_frame_not_in_repo():
    text = (
        "Traceback (most recent call last):\n"
        '  File "/x/.venv/lib/python3.11/site-packages/requests/api.py", line 9, in get\n'
        "    return request()\n"
        "requests.exceptions.ConnectionError: refused\n"
    )
    tb = parse_traceback(text, repo_root=Path("/x"))
    assert tb.frames[0].in_repo is False
    # no in-repo frame -> falls back to deepest frame of any kind
    assert primary_suspect(tb) == (
        "/x/.venv/lib/python3.11/site-packages/requests/api.py:9"
    )


def test_frozen_frame_not_in_repo():
    text = (
        "Traceback (most recent call last):\n"
        '  File "<frozen importlib._bootstrap>", line 1, in _call_with_frames\n'
        "ImportError: bad\n"
    )
    tb = parse_traceback(text, repo_root=REPO)
    assert tb.frames[0].in_repo is False


# ---------------------------------------------------------------------------
# Tolerance / empty-result contract
# ---------------------------------------------------------------------------

def test_no_traceback_returns_empty():
    tb = parse_traceback("just some logs\nnothing to see here\n")
    assert tb.matched is False
    assert tb.exception_type == ""
    assert tb.frames == []
    assert primary_suspect(tb) is None


def test_empty_and_none_inputs_never_raise():
    for bad in ("", None, 12345, [], {}):
        tb = parse_traceback(bad)  # type: ignore[arg-type]
        assert isinstance(tb, Traceback)
        assert tb.matched is False
    assert primary_suspect(Traceback()) is None
    assert primary_suspect(None) is None  # type: ignore[arg-type]


def test_to_dict_roundtrip():
    tb = parse_traceback(
        "Traceback (most recent call last):\n"
        '  File "/app/m.py", line 3, in f\n'
        "    boom()\n"
        "RuntimeError: x\n",
        repo_root=Path("/app"),
    )
    d = tb.to_dict()
    assert d["exception_type"] == "RuntimeError"
    assert isinstance(d["frames"], list)
    assert d["frames"][0]["file"] == "/app/m.py"
