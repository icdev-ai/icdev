# CUI // SP-CTI
"""Tests for Plan-Execute-Verify step verification (agx-verify-03).

The load-bearing rule is FAIL-CLOSED: a check that cannot gather evidence must
NEVER read as ``verified``. "Verify the verifier" — a verification step that
silently passes is worse than none — is asserted directly here.
"""
from __future__ import annotations

import sqlite3

from tools.kanban import pev


# ── policy composition (deterministic-picker) ───────────────────────────────

def test_verified_continues():
    assert pev.compose_step_policy("verified") == pev.CONTINUE


def test_contradicted_replans_then_halts_when_budget_exhausted():
    assert pev.compose_step_policy("contradicted", replans_used=0, max_replans=2) == pev.REPLAN
    assert pev.compose_step_policy("contradicted", replans_used=2, max_replans=2) == pev.HALT


def test_unverified_halts_fail_closed():
    # Fail-closed: no evidence must never proceed.
    assert pev.compose_step_policy("unverified") == pev.HALT


def test_unknown_verdict_normalizes_to_unverified_and_halts():
    assert pev.normalize_verdict("banana") == pev.UNVERIFIED
    assert pev.compose_step_policy("banana") == pev.HALT


# ── file check (cwd-safe) ────────────────────────────────────────────────────

def test_file_exists_verified(tmp_path):
    (tmp_path / "made.py").write_text("x = 1", encoding="utf-8")
    verdict, _ = pev.verify_file_exists("made.py", base_dir=str(tmp_path))
    assert verdict == pev.VERIFIED


def test_missing_file_is_contradicted_not_verified(tmp_path):
    # A claimed artifact that is absent is a CONTRADICTION (phantom), never verified.
    verdict, reason = pev.verify_file_exists("ghost.py", base_dir=str(tmp_path))
    assert verdict == pev.CONTRADICTED
    assert verdict != pev.VERIFIED


def test_file_check_uses_base_dir_not_cwd(tmp_path):
    # The same relative path exists under base_dir but not the repo root — the
    # check must resolve against base_dir (the worktree), dodging the
    # "route verifier reads MAIN checkout" trap.
    (tmp_path / "local_only.py").write_text("1", encoding="utf-8")
    v_local, _ = pev.verify_file_exists("local_only.py", base_dir=str(tmp_path))
    v_root, _ = pev.verify_file_exists("local_only.py")  # repo root — absent
    assert v_local == pev.VERIFIED and v_root == pev.CONTRADICTED


# ── "verify the verifier": pytest checks must not silently pass ──────────────

def test_zero_collected_tests_is_unverified_not_verified():
    # The whole point: "no tests ran" is NOT success.
    runner = lambda cmd, cwd: (5, "no tests ran in 0.01s")  # noqa: E731
    verdict, reason = pev.verify_test_passed("tests/nope.py", runner=runner)
    assert verdict == pev.UNVERIFIED
    assert verdict != pev.VERIFIED


def test_collected_zero_items_is_unverified():
    runner = lambda cmd, cwd: (0, "collected 0 items")  # noqa: E731
    verdict, _ = pev.verify_test_passed("t.py", runner=runner)
    assert verdict == pev.UNVERIFIED


def test_real_pass_is_verified():
    runner = lambda cmd, cwd: (0, "5 passed in 0.3s")  # noqa: E731
    verdict, _ = pev.verify_test_passed("t.py", runner=runner)
    assert verdict == pev.VERIFIED


def test_failing_tests_is_contradicted():
    runner = lambda cmd, cwd: (1, "1 failed, 2 passed")  # noqa: E731
    verdict, _ = pev.verify_test_passed("t.py", runner=runner)
    assert verdict == pev.CONTRADICTED


def test_broken_runner_is_unverified_never_verified():
    def boom(cmd, cwd):
        raise RuntimeError("pytest binary missing")
    verdict, _ = pev.verify_test_passed("t.py", runner=boom)
    assert verdict == pev.UNVERIFIED


# ── route check: no fabricated pass without a prober ─────────────────────────

def test_route_without_prober_is_unverified():
    verdict, _ = pev.verify_route_responds("/health")
    assert verdict == pev.UNVERIFIED


def test_route_200_verified_and_500_contradicted():
    ok = pev.verify_route_responds("/health", base_url="http://x", prober=lambda u: 200)
    bad = pev.verify_route_responds("/health", base_url="http://x", prober=lambda u: 500)
    assert ok[0] == pev.VERIFIED and bad[0] == pev.CONTRADICTED


