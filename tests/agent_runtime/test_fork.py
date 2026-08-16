# CUI // SP-CTI
"""Forking a session at a seq — hcx-evt-05.

The card's claim is that ``agent_session_events`` makes a fork possible for the
first time, so these tests are about the two halves that claim rests on:

  1. **The projection.** A prefix of the log becomes a message list a provider
     will accept. The order matters and is NOT the log's order: the real loop
     fires ``on_turn`` after the post-tool hooks, so a ``tool_result`` routinely
     precedes the ``assistant_message`` that announced its call. Both orders must
     project to the same, legal, message list.
  2. **The refusals.** A boundary that lands inside an open turn is refused
     rather than rounded — the rule borrowed from DSH — and so is one that names
     no event, one whose prefix holds an unanswered tool call, and one whose
     payloads are withheld by the retention policy. Every refusal names the
     legal boundaries either side, because a "no" nobody can act on gets worked
     around.

Written against the REAL table, from the migration's own DDL, for the same
reason ``test_event_recorder.py`` is: a column that exists in one and not the
other must fail here rather than inside somebody's ``except``.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._sql_compat import translating
from tools.agent_runtime.event_log import MIGRATION, append, read_session
from tools.agent_runtime.fork import (
    FORK_EVENT_TYPE,
    REASON_BOUNDARY_NOT_IN_LOG,
    REASON_OPEN_TURN,
    REASON_PAYLOAD_WITHHELD,
    REASON_UNANSWERED_TOOL_CALL,
    ForkRefused,
    describe,
    fork_session,
    legal_boundaries,
    plan_fork,
    project_messages,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

PARENT = "ctx-fork-parent"


# ---------------------------------------------------------------------------
# The real table, from the migration itself
# ---------------------------------------------------------------------------
def _events_ddl() -> str:
    return (
        REPO_ROOT / "tools" / "db" / "migrations" / MIGRATION / "up.sql"
    ).read_text(encoding="utf-8")


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test (``%s`` → ``?``, unclosable).

    Never a bare ``sqlite3.connect``: this repo authors SQL for PostgreSQL, and a
    raw handle makes every ``%s`` statement raise ``near "%": syntax error``
    inside whatever ``except`` is nearest — a test asserting against a no-op it
    caused itself.
    """
    return translating(raw, unclosable=True)


@pytest.fixture
def event_db(monkeypatch, tmp_path):
    raw = sqlite3.connect(str(tmp_path / "events.db"))
    raw.executescript(_events_ddl())
    conn = _translating_conn(raw)
    storage = sys.modules["tools.db.storage"]
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    monkeypatch.delenv("ICDEV_AGENT_EVENT_PAYLOAD_RETENTION", raising=False)
    yield raw
    raw.close()


class _FakeChatManager:
    """Records what a fork writes, without a chat schema."""

    def __init__(self) -> None:
        self._n = 0
        self.contexts: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}

    def create_context(self, *, title="", config=None, **_kw) -> str:
        self._n += 1
        cid = f"ctx-fork-{self._n}"
        self.contexts[cid] = {"title": title, "config": dict(config or {})}
        self.messages[cid] = []
        return cid

    def add_message(self, context_id, *, role, content, metadata=None, **_kw) -> int:
        self.messages.setdefault(context_id, []).append(
            {"role": role, "content": content, "metadata": metadata or {}}
        )
        return len(self.messages[context_id])

    def update_config(self, context_id, updates) -> None:
        self.contexts.setdefault(context_id, {"config": {}})["config"].update(updates)


@pytest.fixture
def manager() -> _FakeChatManager:
    return _FakeChatManager()


@pytest.fixture
def seed_store():
    """A stand-in for ``agent_loop_sessions``: save writes, load reads back."""
    store: dict[str, list[dict[str, Any]]] = {}

    def save(result, **_kw) -> bool:
        store[result.session_id] = list(result.messages)
        return True

    def load(session_id: str) -> list[dict[str, Any]]:
        return list(store.get(session_id, []))

    return store, save, load


