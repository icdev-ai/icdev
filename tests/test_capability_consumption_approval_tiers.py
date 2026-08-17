# CUI // SP-CTI
"""The two narrowings rem-cap-05 put on ``probe_agent_approval_rule``.

Both exist because the naive reading of the ``agent_approval_rule`` class could
never reach zero, in opposite directions:

* **Denominator.** ``build_approval_hook`` auto-allows a tier outside
  ``require_approval_tiers`` *without writing a row* — correct for an audit trail
  of decisions, fatal for a probe that counted all four tiers as declared. 37 of
  the 62 enumerated tools could be classified all day and ``agent_approval_log``
  would still be empty for each, so the budget could never be lowered past 37 and
  a fully wired gate would look identical to an absent one.
* **Numerator.** ``tools/quality/hitl_delta.py`` legitimately reuses
  ``record_decision()`` and this table with tiers ``classify()`` can never emit
  (``review``, ``trust_delta``). The old probe reported zero only because those
  rows' ``tool_name`` values happened not to collide with a policy-enumerated
  tool; a collision would have reported FALSE consumption.

Every zero-assertion below is paired with a positive control on the same table,
so no test here can pass because the probe silently returned nothing.
"""
from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

capcon = importlib.import_module("tools.awareness.capability_consumption")
approval_gate = importlib.import_module("tools.agent_runtime.approval_gate")

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_POLICY = REPO_ROOT / "args" / "agent_approval_policy.yaml"
LIVENESS_GATE = REPO_ROOT / "args" / "liveness_gate.yaml"

NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(days=30)
IN_WINDOW = (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")

# Only the columns the probe reads, plus `tier` — which is the whole point.
DDL = """CREATE TABLE agent_approval_log (
    id INTEGER PRIMARY KEY, decided_at TEXT, actor TEXT, tool_name TEXT,
    tier TEXT, rule TEXT, decision TEXT)"""

# Same shape, no tier column: the drift CLAUDE.md warns about, where an older
# migration left the live table behind the DDL.
DDL_NO_TIER = """CREATE TABLE agent_approval_log (
    id INTEGER PRIMARY KEY, decided_at TEXT, actor TEXT, tool_name TEXT,
    rule TEXT, decision TEXT)"""

INSERT = (
    "INSERT INTO agent_approval_log "
    "(decided_at, actor, tool_name, tier, rule, decision) VALUES (?, ?, ?, ?, ?, ?)"
)

# A policy with one tool in every tier, so "which tiers are declared" is
# answerable by name rather than by arithmetic.
POLICY = {
    "version": 1,
    "default_tier": "unknown",
    "require_approval_tiers": ["irreversible", "unknown"],
    "tools": {
        "reversible": ["read_file", "grep"],
        "recoverable": ["write_file"],
        "irreversible": ["git_push", "terraform_apply"],
        "unknown": [],
    },
    "command_patterns": {},
    "command_tools": [],
}


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    """Point approval_gate at a synthetic policy and clear its memo."""

    def _write(policy):
        path = tmp_path / "agent_approval_policy.yaml"
        path.write_text(yaml.safe_dump(policy), encoding="utf-8")
        monkeypatch.setenv(approval_gate.POLICY_ENV, str(path))
        # load_policy() memoizes; monkeypatch restores the previous cache on
        # teardown so a synthetic policy cannot leak into a sibling test.
        monkeypatch.setattr(approval_gate, "_POLICY_CACHE", None)
        return path

    return _write


@pytest.fixture
def shipped_policy(monkeypatch):
    """Read the real args/agent_approval_policy.yaml, whatever ran before.

    ``load_policy`` memoizes globally and honours ``ICDEV_AGENT_APPROVAL_POLICY``,
    so a sibling module that pointed it elsewhere would otherwise decide what
    "the shipped policy" means in an in-suite run.
    """
    monkeypatch.delenv(approval_gate.POLICY_ENV, raising=False)
    monkeypatch.setattr(approval_gate, "_POLICY_CACHE", None)


@pytest.fixture
def conn_factory(tmp_path, monkeypatch):
    """A StorageConnection over a seeded temp SQLite database."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    made = []

    def _make(rows=(), ddl=DDL, name="approval.db"):
        db_path = tmp_path / name
        raw = sqlite3.connect(str(db_path))
        try:
            raw.execute(ddl)
            for row in rows:
                raw.execute(INSERT, row)
            raw.commit()
        finally:
            raw.close()
        from tools.db.storage import get_connection

        conn = get_connection(db_path=str(db_path))
        made.append(conn)
        return conn

    yield _make
    for conn in made:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _probe(conn):
    return capcon.probe_agent_approval_rule(conn, SINCE, 0, 100).to_dict()


# ---------------------------------------------------------------------------
# The acceptance test: a hitl_delta row cannot masquerade as consumption
# ---------------------------------------------------------------------------


def test_foreign_tier_row_is_not_consumption_even_on_a_declared_tool(
    policy_file, conn_factory
):
    """A ``review`` row on a declared tool name must not count as a gate decision.

    ``tools/quality/hitl_delta.py`` writes ``tier='review'`` / ``rule='claim_guard'``
    through the same ``record_decision()`` seam. `git_push` is enumerated
    ``irreversible`` by the policy, so without the tier filter this row would
    report the approval gate as live on the strength of another module's write.
    """
    policy_file(POLICY)
    conn = conn_factory(
        [
            (IN_WINDOW, "hitl", "git_push", "review", "claim_guard", "approved"),
            (IN_WINDOW, "hitl", "terraform_apply", "trust_delta", "hitl_delta", "denied"),
        ]
    )
    res = _probe(conn)

    assert res["telemetry_available"] is True, res["unmeasured_reason"]
    assert res["events"] == 0
    assert res["consumed"] == 0
    assert res["inert"] == res["declared"] == 2
    assert sorted(res["inert_units"]) == ["git_push", "terraform_apply"]


def test_real_gate_tier_on_the_same_tool_is_counted(policy_file, conn_factory):
    """Positive control for the test above — the filter keeps what it should."""
    policy_file(POLICY)
    conn = conn_factory(
        [
            (IN_WINDOW, "hitl", "git_push", "review", "claim_guard", "approved"),
            (IN_WINDOW, "op", "git_push", "irreversible", "tool_tier", "denied"),
        ]
    )
    res = _probe(conn)

    assert res["events"] == 1
    assert res["consumed"] == 1
    assert res["inert_units"] == ["terraform_apply"]
    assert res["top_consumed"] == [{"unit": "git_push", "events": 1}]


def test_unknown_tier_is_measurable_when_the_policy_requires_it(
    policy_file, conn_factory
):
    """``unknown`` is a tier ``classify()`` emits, so its rows count.

    It enumerates no tools, which is why it contributes nothing to ``declared`` —
    but a decision recorded against it is a real gate decision and lands in
    ``undeclared_units_observed`` rather than being filtered away as foreign.
    """
    policy_file(POLICY)
    conn = conn_factory(
        [(IN_WINDOW, "op", "some_unenumerated_tool", "unknown", "default_tier", "denied")]
    )
    res = _probe(conn)

    assert res["events"] == 0  # not declared, so not consumption
    assert res["extra"]["undeclared_units_observed"] == ["some_unenumerated_tool"]


# ---------------------------------------------------------------------------
# The denominator: declared is scoped to require_approval_tiers
# ---------------------------------------------------------------------------


def test_declared_is_scoped_to_require_approval_tiers(policy_file, conn_factory):
    policy_file(POLICY)
    res = _probe(conn_factory())

    assert res["declared"] == 2
    assert sorted(res["inert_units"]) == ["git_push", "terraform_apply"]
    assert res["extra"]["require_approval_tiers"] == ["irreversible", "unknown"]


def test_excluded_tools_are_enumerated_not_silently_dropped(policy_file, conn_factory):
    """A class that quietly shrinks its own denominator is the same dishonesty."""
    policy_file(POLICY)
    res = _probe(conn_factory())
    nmd = res["extra"]["not_measurable_by_design"]

    assert nmd["count"] == 3
    assert nmd["tiers"] == ["recoverable", "reversible"]
    assert nmd["tools_by_tier"] == {
        "recoverable": ["write_file"],
        "reversible": ["grep", "read_file"],
    }
    assert nmd["truncated"] is False


def test_probe_follows_the_policy_when_an_operator_widens_the_tiers(
    policy_file, conn_factory
):
    """The tier list is read, never hardcoded: adding ``recoverable`` moves it."""
    widened = dict(POLICY)
    widened["require_approval_tiers"] = ["recoverable", "irreversible", "unknown"]
    policy_file(widened)
    res = _probe(conn_factory())

    assert res["declared"] == 3
    assert "write_file" in res["inert_units"]
    nmd = res["extra"]["not_measurable_by_design"]
    assert nmd["count"] == 2
    assert nmd["tiers"] == ["reversible"]


# ---------------------------------------------------------------------------
# Degrade honestly rather than report a clean zero
# ---------------------------------------------------------------------------


def test_no_required_tier_is_unmeasurable_not_a_clean_zero(policy_file, conn_factory):
    disarmed = dict(POLICY)
    disarmed["require_approval_tiers"] = []
    policy_file(disarmed)
    res = _probe(conn_factory())

    assert res["telemetry_available"] is False
    assert res["declared"] == 0
    assert "require_approval_tiers is empty" in res["unmeasured_reason"]


def test_missing_tier_column_is_unmeasurable_not_an_overcount(
    policy_file, conn_factory
):
    """Without the discriminator, another writer's row reads as a live gate."""
    policy_file(POLICY)
    res = _probe(conn_factory(ddl=DDL_NO_TIER, name="no_tier.db"))

    assert res["telemetry_available"] is False
    assert "no tier column" in res["unmeasured_reason"]


# ---------------------------------------------------------------------------
# Against the SHIPPED policy and the SHIPPED budget
# ---------------------------------------------------------------------------


def test_shipped_policy_excludes_at_least_one_tier(shipped_policy, conn_factory):
    """Pins the relationship on the real config, whatever the tiers become."""
    policy = yaml.safe_load(SHIPPED_POLICY.read_text(encoding="utf-8"))
    tools_by_tier = policy["tools"]
    require = set(policy["require_approval_tiers"])
    expected = sum(len(tools_by_tier.get(t) or []) for t in require)
    total = sum(len(v or []) for v in tools_by_tier.values())

    res = _probe(conn_factory())

    assert res["declared"] == expected
    assert res["declared"] < total, (
        "every tier requires approval — the by-design exclusion no longer applies "
        "and this test, plus the liveness budget, need revisiting"
    )
    assert res["extra"]["not_measurable_by_design"]["count"] == total - expected


def test_liveness_budget_cannot_exceed_the_measurable_declaration():
    """A budget above the declared count could never be reached by wiring alone."""
    gate = yaml.safe_load(LIVENESS_GATE.read_text(encoding="utf-8"))
    budget = gate["grandfathered"]["agent_approval_rule"]
    policy = yaml.safe_load(SHIPPED_POLICY.read_text(encoding="utf-8"))
    require = set(policy["require_approval_tiers"])
    measurable = sum(len(policy["tools"].get(t) or []) for t in require)

    assert budget <= measurable, (
        f"agent_approval_rule budget {budget} exceeds the {measurable} tools that "
        "can produce a decision row; lower it — a budget may only ever go DOWN"
    )
