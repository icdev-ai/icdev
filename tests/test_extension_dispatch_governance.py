# CUI // SP-CTI
"""``args/extension_config.yaml`` declared ten hook-point blocks nothing read.

Two defects, one layer apart, both of the shape this platform ships most often —
a declaration with no consumer:

1. **The per-point config was inert.** ``hook_points:`` carries ``enabled``,
   ``allow_modification``, ``max_handlers`` and ``timeout_ms`` for all ten
   points. ``ExtensionManager.dispatch`` read ``extensions.enabled`` and two
   ``extensions.safety`` keys and nothing else, so the only working kill switch
   was the global one — which also kills the nine chat handlers that are in use.
   An operator standing one point down would have edited a key with no effect
   and believed the point was off.

2. **Nothing counted a dispatch.** ``catch_handler_exceptions`` defaults true, so
   a handler that raises is logged and forgotten; and with no count anywhere,
   ``capability_consumption.py`` could not answer whether the seam was consumed
   at all. That is the same measurement gap that let a prior TOOL_EXECUTE_AFTER
   wiring pass the wrong kwargs and have "no handlers ever fire" go unnoticed.

The fail-soft contract is preserved on purpose and pinned here: a raising
handler must still not break the caller. What changes is that the failure is now
VISIBLE — an ``error`` row on the ``extension`` surface of
``runtime_invocations`` naming the handler, plus an in-process counter.
"""
from __future__ import annotations

import importlib
import sqlite3
import time

import pytest

from tools.extensions.extension_manager import ExtensionManager, ExtensionPoint
from tools.observability import invocation_recorder as R


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(points=None, safety=None, enabled=True) -> dict:
    return {
        "extensions": {
            "enabled": enabled,
            "hook_points": points or {},
            "safety": safety or {},
        }
    }


def _manager(points=None, safety=None, enabled=True) -> ExtensionManager:
    """A manager with NO builtins loaded.

    ``load_builtins=False`` matters for more than speed: the nine chat builtins
    import RAG, Bayesian learning and the genesis status reader, and a unit test
    of the dispatch contract must not depend on any of them importing cleanly.
    """
    return ExtensionManager(
        config=_config(points, safety, enabled), load_builtins=False
    )


def _appender(log, label, returns=None, sleep=0.0):
    def handler(context):
        if sleep:
            time.sleep(sleep)
        log.append(label)
        return returns

    handler.__name__ = f"handler_{label}"
    return handler


@pytest.fixture()
def inv_db(tmp_path, monkeypatch):
    """A throwaway SQLite ``runtime_invocations`` the recorder writes into."""
    db = tmp_path / "inv.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE runtime_invocations ("
        " id TEXT PRIMARY KEY, surface TEXT NOT NULL, name TEXT NOT NULL,"
        " session_id TEXT, project_id TEXT, parent_id TEXT,"
        " started_at TEXT NOT NULL, completed_at TEXT, duration_ms INTEGER,"
        " status TEXT NOT NULL DEFAULT 'running', error_class TEXT,"
        " error_message TEXT, arg_keys TEXT, correlation_id TEXT,"
        " arg_values TEXT, result_preview TEXT, classification TEXT,"
        " created_at TEXT)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.delenv("ICDEV_OBS_INVOCATIONS", raising=False)
    monkeypatch.setattr(R, "_table_missing", False, raising=False)
    # Probed once per process and cached; a sibling test may have cached the
    # column set of a DIFFERENT table.
    monkeypatch.setattr(R, "_optional_columns", None, raising=False)
    import tools.db.storage as storage

    monkeypatch.setattr(storage, "DB_PATH", str(db), raising=False)
    monkeypatch.setattr(storage, "_BACKEND", "sqlite", raising=False)
    return db


def _rows(db):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT surface, name, status, error_class, error_message, arg_keys "
                "FROM runtime_invocations ORDER BY started_at"
            ).fetchall()
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. enabled — the per-point kill switch
# ---------------------------------------------------------------------------


def test_a_point_disabled_in_config_runs_none_of_its_handlers():
    """The key an operator would reach for to stand ONE hook point down."""
    log = []
    mgr = _manager({"tool_execute_before": {"enabled": False}})
    mgr.register(ExtensionPoint.TOOL_EXECUTE_BEFORE, _appender(log, "a"), name="a")

    out = mgr.dispatch(ExtensionPoint.TOOL_EXECUTE_BEFORE, {"tool_name": "x"})

    assert log == []
    assert out == {"tool_name": "x"}


