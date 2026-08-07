# [TEMPLATE: CUI // SP-CTI]
"""Every declared agent_* audit event type must have a real emit site.

VALID_EVENT_TYPES declared sixteen ``agent_*`` types. Measured on the live
board 2026-08-02, three had ever been written and thirteen had not, so the
schema advertised sixteen kinds of agent observability and delivered three.

A declared-but-never-emitted event type is worse than an absent one. Querying
``information_schema`` or the CHECK constraint reads as coverage: the type is
there, so someone building a dashboard, an alert rule, or a compliance
narrative assumes rows will arrive. They never do, and nothing fails --- the
absence looks like "no such events happened yet" rather than "no code can ever
produce this".

Of the thirteen, ten turned out to be wired all along and simply cold: veto,
escalation, mailbox, agent-memory and skill-router all emit, but this board
rarely exercises the multi-agent surface. The genuinely dead ones were the four
``agent_execution_*`` types, which had zero emit sites anywhere in the tree.
(``agent_execution_completed`` had exactly one row, hand-written in June 2026,
which is why it did not appear in the thirteen --- a single stale row made an
equally dead declaration look alive.)

This module is the gate that keeps the distinction honest. It fails if any
declared ``agent_*`` type has no emit site, so the next person to add one has to
add the writer in the same change.

Scope note: reachable is not the same as exercised. This asserts that code
exists which writes the type, not that it has run. Proving the latter needs the
live board, and a test that queried it would pass or fail on how busy the week
was.
"""

import ast
import re
from functools import lru_cache
from pathlib import Path

import pytest

from tools.audit.audit_logger import VALID_EVENT_TYPES

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"

AGENT_TYPE_RE = re.compile(r"^agent_[a-z0-9_]+$")

# Functions that forward to audit_logger.log_event. Several modules wrap it
# under a local name, and a few take event_type positionally.
AUDIT_FUNCS = {
    "log_event",
    "audit_log_event",
    "_audit",
    "_audit_log",
    "log_audit_event",
    "_audit_agent_execution",
}

# The declaration itself, and readers that name event types to query them.
# A SELECT is not an emit site; counting one would make the gate vacuous for
# exactly the types most likely to be dead.
NOT_EMIT_SITES = {
    Path("tools/audit/audit_logger.py"),
}


def _agent_constants(node) -> set:
    """Every agent_* string constant in an expression subtree."""
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if AGENT_TYPE_RE.match(child.value):
                found.add(child.value)
    return found


def _emit_sites_in(path: Path) -> dict:
    """Map agent_* event type -> [line numbers] for emit sites in one file.

    Three shapes occur in this tree and all three have to be understood, because
    each is the only shape carrying at least one of the sixteen types:

    * ``log_event(event_type="agent_veto_issued", ...)`` --- the common case
    * ``event_type="agent_task_completed" if ok else "agent_task_failed"`` ---
      tools/browser/scope.py; a scan for a literal kwarg value misses the
      second branch, which is the branch that has never fired
    * ``execute("INSERT INTO audit_trail ...", (now, "agent_task_submitted",
      ...))`` --- tools/kanban/state_machine.py writes the row directly, so the
      type is a positional parameter with no kwarg anywhere near it
    """
    sites: dict = {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:  # pragma: no cover - a parse failure is another test's problem
        return sites

    def _record(types, lineno):
        for event_type in types:
            sites.setdefault(event_type, []).append(lineno)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Shape 1 + 2: an event_type= keyword, ternaries included.
        for kw in node.keywords:
            if kw.arg == "event_type":
                _record(_agent_constants(kw.value), node.lineno)

        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)

        # event_type passed positionally to a known audit writer.
        if name in AUDIT_FUNCS and node.args:
            _record(_agent_constants(node.args[0]), node.lineno)

        # Shape 3: a direct INSERT INTO audit_trail; the type is in the params.
        if node.args and isinstance(node.args[0], ast.Constant):
            sql = node.args[0].value
            if isinstance(sql, str) and "INSERT INTO audit_trail" in sql:
                for arg in node.args[1:]:
                    _record(_agent_constants(arg), node.lineno)

    return sites


@lru_cache(maxsize=1)
def _all_emit_sites() -> dict:
    """Map agent_* event type -> ["path:line", ...] across tools/.

    Cached: the scan parses every module under tools/, and the parametrised
    gate below asks for it once per declared type.
    """
    all_sites: dict = {}
    for path in sorted(TOOLS_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        if rel in NOT_EMIT_SITES or "__pycache__" in rel.parts:
            continue
        for event_type, linenos in _emit_sites_in(path).items():
            for lineno in linenos:
                all_sites.setdefault(event_type, []).append(f"{rel.as_posix()}:{lineno}")
    return all_sites


DECLARED_AGENT_TYPES = sorted(t for t in VALID_EVENT_TYPES if AGENT_TYPE_RE.match(t))


class TestDeclaredAgentTypesAreReachable:
    def test_the_declaration_still_has_agent_types(self):
        """Guard the fixture: an empty list would make every test below pass."""
        assert len(DECLARED_AGENT_TYPES) >= 16, (
            f"expected the sixteen declared agent_* types, found {DECLARED_AGENT_TYPES}"
        )

    @pytest.mark.parametrize("event_type", DECLARED_AGENT_TYPES)
    def test_declared_type_has_an_emit_site(self, event_type):
        sites = _all_emit_sites().get(event_type, [])
        assert sites, (
            f"'{event_type}' is declared in VALID_EVENT_TYPES but nothing under "
            "tools/ writes it. A declared type with no writer reads as coverage "
            "when querying the schema and delivers none.\n"
            "Either wire an emit site, or remove the declaration and rebuild the "
            "CHECK constraint with a migration that calls "
            "tools.audit.audit_logger.rebuild_event_type_constraint(conn)."
        )

    def test_scanner_finds_each_emit_shape(self):
        """Guard the scanner: a no-op scan makes the gate above vacuous.

        One assertion per shape the scanner has to understand. If a refactor
        breaks the ternary walk or the raw-INSERT walk, the type it uniquely
        carries stops being found and this fails loudly, rather than the gate
        quietly passing because it found nothing to check.
        """
        sites = _all_emit_sites()

        # Shape 1: plain event_type= kwarg.
        assert any(
            s.startswith("tools/agent/collaboration.py")
            for s in sites.get("agent_veto_issued", [])
        ), "kwarg scan stopped finding collaboration.py's veto emit"

        # Shape 2: the else-branch of a ternary.
        assert any(
            s.startswith("tools/browser/scope.py")
            for s in sites.get("agent_task_failed", [])
        ), "ternary scan stopped finding scope.py's deny-branch emit"

        # Shape 3: a literal in an INSERT INTO audit_trail parameter tuple.
        assert any(
            s.startswith("tools/kanban/state_machine.py")
            for s in sites.get("agent_task_submitted", [])
        ), "raw-INSERT scan stopped finding state_machine.py's transition emit"

    def test_a_select_is_not_counted_as_an_emit_site(self):
        """Reading a type is not writing it.

        tools/oracle/lenses/lens_workflow_patterns.py names agent_task_failed
        and agent_task_completed to query them. Counting a reader would let a
        type that only appears in dashboards look reachable, which is the exact
        illusion this module exists to break.
        """
        sites = _all_emit_sites()
        for event_type in ("agent_task_failed", "agent_task_completed"):
            assert not any(
                "oracle/lenses" in s for s in sites.get(event_type, [])
            ), f"a SELECT on {event_type} was counted as an emit site"
