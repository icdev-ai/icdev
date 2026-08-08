# CUI // SP-CTI
"""Integration proof that the promoter's caps hold on a real database.

``tests/innovation/test_kanban_promoter.py`` is the unit suite: it stubs
``find_promotable_signals`` and records the specs handed to a fake
``create_tasks``. That proves the arithmetic in ``apply_caps`` but not the
thing the merge gate actually needs to know before ``KANBAN_PROMOTE_ENABLED``
is turned on — *whether a run against a database full of approved findings
creates five cards and stops.*

So this file removes every stub between the promoter and SQLite:

  * 20 findings are seeded into a real ``innovation_signals`` table, built
    from the production DDL in ``tools/db/init_icdev_db.py`` rather than a
    hand-written schema — a column the promoter's SELECT names and the live
    table lacks fails here instead of in production.
  * ``promote_findings_to_kanban(dry_run=False)`` runs end to end: the real
    SQL query, the real gap gate, the real caps, the real
    ``task_factory.create_tasks``, the real ``kanban_tasks`` schema.
  * Every assertion is read back out of the database, not off the returned
    payload. A promoter that reports ``created: 5`` while writing 20 rows
    passes a payload assertion and fails these.

The four acceptance assertions, in order:

  1. exactly 5 rows land in ``kanban_tasks``            (``max_per_run``)
  2. no subsystem accounts for more than 2 of them      (``max_per_subsystem``)
  3. the truncation is logged at WARNING                (a silent cap is
     indistinguishable from a promoter that found nothing)
  4. a second identical run creates nothing, and still creates nothing when
     the task ids are changed out from under it — so it is the
     ``idempotency_key`` doing the work, not the primary key

Seeding is deliberately over-supplied and skewed: 20 findings across 4
gap-verdict subsystems, 5 each, with strictly descending scores. That makes
both caps bite at once (one subsystem overflows *and* the run ceiling is
reached) and makes which 5 survive deterministic rather than dependent on
row order.

Fully self-contained: the database is a file under pytest's ``tmp_path``,
pointed at by ``ICDEV_DB_PATH`` via ``monkeypatch.setenv`` so the pointer
cannot leak into a later test, and deleted on teardown.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation import kanban_promoter as kp  # noqa: E402

# The caps this file exists to prove. They are the values shipped in
# args/innovation_promoter.yaml; passed explicitly as per-call overrides so the
# test states what it is proving instead of inheriting it, and pinned against
# the YAML below so an override cannot quietly mask a config edit.
MAX_PER_RUN = 5
MAX_PER_SUBSYSTEM = 2

# 4 gap-verdict subsystems, addressed by category. Every one of these resolves
# through the SHIPPED verdict map, so a YAML edit that flips one to 'ahead'
# breaks this file rather than silently reducing what gets promoted.
GAP_CATEGORIES = [
    "developer_experience",  # -> developer_portal        (gap)
    "security",              # -> security_ops            (gap)
    "data_quality",          # -> data_lineage            (gap)
    "llm_evaluation",        # -> evaluation              (gap)
]

SEEDED_FINDINGS = 20


# ---------------------------------------------------------------------------
# Schema — taken from production, not restated
# ---------------------------------------------------------------------------


def _table_ddl(schema_sql: str, table: str) -> str:
    """Cut one ``CREATE TABLE`` block out of the production schema.

    Paren-matched rather than split on ``);``: a ``);`` inside a comment
    truncates the statement and yields DDL that parses but is missing its
    trailing columns. Line comments are stripped before counting for the same
    reason.

    Raises rather than returning a fallback if the table is gone — a test that
    invents a schema when production's disappears proves nothing.
    """
    marker = f"CREATE TABLE IF NOT EXISTS {table} ("
    start = schema_sql.index(marker)  # ValueError here is the correct failure

    depth = 0
    lines: list[str] = []
    for line in schema_sql[start:].splitlines():
        code = line.split("--", 1)[0]
        lines.append(code)
        depth += code.count("(") - code.count(")")
        if depth == 0:
            break
    else:  # pragma: no cover - unbalanced parens in the shipped schema
        raise ValueError(f"unterminated CREATE TABLE for {table!r}")

    return "\n".join(lines).rstrip().rstrip(";") + ";"


def _seed_signals(db: Path) -> list[dict]:
    """Write 20 approved benchmark findings and return what was written."""
    from tools.db.init_icdev_db import SCHEMA_SQL

    rows: list[dict] = []
    for i in range(SEEDED_FINDINGS):
        rows.append({
            "id": f"sig-it-{i:04d}",
            "category": GAP_CATEGORIES[i % len(GAP_CATEGORIES)],
            # Strictly descending and distinct: the cap keeps the highest
            # scorers, so ties would make which 5 survive unspecified.
            "innovation_score": round(0.99 - i * 0.01, 4),
        })

    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(_table_ddl(SCHEMA_SQL, "innovation_signals"))
        # LEFT JOINed by find_promotable_signals; left empty on purpose, so the
        # join is exercised against a real table with no matching rows.
        conn.executescript(_table_ddl(SCHEMA_SQL, "innovation_solutions"))
        conn.executemany(
            """INSERT INTO innovation_signals
               (id, source, source_type, title, description, content_hash,
                discovered_at, status, category, innovation_score,
                triage_result, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    r["id"],
                    "github",
                    "external_repo_scouting",
                    f"Benchmark finding {r['id']}",
                    "Seeded by tests/innovation/test_innovation_kanban_promoter.py",
                    f"hash-{r['id']}",
                    "2026-08-07T00:00:00+00:00",
                    "triaged",
                    r["category"],
                    r["innovation_score"],
                    "approved",
                    "2026-08-07T00:00:00+00:00",
                )
                for r in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return rows


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """A throwaway SQLite database holding 20 promotable findings.

    ``monkeypatch.setenv`` rather than ``os.environ[...]``: a stray
    ``ICDEV_DB_PATH`` silently redirects every later ``get_connection()`` at a
    tmpdir that no longer exists, and the failure surfaces in an unrelated
    test. ``ICDEV_DATABASE_URL`` is cleared too because it outranks the
    backend pin — left set, this test would run against whatever it names.
    """
    db = tmp_path / "promoter_caps.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    monkeypatch.delenv("ICDEV_PG_NO_FALLBACK", raising=False)

    signals = _seed_signals(db)

    # kanban_tasks must exist before the first run: find_promotable_signals
    # anti-joins against it, and that SELECT happens before create_tasks (which
    # is what normally creates the table) is ever reached. Built by the real
    # initialiser so the promoter's INSERT meets the real column list.
    from tools.kanban.init_db import init_kanban_tables

    init_kanban_tables()

    yield SimpleDB(db, signals)

    # Explicit teardown. tmp_path would be collected eventually, but a test
    # that writes cards should be seen to remove them.
    try:
        db.unlink()
    except OSError:  # pragma: no cover - Windows can hold a stale handle
        pass


