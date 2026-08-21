# CUI // SP-CTI
"""One live turn must leave a ``request_context`` row NAMING every active injector.

THE ACCEPTANCE TEST FOR hcx-evt-03, AND IT RUNS BACKWARDS (hcx-vv-01).

``tests/agent_runtime/test_context_events.py`` proves the recorder's own
contract by calling :func:`record_injection` directly, and
``tests/agent_runtime/test_event_recorder.py`` proves one ``request_context``
row reaches the table during a real turn. Neither of those can fail the way this
capability actually fails.

A declared capability that nobody consumes is this platform's signature defect,
and an event log wears a second version of it: the log ships, rows appear, the
dashboards look healthy -- and ONE injector was never instrumented, so the block
it put in front of the model is invisible while every other block is not. A
partially-covered log is more dangerous than an absent one, because it reads as
coverage. "Some ``request_context`` rows exist" is therefore not the assertion;
it is the assertion that hides the defect.

So this file runs the proof in the other direction. It does not start from the
log and check that the rows look plausible. It starts from **the system prompt
the provider was actually handed** and requires the log to account for all of
it:

  1. Every source in :data:`context_events.SOURCES` is made genuinely ACTIVE --
     a real ``CLAUDE.md`` on disk, a real ACTIVE standing goal in the table, a
     real operator preference in the table, a real hybrid-memory hit. Nothing
     about the injection or the recording path is stubbed; only the data each
     injector reads.
  2. ONE turn runs through the REAL ``run_agent_loop``, with a router that
     captures the system prompt it was asked to send.
  3. Then, per injector: the block is IN that prompt, an event NAMES that
     source, and the event's ``body_sha256`` is the digest of that same block.

WHAT IT FOUND, WHICH IS THE POINT OF WRITING IT THIS WAY
--------------------------------------------------------
There were FOUR injectors, not three. hcx-evt-03 instrumented the three that
live in ``tools/agent_runtime/`` and the runtime assembles; ``agent_loop``
appends its own retrieved-memory block to ``system_prompt`` AFTER the runtime
has handed the prompt over (``agent_loop.py``, "Inject retrieved memory into
system_prompt before the first turn"). It is the LAST text added before the
request goes out, nothing downstream can tell it apart from the text it was
appended to, and it produced no ``request_context`` row. Three out of four, and
the log looked complete -- which is exactly the shape this card was written to
catch, found by asking the question backwards. It is now announced through the
loop's ``on_context_injection`` hook and recorded by the runtime beside the
other three.

WHY THE EXPECTED SET IS DERIVED AND NOT SPELLED
------------------------------------------------
The set of sources that must appear is read from ``context_events.SOURCES`` --
the module's own vocabulary. A fifth injector declared there and never wired
fails :func:`test_every_declared_source_is_covered_by_this_proof` rather than
quietly widening the gap this file exists to close. The two sides are
independent: :func:`record_injection` cannot change what an injector produces
(it swallows everything and returns ``None``), so "this text was injected" and
"an event names it" are two separate facts, and this file is the assertion that
the second follows the first.

TEST HYGIENE -- both of these have produced false greens in this repo
--------------------------------------------------------------------
* ``tools.X`` and ``icdev.tools.X`` are DISTINCT module objects for
  ``agent_runtime`` (they are the same object for ``db.storage``, which is what
  makes the difference easy to miss). Patching one leaves the other pointing at
  the LIVE board. :func:`_patch_every_alias` patches every loaded alias, and
  :func:`test_the_alias_hygiene_this_file_depends_on_is_real` asserts the two
  really are distinct rather than trusting the comment.
* NOTHING HERE SKIPS. A gated test that skips is unmeasured, not passing. The
  schema comes from the migration's own ``up.sql`` and from the injector
  modules' own ``_ensure_schema``, so a missing table fails here instead of
  being caught by an ``except OperationalError`` and reported green.
"""
from __future__ import annotations

import importlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import tools.agent_runtime.sessions as sess_mod
from tests._sql_compat import translating
from tools.agent_runtime import context_events
from tools.agent_runtime.context_events import (
    EVENT_TYPE,
    SOURCES,
    coverage,
    injections_for_session,
)
from tools.agent_runtime.event_log import MIGRATION, read_session
from tools.agent_runtime.goal_context import INJECTION_SOURCE as GOAL_SOURCE
from tools.agent_runtime.profile_memory import INJECTION_SOURCE as PROFILE_SOURCE
from tools.agent_runtime.project_context import INJECTION_SOURCE as PROJECT_SOURCE
from tools.agent_runtime.runtime import AgentRuntime
from tools.audit.row_hash import compute_payload_hash

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Markers planted in each injector's DATA, so "this block reached the model" is
#: checkable on content and not only on length.
PROJECT_MARKER = "HCXVV01-PROJECT-INSTRUCTION-MARKER"
GOAL_MARKER = "HCXVV01-STANDING-GOAL-MARKER"
PROFILE_MARKER = "HCXVV01-OPERATOR-PREFERENCE-MARKER"
MEMORY_MARKER = "HCXVV01-RETRIEVED-MEMORY-MARKER"

#: The loop's own injector source. Spelled here as well as in ``agent_loop``,
#: and :func:`test_the_loop_declares_the_source_the_recorder_registers` asserts
#: the two agree -- the duplication is deliberate and is the ONLY one in this
#: file. Importing ``agent_loop.MEMORY_INJECTION_SOURCE` at module scope would
#: make this file fail to COLLECT against a tree without the fix, and "the
#: module did not import" is a far weaker recorded RED than "the log did not
#: account for a block that reached the model". The red-first proof for this
#: file should read as the defect, not as a missing symbol.
LOOP_MEMORY_SOURCE = "agent_loop_memory"

PROMPT = "what is the answer?"


# ---------------------------------------------------------------------------
# Alias hygiene
# ---------------------------------------------------------------------------
def _aliases(dotted: str) -> list[Any]:
    """Every distinct module object for ``tools.<dotted>``.

    ``tools.agent_runtime.project_context`` and
    ``icdev.tools.agent_runtime.project_context`` are two module objects loaded
    from two files. A monkeypatch on one is invisible to a caller that imported
    the other, and the code under test then runs unpatched -- against the LIVE
    board, which is how a test asserts happily against its own no-op.
    """
    mods: list[Any] = []
    for name in (f"tools.{dotted}", f"icdev.tools.{dotted}"):
        try:
            mod = importlib.import_module(name)
        except Exception:  # noqa: BLE001 -- a mirror that is absent needs no patch
            continue
        if not any(mod is seen for seen in mods):
            mods.append(mod)
    assert mods, f"neither tools.{dotted} nor icdev.tools.{dotted} is importable"
    return mods


def _patch_every_alias(monkeypatch, dotted: str, attr: str, value: Any) -> None:
    for mod in _aliases(dotted):
        monkeypatch.setattr(mod, attr, value)


