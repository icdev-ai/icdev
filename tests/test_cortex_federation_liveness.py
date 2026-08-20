# CUI // SP-CTI
"""The Cortex federation layer is under the liveness gates (cef-ci-01).

Three new rungs — ``currency`` (cef-bck-01), ``external`` (cef-bck-02) and
``sme`` (cef-bck-03) — behind one governed facade, ``cortex.resolve``
(cef-rsv-01). Registered in CORTEX_BACKENDS, importable, weighted in
args/cortex_config.yaml, documented, and reachable only if something asks: the
exact shape of this repository's signature defect, which shipped three times as
a reflex before it got a check of its own.

What these tests pin is not "the backends work" — it is that the MEASUREMENT
cannot quietly stop working, because a liveness gate whose probe reports a
comfortable zero is strictly worse than no gate at all.
"""
from __future__ import annotations

import ast
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

from tools.awareness import capability_consumption as cc  # noqa: E402
from tools.cortex import governance as gov  # noqa: E402
from tools.workflow.coherence_checker import (  # noqa: E402
    _evaluate_capability_liveness,
    _liveness_check_result,
)

#: The rungs this card exists for. Named literally rather than derived, so a
#: change to CORTEX_BACKENDS that drops one is a test failure and not a silently
#: narrower assertion.
FEDERATION_BACKENDS = ("currency", "external", "sme")


def _yaml(rel: str) -> dict:
    return yaml.safe_load(io.open(REPO_ROOT / rel, encoding="utf-8").read()) or {}


# ---------------------------------------------------------------------------
# Coverage — the classes exist and name the units the card is about
# ---------------------------------------------------------------------------
class TestCoverage:
    def test_both_classes_are_registered_probes(self):
        assert "cortex_backend" in cc.PROBES
        assert "cortex_facade" in cc.PROBES

    def test_both_classes_are_declared_and_enabled(self):
        classes = _yaml("args/capability_consumption.yaml")["classes"]
        for name in ("cortex_backend", "cortex_facade"):
            assert classes[name]["enabled"] is True
            assert classes[name]["description"].strip()

    @pytest.mark.parametrize("backend", FEDERATION_BACKENDS)
    def test_the_three_new_rungs_are_declared_units(self, backend):
        declared = cc._str_tuple_from_source(cc._CORTEX_SCHEMAS_SRC, "CORTEX_BACKENDS")
        assert backend in declared

    def test_the_resolve_facade_is_a_declared_unit(self):
        facades = cc._str_tuple_from_source(cc._CORTEX_API_SRC, "CORTEX_FACADES")
        assert "resolve" in facades

    def test_declarations_are_read_without_importing_cortex(self):
        """A measurement tool must not need a working Cortex to report on Cortex.

        ``tools.cortex`` pulls in the retrieval stack, the LLM router and a
        domain pack registry on import — so a probe that imported it would go
        UNMEASURABLE on precisely the deployment where a backend is broken,
        which is the deployment the gate is for.
        """
        src = io.open(
            REPO_ROOT / "tools/awareness/capability_consumption.py", encoding="utf-8"
        ).read()
        tree = ast.parse(src)
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not any(m.startswith("tools.cortex") for m in imported), imported