# ── migration check ──────────────────────────────────────────────────────────

def test_migration_present_verified(tmp_path):
    mig = tmp_path / "tools" / "db" / "migrations" / "999_thing"
    mig.mkdir(parents=True)
    verdict, _ = pev.verify_migration_applied("999_thing", base_dir=str(tmp_path))
    assert verdict == pev.VERIFIED


def test_migration_absent_contradicted(tmp_path):
    (tmp_path / "tools" / "db" / "migrations").mkdir(parents=True)
    verdict, _ = pev.verify_migration_applied("404_ghost", base_dir=str(tmp_path))
    assert verdict == pev.CONTRADICTED


# ── run_plan loop ────────────────────────────────────────────────────────────

def test_run_plan_halts_on_first_contradiction(tmp_path):
    (tmp_path / "a.py").write_text("1", encoding="utf-8")
    steps = [
        {"name": "s1", "type": "file", "target": "a.py"},
        {"name": "s2", "type": "file", "target": "ghost.py"},  # contradicted -> replan
        {"name": "s3", "type": "file", "target": "a.py"},       # never reached
    ]
    out = pev.run_plan("t-1", steps, base_dir=str(tmp_path), record=False)
    assert out["final_action"] == pev.REPLAN
    assert len(out["steps"]) == 2  # stopped at the contradiction
    assert out["passed"] is False


def test_run_plan_all_verified_passes(tmp_path):
    (tmp_path / "a.py").write_text("1", encoding="utf-8")
    steps = [{"name": "s1", "type": "file", "target": "a.py"}]
    out = pev.run_plan("t-2", steps, base_dir=str(tmp_path), record=False)
    assert out["final_action"] == pev.CONTINUE and out["passed"] is True


def test_unknown_step_type_halts():
    out = pev.run_plan("t-3", [{"name": "s", "type": "mystery", "target": "x"}], record=False)
    assert out["steps"][0]["verdict"] == pev.UNVERIFIED
    assert out["final_action"] == pev.HALT


# ── append-only trail (reuse kanban_verifications; no new table) ─────────────

def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE kanban_verifications (id TEXT PRIMARY KEY, task_id TEXT, "
        "verified_at TEXT, result TEXT, reason TEXT, specific_checks TEXT)"
    )

    class _Wrap:
        def execute(self, sql, params=()):
            return conn.execute(sql.replace("%s", "?"), params)

        def commit(self):
            return conn.commit()

        def raw(self):
            return conn
    return _Wrap()


def test_record_step_verification_writes_row():
    conn = _mk_conn()
    result = {"name": "s1", "type": "file", "verdict": pev.CONTRADICTED,
              "reason": "absent", "action": pev.REPLAN}
    out = pev.record_step_verification("task-9", result, conn=conn)
    assert out["written"] is True
    row = conn.raw().execute(
        "SELECT result, specific_checks FROM kanban_verifications WHERE task_id='task-9'"
    ).fetchone()
    assert row[0] == "failed"  # contradicted -> failed
    import json
    assert json.loads(row[1])["pev"] is True


def test_record_completion_pev_noop_without_env(monkeypatch):
    monkeypatch.delenv("ICDEV_KANBAN_PEV", raising=False)
    assert pev.record_completion_pev("t", verified=True, reason="ok") is None


def test_record_completion_pev_phantom_is_contradicted(monkeypatch):
    monkeypatch.setenv("ICDEV_KANBAN_PEV", "1")
    captured = {}

    def _capture(task_id, step_result, **kw):
        captured.update(step_result)
        return {"written": True}

    monkeypatch.setattr(pev, "record_step_verification", _capture)
    pev.record_completion_pev("t-p", verified=False, reason="PHANTOM COMPLETION")
    # phantom completion -> contradicted verdict -> replan (bounded) action
    assert captured["verdict"] == pev.CONTRADICTED
    assert captured["action"] == pev.REPLAN


def test_record_completion_pev_verified_maps_to_verified(monkeypatch):
    monkeypatch.setenv("ICDEV_KANBAN_PEV", "1")
    captured = {}
    monkeypatch.setattr(pev, "record_step_verification",
                        lambda t, r, **k: captured.update(r) or {"written": True})
    pev.record_completion_pev("t-ok", verified=True, reason="done")
    assert captured["verdict"] == pev.VERIFIED and captured["action"] == pev.CONTINUE