def test_the_kill_switch_is_per_point_and_leaves_its_siblings_running():
    """Standing down the tool path must not take the chat hooks with it.

    That was the whole complaint: the only switch that worked was global.
    """
    log = []
    mgr = _manager({"tool_execute_before": {"enabled": False}})
    mgr.register(ExtensionPoint.TOOL_EXECUTE_BEFORE, _appender(log, "tool"), name="t")
    mgr.register(ExtensionPoint.CHAT_MESSAGE_AFTER, _appender(log, "chat"), name="c")

    mgr.dispatch(ExtensionPoint.TOOL_EXECUTE_BEFORE, {})
    mgr.dispatch(ExtensionPoint.CHAT_MESSAGE_AFTER, {})

    assert log == ["chat"]


def test_a_suppressed_dispatch_is_counted_in_process(inv_db):
    """The kill switch is total — including telemetry — but not silent.

    A disabled point writes no row: an operator who turned a hot-path hook off
    must not keep paying two SQL statements per call for it. The fact is kept in
    the in-process counters instead, so "off" stays distinguishable from
    "nobody calls it" without a database round trip.
    """
    mgr = _manager({"agent_start": {"enabled": False}})
    mgr.register(ExtensionPoint.AGENT_START, _appender([], "a"), name="a")

    mgr.dispatch(ExtensionPoint.AGENT_START, {})

    assert _rows(inv_db) == []
    stats = mgr.stats(ExtensionPoint.AGENT_START)
    assert stats["suppressed"] == 1
    assert stats["dispatches"] == 0
    assert stats["handlers_run"] == 0


# ---------------------------------------------------------------------------
# 2. max_handlers — the bound
# ---------------------------------------------------------------------------


def test_max_handlers_bounds_how_many_handlers_one_dispatch_runs():
    log = []
    mgr = _manager({"chat_message_after": {"max_handlers": 2}})
    for i in range(4):
        mgr.register(
            ExtensionPoint.CHAT_MESSAGE_AFTER,
            _appender(log, f"h{i}"),
            name=f"h{i}",
            priority=i * 10,
        )

    mgr.dispatch(ExtensionPoint.CHAT_MESSAGE_AFTER, {})

    # Priority order decides who survives the cap, so the outcome is stable
    # rather than dependent on registration order.
    assert log == ["h0", "h1"]
    assert mgr.stats(ExtensionPoint.CHAT_MESSAGE_AFTER)["handlers_dropped"] == 2


def test_the_bound_falls_back_to_the_global_safety_limit_when_a_point_omits_it():
    """``safety.max_handlers_per_point`` was declared and unread too."""
    log = []
    mgr = _manager({}, safety={"max_handlers_per_point": 1})
    for i in range(3):
        mgr.register(
            ExtensionPoint.MEMORY_SAVE_AFTER,
            _appender(log, f"h{i}"),
            name=f"h{i}",
            priority=i * 10,
        )

    mgr.dispatch(ExtensionPoint.MEMORY_SAVE_AFTER, {})

    assert log == ["h0"]


# ---------------------------------------------------------------------------
# 3. timeout_ms — the per-point budget
# ---------------------------------------------------------------------------


def test_the_per_point_budget_stops_the_chain_inside_the_global_ceiling():
    """5000ms per point is the tighter bound; 30000ms global must not win."""
    log = []
    mgr = _manager(
        {"agent_end": {"timeout_ms": 10}},
        safety={"max_total_handler_time_ms": 30_000},
    )
    mgr.register(
        ExtensionPoint.AGENT_END, _appender(log, "slow", sleep=0.05), name="slow",
        priority=10,
    )
    mgr.register(ExtensionPoint.AGENT_END, _appender(log, "after"), name="after",
                 priority=20)

    mgr.dispatch(ExtensionPoint.AGENT_END, {})

    assert log == ["slow"], "the per-point timeout did not bound the chain"
    assert mgr.stats(ExtensionPoint.AGENT_END)["timeouts"] == 1


