#!/usr/bin/env python3
# CUI // SP-CTI
"""Policy embedded in kanban descriptions must track the card — CUI // SP-CTI.

Board-free. Every test here exercises the rules file and the pure text
transforms, because the invariants that matter are decidable without a board:

  * a rule cannot reach rows it did not name (fail-closed scoping)
  * a rule cannot overrule a deliberate exemption (AGOV keeps its --draft)
  * a fixed row cannot re-match its own rule (no rewrite loop)
  * migrating a row twice is a no-op (idempotence)

The live-board half runs as the ``kanban_policy_drift`` Genesis reflex, which
is where a real ``kanban_tasks`` row exists to scan. See
``docs/features/kax-merge-02-policy-drift.md``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.kanban.policy_drift import (  # noqa: E402
    DISPATCHABLE_STATUSES,
    PolicyRuleError,
    Project,
    apply,
    apply_policy_block,
    find_block,
    load_exemptions,
    load_projects,
    load_rules,
    project_for_task,
    render_block,
    scan,
    sync_description,
)

_REPO = Path(__file__).resolve().parents[1]
_RULES = _REPO / "args" / "kanban_policy_drift.yaml"


# ---------------------------------------------------------------------------
# The shipped configuration
# ---------------------------------------------------------------------------

def test_shipped_rules_load_and_validate():
    """The rules file in the repo must satisfy every load-time invariant."""
    ruleset = load_rules()
    assert ruleset.rules, "no rules — the file would be dead config"
    assert "agov" in ruleset.exempt_projects


def test_no_shipped_rule_matches_its_cards_current_policy():
    """A rule whose pattern matches the CURRENT text rewrites the board forever.

    The fix replaces the matched span with a block containing the card's policy.
    If the pattern also matches that policy, the next scan finds drift again and
    the checker never converges.
    """
    projects = load_projects()
    for rule in load_rules(projects=projects).rules:
        policy = projects[rule.project].policy
        assert policy is not None
        block = render_block(rule.project, policy)
        assert not rule.pattern.search(block), (
            f"rule {rule.id} matches the block it produces — infinite rewrite"
        )


def test_every_rule_explains_itself():
    raw = yaml.safe_load(_RULES.read_text(encoding="utf-8"))
    for item in raw["rules"]:
        assert len(str(item.get("why", "")).strip()) > 40, (
            f"rule {item.get('id')} needs a real `why` — a rules table nobody "
            "can read is how the next person deletes a rule still in use"
        )


# ---------------------------------------------------------------------------
# Fail-closed scoping
# ---------------------------------------------------------------------------

_PROJECTS = {
    "hgx": Project(key="hgx", task_prefix="hgx-", policy="HGX policy v2."),
    "agov": Project(key="agov", task_prefix="agov-", policy=None),
    "kax": Project(key="kax", task_prefix="kax-", policy=None),
}


def _write_rules(tmp_path: Path, doc: dict) -> Path:
    p = tmp_path / "rules.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8", newline="")
    return p


def test_rule_without_applies_to_is_rejected(tmp_path):
    path = _write_rules(tmp_path, {
        "rules": [{"id": "r1", "project": "hgx", "why": "x" * 50,
                   "legacy_pattern": "old"}],
    })
    with pytest.raises(PolicyRuleError, match="applies_to.task_id_prefix"):
        load_rules(path, projects=_PROJECTS)


def test_rule_reaching_outside_its_card_is_rejected(tmp_path):
    path = _write_rules(tmp_path, {
        "rules": [{"id": "r1", "project": "hgx", "why": "x" * 50,
                   "legacy_pattern": "old",
                   "applies_to": {"task_id_prefix": ["hgx-", "agov-"]}}],
    })
    with pytest.raises(PolicyRuleError, match="outside its own card"):
        load_rules(path, projects=_PROJECTS)


def test_rule_may_not_overrule_an_exemption(tmp_path):
    path = _write_rules(tmp_path, {
        "exempt_projects": {"agov": "MANUAL-ONLY, --draft is deliberate"},
        "rules": [{"id": "r1", "project": "agov", "why": "x" * 50,
                   "legacy_pattern": "--draft",
                   "applies_to": {"task_id_prefix": ["agov-"]}}],
    })
    with pytest.raises(PolicyRuleError, match="exempt_projects"):
        load_rules(path, projects=_PROJECTS)


def test_rule_for_a_card_without_policy_is_rejected(tmp_path):
    """Nothing to rewrite TO — the replacement text comes from the card."""
    path = _write_rules(tmp_path, {
        "rules": [{"id": "r1", "project": "kax", "why": "x" * 50,
                   "legacy_pattern": "old",
                   "applies_to": {"task_id_prefix": ["kax-"]}}],
    })
    with pytest.raises(PolicyRuleError, match="no `policy:` field"):
        load_rules(path, projects=_PROJECTS)


def test_uncompilable_pattern_is_rejected(tmp_path):
    path = _write_rules(tmp_path, {
        "rules": [{"id": "r1", "project": "hgx", "why": "x" * 50,
                   "legacy_pattern": "([unclosed",
                   "applies_to": {"task_id_prefix": ["hgx-"]}}],
    })
    with pytest.raises(PolicyRuleError, match="does not compile"):
        load_rules(path, projects=_PROJECTS)


# ---------------------------------------------------------------------------
# Text transforms
# ---------------------------------------------------------------------------

def _rule(tmp_path, pattern="Card: HGX\\. Old policy\\."):
    path = _write_rules(tmp_path, {
        "rules": [{"id": "hgx-old", "project": "hgx", "why": "x" * 50,
                   "legacy_pattern": pattern,
                   "applies_to": {"task_id_prefix": ["hgx-"]}}],
    })
    return load_rules(path, projects=_PROJECTS).rules


def test_legacy_text_becomes_a_card_linked_block(tmp_path):
    rules = _rule(tmp_path)
    desc = "Card: HGX. Old policy.\n\nBUILD the thing."
    new, action, _ = sync_description(desc, "hgx", "HGX policy v2.", rules,
                                      task_id="hgx-doc-01")
    assert action == "legacy_migrated"
    assert "Old policy" not in new
    assert "HGX policy v2." in new
    assert "BUILD the thing." in new, "the work text must survive untouched"
    assert find_block(new, "hgx") is not None


def test_migration_is_idempotent(tmp_path):
    rules = _rule(tmp_path)
    desc = "Card: HGX. Old policy.\n\nBUILD the thing."
    once, _, _ = sync_description(desc, "hgx", "HGX policy v2.", rules,
                                  task_id="hgx-doc-01")
    twice, action, _ = sync_description(once, "hgx", "HGX policy v2.", rules,
                                        task_id="hgx-doc-01")
    assert action is None
    assert twice == once


def test_a_migrated_row_then_tracks_the_card(tmp_path):
    """The point of the block: the NEXT policy change needs no rule at all."""
    rules = _rule(tmp_path)
    desc = "Card: HGX. Old policy.\n\nBUILD the thing."
    migrated, _, _ = sync_description(desc, "hgx", "HGX policy v2.", rules,
                                      task_id="hgx-doc-01")
    updated, action, _ = sync_description(migrated, "hgx", "HGX policy v3 — no drafts.",
                                          rules=(), task_id="hgx-doc-01")
    assert action == "block_updated"
    assert "HGX policy v3 — no drafts." in updated
    assert "HGX policy v2." not in updated
    assert "BUILD the thing." in updated


def test_a_rule_does_not_touch_another_projects_row(tmp_path):
    rules = _rule(tmp_path)
    desc = "Card: HGX. Old policy."  # same text, wrong task id
    new, action, _ = sync_description(desc, "hgx", "HGX policy v2.", rules,
                                      task_id="kax-merge-02")
    assert action is None
    assert new == desc


def test_longest_task_prefix_wins():
    """projects.yaml permits nested prefixes; first-match would pick wrong."""
    projects = {
        "dt": Project("dt", "dt-", "A"),
        "dtiqe": Project("dtiqe", "dt-iqe-", "B"),
    }
    assert project_for_task("dt-iqe-01", projects).key == "dtiqe"
    assert project_for_task("dt-core-01", projects).key == "dt"


# ---------------------------------------------------------------------------
# Board scan — a fake connection is enough; the SQL is one SELECT
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """Minimal stand-in: SELECT returns seeded rows, UPDATE/INSERT are recorded."""

    def __init__(self, rows):
        self.rows = rows
        self.writes = []
        self.committed = False

    def execute(self, sql, params=()):
        head = sql.strip().split(None, 1)[0].upper()
        if head == "SELECT":
            wanted = set(params)
            return _FakeCursor([r for r in self.rows if r["status"] in wanted])
        self.writes.append((head, params))
        return _FakeCursor([])

    def commit(self):
        self.committed = True


def _agov_row():
    return {
        "id": "agov-det-01",
        "status": "backlog",
        "description": (
            "WORKFLOW: worktree off origin/main, commit, push, open the PR with "
            "`gh pr create --draft` (tools/ci/pr_watcher.py auto-merges green "
            "kanban/*)."
        ),
    }


def test_agov_manual_only_rows_are_never_touched():
    """AC2: 19 agov rows legitimately say --draft. Nothing here may 'fix' them."""
    projects = load_projects()
    ruleset = load_rules(projects=projects)
    conn = _FakeConn([_agov_row()])
    findings = scan(conn, projects, ruleset)
    assert findings == []
    assert not conn.writes


def test_exemption_holds_even_if_the_card_grows_a_policy():
    """The veto outranks a `policy:` field — an exemption is a decision."""
    projects = dict(load_projects())
    projects["agov"] = Project("agov", "agov-", "some policy someone added")
    ruleset = load_rules(projects=projects)
    conn = _FakeConn([_agov_row()])
    assert scan(conn, projects, ruleset) == []


def test_scan_marks_only_dispatchable_rows_fixable():
    projects = load_projects()
    ruleset = load_rules(projects=projects)
    stale = (
        "Card: HGX — Harness Agent Parity and Graph Runtime. MANUAL-ONLY, gated on\n"
        "hgx-gate-00. Open PRs NORMALLY, not --draft. pr_watcher auto-merges green "
        "kanban/* and a draft is the one thing it may NOT touch, so a draft stops the "
        "pipeline at its last step and waits for a human. The hold that matters is the "
        "GATE (depends_on_task_id), not the draft flag.\n\nBUILD it."
    )
    conn = _FakeConn([
        {"id": "hgx-doc-02", "status": "backlog", "description": stale},
        {"id": "hgx-agent-01", "status": "done", "description": stale},
    ])
    findings = {f["task_id"]: f for f in scan(conn, projects, ruleset)}
    assert findings["hgx-doc-02"]["fixable"] is True
    assert findings["hgx-agent-01"]["fixable"] is False


def test_apply_writes_only_the_fixable_rows_and_audits_each():
    conn = _FakeConn([])
    findings = [
        {"task_id": "hgx-doc-02", "status": "backlog", "project": "hgx",
         "action": "legacy_migrated", "detail": "d", "new_description": "new"},
        {"task_id": "hgx-agent-01", "status": "done", "project": "hgx",
         "action": "legacy_migrated", "detail": "d", "new_description": "new"},
    ]
    out = apply(conn, findings)
    assert out["written"] == ["hgx-doc-02"]
    assert [s["task_id"] for s in out["skipped"]] == ["hgx-agent-01"]
    assert [w[0] for w in conn.writes] == ["UPDATE", "INSERT"], (
        "every rewrite must leave an audit comment on the board"
    )
    assert conn.committed


def test_dispatchable_statuses_exclude_in_progress():
    """Rewriting a live row would clobber a session's own edits."""
    assert "in_progress" not in DISPATCHABLE_STATUSES
    assert "done" not in DISPATCHABLE_STATUSES


