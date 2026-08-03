# CUI // SP-CTI
"""Unit tests for tools/quality/run_component_scoring.py (idp-score-01-d4).

Two properties carry the weight here and both are about *not lying*:

1. ``--dry-run`` writes nothing. Not "writes nothing to a table that happens to
   be absent" — the persist call is never made at all, proven with a spy rather
   than by inspecting an empty table, because an empty table is also what a
   silently-swallowed INSERT looks like.
2. An all-unassessed sweep exits ``0``. NOT_ASSESSED is a finding about ICDEV's
   measurement coverage, not a failure of the run, and a runner that reddened CI
   over it would be retired within a week.

Nothing here touches the real database. Every source the scorer reads is
injectable, so the sweep is driven end-to-end over constructed inputs; the one
test that does exercise the real registry (`main` over a single key) runs with
the connection explicitly stubbed out.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.quality import run_component_scoring as runner  # noqa: E402
from tools.quality.component_scorer import ScorecardPersistError  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


@dataclass
class FakeComponent:
    key: str
    kind: str = "canvas"
    display_name: str = ""
    url_prefix: str = ""
    module: str = ""


class FakeRegistry:
    """Just enough registry for ``registry_component_keys`` and lookups."""

    def __init__(self, components):
        self._components = list(components)

    def list_all(self):
        return list(self._components)

    def get(self, key):
        for comp in self._components:
            if comp.key == key:
                return comp
        return None


@pytest.fixture
def registry():
    return FakeRegistry(
        [
            FakeComponent("ndc", "canvas", "Network Design Canvas", "/network"),
            FakeComponent("sdc", "canvas", "Security Design Canvas", "/security"),
        ]
    )


#: Sources that measured nothing, stated explicitly.
#:
#: ``{}`` would NOT do: with no ``canvas_health`` injected, ``extract_health``
#: falls back to the real ``get_canvas_health()``, whose rows are keyed by the
#: same ``ndc``/``sdc`` keys these fakes use — so the health dimension would be
#: assessed from whatever the host checkout happens to hold and the test would
#: pass or fail on ambient state rather than on the fixture. Passing ``[]`` is
#: the documented "nothing probed" injection.
NO_SIGNAL_SOURCES = {"evidence": [], "canvas_health": []}

#: Sources that assess all four dimensions, so a component can reach a grade.
FULLY_ASSESSED_SOURCES = {
    "evidence": [{"status": "pass"}, {"status": "pass"}],
    "posture": [
        {"name": "Network", "score": 90.0, "open_findings": 1, "closed_findings": 4},
        {"name": "Security", "score": 90.0, "open_findings": 1, "closed_findings": 4},
    ],
    "canvas_health": [],
    "completeness_report": {
        "items": [
            {"point": "template", "required": True, "present": True},
            {"point": "route", "required": True, "present": True},
        ]
    },
}

FULL_COHERENCE_REPORT = {
    "checks": [
        {"check_id": "c1", "status": "pass", "expected": ["tools/ndc/blueprint.py"]},
        {"check_id": "c2", "status": "pass", "expected": ["tools/sdc/blueprint.py"]},
    ]
}


# --------------------------------------------------------------------------- #
# Dry run writes nothing
# --------------------------------------------------------------------------- #


def test_dry_run_never_calls_persist(registry, monkeypatch):
    calls = []
    monkeypatch.setattr(
        runner, "persist_scorecard", lambda *a, **k: calls.append((a, k)) or {}
    )

    report = runner.run_scoring(registry=registry, dry_run=True, source_kwargs=NO_SIGNAL_SOURCES)

    assert calls == []
    assert report["persisted"] == []
    assert report["dry_run"] is True
    assert {c["action"] for c in report["components"]} == {"dry-run"}


def test_dry_run_still_scores_every_component(registry, monkeypatch):
    monkeypatch.setattr(runner, "persist_scorecard", lambda *a, **k: {})

    report = runner.run_scoring(registry=registry, dry_run=True, source_kwargs=NO_SIGNAL_SOURCES)

    assert report["component_count"] == 2
    assert report["scored"] == 2
    assert [c["component_id"] for c in report["components"]] == ["ndc", "sdc"]


# --------------------------------------------------------------------------- #
# NOT_ASSESSED accounting — the acceptance criterion
# --------------------------------------------------------------------------- #


def test_no_signal_data_reports_not_assessed_not_zero(registry, monkeypatch):
    """With no sources at all, every dimension is unassessed and no score is 0."""
    monkeypatch.setattr(runner, "persist_scorecard", lambda *a, **k: {})

    report = runner.run_scoring(registry=registry, dry_run=True, source_kwargs=NO_SIGNAL_SOURCES)

    assert report["assessed"] == 0
    assert report["unassessed"] == 2
    for component in report["components"]:
        assert component["status"] == "not_assessed"
        assert component["overall_score"] is None, "unassessed must be NULL, never 0"
        assert component["letter_grade"] is None, "unassessed must be NULL, never 'F'"


def test_unassessed_tally_names_the_dark_dimensions(registry, monkeypatch):
    monkeypatch.setattr(runner, "persist_scorecard", lambda *a, **k: {})

    report = runner.run_scoring(registry=registry, dry_run=True, source_kwargs=NO_SIGNAL_SOURCES)

    # Two components, four dimensions, nothing measured: every dimension is
    # unassessed for both.
    assert report["unassessed_by_dimension"] == {
        "health": 2,
        "compliance": 2,
        "coherence": 2,
        "completeness": 2,
    }


def test_render_logs_not_assessed_for_signal_less_components(registry, monkeypatch):
    monkeypatch.setattr(runner, "persist_scorecard", lambda *a, **k: {})

    report = runner.run_scoring(
        registry=registry, dry_run=True, source_kwargs=NO_SIGNAL_SOURCES
    )
    text = runner.render(report)

    assert "NOT_ASSESSED" in text
    assert "ndc" in text and "sdc" in text
    assert "no signal for: health, compliance, coherence, completeness" in text
    assert "DRY RUN" in text


def test_fully_sourced_component_is_graded(registry):
    """The other half: given all four sources, a component actually scores."""
    report = runner.run_scoring(
        registry=registry,
        dry_run=True,
        source_kwargs=FULLY_ASSESSED_SOURCES,
        coherence_report=FULL_COHERENCE_REPORT,
    )

    assert report["assessed"] == 2
    assert report["unassessed_by_dimension"] == {
        "health": 0,
        "compliance": 0,
        "coherence": 0,
        "completeness": 0,
    }
    for component in report["components"]:
        assert component["status"] == "assessed"
        assert component["overall_score"] is not None
        assert component["letter_grade"] in {"A", "B", "C", "D", "F"}


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def test_persist_called_once_per_component_with_shared_stamp(registry, monkeypatch):
    seen = []

    def _spy(result, conn=None, evaluated_at=None, **kwargs):
        seen.append((result["key"], evaluated_at))
        return {"component_id": result["key"], "action": "insert"}

    monkeypatch.setattr(runner, "persist_scorecard", _spy)

    report = runner.run_scoring(
        registry=registry,
        dry_run=False,
        evaluated_at="2026-08-03T00:00:00+00:00",
        source_kwargs=NO_SIGNAL_SOURCES,
    )

    assert [key for key, _ in seen] == ["ndc", "sdc"]
    # One shared timestamp so a single pass sorts together on
    # idx_sc_component_evaluated.
    assert {stamp for _, stamp in seen} == {"2026-08-03T00:00:00+00:00"}
    assert len(report["persisted"]) == 2
    assert {c["action"] for c in report["components"]} == {"insert"}


def test_unassessed_components_are_still_persisted(registry, monkeypatch):
    """A NULL-scored row records that ICDEV looked and could not measure."""
    seen = []
    monkeypatch.setattr(
        runner,
        "persist_scorecard",
        lambda result, **k: seen.append(result["key"]) or {"action": "insert"},
    )

    report = runner.run_scoring(registry=registry, dry_run=False, source_kwargs=NO_SIGNAL_SOURCES)

    assert seen == ["ndc", "sdc"], "unassessed components must not be skipped"
    assert report["assessed"] == 0


def test_persist_failure_is_recorded_and_does_not_abort_the_sweep(registry, monkeypatch):
    def _spy(result, **kwargs):
        if result["key"] == "ndc":
            raise ScorecardPersistError("developer_scorecards.component_id is missing")
        return {"component_id": result["key"], "action": "insert"}

    monkeypatch.setattr(runner, "persist_scorecard", _spy)

    report = runner.run_scoring(registry=registry, dry_run=False, source_kwargs=NO_SIGNAL_SOURCES)

    assert len(report["errors"]) == 1
    assert report["errors"][0]["component_id"] == "ndc"
    assert report["errors"][0]["phase"] == "persist"
    assert len(report["persisted"]) == 1, "sdc still persisted"
    assert report["components"][0]["action"] == "failed"


def test_scoring_failure_is_recorded_and_does_not_abort_the_sweep(registry, monkeypatch):
    real = runner.score_component

    def _boom(key, **kwargs):
        if key == "ndc":
            raise RuntimeError("canvas module will not import")
        return real(key, **kwargs)

    monkeypatch.setattr(runner, "score_component", _boom)
    monkeypatch.setattr(runner, "persist_scorecard", lambda *a, **k: {})

    report = runner.run_scoring(registry=registry, dry_run=True, source_kwargs=NO_SIGNAL_SOURCES)

    assert report["errors"][0] == {
        "component_id": "ndc",
        "phase": "score",
        "error": "canvas module will not import",
    }
    assert report["scored"] == 1
    assert [c["component_id"] for c in report["components"]] == ["sdc"]


def test_unloadable_registry_raises_rather_than_sweeping_nothing():
    class Broken:
        def list_all(self):
            raise RuntimeError("component_registry.yaml is malformed")

    with pytest.raises(ScorecardPersistError, match="cannot load the component registry"):
        runner.run_scoring(registry=Broken(), dry_run=True, source_kwargs=NO_SIGNAL_SOURCES)


# --------------------------------------------------------------------------- #
# Shared sources
# --------------------------------------------------------------------------- #


def test_prefetch_without_connection_reports_posture_unavailable():
    kwargs, report = runner.prefetch_sources(None)

    assert "posture" not in kwargs, (
        "an unavailable source must not be injected as an empty list — the "
        "extractor's own reason is more accurate than 'returned no rows'"
    )
    assert report["posture"]["available"] is False
    assert report["posture"]["reason"] == "no database connection"


def test_prefetch_injects_posture_once_for_the_whole_sweep(monkeypatch):
    calls = []

    class FakePosture:
        @staticmethod
        def compute_canvas_posture(conn):
            calls.append(conn)
            return [{"name": "Network", "score": 90.0}], 90.0

    monkeypatch.setitem(sys.modules, "tools.canvas_compliance.posture", FakePosture)

    kwargs, report = runner.prefetch_sources(object())

    assert len(calls) == 1
    assert kwargs["posture"] == [{"name": "Network", "score": 90.0}]
    assert report["posture"] == {"available": True, "rows": 1}


def test_coherence_report_absence_is_recorded_in_sources(registry, monkeypatch):
    monkeypatch.setattr(runner, "persist_scorecard", lambda *a, **k: {})

    report = runner.run_scoring(registry=registry, dry_run=True, source_kwargs=NO_SIGNAL_SOURCES)

    assert report["sources"]["coherence_report"] == {"available": False}


def test_load_coherence_report_reads_a_checker_report(tmp_path):
    path = tmp_path / "coherence.json"
    path.write_text(json.dumps(FULL_COHERENCE_REPORT), encoding="utf-8")

    assert runner.load_coherence_report(path)["checks"][0]["check_id"] == "c1"


def test_load_coherence_report_rejects_a_file_that_is_not_a_report(tmp_path):
    """Degrading to None here would silently score one dimension less."""
    path = tmp_path / "not-a-report.json"
    path.write_text(json.dumps({"summary": "nope"}), encoding="utf-8")

    with pytest.raises(ValueError, match="not a coherence report"):
        runner.load_coherence_report(path)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_main_dry_run_over_the_real_registry_exits_zero(monkeypatch, capsys):
    """The acceptance criterion, minus the database.

    Runs the real registry lookup and the real extractors for one component
    with no connection available, which is the state a fresh CI checkout is in.
    """
    monkeypatch.setattr(runner, "_open_connection", lambda: (None, "no database"))

    assert runner.main(["--dry-run", "--keys", "ndc", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["persisted"] == []
    assert payload["errors"] == []
    assert [c["component_id"] for c in payload["components"]] == ["ndc"]


def test_main_exits_zero_when_nothing_could_be_assessed(registry, monkeypatch, capsys):
    monkeypatch.setattr(runner, "_open_connection", lambda: (None, "no database"))
    monkeypatch.setattr(runner, "registry_component_keys", lambda r=None: ["ndc", "sdc"])
    monkeypatch.setattr(runner, "prefetch_sources", lambda conn: (NO_SIGNAL_SOURCES, {}))

    assert runner.main(["--dry-run"]) == 0, "NOT_ASSESSED is a finding, not a failure"
    assert "NOT_ASSESSED" in capsys.readouterr().out


def test_main_exits_one_when_a_component_errors(monkeypatch, capsys):
    monkeypatch.setattr(runner, "_open_connection", lambda: (None, "no database"))
    monkeypatch.setattr(runner, "registry_component_keys", lambda r=None: ["ndc"])
    monkeypatch.setattr(runner, "prefetch_sources", lambda conn: ({}, {}))
    monkeypatch.setattr(
        runner, "score_component", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    assert runner.main(["--dry-run"]) == 1
    assert "ERROR ndc (score): boom" in capsys.readouterr().out


def test_main_refuses_to_persist_without_a_connection(monkeypatch, capsys):
    monkeypatch.setattr(runner, "_open_connection", lambda: (None, "PG unreachable"))

    assert runner.main([]) == 2
    assert "needs a database connection" in capsys.readouterr().err


def test_main_rejects_an_unreadable_coherence_report(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(runner, "_open_connection", lambda: (None, "no database"))

    assert runner.main(["--dry-run", "--coherence-report", str(tmp_path / "missing.json")]) == 2
    assert "cannot read coherence report" in capsys.readouterr().err


def test_main_closes_the_connection_it_opened(monkeypatch):
    class FakeConn:
        closed = False

        def close(self):
            FakeConn.closed = True

    monkeypatch.setattr(runner, "_open_connection", lambda: (FakeConn(), ""))
    monkeypatch.setattr(runner, "registry_component_keys", lambda r=None: [])
    monkeypatch.setattr(runner, "prefetch_sources", lambda conn: ({}, {}))

    assert runner.main(["--dry-run"]) == 0
    assert FakeConn.closed is True