def test_the_global_ceiling_still_wins_when_it_is_the_tighter_of_the_two():
    """min() of the two, not "the per-point one replaces the global one"."""
    log = []
    mgr = _manager(
        {"agent_end": {"timeout_ms": 30_000}},
        safety={"max_total_handler_time_ms": 10},
    )
    mgr.register(ExtensionPoint.AGENT_END, _appender(log, "slow", sleep=0.05),
                 name="slow", priority=10)
    mgr.register(ExtensionPoint.AGENT_END, _appender(log, "after"), name="after",
                 priority=20)

    mgr.dispatch(ExtensionPoint.AGENT_END, {})

    assert log == ["slow"]


# ---------------------------------------------------------------------------
# 4. allow_modification — a CEILING on what a handler may declare
# ---------------------------------------------------------------------------


def test_a_point_that_forbids_modification_overrides_a_handler_that_declares_it():
    """The per-point value is a ceiling, not a duplicate of the per-handler one.

    ``tool_execute_after`` ships ``allow_modification: false`` with the comment
    "Post-hooks observe only by default". Before this, a handler could register
    ``allow_modification=True`` at that point and rewrite the context anyway.
    """
    mgr = _manager({"tool_execute_after": {"allow_modification": False}})
    mgr.register(
        ExtensionPoint.TOOL_EXECUTE_AFTER,
        lambda ctx: {"replaced": True},
        name="rewriter",
        allow_modification=True,
    )

    out = mgr.dispatch(ExtensionPoint.TOOL_EXECUTE_AFTER, {"original": 1})

    assert out == {"original": 1}
    assert mgr.stats(ExtensionPoint.TOOL_EXECUTE_AFTER)["modifications_suppressed"] == 1


def test_a_point_that_permits_modification_does_not_grant_it_to_a_handler():
    """A ceiling raises nothing. Observational handlers stay observational."""
    mgr = _manager({"chat_message_after": {"allow_modification": True}})
    mgr.register(
        ExtensionPoint.CHAT_MESSAGE_AFTER,
        lambda ctx: {"replaced": True},
        name="observer",
        allow_modification=False,
    )

    out = mgr.dispatch(ExtensionPoint.CHAT_MESSAGE_AFTER, {"original": 1})

    assert out == {"original": 1}


def test_a_behavioural_handler_at_a_permissive_point_still_modifies():
    """The positive control: enforcement must not break the working path."""
    mgr = _manager({"chat_message_after": {"allow_modification": True}})
    mgr.register(
        ExtensionPoint.CHAT_MESSAGE_AFTER,
        lambda ctx: {**ctx, "marked": True},
        name="marker",
        allow_modification=True,
    )

    assert mgr.dispatch(ExtensionPoint.CHAT_MESSAGE_AFTER, {"original": 1}) == {
        "original": 1,
        "marked": True,
    }


def test_an_undeclared_point_keeps_the_permissive_default():
    """A config with no block for a point must not silently strip modification."""
    mgr = _manager({})
    mgr.register(
        ExtensionPoint.MEMORY_SAVE_BEFORE,
        lambda ctx: {**ctx, "marked": True},
        name="marker",
        allow_modification=True,
    )

    assert mgr.dispatch(ExtensionPoint.MEMORY_SAVE_BEFORE, {"a": 1})["marked"] is True


def test_point_config_resolves_every_key_the_yaml_declares():
    """All four keys resolve for every point — none may be left unread."""
    mgr = _manager({})
    for point in ExtensionPoint:
        resolved = mgr.point_config(point)
        assert set(resolved) == {
            "enabled", "allow_modification", "max_handlers", "timeout_ms",
        }, point.value


def test_the_shipped_config_declares_no_key_dispatch_ignores():
    """Every key under ``hook_points.<point>`` must be one dispatch consumes.

    The defect this file exists for is a declared key with no consumer. A new
    one added to the YAML has to be wired or removed, not left to rot.
    """
    # A hard import, not importorskip: pyyaml is in requirements.txt and
    # extension_manager itself imports it to read this very file. Skipping here
    # would let the check that no key goes unread quietly stop running.
    import yaml
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    raw = yaml.safe_load(
        (repo / "args" / "extension_config.yaml").read_text(encoding="utf-8")
    )
    points = (raw.get("extensions") or {}).get("hook_points") or {}
    assert points, "hook_points block vanished"

    consumed = {"enabled", "allow_modification", "max_handlers", "timeout_ms"}
    for name, block in points.items():
        assert set(block) <= consumed, f"{name} declares unread key(s)"