# ---------------------------------------------------------------------------
# The distinction the whole class rests on
# ---------------------------------------------------------------------------
class TestUsedIsNotConsulted:
    """``consulted`` is a read of the CONFIG. Counting it would report every
    declared rung live on a deployment where not one of them ever answered —
    which is this gate's failure mode, not its finding."""

    def test_a_rung_that_only_failed_is_not_consumption(self):
        class _Res:
            metadata = {"backends_used": []}
            backends_consulted = ["currency", "rag", "dic"]
            backend_errors = [
                {"backend": "rag"}, {"backend": "dic"}, {"backend": "kb"},
            ]

        fields = gov._backend_fields(_Res())["backends"]
        assert fields["used"] == []
        assert fields["consulted"] == ["currency", "dic", "rag"]
        assert fields["failed"] == ["dic", "kb", "rag"]

    def test_a_rung_that_answered_is_consumption(self):
        class _Res:
            metadata = {"backends_used": ["currency", "sme"]}
            backends_consulted = ["currency", "rag"]
            backend_errors = []

        assert gov._backend_fields(_Res())["backends"]["used"] == ["currency", "sme"]

    def test_a_pack_failure_is_not_a_backend_failure(self):
        """Pack errors are stamped ``pack:<id>`` and are not CORTEX_BACKENDS."""

        class _Res:
            metadata = {}
            backends_consulted = []
            backend_errors = [{"backend": "pack:network_hardware"}, {"backend": "kb"}]

        assert gov._backend_fields(_Res())["backends"]["failed"] == ["kb"]

    def test_search_results_are_read_off_the_hits(self):
        """The search path returns a list of CortexSearchResult, not an object
        carrying ``backends_used``."""

        class _Hit:
            def __init__(self, backend):
                self.backend = backend

        fields = gov._backend_fields([_Hit("rag"), _Hit("graph"), _Hit("rag")])
        assert fields["backends"]["used"] == ["graph", "rag"]

    def test_an_unrecognised_result_shape_never_raises(self):
        """Audit bookkeeping on the governed hot path. Empty means NOT
        RECORDED — the probe reads a lifetime window for exactly that reason —
        and must never take a real Cortex call down."""
        for shape in (None, object(), "a string", 17, {"backends_used": ["rag"]}):
            fields = gov._backend_fields(shape)["backends"]
            assert set(fields) == {"used", "consulted", "failed"}

    def test_the_advisory_rung_counts_on_the_same_terms(self):
        """``sme`` hits are excluded from citations and can never move a
        verdict. That is a statement about CITABILITY. Folding it into
        consumption would make the one advisory backend permanently
        unmeasurable, which is the state the gate exists to end."""
        resolver_src = io.open(
            REPO_ROOT / "tools/cortex/resolver.py", encoding="utf-8"
        ).read()
        # backends_used is built from `hits`, the full set, NOT from
        # `evidentiary` (which is `hits` minus the advisory ones).
        assert '"backends_used": sorted({' in resolver_src
        used_block = resolver_src.split('"backends_used": sorted({', 1)[1][:200]
        assert "for h in hits" in used_block
        assert "evidentiary" not in used_block


# ---------------------------------------------------------------------------
# The audit row is where the count comes from
# ---------------------------------------------------------------------------
class TestAuditCarriesBackends:
    def test_gates_json_carries_a_backends_key(self):
        """No new table and no migration: the free-form ``gates_json`` blob,
        the same seam ctx-obs-02's timings and trust-kg-03's kg_grounding use."""
        src = io.open(
            REPO_ROOT / "tools/cortex/db/init_db.py", encoding="utf-8"
        ).read()
        assert '"backends": payload.get("backends") or {},' in src

    def test_the_pipeline_puts_backends_on_the_payload(self):
        src = io.open(REPO_ROOT / "tools/cortex/governance.py", encoding="utf-8").read()
        assert "payload.update(_backend_fields(result))" in src

    def test_no_new_telemetry_table_was_introduced(self):
        """`existing telemetry only` is the card's constraint and the module's
        first design rule. Both probes must name a table that predates them."""
        classes = _yaml("args/capability_consumption.yaml")["classes"]
        for name in ("cortex_backend", "cortex_facade"):
            assert "cortex_audit" in classes[name]["description"]


# ---------------------------------------------------------------------------
# Substrates
# ---------------------------------------------------------------------------
class TestSubstratesDeclared:
    #: The tables the federation layer is designed AGAINST. entity_currency was
    #: declared by cef-fnd-04; the rest arrived with the layer itself.
    EXPECTED = (
        "entity_currency",
        "cortex_audit",
        "cortex_entity_findings",
        "cortex_finding_runs",
        "databridge_agent_access_log",
        "db_connections",
    )

    @pytest.mark.parametrize("ref", EXPECTED)
    def test_declared_with_a_written_claim(self, ref):
        substrates = _yaml("args/capability_consumption.yaml")["substrates"]
        entry = next((s for s in substrates if s.get("ref") == ref), None)
        assert entry is not None, f"{ref} is not a declared substrate"
        # "This list is CURATED, not a schema dump — the value of the probe is
        # that every entry is a claim somebody made."
        assert len((entry.get("note") or "").split()) >= 20, ref

    def test_the_findings_store_and_its_denominator_are_declared_apart(self):
        """``cortex_finding_runs`` is bumped on EVERY resolution including the
        clean ones. Without it an empty findings table cannot be told apart
        from a surface nothing ever looked at — a different fact, and a
        different fix."""
        substrates = {s["ref"] for s in _yaml("args/capability_consumption.yaml")["substrates"]}
        assert {"cortex_entity_findings", "cortex_finding_runs"} <= substrates


