# CUI // SP-CTI
"""A work task must not be seeded with a manual-gate sentinel's id (kax-exec-04).

``tsg-gate-01`` was work — "decide the CI allowlist policy" — carrying an id that
contains ``-gate-``. ``tools/kanban/gates.py::is_manual_gate`` returns True for
any such id, so ``promote_backlog_to_scheduled`` filtered it out and it was
UNDISPATCHABLE from the moment it was seeded. It sat in backlog from 02:22 while
the board idled with three free dispatch slots and it was one of only four
eligible tasks. Nothing went red: a task nobody can dispatch looks exactly like a
task nobody has got to yet.

The predicate is NOT the bug. Its wide match is a security property: ``hgx-gate-01``
was a card's SECOND gate, matching only on the ``-gate-00`` seeding convention
returned False for it, and it was promoted and dispatched to an unattended session
holding a prompt that told it to mark ITSELF done — releasing six tasks whose work
is an agent editing its own guardrails. A gate that cannot protect itself is not a
control.

So the fix is at SEEDING time, and these tests pin both halves: the seeder refuses
work wearing a sentinel's name, and recognition stays exactly as wide as it was.
"""
from __future__ import annotations

import importlib

import pytest

from tools.kanban.gates import GATE_TITLE_MARKER, declares_gate, is_manual_gate


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _InsertCapturingConn:
    """SELECT-for-existence finds nothing; every write is recorded."""

    def __init__(self):
        self.writes = []
        self.committed = False

    def execute(self, sql, params=()):
        head = sql.strip().split(None, 1)[0].upper()
        if head == "SELECT":
            return _FakeCursor([])
        self.writes.append((head, params))
        return _FakeCursor([])

    def commit(self):
        self.committed = True

    def rollback(self):  # pragma: no cover - failure path only
        pass

    def close(self):
        pass

    @property
    def inserted_ids(self):
        return [p[0] for head, p in self.writes if head == "INSERT"]


@pytest.fixture
def board(monkeypatch):
    """Patch the module OBJECTS ``create_tasks`` resolves at call time.

    ``create_tasks`` imports ``get_connection`` and ``init_kanban_tables`` inside
    the function body, so patching the attribute on the SOURCE module is what
    takes effect — patching a name in ``task_factory`` would not.
    """
    storage = importlib.import_module("tools.db.storage")
    init_db = importlib.import_module("tools.kanban.init_db")
    conn = _InsertCapturingConn()
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(init_db, "init_kanban_tables", lambda *a, **k: None)
    return conn


@pytest.fixture
def board_that_must_not_be_opened(monkeypatch):
    """A connection that fails the test if the seeder ever reaches the database.

    The refusal has to land BEFORE the first insert, the way the ``task_type``
    refusal already does, so a batch never half-lands.
    """
    storage = importlib.import_module("tools.db.storage")

    def _boom(*a, **k):
        raise AssertionError("create_tasks opened the board before refusing")

    monkeypatch.setattr(storage, "get_connection", _boom)


def _create(specs):
    from tools.kanban.task_factory import create_tasks

    return create_tasks(specs)


# ---------------------------------------------------------------------------
# Refusing work that wears a sentinel's name
# ---------------------------------------------------------------------------

