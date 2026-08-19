# CUI // SP-CTI
"""cef-ui-02 — the conflict/gap projection the Explorer browses.

Four properties these tests exist to hold:

1. **No silent winner.** Asserted STRUCTURALLY against the column list and the
   round-tripped row, not against one hand-built payload, so a winner field
   that merely happened to be unset in a fixture cannot ship.
2. **Both claims survive whole**, each with its own source and as-of date —
   that is what makes a conflict renderable side by side.
3. **A gap list is filterable** by entity, reason and backend.
4. **An empty list has four causes and only one of them is "no conflicts".**
   ``conflicts``/``gaps`` are ``None`` — never ``0`` — for the two states that
   are not measurements.
"""
from __future__ import annotations

import importlib
import json
import sqlite3

import pytest

fs = importlib.import_module("tools.cortex.finding_store")


SCHEMA = """
CREATE TABLE IF NOT EXISTS cortex_entity_findings (
    finding_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    classification TEXT NOT NULL DEFAULT 'CUI',
    finding_type TEXT NOT NULL DEFAULT 'gap',
    entity_key TEXT NOT NULL DEFAULT '',
    entity_label TEXT NOT NULL DEFAULT '',
    entity_type TEXT NOT NULL DEFAULT '',
    conflict_kind TEXT NOT NULL DEFAULT '',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    values_json TEXT NOT NULL DEFAULT '[]',
    sides_json TEXT NOT NULL DEFAULT '[]',
    backends_json TEXT NOT NULL DEFAULT '[]',
    backends_failed_json TEXT NOT NULL DEFAULT '[]',
    cross_backend INTEGER NOT NULL DEFAULT 0,
    citations_json TEXT NOT NULL DEFAULT '[]',
    uncited_sides_json TEXT NOT NULL DEFAULT '[]',
    citation_basis TEXT NOT NULL DEFAULT '',
    subject_entity TEXT NOT NULL DEFAULT '',
    subject_verdict TEXT NOT NULL DEFAULT '',
    provenance_id TEXT NOT NULL DEFAULT '',
    seen_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMP,
    last_seen_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS cortex_finding_runs (
    tenant_id TEXT PRIMARY KEY,
    classification TEXT NOT NULL DEFAULT 'CUI',
    resolutions INTEGER NOT NULL DEFAULT 0,
    conflicts_seen INTEGER NOT NULL DEFAULT 0,
    gaps_seen INTEGER NOT NULL DEFAULT 0,
    clean_resolutions INTEGER NOT NULL DEFAULT 0,
    last_run_at TIMESTAMP
);
"""