# ---------------------------------------------------------------------------
# Seeder-facing helper
# ---------------------------------------------------------------------------

def test_apply_policy_block_stamps_a_new_description():
    out = apply_policy_block("BUILD the thing.", "hgx-new-01", _PROJECTS)
    assert out.startswith("<!-- icdev:policy hgx -->")
    assert "HGX policy v2." in out
    assert out.rstrip().endswith("BUILD the thing.")


def test_apply_policy_block_is_a_noop_without_a_card_policy():
    desc = "BUILD the thing."
    assert apply_policy_block(desc, "kax-merge-02", _PROJECTS) == desc
    assert apply_policy_block(desc, "unknown-01", _PROJECTS) == desc


def test_apply_policy_block_respects_the_exemption(tmp_path):
    projects = dict(_PROJECTS)
    projects["agov"] = Project("agov", "agov-", "policy text")
    ruleset = load_rules(
        _write_rules(tmp_path, {"exempt_projects": {"agov": "MANUAL-ONLY"},
                                "rules": []}),
        projects=projects,
    )
    desc = "BUILD the thing."
    assert apply_policy_block(desc, "agov-det-01", projects, ruleset) == desc


def test_missing_projects_yaml_degrades_to_a_noop(tmp_path):
    """A wheel install ships no args/projects.yaml — that must not raise."""
    assert load_projects(tmp_path / "nope.yaml") == {}
    desc = "BUILD the thing."
    assert apply_policy_block(desc, "hgx-doc-01", {}) == desc