# ---------------------------------------------------------------------------
# Session builders
# ---------------------------------------------------------------------------
def _tool_turn(session: str = PARENT, *, user: str = "find the bug") -> None:
    """One tool-using turn, in the order the REAL loop records it.

    ``on_turn`` fires after the post-tool hooks (agent_loop.py:1911), so the
    assistant message announcing the call lands AFTER the result answering it.
    """
    append(session, "turn_start", {"user_input": user})
    append(session, "tool_call", {"name": "read_file", "input": {"path": "a.py"}})
    append(
        session,
        "tool_result",
        {"name": "read_file", "input": {"path": "a.py"}, "result": "contents",
         "is_error": False},
    )
    append(
        session,
        "assistant_message",
        {
            "iteration": 0,
            "content": "let me look",
            "tool_calls": [
                {"id": "t1", "name": "read_file", "input": {"path": "a.py"}}
            ],
        },
    )
    append(session, "assistant_message", {"iteration": 1, "content": "here it is"})
    append(session, "turn_end", {"turns": 2})


def _plain_turn(session: str = PARENT, *, user: str, answer: str) -> None:
    append(session, "turn_start", {"user_input": user})
    append(session, "assistant_message", {"iteration": 0, "content": answer})
    append(session, "turn_end", {"turns": 1})


# ---------------------------------------------------------------------------
# 1. The projection
# ---------------------------------------------------------------------------
class TestProjection:
    def test_a_tool_turn_projects_to_a_legal_message_list(self, event_db):
        _tool_turn()
        messages = project_messages(read_session(PARENT))

        assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
        assert messages[0]["content"] == "find the bug"
        # The assistant's tool_use comes BEFORE the tool_result answering it,
        # which is the opposite of the order the events were recorded in.
        assert messages[1]["content"][0] == {"type": "text", "text": "let me look"}
        assert messages[1]["content"][1]["type"] == "tool_use"
        assert messages[1]["content"][1]["id"] == "t1"
        block = messages[2]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "t1"
        assert block["name"] == "read_file"
        assert block["content"] == [{"type": "text", "text": "contents"}]
        assert "is_error" not in block

    def test_the_other_recorded_order_projects_identically(self, event_db):
        """A caller driving the recorder assistant-first must project the same.

        Both orders occur — the loop records results first, a hand-driven
        recorder (and every test that does so) records the assistant first. A
        projection that only handled one of them would be right in tests and
        wrong in production, or the reverse.
        """
        other = "ctx-fork-other-order"
        append(other, "turn_start", {"user_input": "find the bug"})
        append(
            other,
            "assistant_message",
            {"iteration": 0, "content": "let me look",
             "tool_calls": [{"id": "t1", "name": "read_file", "input": {"path": "a.py"}}]},
        )
        append(other, "tool_call", {"name": "read_file", "input": {"path": "a.py"}})
        append(
            other,
            "tool_result",
            {"name": "read_file", "input": {"path": "a.py"}, "result": "contents",
             "is_error": False},
        )
        append(other, "assistant_message", {"iteration": 1, "content": "here it is"})
        append(other, "turn_end", {"turns": 2})

        _tool_turn()
        assert project_messages(read_session(other)) == project_messages(
            read_session(PARENT)
        )

    def test_an_error_result_keeps_its_flag(self, event_db):
        append(PARENT, "turn_start", {"user_input": "delete everything"})
        append(
            PARENT,
            "assistant_message",
            {"iteration": 0, "content": "",
             "tool_calls": [{"id": "t9", "name": "run", "input": {"cmd": "rm -rf /"}}]},
        )
        append(
            PARENT,
            "tool_result",
            {"name": "run", "input": {"cmd": "rm -rf /"}, "result": "BLOCKED",
             "is_error": True},
        )
        append(PARENT, "turn_end", {})

        messages = project_messages(read_session(PARENT))
        assert messages[-1]["content"][0]["is_error"] is True

    def test_postures_and_tool_calls_are_not_projected(self, event_db):
        """Only the three model-visible types become messages.

        ``permission_posture`` is an operator's act and ``tool_call`` carries no
        ``tool_use`` id — the assistant message is the authoritative source for
        the blocks, and projecting the audit row too would duplicate every call.
        """
        append(PARENT, "permission_posture", {"posture": "read-only"})
        _plain_turn(user="hello", answer="hi")
        messages = project_messages(read_session(PARENT))
        assert [m["role"] for m in messages] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# 2. Boundaries