class TestWorkWithAGateIdIsRefused:
    def test_the_task_that_was_undispatchable(self, board_that_must_not_be_opened):
        """The concrete regression, seeded exactly as it was."""
        with pytest.raises(ValueError) as exc:
            _create([{
                "id": "tsg-gate-01",
                "title": "Decide the CI allowlist policy",
                "description": (
                    "Decide whether the CI allowlist is enumerated in the workflow "
                    "or derived, then write the decision down."
                ),
                "task_type": "chore",
            }])
        assert "tsg-gate-01" in str(exc.value)

    def test_the_message_names_the_id_and_the_alternative(
        self, board_that_must_not_be_opened
    ):
        """A refusal that does not end in an edit just moves the puzzle."""
        with pytest.raises(ValueError) as exc:
            _create([{
                "id": "tsg-gate-01",
                "title": "Decide the CI allowlist policy",
                "description": "Write the decision down.",
            }])
        message = str(exc.value)
        assert "tsg-gate-01" in message
        assert "tsg-<epic>-01" in message, "the message must name a usable id"
        assert GATE_TITLE_MARKER in message, "…and how to say it IS a gate"
        assert "RISK:" in message

    @pytest.mark.parametrize("task_id", [
        "kax-gate-00", "kax-gate-01", "kax-gate-12", "x-gate-9",
    ])
    def test_any_gate_number_is_refused_for_work(
        self, task_id, board_that_must_not_be_opened
    ):
        """Refusal is as wide as recognition — a second gate id is no safer."""
        with pytest.raises(ValueError):
            _create([{"id": task_id, "title": "Ordinary work", "description": "Do it."}])

    def test_the_batch_never_half_lands(self, board):
        """One impostor aborts the whole batch, before the first insert."""
        with pytest.raises(ValueError):
            _create([
                {"id": "kax-exec-99", "title": "Real work", "description": "Do it."},
                {"id": "kax-gate-07", "title": "More work", "description": "Do it too."},
            ])
        assert board.inserted_ids == []
        assert not board.committed


# ---------------------------------------------------------------------------
# A genuine gate is still seedable
# ---------------------------------------------------------------------------

class TestAGenuineGateIsStillAccepted:
    def test_the_canonical_title_marker(self, board):
        assert _create([{
            "id": "kax-gate-00",
            "title": f"{GATE_TITLE_MARKER} — KAX (held in_progress forever)",
            "description": "Held while a human drives the card.",
            "task_type": "chore",
            "status": "in_progress",
        }]) == ["kax-gate-00"]
        assert board.inserted_ids == ["kax-gate-00"]

    def test_a_stated_risk_without_the_marker(self, board):
        """A gate owes a reason; stating one is enough to prove it is a gate."""
        assert _create([{
            "id": "kax-gate-01",
            "title": "KAX SELF-MODIFICATION HOLD - human-driven slices only",
            "description": (
                "RISK: these tasks have an agent edit .claude/hooks/pre_tool_use.py "
                "and the dispatcher, and pr_watcher auto-merges anything CI-green.\n"
                "Release only with a human driving."
            ),
            "task_type": "chore",
            "status": "in_progress",
        }]) == ["kax-gate-01"]

    def test_risk_shaped_prose_the_board_already_uses(self, board):
        """hgx-gate-00's real description, which states its risk in prose."""
        assert _create([{
            "id": "hgx-gate-00",
            "title": "HGX HOLD GATE — do not dispatch until released",
            "description": (
                "This card touches the safety hooks, the executor chain and the "
                "approval path.\nIt must not be built unattended."
            ),
            "task_type": "chore",
            "status": "in_progress",
        }]) == ["hgx-gate-00"]

    def test_ordinary_work_ids_are_untouched(self, board):
        """The rename that fixed the real instance must seed cleanly."""
        assert _create([{
            "id": "tsg-policy-01",
            "title": "Decide the CI allowlist policy",
            "description": "Write the decision down.",
            "task_type": "chore",
        }]) == ["tsg-policy-01"]

    @pytest.mark.parametrize("task_id", [
        "kax-gateway-01",   # 'gate' inside a real word
        "sme-gate-review",  # gate-shaped word, not a number
        "kax-exec-04",
    ])
    def test_ids_that_are_not_sentinel_shaped_seed_normally(self, task_id, board):
        assert _create([{
            "id": task_id, "title": "Ordinary work", "description": "Do it."
        }]) == [task_id]


# ---------------------------------------------------------------------------
# Recognition is NOT narrowed
# ---------------------------------------------------------------------------

class TestTheDispatchPredicateIsUnchanged:
    """The seeding rule is the fix; narrowing ``is_manual_gate`` would be the
    original hgx-gate-01 hole reopened."""

    def test_a_cards_second_gate_is_still_a_gate(self):
        assert is_manual_gate(
            "hgx-gate-01", "HGX SELF-MODIFICATION HOLD - human-driven slices only"
        )

    @pytest.mark.parametrize("task_id", [
        "hgx-gate-00", "hgx-gate-01", "hgx-gate-02", "prem-gate-00", "idp-gate-12",
    ])
    def test_every_gate_number_still_matches(self, task_id):
        assert is_manual_gate(task_id, "no marker in this title")

    def test_the_refused_id_is_exactly_why_it_is_refused(self):
        """tsg-gate-01 IS a gate to the dispatcher — that is the whole problem."""
        assert is_manual_gate("tsg-gate-01", "Decide the CI allowlist policy")
        assert not declares_gate("Decide the CI allowlist policy", "Write it down.")


