# CUI // SP-CTI
"""The launcher must fully supervise every service it defines.

A service is only autonomous if all three of these hold:

  1. it is STARTED in ``main()``
  2. it is RESTARTED when the monitor loop sees it exit
  3. it is TERMINATED on shutdown

Missing (1) is how ``pr_watcher`` came to be defined-but-never-started: the
Kanban scheduler kept building and opening PRs while nothing merged them, so
tasks piled up in ``pr_opened`` until the respawn guard withheld everything.
Board throughput went to zero for four days and presented as "the dispatcher
stopped working" — while the dispatcher was idle and correct.

Missing (2) is the same outage with a slower fuse: the service runs until its
first crash and is never seen again.

These are structural assertions over the launcher's AST rather than a live
process test, because starting the real services would bind ports 5050/5100 and
fight the running instance.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "tools" / "genesis" / "launcher.py"


def _module() -> ast.Module:
    return ast.parse(LAUNCHER.read_text(encoding="utf-8"))


def _main_fn(mod: ast.Module) -> ast.FunctionDef:
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("launcher.py defines no main()")


def _service_starters(mod: ast.Module) -> set[str]:
    """Every ``_start_*`` helper defined at module scope."""
    return {
        n.name
        for n in mod.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("_start_")
    }


def _calls_in(node: ast.AST) -> list[str]:
    return [
        c.func.id
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    ]


def _startup_calls(main_fn: ast.FunctionDef) -> list[str]:
    """Calls in the startup sequence only.

    Deliberately excludes the ``try``/``while`` monitor block: a service that is
    *only* referenced from the restart branch is never started in the first
    place, and counting those call sites would make this test vacuous.
    """
    out: list[str] = []
    for stmt in main_fn.body:
        if isinstance(stmt, (ast.Try, ast.While)):
            continue
        out.extend(_calls_in(stmt))
    return out


def _shutdown_lists(main_fn: ast.FunctionDef) -> list[set[str]]:
    """Every ``for proc in [...]`` process list in main()."""
    return [
        {e.id for e in node.iter.elts if isinstance(e, ast.Name)}
        for node in ast.walk(main_fn)
        if isinstance(node, ast.For) and isinstance(node.iter, ast.List)
    ]


def _proc_vars(main_fn: ast.FunctionDef) -> dict[str, str]:
    """Map ``_start_x`` -> the process variable its result is bound to.

    Matches ``proc_var, log_var = _start_x()``.
    """
    out: dict[str, str] = {}
    for node in ast.walk(main_fn):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            continue
        if not call.func.id.startswith("_start_"):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Tuple) and target.elts:
            first = target.elts[0]
            if isinstance(first, ast.Name):
                out.setdefault(call.func.id, first.id)
    return out


STARTERS = sorted(_service_starters(_module()))


def test_launcher_defines_services():
    """Guard the guard: if this list ever empties, the tests below go vacuous."""
    assert len(STARTERS) >= 5, STARTERS


@pytest.mark.parametrize("starter", STARTERS)
def test_service_is_started_in_main(starter):
    main_fn = _main_fn(_module())
    assert starter in _startup_calls(main_fn), (
        f"{starter}() is never called in main()'s startup sequence — the "
        f"service never runs under the launcher. (Being referenced only from "
        f"the monitor loop's restart branch does not count: there is nothing "
        f"to restart.)"
    )


@pytest.mark.parametrize("starter", STARTERS)
def test_service_is_restarted_by_the_monitor_loop(starter):
    """A second call site is the restart branch inside the ``while True`` loop."""
    main_fn = _main_fn(_module())
    loops = [n for n in ast.walk(main_fn) if isinstance(n, ast.While)]
    assert loops, "main() has no monitor loop"
    restarted = {c for loop in loops for c in _calls_in(loop)}
    assert starter in restarted, (
        f"{starter}() is never called inside the monitor loop — the service "
        f"will not be restarted after it crashes."
    )


@pytest.mark.parametrize("starter", STARTERS)
def test_service_process_is_polled_for_liveness(starter):
    main_fn = _main_fn(_module())
    var = _proc_vars(main_fn).get(starter)
    assert var, f"could not resolve the process variable bound from {starter}()"
    polled = {
        node.func.value.id
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "poll"
        and isinstance(node.func.value, ast.Name)
    }
    assert var in polled, (
        f"{var} (from {starter}) is never .poll()ed — its death goes unnoticed."
    )


@pytest.mark.parametrize("starter", STARTERS)
def test_service_is_terminated_on_shutdown(starter):
    """Every started process must appear in EVERY shutdown process list.

    Checked per-list rather than unioned: shutdown is terminate-then-wait, and a
    process missing from either list is still leaked or still un-reaped.
    """
    main_fn = _main_fn(_module())
    var = _proc_vars(main_fn).get(starter)
    assert var, f"could not resolve the process variable bound from {starter}()"
    lists = _shutdown_lists(main_fn)
    assert lists, "main() has no shutdown process list"
    missing = [i for i, names in enumerate(lists) if var not in names]
    assert not missing, (
        f"{var} (from {starter}) is absent from shutdown process list(s) "
        f"{missing} of {len(lists)} — it survives the launcher and blocks the "
        f"next start."
    )


def test_postgres_readiness_is_awaited_before_services_start():
    """PG-dependent services must not boot into a recovering database.

    With ICDEV_PG_NO_FALLBACK=true a service that connects during recovery dies
    on "the database system is starting up" and crash-loops until PG settles.
    """
    mod = _module()
    main_fn = _main_fn(mod)
    names = [n.name for n in mod.body if isinstance(n, ast.FunctionDef)]
    assert "_wait_for_postgres" in names, "launcher defines no PG readiness wait"

    body_calls = [
        (i, c.func.id)
        for i, stmt in enumerate(main_fn.body)
        for c in ast.walk(stmt)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    ]
    wait_at = [i for i, n in body_calls if n == "_wait_for_postgres"]
    start_at = [i for i, n in body_calls if n.startswith("_start_")]
    assert wait_at, "_wait_for_postgres() is defined but never called in main()"
    assert min(wait_at) < min(start_at), (
        "_wait_for_postgres() must run before the first service starts"
    )