def test_config_resolves_from_either_tree():
    """tools/ and the icdev/ mirror both have to find args/projects.yaml."""
    from tools.kanban.policy_drift import PROJECTS_YAML, RULES_YAML
    assert PROJECTS_YAML.is_file(), PROJECTS_YAML
    assert RULES_YAML.is_file(), RULES_YAML


def test_load_exemptions_reads_the_veto_without_validating_rules(tmp_path):
    """The seeder path must not be breakable by one malformed rule."""
    path = _write_rules(tmp_path, {
        "exempt_projects": {"agov": "MANUAL-ONLY"},
        "rules": [{"id": "broken", "project": "nope"}],  # would fail load_rules
    })
    ruleset = load_exemptions(path)
    assert ruleset.is_exempt("agov")
    assert not ruleset.is_exempt("hgx")
    with pytest.raises(PolicyRuleError):
        load_rules(path, projects=_PROJECTS)


def test_unreadable_exemption_list_exempts_everything(tmp_path):
    """Fail CLOSED: if we cannot prove a card is safe to rewrite, none is."""
    ruleset = load_exemptions(tmp_path / "does-not-exist.yaml")
    assert ruleset.is_exempt("hgx")
    assert ruleset.is_exempt("anything-at-all")
    projects = {"hgx": Project("hgx", "hgx-", "policy")}
    assert apply_policy_block("work", "hgx-1", projects, ruleset) == "work"