# ---------------------------------------------------------------------------
# The gate's two refusals to fabricate a finding
# ---------------------------------------------------------------------------
def _report(classes):
    return {"classes": classes}


def _entry(name, declared, inert, available=True):
    return {
        "capability_class": name,
        "declared": declared,
        "inert": inert,
        "telemetry_available": available,
        "unmeasured_reason": None if available else "no telemetry",
    }


GATE = {
    "evidence_anchor": {"table": "audit_trail", "min_rows": 1000},
    "grandfathered": {"cortex_backend": 6, "cortex_facade": 4},
}


class TestEmptyDatabaseWarns:
    """A fresh worktree or an ephemeral CI database makes EVERY declared unit
    look inert, because nothing has been recorded — not because nothing is
    wired. Failing there would be a fabricated finding, and a gate that cries
    wolf is a gate someone disables."""

    @pytest.mark.parametrize("rows", [0, 1, 999, None])
    def test_below_the_evidence_anchor_the_verdict_is_no_history(self, rows):
        report = _report([_entry("cortex_backend", 7, 7)])
        ev = _evaluate_capability_liveness(report, report, rows, GATE)
        assert ev["verdict"] == "no_history"
        # And it must not have produced a single finding on the way.
        assert ev["over_budget"] == []
        assert ev["classes"] == []

    def test_no_history_renders_as_warn_not_fail(self):
        report = _report([_entry("cortex_backend", 7, 7)])
        ev = _evaluate_capability_liveness(report, report, 0, GATE)
        assert _liveness_check_result(ev, 30).status == "warn"

    def test_an_unmeasurable_class_warns_rather_than_failing(self):
        """An unmeasurable capability is a defect in the MEASUREMENT. It is
        reported, and it is not counted as a never-consumed unit."""
        report = _report([_entry("cortex_backend", 0, 0, available=False)])
        ev = _evaluate_capability_liveness(report, report, 50000, GATE)
        assert ev["verdict"] == "warn"
        assert ev["over_budget"] == []
        assert any("cortex_backend" in u for u in ev["unmeasurable"])


class TestIdleIsNotDead:
    """A rung consulted once and quiet since is a low-cadence capability, not a
    dead one. The lifetime pass decides; the window pass only classifies."""

    def test_a_backend_idle_this_window_does_not_fail_the_gate(self):
        lifetime = _report([_entry("cortex_backend", 7, 0)])
        window = _report([_entry("cortex_backend", 7, 7)])
        ev = _evaluate_capability_liveness(window, lifetime, 50000, GATE)
        assert ev["verdict"] == "pass"
        assert ev["classes"][0]["never_consumed"] == 0
        assert ev["classes"][0]["idle_this_window"] == 7

    def test_a_backend_never_consumed_over_budget_fails(self):
        report = _report([_entry("cortex_backend", 7, 7)])
        ev = _evaluate_capability_liveness(report, report, 50000, GATE)
        assert ev["verdict"] == "fail"
        assert ev["over_budget"][0]["capability_class"] == "cortex_backend"


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------
class TestBudgetsAreARatchet:
    #: Every budget as it stood on origin/main before cef-ci-01. A number here
    #: may only go DOWN. Raising one is how the backlog this gate exists to
    #: drain grows instead — and it is the one repair the card forbids outright.
    PRE_EXISTING = {
        "mcp_dispatch_tool": 467,
        "agent_approval_rule": 25,
        "mcp_tool_authorization": 4,
        "extension_hook_point": 10,
        "skill_optimizer": 0,
    }

    def test_no_pre_existing_budget_was_raised(self):
        live = _yaml("args/liveness_gate.yaml")["grandfathered"]
        for name, ceiling in self.PRE_EXISTING.items():
            assert live[name] <= ceiling, (
                f"{name} was raised from {ceiling} to {live[name]}. Wire the "
                "capability to a consumer or stop declaring it."
            )

    def test_the_new_budgets_are_no_larger_than_their_declared_sets(self):
        """A budget at or above the declared count is not a gate: it can never
        fire, whatever happens to the capability."""
        live = _yaml("args/liveness_gate.yaml")["grandfathered"]
        backends = cc._str_tuple_from_source(cc._CORTEX_SCHEMAS_SRC, "CORTEX_BACKENDS")
        facades = cc._str_tuple_from_source(cc._CORTEX_API_SRC, "CORTEX_FACADES")
        assert live["cortex_backend"] < len(backends)
        assert live["cortex_facade"] < len(facades)

    def test_a_new_backend_without_a_route_or_a_caller_trips_the_gate(self):
        """The point of the card: the NEXT rung added to CORTEX_BACKENDS and
        left unreached takes the class one over budget."""
        live = _yaml("args/liveness_gate.yaml")["grandfathered"]
        declared = len(cc._str_tuple_from_source(cc._CORTEX_SCHEMAS_SRC, "CORTEX_BACKENDS"))
        inert_today = live["cortex_backend"]
        report = _report([_entry("cortex_backend", declared + 1, inert_today + 1)])
        ev = _evaluate_capability_liveness(
            report, report, 50000,
            {"evidence_anchor": GATE["evidence_anchor"], "grandfathered": live},
        )
        assert ev["verdict"] == "fail"