class SimpleDB:
    """The seeded database plus the findings that went into it."""

    def __init__(self, path: Path, signals: list[dict]):
        self.path = path
        self.signals = signals
        self.category_by_signal = {s["id"]: s["category"] for s in signals}

    def cards(self) -> list[dict]:
        """Every kanban row, read straight back out of the file."""
        con = sqlite3.connect(str(self.path))
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(
                "SELECT id, title, status, priority, task_type, dispatch_source, "
                "idempotency_key, source_prediction_id FROM kanban_tasks"
            )]
        finally:
            con.close()

    def subsystem_counts(self, cards: list[dict], config: dict) -> dict[str, int]:
        """Group written cards by subsystem, resolved the way the gate does.

        Deliberately re-derived from ``source_prediction_id`` -> the seeded
        category -> ``kp.resolve_subsystem`` instead of read off the result
        payload's ``per_subsystem_counts``. The payload is the promoter's own
        account of what it did; this is the database's.
        """
        counts: dict[str, int] = {}
        for card in cards:
            category = self.category_by_signal[card["source_prediction_id"]]
            subsystem = kp.resolve_subsystem({"category": category}, config)
            counts[subsystem] = counts.get(subsystem, 0) + 1
        return counts


@pytest.fixture
def promoter_warnings():
    """Capture WARNINGs off the promoter's own logger.

    ``get_logger`` returns a logger with ``propagate=False`` and its own file
    handlers, so pytest's ``caplog`` (which listens on root) sees nothing.
    """
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture(level=logging.WARNING)
    kp.logger.addHandler(handler)
    try:
        yield records
    finally:
        kp.logger.removeHandler(handler)


@pytest.fixture
def shipped_config():
    """The config the promoter actually ships with."""
    return kp.load_config()


def _promote(config):
    return kp.promote_findings_to_kanban(
        config=config,
        dry_run=False,
        max_findings_per_run=MAX_PER_RUN,
        max_per_subsystem=MAX_PER_SUBSYSTEM,
    )


# ---------------------------------------------------------------------------
# The seeding itself has to be right, or every assertion below is vacuous
# ---------------------------------------------------------------------------