def _seeder_specs(module):
    """Every task spec a seeder ships — module-level lists and zero-arg builders."""
    import inspect

    specs = []
    for attr in dir(module):
        value = getattr(module, attr)
        if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
            specs.extend(value)
        elif inspect.isfunction(value) and ("task" in attr.lower() or "spec" in attr.lower()):
            params = inspect.signature(value).parameters.values()
            if any(p.default is inspect.Parameter.empty for p in params):
                continue
            try:
                built = value()
            except Exception:  # noqa: BLE001 - not every builder is pure
                continue
            if isinstance(built, list) and built and all(isinstance(x, dict) for x in built):
                specs.extend(built)
    return specs


def _gate_declaring_seeders():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    found = []
    for path in sorted((root / "tools" / "kanban").glob("seed_*.py")):
        if "-gate-" in path.read_text(encoding="utf-8", errors="replace"):
            found.append("tools.kanban." + path.stem)
    return found


def test_the_edited_seeders_are_inside_the_sweep():
    """The sweep discovers modules, so name the ones this change touched.

    ``tools.kanban.seed_ahx_arr_clx``, ``tools.kanban.seed_bom_concord`` and
    ``tools.kanban.seed_sbx_sbom2026`` are the five gates that gained a RISK:
    line; if discovery ever stops finding them the guard passes vacuously.
    """
    covered = set(_gate_declaring_seeders())
    for module_name in (
        "tools.kanban.seed_ahx_arr_clx",
        "tools.kanban.seed_bom_concord",
        "tools.kanban.seed_sbx_sbom2026",
    ):
        assert module_name in covered


@pytest.mark.parametrize("module_name", _gate_declaring_seeders())
def test_every_shipped_seeder_gate_survives_the_refusal(module_name):
    """A seeder that can no longer re-run is a gate nobody can re-seed.

    The refusal is only safe if the gates this repo actually ships pass it. Five
    did not — ahx/arr/clx-gate-00, cncd-gate-00 and sbx-gate-00 held work without
    ever saying what went wrong if the runner built the card — so they gained the
    RISK: line they already owed under kpr-idle-02.
    """
    module = importlib.import_module(module_name)
    from tools.kanban.gates import has_gate_id

    offenders = [
        spec["id"]
        for spec in _seeder_specs(module)
        if has_gate_id(str(spec.get("id") or ""))
        and not declares_gate(spec.get("title"), spec.get("description"))
    ]
    assert not offenders, (
        f"{module_name} seeds {offenders} with a gate id but nothing says it is a "
        f"gate — create_tasks refuses the batch. Add {GATE_TITLE_MARKER!r} to the "
        "title or a 'RISK:' line saying what goes wrong if the runner builds the "
        "card unattended."
    )


class _BoardConn:
    """Answers exactly the three queries ``check_gate_sentinel_shape`` asks."""

    def __init__(self, tasks, junction=None, junction_missing=False):
        self.tasks = tasks
        self.junction = junction or []
        self.junction_missing = junction_missing

    def execute(self, sql, params=()):
        if "kanban_task_deps" in sql:
            if self.junction_missing:
                raise RuntimeError("no such table: kanban_task_deps")
            return _FakeCursor([{"depends_on_id": d} for d in self.junction])
        if "depends_on_task_id" in sql:
            return _FakeCursor([
                {"depends_on_task_id": t["depends_on_task_id"]}
                for t in self.tasks
                if t.get("depends_on_task_id")
            ])
        return _FakeCursor([
            {"id": t["id"], "title": t.get("title", ""), "status": t.get("status", "backlog")}
            for t in self.tasks
        ])

    def close(self):
        pass