# ---------------------------------------------------------------------------
# Route classification — reported, never exempted
# ---------------------------------------------------------------------------
class TestOptInRungsAreCountedNotExempted:
    """``external`` and ``sme`` are absent from every automatic route ON
    PURPOSE — one opens a socket outside the boundary, the other returns an
    LLM's opinion. Neither is a reason to stop counting them: a caller naming
    ``strategy="external"`` produces a real event, so they are measurable,
    merely un-asked-for. Exempting them would delete the finding."""

    def test_the_config_routes_are_read_from_yaml_not_hardcoded(self):
        routes = cc._cortex_routed_backends()
        assert "resolve.backends" in routes
        assert "search.fan_out.backends" in routes
        cfg = _yaml("args/cortex_config.yaml")
        assert routes["resolve.backends"] == list(cfg["resolve"]["backends"])

    def test_external_and_sme_are_on_no_automatic_route(self):
        routes = cc._cortex_routed_backends()
        automatic = {b for names in routes.values() for b in names}
        assert "external" not in automatic
        assert "sme" not in automatic

    def test_currency_is_on_an_automatic_route(self):
        """cef-bck-01 retrieves rows that existed before the query, so unlike
        its two siblings it belongs in the fan-out — and a regression that
        dropped it would otherwise show up only as a quietly emptier answer."""
        routes = cc._cortex_routed_backends()
        automatic = {b for names in routes.values() for b in names}
        assert "currency" in automatic


# ---------------------------------------------------------------------------
# Window binding — the probe must not silently read nothing
# ---------------------------------------------------------------------------
class TestScanIsBounded:
    def test_the_audit_scan_reports_truncation_rather_than_sampling_silently(self):
        """A silently sampled "never consumed" is the one reading this module
        may not produce."""
        src = io.open(
            REPO_ROOT / "tools/awareness/capability_consumption.py", encoding="utf-8"
        ).read()
        assert 'res.extra["rows_truncated"] = truncated' in src

    def test_the_tally_parses_json_in_python_not_in_sql(self):
        """``gates_json`` is jsonb on PostgreSQL and TEXT on SQLite; one
        dialect's extraction operator does not exist on the other backend."""
        src = io.open(
            REPO_ROOT / "tools/awareness/capability_consumption.py", encoding="utf-8"
        ).read()
        body = src.split("def _cortex_backend_events", 1)[1].split("\ndef ", 1)[0]
        for dialect in ("json_extract", "json_each", "json_array_length", "->>"):
            assert dialect not in body, dialect
        assert "json.loads(blob)" in body


class TestWindowBoundIsTypeAware:
    def test_a_timestamp_column_binds_a_datetime(self):
        """cortex_audit.created_at is a real PostgreSQL timestamp. Binding the
        ISO string form against it drops rows on whichever half it misses —
        which would read as "never consumed"."""

        class _Conn:
            def execute(self, *a, **k):
                raise AssertionError("column type is looked up, not guessed")

        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # _column_type swallows the raise and returns None -> TEXT form. The
        # assertion here is that the bound is DERIVED, never a hardcoded shape.
        bound = cc._window_bound(_Conn(), "cortex_audit", "created_at", since)
        assert isinstance(bound, (str, datetime))