# ---------------------------------------------------------------------------
class TestBoundaries:
    def test_only_closed_turns_are_legal_boundaries(self, event_db):
        _plain_turn(user="one", answer="1")   # seq 1-3
        _tool_turn(user="two")                # seq 4-9
        assert legal_boundaries(PARENT) == [3, 9]

    def test_a_posture_between_turns_is_a_legal_boundary(self, event_db):
        _plain_turn(user="one", answer="1")            # seq 1-3
        append(PARENT, "permission_posture", {"posture": "workspace-write"})  # seq 4
        assert legal_boundaries(PARENT) == [3, 4]

    def test_describe_reports_an_open_turn(self, event_db):
        _plain_turn(user="one", answer="1")
        append(PARENT, "turn_start", {"user_input": "two"})
        report = describe(PARENT)
        assert report["open_turn"] == 4
        assert report["legal_boundaries"] == [3]
        assert report["turns"] == 1


# ---------------------------------------------------------------------------
# 3. The refusals
# ---------------------------------------------------------------------------
class TestRefusals:
    def test_a_boundary_inside_an_open_turn_is_refused_not_rounded(self, event_db):
        """The refusal borrowed from DSH, and the whole reason this card exists.

        seq 5 is a ``tool_result`` in the middle of a turn: a prefix ending there
        holds a ``tool_use`` with no answer, which the next provider call
        rejects. Rounding it back to seq 3 would silently fork a different
        conversation than the operator asked for.
        """
        _plain_turn(user="one", answer="1")   # seq 1-3
        _tool_turn(user="two")                # seq 4-9

        with pytest.raises(ForkRefused) as excinfo:
            plan_fork(PARENT, 6)
        exc = excinfo.value
        assert exc.reason == REASON_OPEN_TURN
        assert exc.detail["open_turn_seq"] == 4
        assert exc.detail["legal_boundaries"] == [3, 9]
        # And it says where a fork WOULD be legal, either side.
        assert "--at 3" in str(exc) and "--at 9" in str(exc)

    def test_a_seq_that_names_no_event_is_refused(self, event_db):
        _plain_turn(user="one", answer="1")
        with pytest.raises(ForkRefused) as excinfo:
            plan_fork(PARENT, 99)
        assert excinfo.value.reason == REASON_BOUNDARY_NOT_IN_LOG
        assert excinfo.value.detail["max_seq"] == 3

    def test_an_unanswered_tool_call_is_refused_even_at_a_turn_end(self, event_db):
        """A closed turn is not enough: the log can have holes.

        ``on_post_tool_use`` fires for every call, so a missing ``tool_result``
        means the recorder itself failed to write one. Projecting that prefix
        produces a ``tool_use`` with nothing answering it.
        """
        append(PARENT, "turn_start", {"user_input": "go"})
        append(
            PARENT,
            "assistant_message",
            {"iteration": 0, "content": "working",
             "tool_calls": [{"id": "t1", "name": "read_file", "input": {"path": "a.py"}}]},
        )
        append(PARENT, "turn_end", {})

        with pytest.raises(ForkRefused) as excinfo:
            plan_fork(PARENT, 3)
        assert excinfo.value.reason == REASON_UNANSWERED_TOOL_CALL
        assert excinfo.value.detail["unanswered"] == ["read_file"]
        assert legal_boundaries(PARENT) == []

    def test_a_withheld_payload_is_refused_rather_than_fabricated(
        self, event_db, monkeypatch
    ):
        """Hash-only mode cannot be forked, and must say so in those words.

        A withheld payload is not an empty one. Projecting it would seed the new
        session with a message the model never saw, carrying a correct-looking
        digest — a fabrication that no reader downstream could detect.
        """
        monkeypatch.setenv("ICDEV_AGENT_EVENT_PAYLOAD_RETENTION", "0")
        _plain_turn(user="one", answer="1")

        events = read_session(PARENT)
        assert all(e.payload_withheld for e in events)
        assert all(e.payload_hash for e in events)  # the hashes are still there

        with pytest.raises(ForkRefused) as excinfo:
            plan_fork(PARENT, 3)
        assert excinfo.value.reason == REASON_PAYLOAD_WITHHELD
        assert excinfo.value.detail["withheld_seqs"] == [1, 2]
        assert "args/agent_event_log.yaml" in str(excinfo.value)

    def test_a_refused_fork_creates_nothing(self, event_db, manager, seed_store):
        _store, save, load = seed_store
        _plain_turn(user="one", answer="1")
        append(PARENT, "turn_start", {"user_input": "two"})

        with pytest.raises(ForkRefused):
            fork_session(PARENT, 4, manager=manager, saver=save, loader=load)
        assert manager.contexts == {}
        assert _store == {}