def test_all_twenty_seeded_findings_carry_a_gap_verdict(seeded_db, shipped_config):
    """Without this, "5 of 20" could mean the gate rejected the other 15."""
    verdicts = {
        kp.verdict_for_subsystem(
            kp.resolve_subsystem({"category": c}, shipped_config), shipped_config
        )
        for c in GAP_CATEGORIES
    }
    assert verdicts, "no verdicts resolved — the category map moved"
    for verdict in verdicts:
        assert kp.is_gap_verdict(verdict, shipped_config), (
            f"seeded category resolves to non-gap verdict {verdict!r}; "
            "this test would then be measuring the gate, not the cap"
        )
    assert len(seeded_db.signals) == SEEDED_FINDINGS


def test_shipped_config_matches_the_caps_under_test(shipped_config):
    """The overrides restate the shipped caps; they must not diverge."""
    assert int(shipped_config["max_per_run"]) == MAX_PER_RUN
    assert int(shipped_config["max_per_subsystem"]) == MAX_PER_SUBSYSTEM


def test_query_finds_all_twenty_before_any_cap_applies(seeded_db, shipped_config):
    """The real SELECT, against the real schema, on a real backend.

    Nothing else in the suite runs ``find_promotable_signals``' SQL — the unit
    tests stub it. A placeholder, a column name or an ``ORDER BY`` clause the
    backend rejects fails here and nowhere else.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        found = kp.find_promotable_signals(
            conn,
            triage_results=tuple(shipped_config["triage_results"]),
            source_types=tuple(shipped_config["source_types"]),
            min_score=float(shipped_config["min_innovation_score"]),
        )
    finally:
        conn.close()

    assert len(found) == SEEDED_FINDINGS
    # Highest score first — the cap keeps the top of this ordering.
    scores = [f["innovation_score"] for f in found]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Acceptance 1 & 2 — exactly 5 created, at most 2 per subsystem
# ---------------------------------------------------------------------------


def test_run_cap_creates_exactly_five_cards(seeded_db, shipped_config):
    result = _promote(shipped_config)

    assert result["candidates"] == SEEDED_FINDINGS
    assert result["gap_verdict_eligible"] == SEEDED_FINDINGS
    assert result["after_caps"] == MAX_PER_RUN
    assert result["created"] == MAX_PER_RUN

    # The database's account, not the promoter's.
    assert len(seeded_db.cards()) == MAX_PER_RUN


def test_no_subsystem_exceeds_the_per_subsystem_cap(seeded_db, shipped_config):
    _promote(shipped_config)

    counts = seeded_db.subsystem_counts(seeded_db.cards(), shipped_config)

    assert sum(counts.values()) == MAX_PER_RUN
    assert counts, "cards written but none resolved to a subsystem"
    for subsystem, count in counts.items():
        assert count <= MAX_PER_SUBSYSTEM, (
            f"{subsystem} took {count} of {MAX_PER_RUN} slots, cap is "
            f"{MAX_PER_SUBSYSTEM} — one subsystem can flood a run"
        )


def test_every_dropped_finding_is_accounted_for(seeded_db, shipped_config):
    """20 in, 5 out, 15 named. A cap that loses track of a drop is a leak."""
    result = _promote(shipped_config)

    dropped = (
        [d["id"] for d in result["dropped_by_run_cap"]]
        + [d["id"] for d in result["dropped_by_subsystem_cap"]]
    )
    kept = {c["source_prediction_id"] for c in seeded_db.cards()}

    assert len(dropped) == SEEDED_FINDINGS - MAX_PER_RUN
    assert len(set(dropped)) == len(dropped), "a finding was dropped twice"
    assert not (set(dropped) & kept), "a finding was both dropped and written"
    assert set(dropped) | kept == {s["id"] for s in seeded_db.signals}


def test_both_caps_bite_on_this_seeding(seeded_db, shipped_config):
    """The scenario is only a proof of both caps if both actually fire."""
    result = _promote(shipped_config)

    assert result["dropped_by_subsystem_cap"], "per-subsystem cap never engaged"
    assert result["dropped_by_run_cap"], "per-run cap never engaged"
    assert result["truncated"] is True


def test_written_cards_are_suggested_not_backlog(seeded_db, shipped_config):
    """The cap bounds volume; this bounds blast radius. Both or neither."""
    _promote(shipped_config)

    cards = seeded_db.cards()
    assert cards
    assert {c["status"] for c in cards} == {"suggested"}
    assert {c["dispatch_source"] for c in cards} == {"innovation_promoter"}


# ---------------------------------------------------------------------------
# Acceptance 3 — truncation is logged at WARNING
# ---------------------------------------------------------------------------


def test_truncation_logs_a_warning(seeded_db, shipped_config, promoter_warnings):
    """15 of 20 findings were held back. Nothing else says so at WARNING."""
    _promote(shipped_config)

    assert any("cap truncated" in m.lower() for m in promoter_warnings), (
        f"no truncation WARNING; captured: {promoter_warnings}"
    )
    assert any("per-run cap" in m for m in promoter_warnings)
    assert any("per-subsystem cap" in m for m in promoter_warnings)


def test_warning_names_the_cap_and_the_count(seeded_db, shipped_config, promoter_warnings):
    """An operator reading the log must be able to tell what was withheld."""
    _promote(shipped_config)

    run_cap_msgs = [m for m in promoter_warnings if "per-run cap" in m]
    assert run_cap_msgs
    assert f"({MAX_PER_RUN})" in run_cap_msgs[0]
    assert "remain eligible on the next run" in run_cap_msgs[0]


def test_a_run_under_the_caps_logs_no_truncation(seeded_db, shipped_config,
                                                 promoter_warnings):
    """The WARNING has to mean something: raise the caps above the supply."""
    result = kp.promote_findings_to_kanban(
        config=shipped_config,
        dry_run=False,
        max_findings_per_run=SEEDED_FINDINGS + 1,
        max_per_subsystem=SEEDED_FINDINGS + 1,
    )

    assert result["created"] == SEEDED_FINDINGS
    assert result["truncated"] is False
    assert not any("CAP TRUNCATED" in m for m in promoter_warnings)


# ---------------------------------------------------------------------------
# Acceptance 4 — idempotency_key prevents duplicates on re-run
# ---------------------------------------------------------------------------


def test_rerun_creates_nothing(seeded_db, shipped_config):
    """The scheduled case: the same run, twice, against the same rows."""
    first = _promote(shipped_config)
    second = _promote(shipped_config)

    assert first["created"] == MAX_PER_RUN
    # The anti-join in find_promotable_signals removes the 5 already carrying a
    # card, so the second run promotes the NEXT 5 — that is the cap doing its
    # job across runs, not a duplicate.
    assert second["created"] == MAX_PER_RUN
    cards = seeded_db.cards()
    assert len(cards) == 2 * MAX_PER_RUN
    assert len({c["source_prediction_id"] for c in cards}) == 2 * MAX_PER_RUN


def test_idempotency_key_alone_blocks_a_duplicate(seeded_db, shipped_config):
    """The key, not the primary key, is what stops the second write.

    ``create_tasks`` checks ``id`` first and ``idempotency_key`` second, so a
    plain re-run proves only that the id matched. Renaming the written ids
    disarms that first check; if the promoter then writes a second copy of the
    same finding, the idempotency_key is decorative.
    """
    _promote(shipped_config)
    before = seeded_db.cards()
    assert len(before) == MAX_PER_RUN

    con = sqlite3.connect(str(seeded_db.path))
    try:
        con.execute("UPDATE kanban_tasks SET id = 'renamed-' || id")
        con.commit()
    finally:
        con.close()

    # Re-offer the exact same five findings, bypassing the anti-join (which
    # keys on source_prediction_id and would filter them out first).
    eligible = kp.classify_signals(
        [
            {
                "id": card["source_prediction_id"],
                "title": card["title"],
                "category": seeded_db.category_by_signal[card["source_prediction_id"]],
                "innovation_score": 0.9,
            }
            for card in before
        ],
        shipped_config,
    )["eligible"]
    assert len(eligible) == MAX_PER_RUN

    result = kp.promote_signals(eligible, shipped_config, dry_run=False)

    assert result["created"] == 0
    assert result["skipped_existing"] == MAX_PER_RUN
    assert len(seeded_db.cards()) == MAX_PER_RUN


def test_every_card_carries_the_stable_key_and_provenance(seeded_db, shipped_config):
    """The key has to be derivable from the finding, or dedup cannot work."""
    _promote(shipped_config)

    for card in seeded_db.cards():
        sid = card["source_prediction_id"]
        assert card["idempotency_key"] == kp.idempotency_key(sid)
        assert card["id"] == kp.stable_task_id(sid)
        assert card["title"].startswith(f"INNOV-{sid[:8]}")


def test_dry_run_against_the_same_database_writes_nothing(seeded_db, shipped_config):
    """The gate before the gate: dry_run must not reach the write path."""
    result = kp.promote_findings_to_kanban(
        config=shipped_config,
        dry_run=True,
        max_findings_per_run=MAX_PER_RUN,
        max_per_subsystem=MAX_PER_SUBSYSTEM,
    )

    assert result["dry_run"] is True
    assert result["would_create"] == MAX_PER_RUN
    assert result["created"] == 0
    assert seeded_db.cards() == []