@pytest.fixture
def coherence(monkeypatch):
    """Point the check at a board we control, and hand back the runner."""
    checker = importlib.import_module("tools.workflow.coherence_checker")
    storage = importlib.import_module("tools.db.storage")

    def _run(tasks, **kwargs):
        conn = _BoardConn(tasks, **kwargs)
        monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
        return checker.check_gate_sentinel_shape()

    return _run


class TestCoherenceReportsStrandedSentinels:
    """The other half: rows already on the board, or written around the factory."""

    def test_the_stranded_task_is_reported(self, coherence):
        result = coherence([
            {"id": "tsg-gate-01", "title": "Decide the CI allowlist policy",
             "status": "backlog"},
            {"id": "tsg-stale-05", "title": "Real work", "status": "backlog"},
        ])
        assert result.status == "warn"
        assert any("tsg-gate-01" in entry for entry in result.extra)
        assert not any("tsg-stale-05" in entry for entry in result.extra)

    def test_a_held_gate_is_not_reported(self, coherence):
        result = coherence([
            {"id": "hgx-gate-00", "title": "HGX HOLD GATE", "status": "in_progress"},
        ])
        assert result.status == "pass", result.extra

    def test_a_gate_something_depends_on_is_not_reported(self, coherence):
        """A released gate still gates the history that pointed at it."""
        result = coherence([
            {"id": "oss-gate-00", "title": "OSS gate", "status": "backlog"},
            {"id": "oss-build-01", "title": "Work", "status": "backlog",
             "depends_on_task_id": "oss-gate-00"},
        ])
        assert result.status == "pass", result.extra

    def test_a_junction_dependency_counts_too(self, coherence):
        result = coherence(
            [{"id": "oss-gate-00", "title": "OSS gate", "status": "backlog"}],
            junction=["oss-gate-00"],
        )
        assert result.status == "pass", result.extra

    def test_a_missing_junction_table_does_not_break_the_check(self, coherence):
        result = coherence(
            [{"id": "tsg-gate-01", "title": "Work", "status": "backlog"}],
            junction_missing=True,
        )
        assert result.status == "warn"
        assert any("tsg-gate-01" in entry for entry in result.extra)

    def test_a_released_gate_is_not_reported(self, coherence):
        """Done holds nothing and cannot be dispatched — it is not the defect."""
        result = coherence([
            {"id": "prem-gate-00", "title": "Released gate", "status": "done"},
        ])
        assert result.status == "pass", result.extra

    def test_an_empty_board_warns_rather_than_passing(self, coherence):
        """A fresh worktree cannot show a stranded task; 'clean' would be a lie."""
        result = coherence([])
        assert result.status == "warn"

    def test_an_unreachable_board_warns(self, monkeypatch):
        checker = importlib.import_module("tools.workflow.coherence_checker")
        storage = importlib.import_module("tools.db.storage")

        def _boom(*a, **k):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(storage, "get_connection", _boom)
        result = checker.check_gate_sentinel_shape()
        assert result.status == "warn"
        assert "NOT verified" in result.message

    def test_the_check_is_registered(self):
        checker = importlib.import_module("tools.workflow.coherence_checker")
        assert "gate_sentinel_shape" in checker.CHECK_REGISTRY
        assert checker._FIX_REGISTRY["gate_sentinel_shape"] == "skip"

    def test_the_fast_tier_runs_it_when_the_seeding_path_changes(self):
        from pathlib import Path

        checker = importlib.import_module("tools.workflow.coherence_checker")
        selected = checker.select_checks(
            tier="fast", changed_files=[Path("tools/kanban/task_factory.py")]
        )
        assert "gate_sentinel_shape" in selected
        assert "gate_sentinel_shape" in checker.select_checks(tier="full")


class TestDeclaresGate:
    """The seed-time predicate on its own — content, not id."""

    def test_marker_in_title(self):
        assert declares_gate(f"{GATE_TITLE_MARKER} — held", None)

    def test_explicit_risk_line(self):
        assert declares_gate("Any title", "RISK: the runner cannot reach the repo.")

    def test_neither(self):
        assert not declares_gate("Ship the thing", "Ship it by Friday.")

    def test_procedure_is_not_a_risk(self):
        """"Do not move this to done" says what to DO, not what goes wrong."""
        assert not declares_gate(
            "Hold", "Do not move this to done without an explicit decision."
        )