# ---------------------------------------------------------------------------
# 4. What a fork actually writes
# ---------------------------------------------------------------------------
class TestForking:
    def test_a_fork_seeds_the_prefix_and_records_its_parent(
        self, event_db, manager, seed_store
    ):
        store, save, load = seed_store
        _plain_turn(user="one", answer="1")   # seq 1-3
        _tool_turn(user="two")                # seq 4-9

        result = fork_session(PARENT, 3, manager=manager, saver=save, loader=load,
                              actor="operator-a")

        # The prefix, and only the prefix.
        assert result.plan.seed_events == 3
        assert result.plan.boundary_seq == 3
        assert len(result.plan.messages) == 2
        assert store[result.seed_loop_session_id] == list(result.plan.messages)

        # The metadata the card asks for, on the context from its first moment.
        fork_meta = manager.contexts[result.context_id]["config"]["fork"]
        assert fork_meta["parent_session_id"] == PARENT
        assert fork_meta["boundary_seq"] == 3
        assert fork_meta["seed_events"] == 3
        assert fork_meta["seed_messages"] == 2
        assert fork_meta["actor"] == "operator-a"
        assert len(fork_meta["seed_digest"]) == 64

        # The fork resumes the seeded history, not the parent's live one.
        config = manager.contexts[result.context_id]["config"]
        assert config["resume_session_id"] == result.seed_loop_session_id
        assert result.warnings == []

    def test_the_new_session_opens_with_its_own_provenance_event(
        self, event_db, manager, seed_store
    ):
        _store, save, load = seed_store
        _plain_turn(user="one", answer="1")

        result = fork_session(PARENT, 3, manager=manager, saver=save, loader=load)

        events = read_session(result.context_id)
        assert [e.event_type for e in events] == [FORK_EVENT_TYPE]
        assert events[0].seq == 1
        payload = events[0].payload
        assert payload["parent_session_id"] == PARENT
        assert payload["boundary_seq"] == 3
        assert payload["seed_digest"] == result.plan.seed_digest
        # Metadata only: nothing the model saw travels in it.
        assert "user_input" not in payload and "content" not in payload
        # The parent's own log is untouched by the fork.
        assert len(read_session(PARENT)) == 3

    def test_the_human_transcript_matches_what_the_model_was_seeded_with(
        self, event_db, manager, seed_store
    ):
        _store, save, load = seed_store
        _tool_turn(user="two")

        result = fork_session(PARENT, 6, manager=manager, saver=save, loader=load)

        seeded = manager.messages[result.context_id]
        # The tool_result rides on the `user` role for the provider's sake; it is
        # NOT replayed into the transcript, where it would read as the operator.
        assert [m["role"] for m in seeded] == ["user", "assistant", "assistant"]
        assert seeded[0]["content"] == "two"
        assert seeded[1]["content"] == "let me look"
        assert seeded[0]["metadata"]["seeded_from"] == PARENT
        assert result.messages_seeded == 3

    def test_an_unverifiable_seed_is_not_linked_as_a_resume_id(
        self, event_db, manager
    ):
        """A save that reports success but reads back empty must not be trusted.

        ``save_session`` returns ``False`` on a DB error and ``load_session``
        returns ``[]`` for a row that is not there. A ``resume_session_id``
        pointing at either produces a session that looks continued and remembers
        nothing — the exact silent-lie shape this repo keeps finding.
        """
        _plain_turn(user="one", answer="1")

        result = fork_session(
            PARENT, 3, manager=manager,
            saver=lambda *a, **k: True,       # claims success
            loader=lambda _sid: [],           # …and nothing is there
        )
        assert result.seed_loop_session_id == ""
        assert "resume_session_id" not in manager.contexts[result.context_id]["config"]
        assert any("read back" in w for w in result.warnings)

    def test_the_slash_command_switches_the_operator_into_the_fork(
        self, event_db, manager, seed_store, monkeypatch
    ):
        """``/fork <seq>`` is only useful if the next thing typed goes to the branch."""
        import icdev.tools.llm.agent_loop_session as als
        import tools.agent_runtime.commands as cmds
        import tools.chat.chat_manager as chat_mod

        _store, save, load = seed_store
        _plain_turn(user="one", answer="1")
        # /fork builds its own manager and reaches the real persistence layer —
        # patched at the two seams fork_session imports from, not at a third
        # module object that nothing resolves.
        monkeypatch.setattr(chat_mod, "ChatManager", lambda *a, **k: manager)
        monkeypatch.setattr(als, "save_session", save)
        monkeypatch.setattr(als, "load_session", load)

        class _Runtime:
            user_id = "operator-a"
            tenant_id = ""

            def __init__(self) -> None:
                self.session = type("S", (), {"context_id": PARENT})()

            def resume_session(self, context_id):
                self.session = type("S", (), {"context_id": context_id})()

        runtime = _Runtime()
        handled, response, should_exit = cmds.dispatch(runtime, "/fork 3 | branch B")
        assert handled and not should_exit
        assert runtime.session.context_id != PARENT
        assert manager.contexts[runtime.session.context_id]["title"] == "branch B"
        assert "You are now in the fork." in response

        # And with no argument it SURVEYS rather than choosing a boundary.
        runtime.session = type("S", (), {"context_id": PARENT})()
        _handled, survey, _exit = cmds.dispatch(runtime, "/fork")
        assert "Legal fork boundaries: 3" in survey
        assert runtime.session.context_id == PARENT

    def test_a_fork_of_a_fork_names_the_fork_it_came_from(
        self, event_db, manager, seed_store
    ):
        """Lineage is a chain, not a single hop — and each link is one row."""
        _store, save, load = seed_store
        _plain_turn(user="one", answer="1")
        first = fork_session(PARENT, 3, manager=manager, saver=save, loader=load)

        _plain_turn(session=first.context_id, user="two", answer="2")
        second = fork_session(first.context_id, 4, manager=manager, saver=save,
                              loader=load)

        assert second.plan.parent_session_id == first.context_id
        # seq 1 is the first fork's own provenance event; the turn is 2-4.
        assert second.plan.seed_events == 4
        assert manager.contexts[second.context_id]["config"]["fork"][
            "parent_session_id"
        ] == first.context_id