# ---------------------------------------------------------------------------
# The real table, from the migration itself
# ---------------------------------------------------------------------------
def _events_ddl() -> str:
    return (
        REPO_ROOT / "tools" / "db" / "migrations" / MIGRATION / "up.sql"
    ).read_text(encoding="utf-8")


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test (``%s`` -> ``?``, unclosable).

    Every module in this dependency chain authors PostgreSQL SQL, so a bare
    ``sqlite3`` handle would raise ``near "%": syntax error`` inside the
    best-effort ``except`` each injector wraps its work in -- and the file would
    then assert against a no-op it caused itself, reading as "the log is inert"
    when the log is fine and the fixture is broken. Same helper name and same
    shape as tests/agent_runtime/test_event_recorder.py, which stands up the
    same table.
    """
    return translating(raw, unclosable=True)


@pytest.fixture
def live_db(monkeypatch, tmp_path):
    """One SQLite database standing in for the platform store.

    It starts with ``agent_session_events`` built from the migration's own
    ``up.sql`` -- a column that exists in the module's DDL and not in the
    migration fails here rather than inside somebody's ``except``. The injector
    tables are NOT pre-created: ``standing_goals`` and ``profile_memory`` each
    self-create their schema through ``_ensure_schema``, and letting them do it
    is part of what "the injector ran for real" means.
    """
    raw = sqlite3.connect(str(tmp_path / "live.db"))
    raw.executescript(_events_ddl())
    conn = _translating_conn(raw)
    for mod in _aliases("db.storage"):
        monkeypatch.setattr(mod, "get_connection", lambda *a, **k: conn)
        monkeypatch.setattr(mod, "table_exists", lambda c, t: True)
    monkeypatch.delenv("ICDEV_AGENT_EVENT_PAYLOAD_RETENTION", raising=False)
    monkeypatch.delenv("ICDEV_SAG_EVENT_RECORDING", raising=False)
    yield raw
    raw.close()


# ---------------------------------------------------------------------------
# Fakes -- no chat DB, no session persistence, no LLM
# (the same seams tests/agent_runtime/test_event_recorder.py stubs)
# ---------------------------------------------------------------------------
class _FakeChatManager:
    def __init__(self) -> None:
        self._n = 0
        self.messages: dict[str, list[dict[str, Any]]] = {}

    def create_context(self, *, title="", **_kw) -> str:
        self._n += 1
        cid = f"ctx-hcxvv01-{self._n}"
        self.messages[cid] = []
        return cid

    def add_message(self, context_id, *, role, content, **_kw) -> int:
        self.messages.setdefault(context_id, []).append(
            {"role": role, "content": content}
        )
        return len(self.messages[context_id])

    def get_messages(self, context_id, *, limit=200, offset=0):
        return list(self.messages.get(context_id, []))[offset: offset + limit]

    def update_title(self, context_id, title) -> None:
        pass

    def update_config(self, context_id, cfg) -> None:
        pass


@dataclass
class _CapturedCall:
    system_prompt: str
    messages: list = field(default_factory=list)


class _CapturingRouter:
    """A real router seam that remembers the prompt it was asked to send.

    The system prompt is the artefact this whole file is about: it is what the
    injectors wrote into, and what the log claims to describe.
    """

    def __init__(self) -> None:
        self.calls: list[_CapturedCall] = []

    def get_provider_for_function(self, function: str):
        return (
            type("P", (), {"provider_name": "fake"})(),
            "m",
            {"supports_tools": True},
        )

    def invoke(self, function, request):
        from icdev.tools.llm.provider import LLMResponse

        self.calls.append(
            _CapturedCall(
                system_prompt=request.system_prompt,
                messages=list(getattr(request, "messages", []) or []),
            )
        )
        return LLMResponse(
            content="the answer", stop_reason="end_turn", provider="fake"
        )


@pytest.fixture
def fake_manager(monkeypatch):
    mgr = _FakeChatManager()
    monkeypatch.setattr(sess_mod, "ChatManager", lambda *a, **k: mgr)
    return mgr


@pytest.fixture
def no_save(monkeypatch):
    import icdev.tools.llm.agent_loop_session as als

    monkeypatch.setattr(als, "save_session", lambda *a, **k: True)


# ---------------------------------------------------------------------------
# Making each injector genuinely active
# ---------------------------------------------------------------------------
@pytest.fixture
def project_root(monkeypatch, tmp_path):
    """A real project tree for ``project_context`` to read.

    ``repo_root`` is redirected -- not ``describe``, and not
    ``build_for_runtime``. Section collection, the window budget, the truncation
    accounting and the recording call all run exactly as they do in production;
    redirecting the root only makes the CONTENT deterministic, so the marker
    assertions below mean something and so this file does not silently depend on
    whichever checkout it happens to run from having a ``CLAUDE.md``.
    """
    root = tmp_path / "project"
    root.mkdir()
    (root / "CLAUDE.md").write_text(
        "# Project instructions\n\n" + PROJECT_MARKER + "\n", encoding="utf-8"
    )
    _patch_every_alias(
        monkeypatch, "agent_runtime.project_context", "repo_root", lambda: root
    )
    # File-only, so the block does not depend on whichever project-state tables
    # this database happens to have. The injector, its budget and its recording
    # are untouched.
    monkeypatch.setenv("ICDEV_SAG_PROJECT_STATE", "0")
    monkeypatch.setenv("ICDEV_SAG_PROJECT_CONTEXT", "1")
    return root


@pytest.fixture
def standing_goal(live_db, monkeypatch):
    """One ACTIVE standing goal, written through the real ``GoalManager``."""
    from tools.agent_runtime.standing_goals import GoalManager, GoalStatus

    monkeypatch.setenv("ICDEV_SAG_GOALS", "1")
    goal = GoalManager(user_id="default", tenant_id="").create(
        GOAL_MARKER, detail="Keep the event log honest.", status=GoalStatus.ACTIVE
    )
    assert goal is not None, "the goal store rejected the goal -- fixture is broken"
    return goal


@pytest.fixture
def operator_profile(live_db, monkeypatch):
    """One durable operator preference, written through the real API."""
    from tools.agent_runtime import profile_memory

    monkeypatch.setenv("ICDEV_SAG_PROFILE_MEMORY", "1")
    assert profile_memory.set_preference("style", PROFILE_MARKER)
    return PROFILE_MARKER


@pytest.fixture
def retrieved_memory(monkeypatch):
    """One hybrid-memory hit, so the LOOP's own injector has something to inject.

    ``hybrid_search.search`` is the data source; ``_retrieve_memory_context``,
    its formatting, the append into ``system_prompt`` and the announcement hook
    all run for real. Patched on every alias -- ``agent_loop`` imports it from
    ``icdev.tools.memory.hybrid_search`` and ``profile_memory`` from
    ``tools.memory.hybrid_search``, and those are the two objects a
    single-module patch chooses between.
    """
    def _search(query, *_a, **_kw):
        return [{"content": MEMORY_MARKER, "type": "event", "score": 1.0}]

    _patch_every_alias(monkeypatch, "memory.hybrid_search", "search", _search)
    return MEMORY_MARKER


@pytest.fixture
def all_injectors_active(project_root, standing_goal, operator_profile,
                         retrieved_memory):
    """Every source in :data:`SOURCES` has something real to inject."""
    return {
        PROJECT_SOURCE: PROJECT_MARKER,
        GOAL_SOURCE: GOAL_MARKER,
        PROFILE_SOURCE: PROFILE_MARKER,
        LOOP_MEMORY_SOURCE: MEMORY_MARKER,
    }


# ---------------------------------------------------------------------------
# Reading back what the turn actually did
# ---------------------------------------------------------------------------
#: Source name -> the ``AgentRuntime`` attribute caching the block that source
#: injected. Keyed by each module's own ``INJECTION_SOURCE`` constant rather
#: than a re-spelled string, so a renamed source breaks the import instead of
#: quietly dropping out of the comparison.
#:
#: ``agent_loop_memory`` is deliberately absent: the loop's block is not
#: assembled by the runtime and there is no attribute holding it. That is the
#: whole reason it went uninstrumented, so this file recovers it a different
#: way -- see :func:`_loop_injected_block`.
PREAMBLE_ATTRS = {
    PROJECT_SOURCE: "_project_preamble",
    GOAL_SOURCE: "_goals_preamble",
    PROFILE_SOURCE: "_profile_preamble",
}


def _loop_injected_block(rt: AgentRuntime, sent_prompt: str) -> str:
    """The block the LOOP appended, recovered by SUBTRACTION from what was sent.

    ``run_agent_loop`` does ``system_prompt = system_prompt + "\\n\\n" +
    _mem_ctx``, so the loop's block is exactly the tail of the prompt beyond
    what the runtime composed. Recovering it this way rather than by
    re-formatting a search result is what makes it an independent ground truth:
    a re-implementation of ``_retrieve_memory_context`` would agree with a
    broken one, and a marker check alone would not notice a truncated body.

    ``_effective_system_prompt`` is safe to call again -- every preamble is
    cached by the turn that already ran, so it recomposes and injects nothing.
    """
    composed = rt._effective_system_prompt(PROMPT)
    if not sent_prompt.startswith(composed):
        raise AssertionError(
            "the prompt the provider received does not start with the prompt "
            "the runtime composed; the subtraction below would be meaningless"
        )
    tail = sent_prompt[len(composed):]
    return tail[2:] if tail.startswith("\n\n") else tail


def _injected_blocks(rt: AgentRuntime, sent_prompt: str) -> dict[str, str]:
    """What each injector ACTUALLY put in front of the model, per source.

    The ground truth the log is measured against, and independent of it:
    ``record_injection`` swallows every exception and returns ``None``, so it
    cannot influence the text an injector produces.

    Every runtime attribute must EXIST -- ``getattr`` with a default would turn
    a renamed cache into an injector that silently never injects, and the whole
    file would then pass by measuring nothing.
    """
    blocks: dict[str, str] = {}
    for source, attr in PREAMBLE_ATTRS.items():
        assert hasattr(rt, attr), (
            f"AgentRuntime has no {attr!r}: the cache {source} injects from was "
            "renamed, and this proof can no longer see what it injected"
        )
        blocks[source] = getattr(rt, attr) or ""
    blocks[LOOP_MEMORY_SOURCE] = _loop_injected_block(rt, sent_prompt)
    return blocks


@dataclass
class _Turn:
    """Everything one live turn touched, for the assertions to read."""

    rt: AgentRuntime
    router: _CapturingRouter
    ctx_id: str
    result: Any

    @property
    def prompt(self) -> str:
        assert self.router.calls, "the real loop never reached the provider"
        return self.router.calls[0].system_prompt

    @property
    def injected(self) -> dict[str, str]:
        """Source -> block, for the injectors that actually produced one."""
        return {
            s: b for s, b in _injected_blocks(self.rt, self.prompt).items() if b
        }

    def events(self, *, include_body: bool = True) -> list[dict[str, Any]]:
        return injections_for_session(self.ctx_id, include_body=include_body)


def _run_one_turn(prompt: str = PROMPT) -> _Turn:
    """One turn through the REAL ``run_agent_loop``."""
    router = _CapturingRouter()
    rt = AgentRuntime(router=router)
    ctx_id = rt.session.context_id
    result = rt.run_turn(prompt)
    return _Turn(rt=rt, router=router, ctx_id=ctx_id, result=result)


# ---------------------------------------------------------------------------
# 0. The hygiene this file's correctness rests on
# ---------------------------------------------------------------------------
def test_the_alias_hygiene_this_file_depends_on_is_real():
    """``tools.X`` and ``icdev.tools.X`` are ONE object for agent_runtime.

    Asserted rather than commented. xit-decl-02 collapsed the two spellings
    onto one module object (icdev/core/shim.py), which is what makes a
    monkeypatch on either spelling reach the code under test. If the packaging
    ever SPLITS them again, ``_patch_every_alias`` would be patching a module
    nothing reads, and every assertion in this file would be measuring a no-op
    while the code under test wrote to the live board -- so the split must
    fail here first.
    """
    project = _aliases("agent_runtime.project_context")
    assert len(project) == 1, (
        "expected ONE module object for agent_runtime.project_context; "
        f"got {[m.__file__ for m in project]}"
    )
    for mod in project:
        assert mod.INJECTION_SOURCE == PROJECT_SOURCE


def test_every_declared_source_is_covered_by_this_proof():
    """A fifth injector added to ``SOURCES`` fails here until it is proven.

    :data:`PREAMBLE_ATTRS` plus the loop's own source is what this file can
    speak for. Declaring a source and leaving it out of the acceptance test is
    how a log becomes partially covered while every existing assertion stays
    green -- which is the failure mode this card exists to make impossible, and
    which is exactly what ``agent_loop_memory`` was before this file existed.
    """
    assert set(PREAMBLE_ATTRS) | {LOOP_MEMORY_SOURCE} == set(SOURCES)


def test_the_loop_declares_the_source_the_recorder_registers():
    """One spelling of ``agent_loop_memory``, across the three that hold it.

    The loop announces the source, ``context_events`` registers it, and this
    file names it. Three independently-spelled literals would drift into a
    ``source_registered: false`` row that still reads as coverage -- so the
    agreement is asserted rather than assumed, and this is where the local
    constant above is checked against the module that emits it.
    """
    from icdev.tools.llm.agent_loop import MEMORY_INJECTION_SOURCE

    assert MEMORY_INJECTION_SOURCE == LOOP_MEMORY_SOURCE
    assert LOOP_MEMORY_SOURCE in SOURCES


# ---------------------------------------------------------------------------
# 1. THE CARD: one live turn, one event per active injector, each NAMED
# ---------------------------------------------------------------------------
class TestALiveTurnAccountsForEveryInjector:
    def test_the_log_names_every_source_that_reached_the_model(
        self, live_db, fake_manager, no_save, all_injectors_active
    ):
        turn = _run_one_turn()
        assert turn.result.done is True

        # The premise: this turn really did inject every block. Asserted, not
        # assumed -- a fixture that silently stopped activating an injector
        # would turn the assertion below into a tautology over a smaller set.
        injected = turn.injected
        assert set(injected) == set(SOURCES), (
            "the fixture failed to activate every injector, which would make "
            f"this proof vacuous. active={sorted(injected)}"
        )

        # THE REVERSE-DIRECTION ASSERTION. Not "rows exist" -- the set of
        # sources NAMED in the log is exactly the set of injectors that ran.
        named = {e["source"] for e in turn.events()}
        assert named == set(injected), (
            "the event log does not account for every injector that reached the "
            f"model. named={sorted(named)} injected={sorted(injected)}"
        )

    def test_each_event_carries_the_block_that_was_actually_injected(
        self, live_db, fake_manager, no_save, all_injectors_active
    ):
        """Naming the source is necessary and not sufficient.

        An event that names ``goal_context`` while carrying the project block
        would satisfy every set comparison above and describe the wrong
        injection. ``body_sha256`` is the stable identity of the injected text
        under every retention setting, so it is the field that ties row to
        block.
        """
        turn = _run_one_turn()
        by_source = {e["source"]: e for e in turn.events()}

        for source, block in turn.injected.items():
            event = by_source[source]
            assert event["body_sha256"] == compute_payload_hash(block), source
            assert event["body"] == block, source
            assert event["size_chars"] == len(block), source
            assert event["source_registered"] is True, source

    def test_every_recorded_block_is_in_the_prompt_the_provider_received(
        self, live_db, fake_manager, no_save, all_injectors_active
    ):
        """The log describes the MODEL REQUEST, not an intention to build one.

        ``_effective_system_prompt`` composes three of the blocks and hands them
        to ``run_agent_loop``, which appends the fourth and hands the lot to the
        provider. Recording at each injector and never checking the far end
        would let a block be logged as injected and then dropped on the way --
        measured at the wrong seam.
        """
        turn = _run_one_turn()
        prompt = turn.prompt

        events = turn.events()
        assert events, (
            "no request_context rows after a live turn -- the event log shipped "
            "inert, which is the exact defect this card was written to fail on"
        )
        for event in events:
            assert event["body"] in prompt, (
                f"{event['source']} was recorded but its block is not in the "
                "system prompt the provider was handed"
            )

        # And the markers, so "in the prompt" is about content rather than an
        # empty string trivially satisfying ``in``.
        for marker in (PROJECT_MARKER, GOAL_MARKER, PROFILE_MARKER, MEMORY_MARKER):
            assert marker in prompt

    def test_the_block_the_loop_appended_is_recorded_and_not_the_prompt_it_grew_from(
        self, live_db, fake_manager, no_save, all_injectors_active
    ):
        """The injector the runtime cannot see, pinned exactly.

        ``agent_loop`` appends its retrieved-memory block last, so its event's
        body must be precisely the tail of the prompt -- not a prefix of it
        (a truncated body), and not the whole thing (a body that swallowed the
        three blocks the runtime composed).
        """
        turn = _run_one_turn()
        prompt = turn.prompt
        event = {e["source"]: e for e in turn.events()}[LOOP_MEMORY_SOURCE]
        body = event["body"]

        assert prompt.endswith(body)
        assert MEMORY_MARKER in body
        assert PROJECT_MARKER not in body
        assert GOAL_MARKER not in body
        assert body != prompt
        # The loop's own accounting, recorded alongside the text.
        assert event["detail"]["tier"]
        assert event["detail"]["query_chars"] == len(PROMPT)

    def test_coverage_reports_every_source_recorded_for_the_session(
        self, live_db, fake_manager, no_save, all_injectors_active
    ):
        """The module's own reporting surface agrees with the rows.

        ``coverage`` is what an operator and hcx-evt-06 read. A reporting
        surface that disagreed with ``injections_for_session`` would make the
        defect visible in one place and invisible in the other.
        """
        turn = _run_one_turn()
        report = coverage(turn.ctx_id)
        assert report["unregistered"] == []
        for source in SOURCES:
            assert report["sources"][source]["recorded"] is True, source
            assert report["sources"][source]["count"] == 1, source
        assert report["total"] == len(SOURCES)

    def test_one_turn_writes_exactly_one_event_per_injector(
        self, live_db, fake_manager, no_save, all_injectors_active
    ):
        """Not two, and not one shared row for all four.

        A single merged ``request_context`` row would satisfy "rows exist" and
        destroy the only property the card asks for: which injector produced
        which block.
        """
        turn = _run_one_turn()
        sources = [e["source"] for e in turn.events(include_body=False)]
        assert sorted(sources) == sorted(SOURCES)

    def test_the_events_are_in_the_real_table_under_the_chat_context_id(
        self, live_db, fake_manager, no_save, all_injectors_active
    ):
        """Filed under ``context_id``, in ``agent_session_events``, on turn one.

        ``AgentLoopResult.session_id`` does not exist until the turn COMPLETES
        and injection happens before it STARTS, so a log keyed on it would be
        empty for exactly the turn this test runs. Read straight out of SQL so
        the assertion cannot be satisfied by a reader's own defaulting.
        """
        turn = _run_one_turn()
        rows = live_db.execute(
            "SELECT session_id, event_type FROM agent_session_events "
            "WHERE event_type = ?",
            (EVENT_TYPE,),
        ).fetchall()
        assert len(rows) == len(SOURCES)
        assert {r[0] for r in rows} == {turn.ctx_id}
        assert turn.ctx_id != turn.result.session_id

    def test_the_injection_events_precede_the_first_assistant_message(
        self, live_db, fake_manager, no_save, all_injectors_active
    ):
        """Order carries the claim "this is what the model had, before it answered".

        An injection recorded after the response would describe the next turn,
        not this one.
        """
        turn = _run_one_turn()
        types = [e.event_type for e in read_session(turn.ctx_id)]
        first_answer = types.index("assistant_message")
        assert types.count(EVENT_TYPE) == len(SOURCES)
        assert all(
            i < first_answer for i, t in enumerate(types) if t == EVENT_TYPE
        ), types


# ---------------------------------------------------------------------------
# 2. The proof tracks reality -- it is not a constant that happens to hold
# ---------------------------------------------------------------------------
class TestTheProofIsNotVacuous:
    def test_a_disabled_injector_leaves_the_prompt_and_the_log_together(
        self, live_db, fake_manager, no_save, all_injectors_active, monkeypatch
    ):
        """Turn one injector off: it leaves BOTH sides, together.

        This is what makes the equality above a measurement. A test asserting a
        constant four sources would pass here too -- and would pass on a build
        that wrote four rows regardless of what was injected, which is
        fabricated coverage rather than evidence.
        """
        monkeypatch.setenv("ICDEV_SAG_GOALS", "0")
        turn = _run_one_turn()

        injected = turn.injected
        assert GOAL_SOURCE not in injected
        assert GOAL_MARKER not in turn.prompt

        named = {e["source"] for e in turn.events(include_body=False)}
        assert named == set(injected)
        assert named == {PROJECT_SOURCE, PROFILE_SOURCE, LOOP_MEMORY_SOURCE}

    def test_with_nothing_to_inject_there_are_no_rows_and_that_is_correct(
        self, live_db, fake_manager, no_save, project_root, monkeypatch
    ):
        """An injector with nothing to say writes nothing -- not an empty row.

        A row per configured injector regardless of content would make the
        coverage report say "all four ran" on a machine where they had no data,
        which is the fabricated coverage ``skipped_empty`` exists to keep out of
        the log. Note what is NOT requested here: ``retrieved_memory``, so the
        loop's own search runs for real against a store holding nothing.
        """
        monkeypatch.setenv("ICDEV_SAG_PROJECT_CONTEXT", "0")
        monkeypatch.setenv("ICDEV_SAG_GOALS", "0")
        monkeypatch.setenv("ICDEV_SAG_PROFILE_MEMORY", "0")
        turn = _run_one_turn()

        assert not [b for b in turn.injected.values() if b]
        assert turn.events() == []
        # The turn still happened: absence of injections is not absence of a log.
        assert turn.result.done is True
        assert [
            e.event_type for e in read_session(turn.ctx_id)
        ].count("turn_start") == 1

    def test_the_recorder_counted_one_success_per_injection_and_no_failures(
        self, live_db, fake_manager, no_save, all_injectors_active
    ):
        """``record_injection`` swallows everything, so silence is not success.

        The counters are the only thing separating "recorded" from "failed and
        was swallowed" inside one process. A build where every write raised
        would still produce a coherent-looking empty log; it would not produce
        ``failed == 0`` alongside four ``recorded``.
        """
        context_events.reset_stats()
        _run_one_turn()
        stats = context_events.stats()
        assert stats["failed"] == 0, stats
        assert stats["last_error"] == ""
        assert stats["recorded"] == len(SOURCES), stats


# ---------------------------------------------------------------------------
# 3. The new hook cannot become a new way for a turn to fail
# ---------------------------------------------------------------------------
class TestTheAnnouncementCannotEndATurn:
    def test_a_hook_that_raises_does_not_stop_the_block_reaching_the_model(
        self, live_db, fake_manager, no_save, all_injectors_active, monkeypatch
    ):
        """An audit sink falling over must not turn into a refusal to answer.

        The same posture ``record_injection`` takes one layer up, restated here
        because ``agent_loop`` does not import that module and so inherits none
        of its guarantees. The announcement happens AFTER the append, so a
        broken hook costs the log a row and costs the turn nothing.
        """
        def boom(source, text, detail):
            raise RuntimeError("the audit sink is down")

        monkeypatch.setattr(AgentRuntime, "_record_loop_injection", boom)
        turn = _run_one_turn()

        assert turn.result.done is True
        assert MEMORY_MARKER in turn.prompt
        named = {e["source"] for e in turn.events(include_body=False)}
        assert LOOP_MEMORY_SOURCE not in named
        # The other three are unaffected: one broken sink is not four.
        assert named == set(PREAMBLE_ATTRS)

    def test_no_hook_means_no_announcement_and_an_unchanged_turn(
        self, live_db, retrieved_memory
    ):
        """``run_agent_loop`` called WITHOUT the hook behaves exactly as before.

        Every other caller of the loop passes no ``on_context_injection``, so
        the default path is the one that must not have moved.
        """
        from icdev.tools.llm.agent_loop import run_agent_loop

        router = _CapturingRouter()
        result = run_agent_loop(
            router,
            system_prompt="base instructions",
            user_prompt=PROMPT,
            tools=[],
            tool_handlers={},
        )
        assert result.final_content == "the answer"
        assert MEMORY_MARKER in router.calls[0].system_prompt

    def test_the_loop_announces_what_it_appended_and_only_when_it_appended(
        self, live_db, retrieved_memory, monkeypatch
    ):
        """The hook's contract, at the loop's own seam.

        Two halves, and the second is the one that keeps the log honest: an
        announcement with nothing behind it would put a row in the log for a
        block that never existed.
        """
        from icdev.tools.llm.agent_loop import run_agent_loop

        seen: list[tuple[str, str, dict]] = []
        router = _CapturingRouter()
        run_agent_loop(
            router,
            system_prompt="base instructions",
            user_prompt=PROMPT,
            tools=[],
            tool_handlers={},
            on_context_injection=lambda s, t, d: seen.append((s, t, d)),
        )
        assert [s for s, _t, _d in seen] == [LOOP_MEMORY_SOURCE]
        assert MEMORY_MARKER in seen[0][1]
        assert router.calls[0].system_prompt.endswith(seen[0][1])

        # Nothing retrieved -> nothing appended -> nothing announced.
        _patch_every_alias(monkeypatch, "memory.hybrid_search", "search",
                           lambda *a, **k: [])
        quiet: list[tuple[str, str, dict]] = []
        router2 = _CapturingRouter()
        run_agent_loop(
            router2,
            system_prompt="base instructions",
            user_prompt=PROMPT,
            tools=[],
            tool_handlers={},
            on_context_injection=lambda s, t, d: quiet.append((s, t, d)),
        )
        assert quiet == []
        assert router2.calls[0].system_prompt == "base instructions"
