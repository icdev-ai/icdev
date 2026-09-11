# CUI // SP-CTI
"""xrv-mem-02 — PostToolUse observation capture into the auto_capture buffer.

``auto_capture.capture`` was complete and had no hook caller; the buffer it
fills was flushed by a maintenance path nothing ever filled. These tests pin
the shape that makes the hook-side capture safe to ship:

* an Edit event yields ONE memory_buffer row, deterministically
* a ``<private>`` span never reaches the table (asserted by content), and a
  wholly private observation writes nothing
* the per-session cap holds, across the buffer's own auto-flush
* a malformed event captures nothing and the hook exits 0
* a commit is recorded from git's confirmation line, never from ``-m``
* pattern_detector's scored chains reach memory_entries ONCE (dedup)
* both hook paths and the synthesize reflex call the ONE seam

Every write goes to a SQLite fixture through ``db_path=``; the checkout's
board is never touched.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from tools.db.storage import get_connection
from tools.hooks import observation_capture as oc
from tools.memory.auto_capture import flush_buffer

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
HOOK_PATH = HOOKS_DIR / "post_tool_use.py"
PROMPT_HOOK_PATH = HOOKS_DIR / "user_prompt_submit.py"


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    """A fixture board: memory_entries as the flush needs it; the buffer creates itself."""
    path = tmp_path / "board.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE memory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            type TEXT DEFAULT 'event',
            importance INTEGER DEFAULT 5,
            content_hash TEXT,
            user_id TEXT, tenant_id TEXT,
            source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / "capture_state"


def _rows(db, table="memory_buffer"):
    conn = get_connection(db_path=str(db))
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]  # nosec B608
    except Exception:
        return []
    finally:
        conn.close()


def _capture(obs, sid, db, state_dir, **kw):
    return oc.capture_observation(obs, sid, db_path=str(db), state_dir=state_dir, **kw)


# ── observations are derived deterministically ────────────────────────


def test_edit_event_yields_one_buffer_row(db, state_dir):
    obs = oc.observe_tool("Edit", {"file_path": str(REPO_ROOT / "tools" / "foo.py"), "old_string": "a", "new_string": "b"}, project_root=REPO_ROOT)
    assert obs is not None and obs.kind == "edit" and obs.content == "edited tools/foo.py"
    out = _capture(obs, "sess-1", db, state_dir)
    assert out["status"] == "captured"
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["content"] == "edited tools/foo.py"
    assert rows[0]["session_id"] == "sess-1" and rows[0]["source"] == "hook"
    assert json.loads(rows[0]["metadata"])["kind"] == "edit"
    # the same edit again is a duplicate, not a second row
    assert _capture(obs, "sess-1", db, state_dir)["status"] == "duplicate"
    assert len(_rows(db)) == 1


def test_edit_carries_the_first_intent_line_only():
    obs = oc.observe_tool("Write", {"file_path": "docs/x.md", "content": "...", "description": "add the audit doc\nsecond line"})
    assert obs.content == "edited docs/x.md: add the audit doc"


def test_read_and_plain_bash_yield_nothing():
    assert oc.observe_tool("Read", {"file_path": "x.py"}) is None
    assert oc.observe_tool("Bash", {"command": "ls -la"}, {"stdout": "a\nb"}) is None
    assert oc.observe_tool("Bash", "not a dict") is None


def test_commit_is_recorded_from_gits_confirmation_never_from_dash_m():
    cmd = 'git commit -m "feat: the subject I typed"'
    # refused / failed: no confirmation line -> nothing, even though -m is there
    assert oc.observe_tool("Bash", {"command": cmd}, {"stdout": "", "stderr": "hook refused"}) is None
    out = {"stdout": "[kanban/xrv-mem-02 abc1234def] feat: the subject git wrote\n 2 files changed", "stderr": ""}
    obs = oc.observe_tool("Bash", {"command": cmd}, out)
    assert obs.kind == "commit"
    assert obs.content == "committed abc1234def on kanban/xrv-mem-02: feat: the subject git wrote"
    assert obs.metadata["sha"] == "abc1234def"


def test_pytest_summary_line_is_the_observation():
    out = {"stdout": "collected 3 items\n\ntests/x.py ..F\n\n=========== 1 failed, 2 passed in 0.42s ===========\n"}
    obs = oc.observe_tool("Bash", {"command": "pytest tests/x.py -q"}, out)
    assert obs.kind == "test_summary"
    assert obs.content == "pytest: 1 failed, 2 passed (pytest tests/x.py -q)"  # duration stripped: the outcome is the fact
    assert obs.metadata["summary"] == "1 failed, 2 passed in 0.42s"
    # a run whose output carries no summary (truncated, withheld) is not a result
    assert oc.observe_tool("Bash", {"command": "python -m pytest tests"}, {"stdout": "collecting..."}) is None


def test_decision_prompt_yields_its_first_sentence():
    obs = oc.observe_prompt("decision: keep the flush on the reflex path. Second sentence is context.")
    assert obs.kind == "decision" and obs.content == "decision: keep the flush on the reflex path."
    assert oc.observe_prompt("please decide something") is None
    assert oc.observe_prompt("decision:   ") is None


# ── <private> spans never reach the table ─────────────────────────────


def test_private_span_is_absent_from_the_buffer(db, state_dir):
    obs = oc.Observation("edit", "edited tools/foo.py: rotate <private>API key sk-live-ABC123</private> handling")
    out = _capture(obs, "sess-p", db, state_dir)
    assert out["status"] == "captured" and out["private_stripped"] is True
    rows = _rows(db)
    assert len(rows) == 1
    blob = json.dumps(rows[0])
    assert "sk-live-ABC123" not in blob and "private" not in rows[0]["content"].lower()
    assert rows[0]["content"] == "edited tools/foo.py: rotate handling"


def test_wholly_private_observation_is_skipped(db, state_dir):
    obs = oc.Observation("decision", "<PRIVATE>decision: the whole thing is private</PRIVATE>")
    out = _capture(obs, "sess-p2", db, state_dir)
    assert out["status"] == "private_only"
    assert _rows(db) == []


def test_unclosed_private_tag_drops_the_rest():
    clean, had = oc.strip_private("edited a.py: before <private>secret and everything after")
    assert had is True and clean == "edited a.py: before"


# ── the per-session cap ───────────────────────────────────────────────


def test_per_session_cap_holds_and_is_per_session(db, state_dir):
    for i in range(3):
        out = _capture(oc.Observation("edit", f"edited file{i}.py"), "sess-cap", db, state_dir, max_per_session=2)
        assert out["status"] == ("captured" if i < 2 else "over_budget")
    assert len(_rows(db)) == 2
    # another session is not spent by this one's budget
    assert _capture(oc.Observation("edit", "edited other.py"), "sess-other", db, state_dir, max_per_session=2)["status"] == "captured"
    assert oc.session_capture_count("sess-cap", state_dir) == 2


def test_cap_survives_the_buffers_own_flush(db, state_dir):
    """A buffer that empties itself cannot count a session; the state file does."""
    for i in range(2):
        _capture(oc.Observation("edit", f"edited f{i}.py"), "sess-f", db, state_dir, max_per_session=3)
    assert flush_buffer(db_path=str(db))["flushed"] == 2
    assert _rows(db) == []  # the buffer is empty again
    assert _capture(oc.Observation("edit", "edited f2.py"), "sess-f", db, state_dir, max_per_session=3)["status"] == "captured"
    assert _capture(oc.Observation("edit", "edited f3.py"), "sess-f", db, state_dir, max_per_session=3)["status"] == "over_budget"


def test_config_declares_the_cap():
    cfg = oc.load_config()
    assert cfg["max_per_session"] == 200


def test_kill_switch_writes_nothing(db, state_dir, monkeypatch):
    monkeypatch.setenv("ICDEV_MEMORY_CAPTURE", "0")
    assert _capture(oc.Observation("edit", "edited x.py"), "s", db, state_dir)["status"] == "disabled"
    assert _rows(db) == []


def test_missing_sqlite_board_is_never_created(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    ghost = tmp_path / "nowhere" / "icdev.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(ghost))
    out = oc.capture_observation(oc.Observation("edit", "edited x.py"), "s", state_dir=tmp_path / "st")
    assert out["status"] == "no_board"
    assert not ghost.exists()


# ── the hook itself ────────────────────────────────────────────────────


def _load_hook(path: Path, name: str):
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_hook(module, monkeypatch, raw_stdin: str):
    import send_event

    seen = []

    def fake_store_event(session_id, hook_type=None, tool_name=None, payload=None, classification="CUI"):
        seen.append({"session_id": session_id, "hook_type": hook_type, "tool_name": tool_name, "payload": payload})
        return 1

    monkeypatch.setattr(send_event, "store_event", fake_store_event)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw_stdin))
    if hasattr(module, "dispatch_extension_hook"):
        monkeypatch.setattr(module, "dispatch_extension_hook", lambda *a, **k: None)
    with pytest.raises(SystemExit) as exc:
        module.main()
    return exc.value.code, seen


def _wire_capture_to_fixture(monkeypatch, db, state_dir):
    real = oc.capture_tool_event
    monkeypatch.setattr(oc, "capture_tool_event", lambda *a, **k: real(*a, db_path=str(db), state_dir=state_dir, **k))
    real_prompt = oc.capture_prompt_event
    monkeypatch.setattr(oc, "capture_prompt_event", lambda *a, **k: real_prompt(*a, db_path=str(db), state_dir=state_dir, **k))


def test_hook_edit_event_lands_one_row_and_records_the_verdict(db, state_dir, monkeypatch):
    _wire_capture_to_fixture(monkeypatch, db, state_dir)
    hook = _load_hook(HOOK_PATH, "icdev_post_tool_use_capture_test")
    event = {"session_id": "hook-sess", "tool_name": "Edit", "tool_input": {"file_path": "tools/hooks/x.py", "old_string": "a", "new_string": "b"}, "tool_response": {"filePath": "tools/hooks/x.py"}}
    code, seen = _run_hook(hook, monkeypatch, json.dumps(event))
    assert code == 0
    rows = _rows(db)
    assert len(rows) == 1 and rows[0]["content"] == "edited tools/hooks/x.py" and rows[0]["session_id"] == "hook-sess"
    assert seen[0]["payload"]["memory_capture"] == {"kind": "edit", "status": "captured", "private_stripped": False}
    # the event row carries the verdict, never the content
    assert "tools/hooks/x.py" not in json.dumps(seen[0]["payload"]["memory_capture"])


def test_hook_malformed_event_captures_nothing_and_exits_zero(db, state_dir, monkeypatch):
    _wire_capture_to_fixture(monkeypatch, db, state_dir)
    hook = _load_hook(HOOK_PATH, "icdev_post_tool_use_capture_test2")
    assert _run_hook(hook, monkeypatch, "this is not json")[0] == 0
    code, seen = _run_hook(hook, monkeypatch, json.dumps({"session_id": "s", "tool_name": "Edit", "tool_input": "a string, not a dict"}))
    assert code == 0 and "memory_capture" not in seen[0]["payload"]
    code, seen = _run_hook(hook, monkeypatch, json.dumps({"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": "x"}}))
    assert code == 0 and "memory_capture" not in seen[0]["payload"]
    assert _rows(db) == []


def test_prompt_hook_captures_a_decision_turn(db, state_dir, monkeypatch):
    _wire_capture_to_fixture(monkeypatch, db, state_dir)
    hook = _load_hook(PROMPT_HOOK_PATH, "icdev_user_prompt_submit_capture_test")
    code, seen = _run_hook(hook, monkeypatch, json.dumps({"session_id": "p-sess", "prompt": "decision: ship <private>the token</private> capture behind a cap. Then more."}))
    assert code == 0
    rows = _rows(db)
    assert len(rows) == 1 and rows[0]["content"] == "decision: ship capture behind a cap."
    assert seen[0]["payload"]["memory_capture"]["private_stripped"] is True
    code, seen = _run_hook(hook, monkeypatch, json.dumps({"session_id": "p-sess", "prompt": "ordinary question?"}))
    assert code == 0 and "memory_capture" not in seen[0]["payload"] and len(_rows(db)) == 1


def test_hook_tool_literal_matches_the_library_sets():
    hook = _load_hook(HOOK_PATH, "icdev_post_tool_use_capture_test3")
    assert hook._CAPTURE_TOOLS == oc.EDIT_TOOLS | oc.SHELL_TOOLS


def test_both_hook_paths_and_the_reflex_call_the_one_seam():
    hook_src = HOOK_PATH.read_text(encoding="utf-8")
    assert "capture_memory_observation(tool_name, tool_input, tool_output, session_id)" in hook_src
    assert "from tools.hooks.observation_capture import capture_tool_event" in hook_src
    prompt_src = PROMPT_HOOK_PATH.read_text(encoding="utf-8")
    assert "from tools.hooks.observation_capture import capture_prompt_event" in prompt_src
    from tools.airgap import hook_compat

    assert "capture_tool_event" in inspect.getsource(hook_compat.run_post_tool_capture)
    assert "capture_prompt_event" in inspect.getsource(hook_compat.run_prompt_capture)
    synth_src = (REPO_ROOT / "tools" / "genesis" / "reflexes" / "synthesize.py").read_text(encoding="utf-8")
    assert "buffer_procedural_patterns(patterns" in synth_src
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    cmds = [h["command"] for entry in settings["hooks"]["PostToolUse"] for h in entry["hooks"]]
    assert any("post_tool_use.py" in c for c in cmds) and HOOK_PATH.exists()


def test_status_vocabulary_is_closed():
    src = (REPO_ROOT / "tools" / "hooks" / "observation_capture.py").read_text(encoding="utf-8")
    import re

    literal = set(re.findall(r'result\["status"\] = "([a-z_]+)"', src))
    assert literal <= set(oc.STATUSES)
    assert set(oc.STATUSES) == {"captured", "duplicate", "private_only", "over_budget", "disabled", "no_board", "error"}


# ── the second sink: scored chains -> memory_entries, once ────────────


PATTERNS = [
    {"pattern": ["Read", "Edit", "Bash"], "frequency": 5, "caller_diversity": 2, "composite_score": 15.85},
    {"pattern": ["Grep", "Read"], "frequency": 1, "caller_diversity": 1, "composite_score": 1.0},
]


def test_chain_above_threshold_reaches_memory_entries_exactly_once(db):
    first = oc.buffer_procedural_patterns(PATTERNS, db_path=str(db), min_frequency=3)
    assert first["status"] == "ok" and first["buffered"] == 1 and first["below_threshold"] == 1
    buffered = _rows(db)
    assert len(buffered) == 1 and buffered[0]["type"] == "procedural" and buffered[0]["source"] == "auto"
    assert buffered[0]["content"] == "Recurring tool chain (5x across 2 session(s)): Read -> Edit -> Bash"
    assert json.loads(buffered[0]["metadata"])["chain_hash"] == oc.chain_hash(["Read", "Edit", "Bash"])
    # a second run before any flush is an in-buffer duplicate
    assert oc.buffer_procedural_patterns(PATTERNS, db_path=str(db), min_frequency=3)["duplicates"] == 1
    # delivery is the EXISTING flush, not this module's
    assert flush_buffer(db_path=str(db))["flushed"] == 1
    entries = _rows(db, "memory_entries")
    assert len(entries) == 1 and entries[0]["type"] == "procedural"
    # a second run + flush after delivery: dedup against memory_entries, still one row
    oc.buffer_procedural_patterns(PATTERNS, db_path=str(db), min_frequency=3)
    flushed = flush_buffer(db_path=str(db))
    assert flushed["flushed"] == 0 and flushed["duplicates"] == 1
    assert len(_rows(db, "memory_entries")) == 1


def test_chain_hash_is_store_patterns_own():
    src = inspect.getsource(importlib.import_module("tools.genesis.pattern_detector").store_patterns)
    assert 'json.dumps(p["pattern"], sort_keys=True)' in src and "hexdigest()[:16]" in src
    assert len(oc.chain_hash(["A", "B"])) == 16


def test_detector_failure_is_unmeasurable_not_empty(db):
    def boom():
        raise RuntimeError("hook_events on fire")

    out = oc.buffer_procedural_patterns(None, db_path=str(db), min_frequency=3, detect=boom)
    assert out["status"] == "unmeasurable" and out["buffered"] == 0
    out = oc.buffer_procedural_patterns(None, db_path=str(db), min_frequency=3, detect=lambda: {"patterns": []})
    assert out["status"] == "empty"


def test_survey_rate_is_none_over_no_transcripts(tmp_path):
    report = oc.survey_transcripts(since_days=1.0, project_filter="nothing-matches", root=tmp_path)
    assert report["transcripts"] == 0 and report["rows_per_day"] is None
    assert report["days_measured"] == 0