class _Conn:
    """A SQLite connection speaking the ``%s`` placeholder dialect.

    The store authors PostgreSQL, which is the primary backend; this wraps a
    temp SQLite file so the test never opens the live board — the trap where a
    worktree with no .env silently reads a throwaway database and reports
    success is the same one in reverse.
    """

    def __init__(self, path):
        self._conn = sqlite3.connect(str(path))

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("%s", "?"), tuple(params))

    def commit(self):
        self._conn.commit()

    def close(self):  # the store closes what it opens; the fixture keeps it alive
        pass

    def dispose(self):
        self._conn.close()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A live store on a temp SQLite file, with EVERY ``storage`` alias patched.

    Patching only ``tools.db.storage`` leaves ``icdev.tools.db.storage`` a
    separate module object holding the real ``get_connection``, and a test that
    misses one alias writes to the live board while looking green.
    """
    path = tmp_path / "findings.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(SCHEMA)
    raw.commit()
    raw.close()

    conn = _Conn(path)
    for name in ("tools.db.storage", "icdev.tools.db.storage"):
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001 — one tree may be absent
            continue
        monkeypatch.setattr(module, "get_connection", lambda: conn, raising=False)
    yield conn
    conn.dispose()


class _Result:
    def __init__(self, entity="TLS 1.1", verdict="deprecated",
                 conflicts=(), gaps=(), citations=()):
        self.entity = entity
        self.verdict = verdict
        self.conflicts = list(conflicts)
        self.gaps = list(gaps)
        self.citations = list(citations)
        self.metadata = {}


class _Ctx:
    tenant_id = "default"
    classification = "CUI"


def _conflict():
    """The motivating case: the curated catalog vs a 2019 runbook."""
    return {
        "entity_key": "tls 1.1",
        "entity_label": "TLS 1.1",
        "kind": "status",
        "values": ["current", "deprecated"],
        "sides": [
            {
                "backend": "currency", "backends": ["currency"],
                "source": "nist", "source_id": "ec-42",
                "source_table": "entity_currency", "as_of": "2026-01-14",
                "status": "deprecated", "raw_status": "withdrawn",
                "authoritative": True, "confidence": 0.95,
                "extraction": "structured", "entity_type": "protocol",
            },
            {
                "backend": "rag", "backends": ["rag", "dic"],
                "source": "Enclave Interconnect Runbook",
                "source_id": "chunk-9001", "source_table": "rag_chunks",
                "as_of": "2019-05-02", "status": "current",
                "raw_status": "remains approved", "authoritative": False,
                "confidence": 0.4, "extraction": "text_pattern",
            },
        ],
        "backends": ["currency", "rag", "dic"],
        "cross_backend": True,
        "citations": [{"source_id": "ec-42"}],
        "uncited_sides": [{"source": "vendor-feed", "backend": "currency",
                           "status": "superseded", "reason": "no_row_id"}],
    }


def _gap(entity="Catalyst 4500-X", reasons=("no_claim",), failed=()):
    return {
        "entity": entity,
        "entity_key": entity.lower(),
        "reasons": list(reasons),
        "backends_consulted": ["currency", "rag", "dic", "graph", "kb"],
        "backends_failed": list(failed),
        "citations": [{"source_id": "chunk-7"}],
        "citation_basis": "evidence",
    }


# ---------------------------------------------------------------------------
# 1. No silent winner
# ---------------------------------------------------------------------------
class TestNoSilentWinner:
    """The store must not supply the side cef-rsv-02 refuses to pick."""

    _BANNED = ("winner", "resolved_value", "consensus", "winning",
               "chosen", "preferred", "score")

    def test_no_winner_column_exists(self):
        # Structural: against the column list itself, so no fixture can hide it.
        for column in fs.FINDING_COLUMNS:
            for banned in self._BANNED:
                assert banned not in column, (
                    f"column {column!r} names a winner; the store must persist "
                    "the disagreement, not resolve it"
                )

    def test_conflict_row_carries_no_winner_key(self):
        row = fs.conflict_row(_conflict(), "default", "CUI", "TLS 1.1", "deprecated")
        for key in row:
            for banned in self._BANNED:
                assert banned not in key

    def test_both_values_are_kept(self):
        row = fs.conflict_row(_conflict(), "default", "CUI", "TLS 1.1", "deprecated")
        assert json.loads(row["values_json"]) == ["current", "deprecated"]

    def test_authoritative_side_does_not_evict_the_other(self, store):
        """A declared authority is RECORDED, never APPLIED.

        ``entity_currency.resolve()`` resolves authority at read time to answer
        "what is the best available answer". That is a different question from
        "do my sources agree", and answering the second with the first is how
        the finding gets deleted.
        """
        fs.record_findings(_Result(conflicts=[_conflict()]), _Ctx())
        (row,) = fs.list_findings("default", finding_type="conflict", conn=store)
        assert len(row["sides"]) == 2
        assert {s["authoritative"] for s in row["sides"]} == {True, False}
        assert {s["status"] for s in row["sides"]} == {"deprecated", "current"}


# ---------------------------------------------------------------------------
# 2. Both claims survive whole, with sources and as-of dates
# ---------------------------------------------------------------------------
class TestConflictRoundTrip:
    def test_each_side_keeps_its_own_source_and_as_of(self, store):
        fs.record_findings(_Result(conflicts=[_conflict()]), _Ctx())
        (row,) = fs.list_findings("default", finding_type="conflict", conn=store)
        by_source = {s["source"]: s for s in row["sides"]}
        assert by_source["nist"]["as_of"] == "2026-01-14"
        assert by_source["Enclave Interconnect Runbook"]["as_of"] == "2019-05-02"
        assert by_source["nist"]["source_table"] == "entity_currency"

    def test_extraction_lane_is_carried_on_every_side(self, store):
        """A reader discounts a weaker lane; the detector must not have."""
        fs.record_findings(_Result(conflicts=[_conflict()]), _Ctx())
        (row,) = fs.list_findings("default", finding_type="conflict", conn=store)
        assert {s["extraction"] for s in row["sides"]} == {"structured", "text_pattern"}

    def test_uncited_sides_are_reported_not_dropped(self, store):
        fs.record_findings(_Result(conflicts=[_conflict()]), _Ctx())
        (row,) = fs.list_findings("default", finding_type="conflict", conn=store)
        assert [u["reason"] for u in row["uncited_sides"]] == ["no_row_id"]

    def test_reobservation_updates_one_row_and_counts_it(self, store):
        for _ in range(3):
            fs.record_findings(_Result(conflicts=[_conflict()]), _Ctx())
        rows = fs.list_findings("default", finding_type="conflict", conn=store)
        assert len(rows) == 1, "one disagreement must not render as three findings"
        assert rows[0]["seen_count"] == 3

    def test_a_changed_disagreement_is_a_new_finding(self, store):
        fs.record_findings(_Result(conflicts=[_conflict()]), _Ctx())
        other = _conflict()
        other["values"] = ["current", "superseded"]
        fs.record_findings(_Result(conflicts=[other]), _Ctx())
        rows = fs.list_findings("default", finding_type="conflict", conn=store)
        assert len(rows) == 2, (
            "what a human adjudicated is no longer what is on the table"
        )

    def test_provenance_id_is_taken_from_the_stamped_citations(self, store):
        fs.record_findings(
            _Result(conflicts=[_conflict()],
                    citations=[{"source_id": "ec-42", "provenance_id": "scr-777"}]),
            _Ctx(),
        )
        (row,) = fs.list_findings("default", finding_type="conflict", conn=store)
        assert row["provenance_id"] == "scr-777"


# ---------------------------------------------------------------------------
# 3. The gap list is browsable and filterable
# ---------------------------------------------------------------------------
class TestGapBrowsing:
    @pytest.fixture
    def seeded(self, store):
        fs.record_findings(
            _Result(gaps=[
                _gap("Catalyst 4500-X", ["no_claim"]),
                _gap("Nexus 7000", ["no_evidence"]),
                _gap("IPsec IKEv1", ["no_claim"], failed=["kb"]),
            ]),
            _Ctx(),
        )
        return store

    def test_all_gaps_are_browsable(self, seeded):
        assert len(fs.list_findings("default", finding_type="gap", conn=seeded)) == 3

    def test_filter_by_entity(self, seeded):
        rows = fs.list_findings("default", finding_type="gap", entity="nexus",
                                conn=seeded)
        assert [r["entity_label"] for r in rows] == ["Nexus 7000"]

    def test_filter_by_reason(self, seeded):
        rows = fs.list_findings("default", finding_type="gap",
                                reason="no_evidence", conn=seeded)
        assert [r["entity_label"] for r in rows] == ["Nexus 7000"]

    def test_filter_by_backend(self, seeded):
        assert len(fs.list_findings("default", finding_type="gap",
                                    backend="graph", conn=seeded)) == 3
        assert fs.list_findings("default", finding_type="gap",
                                backend="external", conn=seeded) == []

    def test_conflicts_and_gaps_are_separately_addressable(self, store):
        fs.record_findings(_Result(conflicts=[_conflict()], gaps=[_gap()]), _Ctx())
        assert len(fs.list_findings("default", finding_type="conflict",
                                    conn=store)) == 1
        assert len(fs.list_findings("default", finding_type="gap", conn=store)) == 1

    def test_filter_options_come_from_the_rows_on_screen(self, seeded):
        rows = fs.list_findings("default", finding_type="gap", conn=seeded)
        options = fs.filter_options(rows)
        assert set(options["reasons"]) == {"no_claim", "no_evidence"}
        # A chip can never offer a value that matches nothing.
        for reason in options["reasons"]:
            assert fs.list_findings("default", finding_type="gap",
                                    reason=reason, conn=seeded)


# ---------------------------------------------------------------------------
# 4. An outage is not a statement about the corpus
# ---------------------------------------------------------------------------
class TestOutageStaysContext:
    def test_backends_failed_never_becomes_a_reason(self, store):
        fs.record_findings(
            _Result(gaps=[_gap("IPsec IKEv1", ["no_claim"], failed=["kb", "graph"])]),
            _Ctx(),
        )
        (row,) = fs.list_findings("default", finding_type="gap", conn=store)
        assert row["reasons"] == ["no_claim"]
        assert row["backends_failed"] == ["kb", "graph"]
        assert "backends_failed" not in row["reasons"]

    def test_filtering_by_reason_does_not_match_a_failed_backend(self, store):
        fs.record_findings(
            _Result(gaps=[_gap("IPsec IKEv1", ["no_claim"], failed=["kb"])]),
            _Ctx(),
        )
        assert fs.list_findings("default", finding_type="gap",
                                reason="backends_failed", conn=store) == []


# ---------------------------------------------------------------------------
# 5. Four causes of an empty list, and only one is "no conflicts"
# ---------------------------------------------------------------------------
class TestMeasurementState:
    def test_disabled_is_not_a_claim_about_the_corpus(self, store):
        stats = fs.finding_stats("default", config={"resolve": {"persist_findings": False}},
                                 conn=store)
        assert stats["state"] == fs.STATE_DISABLED
        assert stats["conflicts"] is None and stats["gaps"] is None

    def test_disabled_records_nothing(self, store):
        record = fs.record_findings(_Result(conflicts=[_conflict()]), _Ctx(),
                                    config={"resolve": {"persist_findings": False}})
        assert record["recorded"] == 0
        assert fs.list_findings("default", conn=store) == []

    def test_unmeasured_leaves_the_counts_null(self, store):
        stats = fs.finding_stats("default", config={}, conn=store)
        assert stats["state"] == fs.STATE_UNMEASURED
        assert stats["conflicts"] is None, (
            "0 would read as a clean bill of health for a surface that never looked"
        )
        assert stats["gaps"] is None

    def test_clean_is_a_measurement_and_says_so(self, store):
        fs.record_findings(_Result(conflicts=[], gaps=[]), _Ctx())
        stats = fs.finding_stats("default", config={}, conn=store)
        assert stats["state"] == fs.STATE_CLEAN
        assert stats["conflicts"] == 0 and stats["gaps"] == 0
        assert stats["resolutions"] == 1
        assert stats["detail"]

    def test_findings_state_counts_cross_backend_separately(self, store):
        fs.record_findings(_Result(conflicts=[_conflict()], gaps=[_gap()]), _Ctx())
        stats = fs.finding_stats("default", config={}, conn=store)
        assert stats["state"] == fs.STATE_FINDINGS
        assert stats["conflicts"] == 1 and stats["gaps"] == 1
        assert stats["cross_backend"] == 1

    def test_the_denominator_is_written_even_for_a_clean_resolution(self, store):
        for _ in range(4):
            fs.record_findings(_Result(conflicts=[], gaps=[]), _Ctx())
        stats = fs.finding_stats("default", config={}, conn=store)
        assert stats["resolutions"] == 4 and stats["clean_resolutions"] == 4


# ---------------------------------------------------------------------------
# 6. The projection never breaks a resolution
# ---------------------------------------------------------------------------
class TestNeverRaises:
    def test_an_unreachable_store_is_reported_not_raised(self, monkeypatch):
        def boom():
            raise RuntimeError("no database")

        for name in ("tools.db.storage", "icdev.tools.db.storage"):
            try:
                module = importlib.import_module(name)
            except Exception:  # noqa: BLE001
                continue
            monkeypatch.setattr(module, "get_connection", boom, raising=False)
        record = fs.record_findings(_Result(conflicts=[_conflict()]), _Ctx())
        assert record["status"] == "error"
        assert record["detail"]

    def test_an_unmigrated_table_reads_as_empty_not_a_crash(self, tmp_path):
        conn = _Conn(tmp_path / "bare.db")
        try:
            assert fs.list_findings("default", conn=conn) == []
            assert fs.finding_stats("default", config={}, conn=conn)["conflicts"] is None
        finally:
            conn.dispose()


# ---------------------------------------------------------------------------
# 7. The seam is actually wired — a declared capability nobody calls is the
#    defect this platform ships most.
# ---------------------------------------------------------------------------
def test_resolver_calls_the_finding_store():
    import inspect

    resolver = importlib.import_module("tools.cortex.resolver")
    assert resolver.record_findings is fs.record_findings
    source = inspect.getsource(resolver.resolve)
    assert "record_findings(" in source, (
        "cortex.resolve() must project its findings, or the store is a "
        "declared-but-unconsumed capability"
    )
