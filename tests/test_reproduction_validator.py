# CUI // SP-CTI
"""Tests for the reproduce-or-drop rule (oss-poc-01).

The load-bearing test is :class:`TestDiscrimination`, which is the task's
success criterion: a *seeded, deliberately-fixed* vulnerability is confirmed by
a replay that then FAILS once the fix is applied. Two loopback servers are
stood up from the same handler — one with the authz check missing, one with it
present — and the same reproduction is replayed against both. A reproduction
that fires against both is a tautology and the module must say so.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tools.security import reproduction_validator as rv

MIGRATION = (
    Path(rv.__file__).resolve().parents[2]
    / "tools"
    / "db"
    / "migrations"
    / "303_dynamic_finding_reproductions.sql"
)


# ---------------------------------------------------------------------------
# Seeded target — one handler, one switch
# ---------------------------------------------------------------------------
SECRET_MARKER = "TENANT_B_SALARY_ROLL"


def _make_handler(*, authz_enforced: bool):
    """Build a handler for /admin/secret with the authz check on or off.

    The seeded defect is the documented ICDEV pathology: an endpoint that
    fails open when the caller has no entitlement. ``authz_enforced=False`` is
    the vulnerable build; ``True`` is the fix.
    """

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
            if self.path != "/admin/secret":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found")
                return

            entitled = self.headers.get("X-Role", "") == "admin"
            if authz_enforced and not entitled:
                body = b'{"error": "forbidden"}'
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            body = json.dumps({"records": [SECRET_MARKER]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: A003 — silence the test server
            pass

    return _Handler


class _Target:
    """A loopback HTTP server with a known base URL."""

    def __init__(self, *, authz_enforced: bool):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(authz_enforced=authz_enforced))
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def vulnerable_target():
    target = _Target(authz_enforced=False)
    yield target
    target.close()


@pytest.fixture
def fixed_target():
    target = _Target(authz_enforced=True)
    yield target
    target.close()


def _authz_reproduction(base_url: str = "") -> rv.Reproduction:
    """Unauthenticated GET that should be refused; asserts it was served."""
    return rv.Reproduction(
        kind="http",
        target=base_url,
        steps=[{"method": "GET", "path": "/admin/secret"}],
        predicate={
            "type": "all_of",
            "clauses": [
                {"type": "status_in", "codes": [200]},
                {"type": "body_contains", "value": SECRET_MARKER},
            ],
        },
        description="unentitled caller receives tenant-scoped records",
    )


# ---------------------------------------------------------------------------
# The success criterion
# ---------------------------------------------------------------------------
class TestDiscrimination:
    def test_reproduction_fires_on_vulnerable_and_stops_on_fixed(self, vulnerable_target, fixed_target):
        """The reproduction must discriminate, not merely run."""
        proof = rv.verify_discrimination(
            _authz_reproduction(),
            vulnerable_target=vulnerable_target.base_url,
            fixed_target=fixed_target.base_url,
        )

        assert proof.vulnerable.outcome == rv.ReplayOutcome.reproduced
        assert proof.fixed.outcome == rv.ReplayOutcome.not_reproduced
        assert proof.discriminating is True

    def test_tautological_reproduction_is_rejected(self, vulnerable_target, fixed_target):
        """A predicate that accepts the fixed response too proves nothing."""
        tautology = rv.Reproduction(
            kind="http",
            steps=[{"method": "GET", "path": "/admin/secret"}],
            # Accepts both the 200 leak and the 403 refusal.
            predicate={"type": "status_in", "codes": [200, 403]},
        )
        proof = rv.verify_discrimination(
            tautology,
            vulnerable_target=vulnerable_target.base_url,
            fixed_target=fixed_target.base_url,
        )
        assert proof.discriminating is False
        assert "tautology" in proof.reason

    def test_inverted_predicate_is_rejected(self, vulnerable_target, fixed_target):
        inverted = rv.Reproduction(
            kind="http",
            steps=[{"method": "GET", "path": "/admin/secret"}],
            predicate={"type": "status_in", "codes": [403]},
        )
        proof = rv.verify_discrimination(
            inverted,
            vulnerable_target=vulnerable_target.base_url,
            fixed_target=fixed_target.base_url,
        )
        assert proof.discriminating is False
        assert "inverted" in proof.reason

    def test_indecisive_replay_never_claims_discrimination(self, vulnerable_target):
        proof = rv.verify_discrimination(
            _authz_reproduction(),
            vulnerable_target=vulnerable_target.base_url,
            fixed_target="http://evil.example.com",  # refused, not decisive
        )
        assert proof.discriminating is False
        assert proof.fixed.outcome == rv.ReplayOutcome.refused


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------
class TestReproduceOrDrop:
    def test_finding_without_reproduction_is_unconfirmed_and_never_blocks(self):
        verdict = rv.classify_finding(
            {"finding_key": "F-1", "severity": "critical", "analysis_kind": "dynamic"}
        )
        assert verdict.status == rv.FindingStatus.unconfirmed
        assert verdict.gate_blocking is False
        assert "no stored reproduction" in verdict.reason

    def test_reproduced_finding_is_confirmed_and_may_block(self, vulnerable_target):
        verdict = rv.classify_finding(
            {
                "finding_key": "F-2",
                "severity": "high",
                "analysis_kind": "dynamic",
                "reproduction": _authz_reproduction(vulnerable_target.base_url).to_dict(),
            }
        )
        assert verdict.status == rv.FindingStatus.confirmed
        assert verdict.gate_blocking is True

    def test_confirmed_finding_below_blocking_severity_does_not_block(self, vulnerable_target):
        verdict = rv.classify_finding(
            {
                "finding_key": "F-3",
                "severity": "low",
                "analysis_kind": "dynamic",
                "reproduction": _authz_reproduction(vulnerable_target.base_url).to_dict(),
            }
        )
        assert verdict.status == rv.FindingStatus.confirmed
        assert verdict.gate_blocking is False

    def test_previously_confirmed_finding_becomes_remediated_after_fix(self, fixed_target):
        verdict = rv.classify_finding(
            {
                "finding_key": "F-4",
                "severity": "high",
                "analysis_kind": "dynamic",
                "reproduction": _authz_reproduction(fixed_target.base_url).to_dict(),
            },
            prior_status=rv.FindingStatus.confirmed,
        )
        assert verdict.status == rv.FindingStatus.remediated
        assert verdict.gate_blocking is False

    def test_first_replay_that_does_not_fire_is_unconfirmed_not_remediated(self, fixed_target):
        """Never-established is not the same as fixed."""
        verdict = rv.classify_finding(
            {
                "finding_key": "F-5",
                "severity": "high",
                "analysis_kind": "dynamic",
                "reproduction": _authz_reproduction(fixed_target.base_url).to_dict(),
            }
        )
        assert verdict.status == rv.FindingStatus.unconfirmed

    def test_indecisive_replay_leaves_finding_unconfirmed(self):
        verdict = rv.classify_finding(
            {
                "finding_key": "F-6",
                "severity": "critical",
                "analysis_kind": "dynamic",
                "reproduction": _authz_reproduction("http://127.0.0.1:1").to_dict(),
            }
        )
        assert verdict.status == rv.FindingStatus.unconfirmed
        assert verdict.gate_blocking is False
        assert verdict.replay.outcome == rv.ReplayOutcome.error

    def test_static_findings_pass_through_untouched(self):
        verdict = rv.classify_finding(
            {"finding_key": "B-1", "severity": "high", "analysis_kind": "static"}
        )
        assert verdict.status == rv.FindingStatus.confirmed
        assert verdict.gate_blocking is True
        assert "does not apply" in verdict.reason

    def test_enforce_gate_ignores_unconfirmed_findings(self, vulnerable_target, fixed_target):
        report = rv.enforce(
            [
                {"finding_key": "G-1", "severity": "critical", "analysis_kind": "dynamic"},
                {
                    "finding_key": "G-2",
                    "severity": "critical",
                    "analysis_kind": "dynamic",
                    "reproduction": _authz_reproduction(fixed_target.base_url).to_dict(),
                },
                {
                    "finding_key": "G-3",
                    "severity": "critical",
                    "analysis_kind": "dynamic",
                    "reproduction": _authz_reproduction(vulnerable_target.base_url).to_dict(),
                },
            ],
            persist=False,
        )
        assert len(report.unconfirmed) == 2
        assert len(report.confirmed) == 1
        assert [v.finding_key for v in report.blocking] == ["G-3"]
        assert report.to_dict()["gate_pass"] is False


# ---------------------------------------------------------------------------
# Scope lock
# ---------------------------------------------------------------------------
class TestScopeLock:
    def test_non_allowlisted_host_is_refused_without_a_request(self):
        result = rv.replay(_authz_reproduction("http://scanme.example.org"))
        assert result.outcome == rv.ReplayOutcome.refused
        assert result.observations == []

    def test_loopback_is_allowlisted(self):
        assert rv.is_target_allowed("http://127.0.0.1:5050") is True
        assert rv.is_target_allowed("http://localhost:5050/x") is True

    def test_env_override_extends_the_allowlist(self, monkeypatch):
        assert rv.is_target_allowed("http://staging.internal") is False
        monkeypatch.setenv("ICDEV_REPRO_TARGET_ALLOWLIST", "staging.internal")
        assert rv.is_target_allowed("http://staging.internal") is True

    def test_unparseable_target_is_refused(self):
        assert rv.is_target_allowed("not a url") is False
        assert rv.is_target_allowed("") is False


# ---------------------------------------------------------------------------
# Predicate hygiene — the static half of "must discriminate"
# ---------------------------------------------------------------------------
class TestPredicateValidation:
    def test_permissive_status_list_is_rejected(self):
        errors = rv.validate_predicate({"type": "status_in", "codes": list(range(200, 212))})
        assert any("too permissive" in e for e in errors)

    def test_bare_negative_predicate_is_rejected(self):
        errors = rv.validate_predicate({"type": "body_not_contains", "value": "forbidden"})
        assert any("cannot stand alone" in e for e in errors)

    def test_negative_predicate_is_allowed_inside_a_positive_conjunction(self):
        assert (
            rv.validate_predicate(
                {
                    "type": "all_of",
                    "clauses": [
                        {"type": "status_in", "codes": [200]},
                        {"type": "body_not_contains", "value": "forbidden"},
                    ],
                }
            )
            == []
        )

    def test_all_negative_conjunction_is_rejected(self):
        errors = rv.validate_predicate(
            {
                "type": "all_of",
                "clauses": [
                    {"type": "body_not_contains", "value": "a"},
                    {"type": "body_not_contains", "value": "b"},
                ],
            }
        )
        assert any("only negative clauses" in e for e in errors)

    def test_regex_matching_the_empty_string_is_rejected(self):
        errors = rv.validate_predicate({"type": "body_regex", "pattern": "x*"})
        assert any("empty string" in e for e in errors)

    def test_empty_substring_is_rejected(self):
        assert rv.validate_predicate({"type": "body_contains", "value": "   "})

    def test_unknown_predicate_type_is_rejected(self):
        assert rv.validate_predicate({"type": "vibes"})

    def test_unknown_predicate_never_evaluates_true(self):
        assert rv.evaluate_predicate({"type": "vibes"}, {"status": 200, "body": "x"}) is False

    def test_empty_all_of_never_evaluates_true(self):
        assert rv.evaluate_predicate({"type": "all_of", "clauses": []}, {"status": 200}) is False


class TestReproductionValidation:
    def test_stepless_reproduction_is_rejected(self):
        errors = rv.validate_reproduction(
            rv.Reproduction(kind="http", target="http://127.0.0.1:1", steps=[])
        )
        assert any("no steps" in e for e in errors)

    def test_step_without_path_is_rejected(self):
        errors = rv.validate_reproduction(
            rv.Reproduction(
                kind="http",
                target="http://127.0.0.1:1",
                steps=[{"method": "GET"}],
                predicate={"type": "status_in", "codes": [200]},
            )
        )
        assert any("requires a 'path'" in e for e in errors)

    def test_unknown_kind_is_rejected(self):
        errors = rv.validate_reproduction(rv.Reproduction(kind="telepathy", steps=[{"path": "/"}]))
        assert any("unknown reproduction kind" in e for e in errors)

    def test_invalid_reproduction_replays_as_error_not_as_reproduced(self):
        result = rv.replay(rv.Reproduction(kind="http", target="http://127.0.0.1:1", steps=[]))
        assert result.outcome == rv.ReplayOutcome.error

    def test_roundtrip_serialization(self):
        repro = _authz_reproduction("http://127.0.0.1:5050")
        assert rv.Reproduction.from_dict(repro.to_dict()).to_dict() == repro.to_dict()


# ---------------------------------------------------------------------------
# Agent action traces (oss-browse-01 not yet installed)
# ---------------------------------------------------------------------------
class TestAgentTraceReplay:
    def test_unregistered_trace_replayer_yields_unavailable_not_a_finding(self):
        repro = rv.Reproduction(
            kind="agent_trace",
            target="http://127.0.0.1:5050",
            steps=[{"path": "/admin", "action": "click", "index": 3}],
            predicate={"type": "body_contains", "value": SECRET_MARKER},
        )
        result = rv.replay(repro)
        assert result.outcome == rv.ReplayOutcome.unavailable
        assert result.decisive is False

        verdict = rv.classify_finding(
            {
                "finding_key": "T-1",
                "severity": "critical",
                "analysis_kind": "dynamic",
                "reproduction": repro.to_dict(),
            },
            replay_result=result,
        )
        assert verdict.status == rv.FindingStatus.unconfirmed
        assert verdict.gate_blocking is False

    def test_registered_trace_replayer_is_used(self, monkeypatch):
        repro = rv.Reproduction(
            kind="agent_trace",
            target="http://127.0.0.1:5050",
            steps=[{"path": "/admin", "action": "click", "index": 3}],
            predicate={"type": "body_contains", "value": SECRET_MARKER},
        )
        monkeypatch.setattr(
            rv,
            "_TRACE_REPLAYER",
            lambda r: rv.ReplayResult(outcome=rv.ReplayOutcome.reproduced, detail="trace fired"),
        )
        assert rv.replay(repro).outcome == rv.ReplayOutcome.reproduced

    def test_trace_replayer_exception_degrades_to_error(self, monkeypatch):
        def _boom(_r):
            raise RuntimeError("driver gone")

        monkeypatch.setattr(rv, "_TRACE_REPLAYER", _boom)
        result = rv.replay(
            rv.Reproduction(
                kind="agent_trace",
                steps=[{"path": "/x"}],
                predicate={"type": "status_in", "codes": [200]},
            )
        )
        assert result.outcome == rv.ReplayOutcome.error


# ---------------------------------------------------------------------------
# Evidence hygiene — ICDEV is a public repo
# ---------------------------------------------------------------------------
class TestEvidenceHygiene:
    def test_response_bodies_are_not_retained_in_observations(self, vulnerable_target):
        result = rv.replay(_authz_reproduction(vulnerable_target.base_url))
        assert result.outcome == rv.ReplayOutcome.reproduced
        assert result.observations, "a reproduced replay must carry observations"
        for obs in result.observations:
            assert "body" not in obs
            assert obs["body_sha256"]
            assert SECRET_MARKER not in json.dumps(obs)


# ---------------------------------------------------------------------------
# Persistence — the audit trail behind a "confirmed" claim
# ---------------------------------------------------------------------------
class TestPersistence:
    def test_confirmed_finding_and_replay_attempt_are_recorded(
        self, vulnerable_target, icdev_db, monkeypatch
    ):
        monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
        from tools.db.storage import get_connection

        report = rv.enforce(
            [
                {
                    "finding_key": "P-1",
                    "severity": "high",
                    "analysis_kind": "dynamic",
                    "detector": "test",
                    "title": "authz fails open",
                    "reproduction": _authz_reproduction(vulnerable_target.base_url).to_dict(),
                }
            ]
        )
        assert report.confirmed

        conn = get_connection()
        finding = conn.execute(
            "SELECT status, discriminating FROM dynamic_findings WHERE finding_key = %s", ("P-1",)
        ).fetchone()
        attempts = conn.execute(
            "SELECT outcome, purpose FROM finding_replay_attempts WHERE finding_key = %s", ("P-1",)
        ).fetchall()
        conn.close()

        assert finding is not None
        assert finding[0] == rv.FindingStatus.confirmed
        assert finding[1] == 0, "discriminating is only set by verify_discrimination"
        assert [a[0] for a in attempts] == [rv.ReplayOutcome.reproduced]

    def test_recheck_after_fix_updates_status_and_appends_a_second_attempt(
        self, vulnerable_target, fixed_target, icdev_db, monkeypatch
    ):
        monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
        from tools.db.storage import get_connection

        finding = {
            "finding_key": "P-2",
            "severity": "high",
            "analysis_kind": "dynamic",
            "reproduction": _authz_reproduction(vulnerable_target.base_url).to_dict(),
        }
        assert rv.enforce([finding]).confirmed

        # Same finding, same reproduction shape — now pointed at the fixed build.
        finding["status"] = rv.FindingStatus.confirmed
        finding["reproduction"] = _authz_reproduction(fixed_target.base_url).to_dict()
        assert rv.enforce([finding]).remediated

        conn = get_connection()
        status = conn.execute(
            "SELECT status FROM dynamic_findings WHERE finding_key = %s", ("P-2",)
        ).fetchone()[0]
        outcomes = [
            r[0]
            for r in conn.execute(
                "SELECT outcome FROM finding_replay_attempts WHERE finding_key = %s "
                "ORDER BY created_at, id",
                ("P-2",),
            ).fetchall()
        ]
        conn.close()

        assert status == rv.FindingStatus.remediated
        assert sorted(outcomes) == sorted(
            [rv.ReplayOutcome.reproduced, rv.ReplayOutcome.not_reproduced]
        )

    def test_mark_discriminating_sets_the_flag(self, vulnerable_target, fixed_target, icdev_db, monkeypatch):
        monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
        from tools.db.storage import get_connection

        rv.enforce(
            [
                {
                    "finding_key": "P-3",
                    "severity": "high",
                    "analysis_kind": "dynamic",
                    "reproduction": _authz_reproduction(vulnerable_target.base_url).to_dict(),
                }
            ]
        )
        proof = rv.verify_discrimination(
            _authz_reproduction(),
            vulnerable_target=vulnerable_target.base_url,
            fixed_target=fixed_target.base_url,
        )
        assert rv.mark_discriminating("P-3", proof) is True

        conn = get_connection()
        flag = conn.execute(
            "SELECT discriminating FROM dynamic_findings WHERE finding_key = %s", ("P-3",)
        ).fetchone()[0]
        conn.close()
        assert flag == 1

    def test_mark_discriminating_refuses_an_unproven_reproduction(self):
        assert rv.mark_discriminating("P-4", rv.DiscriminationProof(discriminating=False)) is False

    def test_persistence_failure_never_breaks_the_rule(self, vulnerable_target, monkeypatch):
        """An un-migrated checkout still gets the verdict, just no audit row."""
        # Shim-aware: `tools.db.storage` and `icdev.tools.db.storage` are
        # distinct module objects, and _persist_verdict imports the former.
        import importlib

        storage = importlib.import_module("tools.db.storage")

        def _boom(*_a, **_k):
            raise RuntimeError("no database")

        monkeypatch.setattr(storage, "get_connection", _boom)
        report = rv.enforce(
            [
                {
                    "finding_key": "P-5",
                    "severity": "high",
                    "analysis_kind": "dynamic",
                    "reproduction": _authz_reproduction(vulnerable_target.base_url).to_dict(),
                }
            ]
        )
        assert [v.status for v in report.verdicts] == [rv.FindingStatus.confirmed]


# ---------------------------------------------------------------------------
# Schema/constant coherence
# ---------------------------------------------------------------------------
class TestSchemaConstantSync:
    """The migration's CHECK list must track the Python vocabulary.

    CLAUDE.md: SQL CHECK constraints derive from Python constants, never
    hardcode. SQL cannot import, so the enforcement is this test — if someone
    adds a FindingStatus without widening the CHECK, every write of the new
    status would fail at runtime with an opaque IntegrityError.
    """

    def test_status_check_matches_finding_status_constants(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        match = re.search(r"CHECK \(status IN \(([^)]*)\)\)", sql)
        assert match, "migration 303 must constrain dynamic_findings.status"
        in_sql = {v.strip().strip("'") for v in match.group(1).split(",")}
        in_python = {
            getattr(rv.FindingStatus, name)
            for name in vars(rv.FindingStatus)
            if not name.startswith("_")
        }
        assert in_sql == in_python

    def test_documented_outcomes_match_replay_outcome_constants(self):
        """`outcome` is free-text by design; the comment above it is the contract.

        A CHECK here would be wrong — a new ReplayOutcome must be *writable*
        before the migration that widens it lands, or a mid-upgrade replay
        would fail to record its own evidence. The comment carries the
        vocabulary instead, so this test keeps it honest.
        """
        sql = MIGRATION.read_text(encoding="utf-8")
        lines = sql.splitlines()
        comment = next(
            (
                lines[i - 1]
                for i, line in enumerate(lines)
                if line.strip().startswith("outcome ") and i
            ),
            "",
        )
        documented = set(re.findall(r"'(\w+)'", comment))
        in_python = {
            getattr(rv.ReplayOutcome, name)
            for name in vars(rv.ReplayOutcome)
            if not name.startswith("_")
        }
        assert documented == in_python, f"comment above `outcome` documents {documented}"


# ---------------------------------------------------------------------------
# Rubric-loop integration
# ---------------------------------------------------------------------------
class _FakeLoopResult:
    def __init__(self, final_content: str):
        self.final_content = final_content
        self.done = True


class TestAgentReproductionParsing:
    def test_parses_fenced_json(self):
        repro = rv.parse_agent_reproduction(
            "Here is my PoC:\n```json\n"
            + json.dumps(_authz_reproduction("http://127.0.0.1:5050").to_dict())
            + "\n```\nDone."
        )
        assert repro is not None
        assert repro.steps[0]["path"] == "/admin/secret"

    def test_prose_without_json_yields_none(self):
        assert rv.parse_agent_reproduction("I am confident this endpoint is vulnerable.") is None


class TestReproductionGrader:
    def test_grader_rejects_prose_without_a_poc(self):
        grader = rv.make_reproduction_grader()
        grade = grader(_FakeLoopResult("This endpoint definitely fails open to admin."))
        assert grade.verdict == "needs_revision"
        assert "No reproduction found" in grade.feedback

    def test_grader_rejects_an_unreplayable_poc(self):
        grader = rv.make_reproduction_grader()
        grade = grader(
            _FakeLoopResult(json.dumps({"kind": "http", "target": "http://127.0.0.1:1", "steps": []}))
        )
        assert grade.verdict == "needs_revision"
        assert "not replayable" in grade.feedback

    def test_grader_satisfied_only_when_the_poc_actually_fires(self, vulnerable_target):
        grader = rv.make_reproduction_grader()
        payload = json.dumps(_authz_reproduction(vulnerable_target.base_url).to_dict())
        assert grader(_FakeLoopResult(payload)).verdict == "satisfied"

    def test_grader_rejects_a_poc_that_runs_but_does_not_fire(self, fixed_target):
        grader = rv.make_reproduction_grader()
        payload = json.dumps(_authz_reproduction(fixed_target.base_url).to_dict())
        grade = grader(_FakeLoopResult(payload))
        assert grade.verdict == "needs_revision"
        assert "did NOT fire" in grade.feedback