# ---------------------------------------------------------------------------
# 5. Telemetry — dispatch counts and handler failures
# ---------------------------------------------------------------------------


def test_every_dispatch_records_one_row_on_the_extension_surface(inv_db):
    mgr = _manager({})
    mgr.register(ExtensionPoint.CHAT_MESSAGE_BEFORE, _appender([], "a"), name="a")

    mgr.dispatch(ExtensionPoint.CHAT_MESSAGE_BEFORE, {"content": "hello"})
    mgr.dispatch(ExtensionPoint.CHAT_MESSAGE_BEFORE, {"content": "again"})

    rows = _rows(inv_db)
    assert len(rows) == 2
    assert {r["surface"] for r in rows} == {R.SURFACE_EXTENSION}
    assert {r["name"] for r in rows} == {"chat_message_before"}
    assert all(r["status"] == "ok" for r in rows)


def test_a_dispatch_with_no_handlers_is_still_recorded(inv_db):
    """The load-bearing case for measurement.

    Eight of the ten points have no handler registered. "Dispatched, nothing
    listening" and "never dispatched" are different defects with different
    fixes, and only a row for the empty dispatch tells them apart.
    """
    mgr = _manager({})

    mgr.dispatch(ExtensionPoint.COMPLIANCE_CHECK_BEFORE, {})

    rows = _rows(inv_db)
    assert [r["name"] for r in rows] == ["compliance_check_before"]


def test_the_row_stores_context_key_names_and_never_their_values(inv_db):
    """Same rule the recorder applies to MCP arguments: a chat body is CUI."""
    mgr = _manager({})

    mgr.dispatch(
        ExtensionPoint.CHAT_MESSAGE_AFTER,
        {"content": "patient SSN 123-45-6789", "context_id": "c1"},
    )

    row = _rows(inv_db)[0]
    assert "content" in row["arg_keys"]
    assert "123-45-6789" not in (row["arg_keys"] or "")


def test_a_raising_handler_is_recorded_as_an_error_and_still_fails_soft(inv_db):
    """The headline. The exception stays caught; the FAILURE stops being silent."""
    log = []

    def boom(context):
        raise RuntimeError("handler is broken")

    boom.__name__ = "boom"

    mgr = _manager({})
    mgr.register(ExtensionPoint.CHAT_MESSAGE_AFTER, boom, name="boom", priority=10)
    mgr.register(ExtensionPoint.CHAT_MESSAGE_AFTER, _appender(log, "after"),
                 name="after", priority=20)

    out = mgr.dispatch(ExtensionPoint.CHAT_MESSAGE_AFTER, {"content": "hi"})

    # fail-soft: the caller is unharmed and the chain continues
    assert out == {"content": "hi"}
    assert log == ["after"]

    row = _rows(inv_db)[0]
    assert row["status"] == "error"
    assert row["error_class"] == "RuntimeError"
    assert "boom" in row["error_message"]
    assert mgr.stats(ExtensionPoint.CHAT_MESSAGE_AFTER)["handler_failures"] == 1


def test_catch_handler_exceptions_false_still_propagates(inv_db):
    """The opt-out is unchanged — and the failure is recorded on the way out."""
    def boom(context):
        raise ValueError("nope")

    mgr = _manager({}, safety={"catch_handler_exceptions": False})
    mgr.register(ExtensionPoint.AGENT_START, boom, name="boom")

    with pytest.raises(ValueError):
        mgr.dispatch(ExtensionPoint.AGENT_START, {})

    assert _rows(inv_db)[0]["status"] == "error"


def test_telemetry_that_itself_fails_never_breaks_the_dispatch(monkeypatch, inv_db):
    """Rule 1 of the recorder, re-asserted at this call site.

    This hook now sits on the chat path and (after hcx-live-01) the tool path.
    A telemetry bug that broke either would be strictly worse than no telemetry.
    """
    # NOT `import tools.extensions.extension_manager as em_mod`: the package
    # __init__ re-exports the SINGLETON under that name, so the plain import
    # binds the instance and the patch lands on the wrong object.
    em_mod = importlib.import_module("tools.extensions.extension_manager")

    def exploding_record(*args, **kwargs):
        raise RuntimeError("telemetry is down")

    monkeypatch.setattr(em_mod, "_record_dispatch", exploding_record)

    log = []
    mgr = _manager({})
    mgr.register(ExtensionPoint.CHAT_MESSAGE_AFTER, _appender(log, "a"), name="a")

    assert mgr.dispatch(ExtensionPoint.CHAT_MESSAGE_AFTER, {"x": 1}) == {"x": 1}
    assert log == ["a"]