def test_shipped_exemptions_match_the_validating_loader():
    assert load_exemptions().exempt_projects == load_rules().exempt_projects


# ---------------------------------------------------------------------------
# task_factory integration — new rows are born card-linked
# ---------------------------------------------------------------------------

class _InsertCapturingConn(_FakeConn):
    """SELECT-for-existence returns nothing; INSERT params are kept."""

    def execute(self, sql, params=()):
        head = sql.strip().split(None, 1)[0].upper()
        if head == "SELECT":
            return _FakeCursor([])
        self.writes.append((head, params))
        return _FakeCursor([])

    def close(self):
        pass

    def rollback(self):  # pragma: no cover - only on failure paths
        pass


@pytest.fixture
def _seeded(monkeypatch):
    """Patch the module OBJECTS task_factory resolves at call time.

    ``create_tasks`` imports ``get_connection`` and ``init_kanban_tables``
    inside the function body, so patching the attribute on the source module is
    what takes effect — patching a name in ``task_factory`` would not.
    """
    import importlib

    storage = importlib.import_module("tools.db.storage")
    init_db = importlib.import_module("tools.kanban.init_db")
    conn = _InsertCapturingConn([])
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(init_db, "init_kanban_tables", lambda *a, **k: None)
    return conn


def _inserted_description(conn):
    inserts = [p for head, p in conn.writes if head == "INSERT"]
    assert len(inserts) == 1, conn.writes
    return inserts[0][2]  # (id, title, description, ...)


def test_create_tasks_stamps_the_card_policy_block(_seeded):
    """A freshly seeded row is born card-linked, so it never needs a rule."""
    from tools.kanban.task_factory import create_tasks

    created = create_tasks([{
        "id": "hgx-fresh-01",
        "title": "Fresh hgx task",
        "description": "BUILD the thing.",
        "task_type": "build",
    }])
    assert created == ["hgx-fresh-01"]
    desc = _inserted_description(_seeded)
    assert desc.startswith("<!-- icdev:policy hgx -->")
    assert "BUILD the thing." in desc
    assert load_projects()["hgx"].policy in desc


def test_create_tasks_leaves_an_exempt_cards_row_alone(_seeded):
    """AC2 again, at the seeding end: AGOV rows are never stamped."""
    from tools.kanban.task_factory import create_tasks

    body = "Open the PR with `gh pr create --draft`."
    create_tasks([{"id": "agov-det-99", "title": "t", "description": body,
                   "task_type": "build"}])
    assert _inserted_description(_seeded) == body


def test_create_tasks_leaves_a_card_without_policy_alone(_seeded):
    from tools.kanban.task_factory import create_tasks

    body = "Do the chore."
    create_tasks([{"id": "kax-merge-99", "title": "t", "description": body,
                   "task_type": "chore"}])
    assert _inserted_description(_seeded) == body


def test_block_markers_are_matched_per_project():
    desc = render_block("hgx", "A") + "\n\nwork"
    assert find_block(desc, "hgx") is not None
    assert find_block(desc, "agov") is None


def test_render_block_round_trips_multiline_policy():
    policy = "line one\nline two\nline three"
    block = render_block("hgx", policy)
    assert find_block(block, "hgx").group("body") == policy
    assert not re.search(r"\n\n<!-- /icdev:policy", block)