# ---------------------------------------------------------------------------
# 5. The CLI refuses the combinations that have no meaning
# ---------------------------------------------------------------------------
class TestCli:
    def test_fork_and_resume_are_mutually_exclusive(self, capsys):
        from tools.agent_runtime.cli import chat_main

        assert chat_main(["--fork", "ctx-a", "--resume", "ctx-b"]) == 2
        assert "mutually exclusive" in capsys.readouterr().err

    def test_at_without_fork_is_refused(self, capsys):
        """Rather than silently ignored — ``--at`` alone is a mistyped fork."""
        from tools.agent_runtime.cli import chat_main

        assert chat_main(["--at", "3"]) == 2
        assert "--at is only meaningful with --fork" in capsys.readouterr().err

    def test_fork_without_at_surveys_and_creates_nothing(
        self, event_db, manager, monkeypatch, capsys
    ):
        """No boundary is CHOSEN for the operator — the legal ones are printed.

        Defaulting to the latest turn would make a fork at the wrong seq
        indistinguishable from one at the right seq until the branch answered
        the wrong question.
        """
        import argparse

        from tools.agent_runtime import cli as cli_mod

        _plain_turn(user="one", answer="1")
        args = argparse.Namespace(
            fork=PARENT, at=None, user=None, tenant=None, no_banner=False
        )
        assert cli_mod._apply_fork(object(), args) == 2
        err = capsys.readouterr().err
        assert "--fork needs --at" in err
        assert "legal boundaries: 3" in err
        assert manager.contexts == {}