def test_a_raising_handler_with_broken_telemetry_still_fails_soft(monkeypatch, inv_db):
    """Both failures at once — the annotation path runs against the null span.

    Without this, the ``_NullSpan`` fallback is only ever exercised on the happy
    path, and the branch that writes the failure onto it is untested exactly
    when both things are wrong.
    """
    em_mod = importlib.import_module("tools.extensions.extension_manager")
    monkeypatch.setattr(
        em_mod, "_record_dispatch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("telemetry is down")),
    )

    def boom(context):
        raise RuntimeError("handler is broken")

    mgr = _manager({})
    mgr.register(ExtensionPoint.AGENT_END, boom, name="boom")

    assert mgr.dispatch(ExtensionPoint.AGENT_END, {"x": 1}) == {"x": 1}
    assert mgr.stats(ExtensionPoint.AGENT_END)["handler_failures"] == 1
    assert "boom" in mgr.stats(ExtensionPoint.AGENT_END)["last_error"]


def test_extension_is_a_declared_surface_with_a_filter_button():
    """A surface the monitoring panel cannot filter to is one nobody will read."""
    from pathlib import Path
    import re

    assert R.SURFACE_EXTENSION in R.SURFACES

    repo = Path(__file__).resolve().parent.parent
    for tree in ("tools", "icdev/tools"):
        template = (
            repo / tree / "dashboard" / "templates" / "monitoring"
            / "_runtime_performance.html"
        )
        buttons = set(re.findall(r'data-surface="([^"]*)"', template.read_text(encoding="utf-8")))
        assert R.SURFACE_EXTENSION in buttons, template


# ---------------------------------------------------------------------------
# 6. What the new telemetry found on its first run
# ---------------------------------------------------------------------------


def test_every_registered_handler_accepts_the_one_dict_the_contract_passes():
    """``handler(context) -> dict | None``. A second required argument is fatal.

    ``081_build_kanban_sync`` declared ``handle_chat_message_after(event, ctx)``
    and raised ``TypeError`` on every chat message the platform ever served —
    registered, catalogued, ``enabled: True``, and structurally incapable of
    running. The swallow hid it and nothing counted it; the first dispatch under
    the new telemetry reported it as an ``error`` row naming the handler.

    Asserted over the REAL singleton rather than by parsing the files, because
    what matters is the callable dispatch will actually invoke.
    """
    import inspect

    from tools.extensions.extension_manager import extension_manager

    positional = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    bad = []
    # The registry is private; there is no public accessor that hands back the
    # callable, and the callable is the thing under test.
    for handlers in extension_manager._handlers.values():
        for ext in handlers:
            try:
                params = inspect.signature(ext.handler).parameters.values()
            except (TypeError, ValueError):  # a builtin/C callable — not ours
                continue
            required = [
                p for p in params
                if p.default is inspect.Parameter.empty and p.kind in positional
            ]
            if len(required) != 1:
                bad.append(f"{ext.hook_point.value}.{ext.name} takes {len(required)}")

    assert bad == [], f"handler(s) dispatch can never call: {bad}"


# ---------------------------------------------------------------------------
# 7. The mirror
# ---------------------------------------------------------------------------


def test_the_icdev_mirror_of_the_manager_is_byte_identical():
    """Two distinct module objects hold two distinct singletons.

    They must at least agree on the dispatch contract, or which one a caller
    imported decides whether a kill switch works.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    left = repo / "tools" / "extensions" / "extension_manager.py"
    right = repo / "icdev" / "tools" / "extensions" / "extension_manager.py"
    assert left.read_bytes() == right.read_bytes()


def test_the_icdev_mirror_of_the_recorder_declares_the_same_surfaces():
    icdev_recorder = importlib.import_module("icdev.tools.observability.invocation_recorder")
    assert icdev_recorder.SURFACES == R.SURFACES
