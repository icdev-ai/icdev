#!/usr/bin/env python3
# CUI // SP-CTI
"""rmf-zt-01 — a ZT check with no probe behind it reports ``unknown``, never a pass.

``device_compliance_scanner.scan_device()`` evaluated every CIS/STIG check as
``bool(ctx.get(check_id, True))``. Absent probe data read as a PASS, so a device
nothing had measured scored 100% compliant and the ZIG device-pillar maturity
number was computed over it. Measured on the SQLite canvas corpus 2026-09-02:
108 of 108 recorded checks passed, all six devices scored 1.0, and no caller
anywhere in the tree supplied a probe — a flip count of 96 (88.89% of recorded
passes), with the remaining 12 derived checks undetermined from the record.

What these tests pin, in the order the defect is fixed:

  1. THREE VERDICTS. An absent probe, and an explicit ``None`` probe, are both
     ``unknown``. There is no arm that turns absence into a pass.
  2. UNKNOWN LEAVES BOTH SIDES OF EVERY RATIO. Adding an unprobed check must
     not move the score at all — not up as a pass, and not down as a failure.
  3. NO SCORE OVER AN EMPTY DENOMINATOR. ``compliance_score`` is ``None`` and
     ``overall_pass`` is ``None``, never 1.0/True and never 0.0/False.
  4. GAPS AND UNKNOWNS ARE DIFFERENT FINDINGS. A gap is a known deficiency with
     a remediation; an unknown is an unmeasured control with an instrumentation
     task. Merging them loses the distinction that makes either actionable.
  5. THE RECORD KEEPS THE THIRD STATE. ``verdict`` is persisted, an older
     canvas table gains the column in place, and the legacy ``passed`` integer
     records an unknown as 0 (fail-CLOSED) rather than 1.
  6. THE FLEET AND THE SUMMARY COUNT IT SEPARATELY, and an uninstrumented sweep
     does not mark the ZIG activity complete.
  7. ICDEV_ZT_ALLOW_STUB IS AUDITED — both the permit leg and the refusal leg —
     and a failed audit write is REPORTED rather than swallowed.
  8. A STUBBED DEPLOYMENT SAYS SO ON SCREEN, and its silence when the gate is
     closed is not an all-clear it has to fabricate.

NIST 800-53: AU-2, AU-12, CA-7, CM-6, SI-4
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import jinja2
import pytest

import tools.security.stub_gate as _gate
import tools.security_canvas.device_compliance_scanner as _dcs
from tools.db.storage import StorageConnection
from tools.security.device_trust import DeviceTrustResult

REPO_ROOT = Path(__file__).resolve().parents[2]
ALL_CHECKS = dict(_dcs.CIS_CONTROL_CHECKS) | dict(_dcs.STIG_CHECKS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def canvas_conn(monkeypatch):
    """One in-memory canvas DB held open across a whole test.

    ``scan_device`` closes its connection in a ``finally``; an in-memory SQLite
    database dies with its connection, so the close is neutered here in order to
    read back what the scan actually persisted.
    """
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    conn = StorageConnection(raw, "sqlite")
    conn.close = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(_dcs, "get_connection", lambda: conn)
    return conn


@pytest.fixture(autouse=True)
def no_real_audit(monkeypatch):
    """Never write to the real audit_trail from a unit test.

    The recorded calls are what the audit assertions read, so the seam is
    observed rather than merely silenced.
    """
    calls: list[dict] = []

    def _record(**kwargs):
        calls.append(kwargs)
        return {"recorded": True, "entry_id": len(calls), "action": "test"}

    monkeypatch.setattr(_dcs, "record_stub_decision", _record)
    return calls


def _posture(monkeypatch, **kwargs):
    result = DeviceTrustResult(**kwargs)
    monkeypatch.setattr(_dcs, "verify_device_posture", lambda device_id: result)
    return result


def _healthy(monkeypatch):
    return _posture(
        monkeypatch,
        trusted=True,
        device_id="d",
        health_score=0.92,
        status="healthy",
        last_seen_seconds_ago=120,
    )


def _not_evaluated(monkeypatch):
    """Device trust switched off — the DEFAULT on this deployment."""
    return _posture(monkeypatch, trusted=True, reason="device trust not required")


# ---------------------------------------------------------------------------
# 1. Three verdicts
# ---------------------------------------------------------------------------


class TestThreeVerdicts:
    def test_absent_probe_is_unknown_never_pass(self, monkeypatch, canvas_conn):
        _not_evaluated(monkeypatch)

        result = _dcs.scan_device("h-absent.example.mil")

        verdicts = dict(result["cis_results"]) | dict(result["stig_results"])
        assert set(verdicts) == set(ALL_CHECKS)
        assert set(verdicts.values()) == {_dcs.UNKNOWN}, (
            "a check nobody probed must not report a pass"
        )

    def test_explicit_none_probe_is_unknown(self, monkeypatch, canvas_conn):
        _healthy(monkeypatch)

        result = _dcs.scan_device(
            "h-none.example.mil", context={"cc-01-inventory": None}
        )

        # A probe that RAN and could not determine the answer is exactly as
        # unmeasured as one that never ran; coercing it to a boolean would put
        # the fail-open default straight back.
        assert result["cis_results"]["cc-01-inventory"] == _dcs.UNKNOWN

    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, "pass"),
            (False, "fail"),
            ("pass", "pass"),
            ("fail", "fail"),
            ("unknown", "unknown"),
        ],
    )
    def test_probe_values_map_to_verdicts(self, monkeypatch, canvas_conn, value, expected):
        _healthy(monkeypatch)

        result = _dcs.scan_device(
            "h-map.example.mil", context={"cc-01-inventory": value}
        )

        assert result["cis_results"]["cc-01-inventory"] == expected

    def test_only_three_verdicts_exist(self):
        assert _dcs.VERDICTS == ("pass", "fail", "unknown")


# ---------------------------------------------------------------------------
# 2. Unknown leaves BOTH sides of every ratio
# ---------------------------------------------------------------------------


class TestUnknownIsExcludedFromBothSides:
    def test_adding_an_unprobed_check_does_not_move_the_score(self):
        """The invariant, stated over the reduction itself.

        One pass and one fail is 0.5. Adding twenty checks nobody probed must
        leave it at 0.5 — not 0.09 (unknown counted as failing) and not 0.95
        (unknown counted as passing). Asserted on ``_score`` rather than through
        a scan because the check catalogue is fixed, so no scan can vary the
        number of unknowns while holding the measured ones constant.
        """
        measured = {"a": _dcs.PASS, "b": _dcs.FAIL}
        score, n_measured, n_unknown = _dcs._score(measured)
        assert (score, n_measured, n_unknown) == (0.5, 2, 0)

        padded = dict(measured)
        padded.update({"u%d" % i: _dcs.UNKNOWN for i in range(20)})
        padded_score, padded_measured, padded_unknown = _dcs._score(padded)

        assert padded_score == score
        assert padded_measured == 2
        assert padded_unknown == 20

    def test_all_unknown_scores_none_not_zero_and_not_one(self):
        score, measured, unknown = _dcs._score({"a": _dcs.UNKNOWN, "b": _dcs.UNKNOWN})
        assert score is None
        assert (measured, unknown) == (0, 2)

    def test_one_pass_one_unknown_is_not_fifty_percent(self, monkeypatch, canvas_conn):
        _healthy(monkeypatch)

        result = _dcs.scan_device(
            "h-half.example.mil",
            context={"cc-01-inventory": True, "cc-02-software-inv": None},
        )

        # CIS: one measured pass. cc-07 is derived and MEASURED here (healthy
        # posture, last seen 120s) so it passes too => cis rate 1.0, not 0.5.
        assert result["cis_results"]["cc-02-software-inv"] == _dcs.UNKNOWN
        assert result["coverage"]["measured_checks"] == 3  # cc-01, cc-07, stig-antivirus
        assert result["compliance_score"] == 1.0

    def test_coverage_reports_the_denominator_it_used(self, monkeypatch, canvas_conn):
        _healthy(monkeypatch)

        result = _dcs.scan_device(
            "h-cov.example.mil", context={"stig-firewall": True}
        )

        cov = result["coverage"]
        assert cov["total_checks"] == len(ALL_CHECKS)
        assert cov["measured_checks"] + cov["unknown_checks"] == cov["total_checks"]
        assert cov["measured_pct"] == round(
            100.0 * cov["measured_checks"] / cov["total_checks"], 1
        )


# ---------------------------------------------------------------------------
# 3. No score over an empty denominator
# ---------------------------------------------------------------------------


class TestNoScoreOverAnEmptyDenominator:
    def test_nothing_measured_scores_none_not_one(self, monkeypatch, canvas_conn):
        _not_evaluated(monkeypatch)

        result = _dcs.scan_device("h-empty.example.mil")

        assert result["compliance_score"] is None
        assert result["score_basis"] == "unmeasured"
        assert result["overall_pass"] is None, (
            "an unmeasured device must not report a pass OR a fail"
        )

    def test_health_score_is_none_not_the_legacy_constant(self, monkeypatch, canvas_conn):
        _not_evaluated(monkeypatch)

        result = _dcs.scan_device("h-health.example.mil")

        assert result["health_score"] is None
        assert result["health_score"] != _dcs._LEGACY_DEV_HEALTH_SCORE
        assert result["health_basis"] == "unmeasured_posture_not_evaluated"

    def test_derived_checks_are_unknown_when_posture_is_not_measured(
        self, monkeypatch, canvas_conn
    ):
        # The old code read a fabricated last_seen of 0 as "reporting now" and
        # a non-evaluation's trusted=True as "antivirus active".
        _not_evaluated(monkeypatch)

        result = _dcs.scan_device("h-derived.example.mil")

        assert result["cis_results"]["cc-07-continuous-mon"] == _dcs.UNKNOWN
        assert result["stig_results"]["stig-antivirus"] == _dcs.UNKNOWN
        assert set(_dcs.DERIVED_CHECKS) == {"cc-07-continuous-mon", "stig-antivirus"}

    def test_fail_closed_zero_is_labelled_a_refusal(self, monkeypatch, canvas_conn):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        _posture(
            monkeypatch, trusted=False, device_id="d", health_score=0.0,
            status="unknown", reason="stub",
        )

        result = _dcs.scan_device("h-closed.example.mil")

        assert result["compliance_score"] == 0.0
        assert result["overall_pass"] is False
        # The zero is a POLICY VERDICT about an unverifiable device, not a
        # measurement of its STIGs — which were unknown either way.
        assert result["score_basis"] == "fail_closed_unknown_posture"


# ---------------------------------------------------------------------------
# 4. Gaps and unknowns are different findings
# ---------------------------------------------------------------------------


class TestGapsAndUnknownsAreSeparate:
    def test_unknown_never_appears_as_a_gap(self, monkeypatch, canvas_conn):
        _healthy(monkeypatch)

        result = _dcs.scan_device(
            "h-sep.example.mil", context={"cc-03-data-protect": False}
        )

        assert any("cc-03-data-protect" in g for g in result["gaps"])
        assert not any("cc-01-inventory" in g for g in result["gaps"])
        assert any("cc-01-inventory" in u for u in result["unknown_checks"])
        assert set(result["gaps"]).isdisjoint(result["unknown_checks"])


# ---------------------------------------------------------------------------
# 5. The record keeps the third state
# ---------------------------------------------------------------------------


class TestPersistedVerdict:
    def test_verdict_column_records_all_three(self, monkeypatch, canvas_conn):
        _healthy(monkeypatch)

        _dcs.scan_device(
            "h-persist.example.mil",
            context={"cc-01-inventory": True, "cc-03-data-protect": False},
        )

        rows = canvas_conn.execute(
            "SELECT verdict, COUNT(*) AS n FROM zig_device_compliance_scans "
            "GROUP BY verdict"
        ).fetchall()
        counts = {dict(r)["verdict"]: dict(r)["n"] for r in rows}
        assert counts.get("unknown", 0) > 0
        assert counts.get("pass", 0) > 0
        assert counts.get("fail", 0) == 1

    def test_legacy_passed_column_records_unknown_as_zero(self, monkeypatch, canvas_conn):
        """`passed` cannot spell `unknown`; it must degrade fail-CLOSED."""
        _not_evaluated(monkeypatch)

        _dcs.scan_device("h-legacy.example.mil")

        rows = canvas_conn.execute(
            "SELECT passed, verdict FROM zig_device_compliance_scans"
        ).fetchall()
        assert rows
        for row in rows:
            data = dict(row)
            assert data["verdict"] == "unknown"
            assert data["passed"] == 0, "an unknown must never be recorded as a pass"

    def test_pre_existing_table_gains_the_verdict_column(self, monkeypatch):
        """A canvas DB created before rmf-zt-01 keeps its old shape.

        ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so
        without the in-place ALTER the INSERT would raise on every scan of
        every device already on the deployment.
        """
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        raw.execute(
            "CREATE TABLE zig_device_compliance_scans ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL,"
            " scan_type TEXT NOT NULL, check_id TEXT NOT NULL,"
            " check_name TEXT NOT NULL, passed INTEGER NOT NULL DEFAULT 0,"
            " severity TEXT, detail TEXT, scanned_at TEXT)"
        )
        conn = StorageConnection(raw, "sqlite")
        conn.close = lambda: None  # type: ignore[method-assign]
        monkeypatch.setattr(_dcs, "get_connection", lambda: conn)
        _not_evaluated(monkeypatch)

        result = _dcs.scan_device("h-old-schema.example.mil")

        assert "verdict" in _dcs._column_names(conn, "zig_device_compliance_scans")
        assert result["coverage"]["unknown_checks"] == len(ALL_CHECKS)


# ---------------------------------------------------------------------------
# 6. Fleet + summary counts, and the ZIG activity
# ---------------------------------------------------------------------------


class TestFleetAndSummary:
    def test_unmeasured_devices_are_their_own_count(self, monkeypatch, canvas_conn):
        _not_evaluated(monkeypatch)
        recorded: list[tuple] = []
        monkeypatch.setattr(
            "tools.security_canvas.zig_activity_tracker.set_activity_status",
            lambda *a, **k: recorded.append(a) or {},
        )

        report = _dcs.run_fleet_scan(["a.example.mil", "b.example.mil"])

        assert report["unmeasured"] == 2
        assert report["passing"] == 0
        assert report["failing"] == 0, (
            "an uninstrumented fleet is not a broken fleet — different fix"
        )
        assert report["fleet_compliance_score"] is None

    def test_uninstrumented_sweep_does_not_complete_the_zig_activity(
        self, monkeypatch, canvas_conn
    ):
        _not_evaluated(monkeypatch)
        recorded: list[tuple] = []
        monkeypatch.setattr(
            "tools.security_canvas.zig_activity_tracker.set_activity_status",
            lambda *a, **k: recorded.append(a) or {},
        )

        report = _dcs.run_fleet_scan(["a.example.mil"])

        assert report["activity_status"] == "in_progress"
        assert recorded and recorded[0][1] == "in_progress"
        assert "NOTHING WAS MEASURED" in recorded[0][2]

    def test_measured_sweep_completes_the_activity_with_its_coverage(
        self, monkeypatch, canvas_conn
    ):
        _healthy(monkeypatch)
        recorded: list[tuple] = []
        monkeypatch.setattr(
            "tools.security_canvas.zig_activity_tracker.set_activity_status",
            lambda *a, **k: recorded.append(a) or {},
        )
        probes = {c: True for c in ALL_CHECKS}

        report = _dcs.run_fleet_scan(
            ["a.example.mil"], context_by_host={"a.example.mil": probes}
        )

        assert report["activity_status"] == "complete"
        assert report["passing"] == 1
        assert report["fleet_compliance_score"] == 1.0

    def test_summary_excludes_unmeasured_devices_from_both_sides(
        self, monkeypatch, canvas_conn
    ):
        _healthy(monkeypatch)
        _dcs.scan_device("measured.example.mil", context={c: True for c in ALL_CHECKS})
        _not_evaluated(monkeypatch)
        _dcs.scan_device("unmeasured.example.mil")

        summary = _dcs.get_compliance_summary()

        assert summary["total_devices"] == 2
        assert summary["measured_devices"] == 1
        assert summary["unmeasured_devices"] == 1
        assert summary["compliance_rate"] == 1.0, (
            "the unmeasured device must not drag the rate to 0.5"
        )

    def test_summary_rate_is_none_over_an_empty_denominator(
        self, monkeypatch, canvas_conn
    ):
        _not_evaluated(monkeypatch)
        _dcs.scan_device("only-unmeasured.example.mil")

        summary = _dcs.get_compliance_summary()

        assert summary["compliance_rate"] is None
        assert summary["avg_compliance_score"] is None


# ---------------------------------------------------------------------------
# 7. The stub gate is audited
# ---------------------------------------------------------------------------


class TestStubGateAudit:
    def test_permit_leg_is_audited(self, monkeypatch, canvas_conn, no_real_audit):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")
        _posture(monkeypatch, trusted=True, device_id="d", status="unknown")

        result = _dcs.scan_device("h-audit-permit.example.mil")

        assert len(no_real_audit) == 1
        assert no_real_audit[0]["honored"] is True
        assert no_real_audit[0]["component"] == "device_compliance_scanner"
        assert result["stub"]["audit"]["recorded"] is True

    def test_refusal_leg_is_audited_too(self, monkeypatch, canvas_conn, no_real_audit):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        _posture(monkeypatch, trusted=False, device_id="d", status="unknown")

        _dcs.scan_device("h-audit-deny.example.mil")

        # A surface that records only its POSITIVE outcome can answer "was this
        # permitted?" but never "was this evaluated?".
        assert len(no_real_audit) == 1
        assert no_real_audit[0]["honored"] is False

    def test_gate_not_consulted_when_posture_was_measured(
        self, monkeypatch, canvas_conn, no_real_audit
    ):
        _healthy(monkeypatch)

        result = _dcs.scan_device("h-audit-none.example.mil")

        assert no_real_audit == []
        assert result["stub"]["consulted"] is False

    def test_failed_audit_write_is_reported_never_swallowed(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("audit_trail unreachable")

        monkeypatch.setattr("tools.audit.audit_logger.log_event", _boom)

        outcome = _gate.record_stub_decision(
            component="device_compliance_scanner", subject="d", honored=True
        )

        assert outcome["recorded"] is False
        assert "unreachable" in outcome["error"]

    def test_event_type_is_admitted_by_the_audit_vocabulary(self):
        from tools.audit.audit_logger import VALID_EVENT_TYPES

        # An event type the deployed CHECK does not admit is rejected on
        # log_event's first line and every caller's best-effort except hides
        # it — the control would look audited while nothing was written.
        assert _gate.AUDIT_EVENT_TYPE in VALID_EVENT_TYPES


# ---------------------------------------------------------------------------
# 8. A stubbed deployment says so on screen
# ---------------------------------------------------------------------------

_BANNER_MARKER = 'id="zt-stub-banner"'


def _render_base(**context) -> str:
    env = jinja2.Environment(  # nosec B701 - assertion fixture, not a response
        loader=jinja2.FileSystemLoader(
            str(REPO_ROOT / "tools" / "dashboard" / "templates")
        ),
        autoescape=True,
    )
    env.globals.setdefault("url_for", lambda *a, **k: "#")
    env.globals.setdefault("request", type("R", (), {"path": "/security/"})())
    env.globals.setdefault("config", {})
    template = env.get_template("security_canvas/base.html")
    return template.render(**context)


class TestStandingStubBanner:
    def test_status_carries_a_banner_only_while_the_gate_is_open(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")
        assert _gate.stub_status()["enabled"] is True
        assert _gate.STUB_ENV_VAR in _gate.stub_status()["banner"]

        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        closed = _gate.stub_status()
        assert closed["enabled"] is False
        assert closed["banner"] is None

    def test_banner_renders_when_stubbed(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")

        html = _render_base(zt_stub=_gate.stub_status())

        assert _BANNER_MARKER in html
        assert "DEVICE POSTURE IS STUBBED" in html

    def test_no_banner_when_the_gate_is_closed(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)

        html = _render_base(zt_stub=_gate.stub_status())

        assert _BANNER_MARKER not in html

    def test_unavailable_status_shows_nothing_rather_than_an_all_clear(self):
        # The context processor degrades to banner=None when stub_status()
        # cannot be read. Absence is not an assertion that the estate is live.
        html = _render_base(zt_stub={"enabled": None, "banner": None, "env_var": None})
        assert _BANNER_MARKER not in html
        assert _BANNER_MARKER not in _render_base()

    def test_blueprint_wires_the_banner_into_every_security_page(self):
        """The canvas blueprint registers a context processor supplying zt_stub.

        Asserted structurally rather than by booting the dashboard: creating the
        blueprint seeds SOPs and opens the live canvas database, and a unit test
        that writes to the real canvas DB is a worse trade than reading the
        wiring. The RENDER is covered by the cases above.
        """
        src = (REPO_ROOT / "tools" / "security_canvas" / "blueprint.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        wired = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            decorated = any(
                isinstance(d, ast.Attribute) and d.attr == "context_processor"
                for d in node.decorator_list
            )
            if decorated and "zt_stub" in ast.unparse(node):
                assert "stub_status" in ast.unparse(node)
                wired = True
        assert wired, "no context processor supplies zt_stub to /security templates"


# ---------------------------------------------------------------------------
# 9. The fail-open default is gone from the source, not merely unreachable
# ---------------------------------------------------------------------------


class TestNoFailOpenDefault:
    @pytest.mark.parametrize(
        "rel",
        [
            "tools/security_canvas/device_compliance_scanner.py",
            "icdev/tools/security_canvas/device_compliance_scanner.py",
        ],
    )
    def test_no_optimistic_ctx_default_survives(self, rel):
        """``ctx.get(<check>, True)`` is the defect, spelled out.

        A behavioural test can only prove the arms it exercises; this proves no
        arm of either copy of the scanner still defaults a missing probe to
        True. Both spellings are checked because a fix landing in one tree and
        not the other is this repository's standing mirror hazard.
        """
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and len(node.args) == 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value is True
            ):
                pytest.fail(
                    "%s:%d still defaults a missing probe to True" % (rel, node.lineno)
                )


# ---------------------------------------------------------------------------
# 10. The survey that measured the flip count before this was armed
# ---------------------------------------------------------------------------

import tools.security_canvas.zt_verdict_survey as _survey  # noqa: E402


class _FakeConn:
    """A canvas connection that either raises (absent table) or yields rows."""

    def __init__(self, rows=None, error=None):
        self._rows = rows
        self._error = error
        self.closed = False

    def execute(self, sql, params=None):
        if self._error:
            raise self._error
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


class TestFlipSurvey:
    def test_absent_table_is_unmeasurable_never_zero_flips(self):
        corpus = _survey.read_corpus(
            _FakeConn(error=Exception("no such table: zig_device_compliance_scans"))
        )
        assert corpus["state"] == "absent"

        report = _survey.survey(conn=_FakeConn(error=Exception("no such table")))

        assert report["measurable"] is False
        assert report["flips_to_unknown"] is None, (
            "nothing measured must never render as zero findings"
        )
        assert report["flip_rate_pct"] is None

    def test_empty_table_is_distinct_from_absent(self):
        corpus = _survey.read_corpus(_FakeConn(rows=[]))
        assert corpus["state"] == "empty"

    def test_recorded_passes_with_no_probe_are_flips(self):
        rows = [
            {"check_id": "cc-01-inventory", "scan_type": "cis", "passed": 1, "device_id": "d1"},
            {"check_id": "cc-03-data-protect", "scan_type": "cis", "passed": 1, "device_id": "d1"},
            {"check_id": "stig-firewall", "scan_type": "stig", "passed": 0, "device_id": "d1"},
            # DERIVED: the row does not record whether the posture behind it was
            # measured, so it is neither a flip nor a non-flip.
            {"check_id": "stig-antivirus", "scan_type": "stig", "passed": 1, "device_id": "d1"},
        ]

        report = _survey.survey(conn=_FakeConn(rows=rows))

        assert report["measurable"] is True
        assert report["recorded_checks"] == 4
        assert report["flips_to_unknown"] == 2
        assert report["undetermined_derived"] == 1
        assert report["unchanged"] == 1
        assert report["flip_rate_pct"] == round(100.0 * 2 / 3, 2)

    def test_flip_rate_is_none_when_nothing_passed(self):
        rows = [
            {"check_id": "cc-01-inventory", "scan_type": "cis", "passed": 0, "device_id": "d1"},
        ]

        report = _survey.survey(conn=_FakeConn(rows=rows))

        assert report["recorded_pass"] == 0
        assert report["flip_rate_pct"] is None

    def test_optional_forwarding_is_not_counted_as_probe_data(self):
        """``context=probes.get(h)`` forwards a parameter; it supplies nothing.

        ``run_fleet_scan`` is exactly this shape. Counting it as a probe would
        report an uninstrumented fleet as an instrumented one — the defect the
        survey exists to measure.
        """
        census = _survey.call_site_census()

        assert census["callers"] >= 1
        assert census["supplying_context"] == 0
        assert census["conditional_context"] >= 1
        for site in census["sites"]:
            assert site["context"] in ("supplies", "conditional", "no_probe")

    def test_derived_check_list_comes_from_the_scanner(self):
        # A second copy here could drift from the rule the scanner applies.
        assert _survey.DERIVED_CHECKS is _dcs.DERIVED_CHECKS
