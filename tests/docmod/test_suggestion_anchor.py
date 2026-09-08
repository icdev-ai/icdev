# CUI // SP-CTI
"""dwr-anchor-03 — dic_suggestions is an anchored, addressable change.

Round-trips every ``anchor_basis`` through ``create_suggestion`` and back out
of ``get_suggestion``; proves the store REFUSES an inconsistent anchor and
writes nothing; proves ``resolve_anchor`` never resolves an ambiguous match by
picking one; proves ``applied_text`` is stored beside the AI draft, never over
it; and runs migration 20260907213944 against the three populations it faces.

The database is the per-module SQLite file tests/docmod/conftest.py points
``tools.db.storage`` at, so the store's own ``get_connection`` is exercised.
"""
from __future__ import annotations

import ast
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.document_intelligence import suggestion_store as store  # noqa: E402

MIGRATION_DIR = ROOT / "tools" / "db" / "migrations" / "20260907213944_dic_suggestions_anchor"

SECTION = "The enclave shall use TLS 1.1 for transport. Legacy hosts still use TLS 1.1 on port 8443."


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_dic_suggestions_anchor", MIGRATION_DIR / "up.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _later_migrations_for_dic_suggestions():
    """Every migration AFTER this one that declares columns for the table.

    Discovered from the migrations directory rather than listed here, so a
    column added by a later card is picked up without editing this file.
    """
    out = []
    for d in sorted(MIGRATION_DIR.parent.iterdir()):
        if not d.is_dir() or d.name <= MIGRATION_DIR.name:
            continue
        up = d / "up.py"
        if not up.exists():
            continue
        spec = importlib.util.spec_from_file_location(f"mig_{d.name}", up)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001 -- a migration that will not import is
            # another test's finding, not this one's.
            continue
        if getattr(mod, "TABLE", "") == "dic_suggestions" and hasattr(mod, "NEW_COLUMNS"):
            out.append(mod)
    return out


def _row_count() -> int:
    from tools.db.storage import get_connection
    with get_connection() as conn:
        store._ensure_tables(conn)
        row = conn.execute("SELECT COUNT(*) AS n FROM dic_suggestions").fetchone()
    return int(dict(row)["n"] if hasattr(row, "keys") else row[0])


# ── resolve_anchor: pure ──────────────────────────────────────────────────────

class TestResolveAnchor:
    def test_verified_offsets_are_exact(self):
        text = "enclave"
        s = SECTION.index(text)
        r = store.resolve_anchor(SECTION, text, anchor_start=s, anchor_end=s + len(text))
        assert r["anchor_basis"] == "exact"
        assert SECTION[r["anchor_start"]:r["anchor_end"]] == text

    def test_wrong_offsets_fall_back_to_relocation_not_exact(self):
        text = "enclave"
        r = store.resolve_anchor(SECTION, text, anchor_start=0, anchor_end=len(text))
        assert r["anchor_basis"] == "relocated"
        assert r["reason"] == "found_once"
        assert SECTION[r["anchor_start"]:r["anchor_end"]] == text

    def test_found_once_is_relocated(self):
        r = store.resolve_anchor(SECTION, "port 8443")
        assert r["anchor_basis"] == "relocated"
        assert SECTION[r["anchor_start"]:r["anchor_end"]] == "port 8443"

    def test_ambiguous_match_stays_unanchored_and_picks_nothing(self):
        assert SECTION.count("TLS 1.1") == 2
        r = store.resolve_anchor(SECTION, "TLS 1.1")
        assert r["anchor_basis"] == "unanchored"
        assert r["reason"] == "ambiguous:2"
        assert r["anchor_start"] is None and r["anchor_end"] is None
        assert r["anchor_text"] == "TLS 1.1"  # what was sought is kept

    def test_not_found_is_unanchored(self):
        r = store.resolve_anchor(SECTION, "TLS 1.3")
        assert r["anchor_basis"] == "unanchored"
        assert r["reason"] == "not_found"

    def test_nothing_to_search_is_unanchored(self):
        assert store.resolve_anchor("", "x")["anchor_basis"] == "unanchored"
        assert store.resolve_anchor(SECTION, "")["anchor_basis"] == "unanchored"
        assert store.resolve_anchor(SECTION, None)["anchor_basis"] == "unanchored"

    def test_exact_insertion_point_over_empty_section(self):
        r = store.resolve_anchor("", "", anchor_start=0, anchor_end=0)
        assert r["anchor_basis"] == "exact"

    def test_whole_section_anchor_is_exact_over_the_content(self):
        a = store.whole_section_anchor("sec-1", SECTION)
        assert a == {"anchor_section_id": "sec-1", "anchor_start": 0,
                     "anchor_end": len(SECTION), "anchor_text": SECTION,
                     "anchor_basis": "exact"}


# ── Round trip over all three bases ───────────────────────────────────────────

class TestRoundTrip:
    def test_exact_round_trip(self):
        text = "port 8443"
        s = SECTION.index(text)
        sid = store.create_suggestion(
            section_id="sec-rt-1", suggested_content="port 443",
            current_content=SECTION, origin_kind="docmod_redline",
            anchor_section_id="sec-rt-1", anchor_start=s, anchor_end=s + len(text),
            anchor_text=text, anchor_basis="exact",
        )
        row = store.get_suggestion(sid)
        assert row["anchor_basis"] == "exact"
        assert row["anchor_section_id"] == "sec-rt-1"
        assert (row["anchor_start"], row["anchor_end"]) == (s, s + len(text))
        assert row["anchor_text"] == text
        assert row["current_content"][row["anchor_start"]:row["anchor_end"]] == row["anchor_text"]
        assert row["origin_kind"] == "docmod_redline"
        assert row["applied_text"] is None and row["applied_by"] is None
        assert row["status"] == "pending"

    def test_relocated_round_trip(self):
        r = store.resolve_anchor(SECTION, "Legacy hosts")
        assert r["anchor_basis"] == "relocated"
        sid = store.create_suggestion(
            section_id="sec-rt-2", suggested_content="Remaining hosts",
            current_content=SECTION, origin_kind="section_draft",
            anchor_section_id="sec-rt-2", anchor_start=r["anchor_start"],
            anchor_end=r["anchor_end"], anchor_text=r["anchor_text"],
            anchor_basis="relocated",
        )
        row = store.get_suggestion(sid)
        assert row["anchor_basis"] == "relocated"
        assert row["current_content"][row["anchor_start"]:row["anchor_end"]] == "Legacy hosts"

    def test_unanchored_round_trip_keeps_sought_text_and_no_offsets(self):
        sid = store.create_suggestion(
            suggested_content="TLS 1.2 or higher", current_content="TLS 1.1",
            origin_kind="docmod_redline", anchor_basis="unanchored",
            anchor_text="TLS 1.1",
        )
        row = store.get_suggestion(sid)
        assert row["anchor_basis"] == "unanchored"
        assert row["anchor_section_id"] is None
        assert row["anchor_start"] is None and row["anchor_end"] is None
        assert row["anchor_text"] == "TLS 1.1"

    def test_default_is_unanchored_with_nothing_recorded(self):
        # The legacy call shape still works and is honest about what it knows.
        sid = store.create_suggestion(suggested_content="x")
        row = store.get_suggestion(sid)
        assert row["anchor_basis"] == "unanchored"
        assert row["origin_kind"] is None
        assert row["anchor_text"] is None

    def test_whole_section_anchor_round_trip(self):
        sid = store.create_suggestion(
            section_id="sec-rt-3", suggested_content="rewritten",
            current_content=SECTION, origin_kind="crowdsource",
            **store.whole_section_anchor("sec-rt-3", SECTION),
        )
        row = store.get_suggestion(sid)
        assert row["anchor_basis"] == "exact"
        assert (row["anchor_start"], row["anchor_end"]) == (0, len(SECTION))
        assert row["anchor_text"] == SECTION

    def test_pending_listing_carries_the_anchor_columns(self):
        sid = store.create_suggestion(
            section_id="sec-rt-4", suggested_content="y", current_content=SECTION,
            origin_kind="crowdsource", **store.whole_section_anchor("sec-rt-4", SECTION),
        )
        rows = [r for r in store.get_pending_suggestions() if r["suggestion_id"] == sid]
        assert rows and rows[0]["anchor_basis"] == "exact"
        assert set(name for name, _ in store.ANCHOR_COLUMNS) <= set(rows[0])


# ── Refusals: nothing is written ──────────────────────────────────────────────

class TestRefusals:
    @pytest.mark.parametrize("kwargs, needle", [
        # the invariant: content[start:end] must equal anchor_text
        (dict(anchor_section_id="s", anchor_start=0, anchor_end=7, anchor_text="enclave",
              anchor_basis="exact", current_content=SECTION), "verbatim slice"),
        # span length disagrees with the text
        (dict(anchor_section_id="s", anchor_start=4, anchor_end=8, anchor_text="enclave",
              anchor_basis="exact", current_content=SECTION), "length"),
        # anchored with no section of record
        (dict(anchor_section_id="s", anchor_start=4, anchor_end=11, anchor_text="enclave",
              anchor_basis="exact", current_content=None), "current_content"),
        # anchored with no section id
        (dict(anchor_section_id="", anchor_start=4, anchor_end=11, anchor_text="enclave",
              anchor_basis="exact", current_content=SECTION), "anchor_section_id"),
        # an unanchored row may not smuggle a span
        (dict(anchor_start=0, anchor_end=3, anchor_basis="unanchored",
              current_content=SECTION), "no offsets"),
        # relocation of nothing
        (dict(anchor_section_id="s", anchor_start=0, anchor_end=0, anchor_text="",
              anchor_basis="relocated", current_content=SECTION), "non-empty"),
        # end before start
        (dict(anchor_section_id="s", anchor_start=5, anchor_end=2, anchor_text="",
              anchor_basis="exact", current_content=SECTION), "0 <= start <= end"),
        # a basis nobody declared
        (dict(anchor_basis="guessed"), "anchor_basis"),
        # an origin nobody declared
        (dict(origin_kind="robot"), "origin_kind"),
        # half an application record
        (dict(applied_text="x"), "together"),
        (dict(applied_by="alice"), "together"),
    ])
    def test_inconsistent_anchor_is_refused_and_unwritten(self, kwargs, needle):
        before = _row_count()
        with pytest.raises(ValueError) as exc:
            store.create_suggestion(suggested_content="z", **kwargs)
        assert needle in str(exc.value)
        assert _row_count() == before

    def test_exact_is_never_inferred_from_a_bare_span(self):
        # A caller must SAY exact; the store does not promote a matching slice
        # supplied under `unanchored` into an exact anchor.
        with pytest.raises(ValueError):
            store.create_suggestion(
                suggested_content="z", current_content=SECTION, anchor_basis="unanchored",
                anchor_section_id="s", anchor_start=4, anchor_end=11, anchor_text="enclave",
            )

    def test_bool_offsets_are_not_integers(self):
        with pytest.raises(ValueError):
            store.validate_anchor(anchor_basis="exact", anchor_section_id="s",
                                  anchor_start=False, anchor_end=True, anchor_text="T",
                                  current_content="T")


# ── applied_text sits beside the draft, never over it ─────────────────────────

class TestApplication:
    def test_record_application_needs_an_accepted_row(self):
        sid = store.create_suggestion(suggested_content="draft", origin_kind="section_draft")
        assert store.record_application(sid, "human rewrite", "alice") is False
        assert store.record_application("sug_missing", "x", "alice") is False
        row = store.get_suggestion(sid)
        assert row["applied_text"] is None

    def test_record_application_after_accept_keeps_both_texts(self):
        sid = store.create_suggestion(suggested_content="AI draft", origin_kind="section_draft")
        assert store.decide_suggestion(sid, "accepted", "alice") is True
        assert store.record_application(sid, "human rewrite", "alice") is True
        row = store.get_suggestion(sid)
        assert row["suggested_content"] == "AI draft"
        assert row["applied_text"] == "human rewrite"
        assert row["applied_by"] == "alice"

    def test_record_application_refuses_a_half_record(self):
        sid = store.create_suggestion(suggested_content="AI draft", origin_kind="section_draft")
        store.decide_suggestion(sid, "accepted", "alice")
        with pytest.raises(ValueError):
            store.record_application(sid, "text", "")

    def test_human_edit_origin_can_carry_its_application_at_creation(self):
        sid = store.create_suggestion(
            suggested_content="typed by hand", origin_kind="human_edit",
            applied_text="typed by hand", applied_by="bob",
        )
        row = store.get_suggestion(sid)
        assert row["origin_kind"] == "human_edit"
        assert row["applied_by"] == "bob"


# ── Migration 20260907213944 ──────────────────────────────────────────────────

OLD_SHAPE = """
CREATE TABLE dic_suggestions (
    suggestion_id       TEXT    PRIMARY KEY,
    section_id          TEXT,
    doc_id              TEXT,
    collection_id       TEXT,
    trigger_event_id    TEXT,
    canvas_source       TEXT    NOT NULL DEFAULT 'unknown',
    suggested_content   TEXT    NOT NULL DEFAULT '',
    current_content     TEXT,
    rationale           TEXT,
    status              TEXT    NOT NULL DEFAULT 'pending',
    created_at          TEXT    NOT NULL,
    updated_at          TEXT,
    tenant_id           TEXT,
    classification      TEXT    NOT NULL DEFAULT 'CUI'
)
"""


class TestMigration:
    def test_migration_is_a_python_migration_with_no_sql_twin(self):
        assert (MIGRATION_DIR / "up.py").exists()
        assert (MIGRATION_DIR / "down.py").exists()
        # A directory carrying BOTH is ambiguous to the runner (up.sql wins).
        assert not (MIGRATION_DIR / "up.sql").exists()
        assert not (MIGRATION_DIR / "down.sql").exists()

    def test_migration_columns_match_the_store_declaration(self):
        mig = _load_migration()
        assert tuple(mig.NEW_COLUMNS) == tuple(store.ANCHOR_COLUMNS)

    def test_existing_old_shape_table_gains_exactly_the_missing_columns(self, tmp_path):
        from tools.db.storage import get_connection
        db = tmp_path / "old.db"
        raw = sqlite3.connect(db)
        raw.executescript(OLD_SHAPE)
        raw.execute("INSERT INTO dic_suggestions (suggestion_id, created_at) VALUES ('sug_old', 't0')")
        raw.commit()
        raw.close()

        mig = _load_migration()
        with get_connection(db_path=str(db)) as conn:
            mig.up(conn)
            cols = mig.existing_columns(conn)
            assert {n for n, _ in mig.NEW_COLUMNS} <= cols
            # idempotent: a second run adds nothing and raises nothing
            mig.up(conn)
            assert mig.existing_columns(conn) == cols
            # the pre-existing row reads as NOT RECORDED, never as a default
            row = dict(conn.execute(
                "SELECT anchor_basis, origin_kind, applied_text FROM dic_suggestions "
                "WHERE suggestion_id = 'sug_old'").fetchone())
            assert row == {"anchor_basis": None, "origin_kind": None, "applied_text": None}

    def test_absent_table_is_created_in_full(self, tmp_path):
        from tools.db.storage import get_connection
        db = tmp_path / "fresh.db"
        mig = _load_migration()
        with get_connection(db_path=str(db)) as conn:
            mig.up(conn)
            cols = mig.existing_columns(conn)
        assert {"suggestion_id", "status", "created_at"} <= cols
        assert {n for n, _ in mig.NEW_COLUMNS} <= cols

    def test_down_drops_exactly_the_added_columns(self, tmp_path):
        from tools.db.storage import get_connection
        db = tmp_path / "down.db"
        raw = sqlite3.connect(db)
        raw.executescript(OLD_SHAPE)
        raw.commit()
        raw.close()
        mig = _load_migration()
        with get_connection(db_path=str(db)) as conn:
            before = mig.existing_columns(conn)
            mig.up(conn)
            mig.down(conn)
            after = mig.existing_columns(conn)
        assert after == before

    def test_store_ddl_and_migration_ddl_agree(self):
        """A fresh table from either path has the same columns.

        "Either path" is the store's ``_ensure_tables`` against the MIGRATION
        CHAIN, not against this one migration: a later migration that adds a
        column adds it to the store's DDL too, and comparing the store to a
        single earlier snapshot would fail on every future column with nothing
        actually out of step (dwr-ev-03 added `superseded_by`).
        """
        from tests._sql_compat import translating
        mig = _load_migration()
        a = sqlite3.connect(":memory:")
        a.execute(mig.CREATE_FULL)
        from_mig = {r[1] for r in a.execute("PRAGMA table_info(dic_suggestions)")}
        for later in _later_migrations_for_dic_suggestions():
            from_mig |= {n for n, _ in later.NEW_COLUMNS}

        b = sqlite3.connect(":memory:")
        store._ensure_tables(translating(b, unclosable=True))
        from_store = {r[1] for r in b.execute("PRAGMA table_info(dic_suggestions)")}
        assert from_mig == from_store


# ── Every writer says what it is ─────────────────────────────────────────────

def _create_suggestion_call_sites() -> list[tuple[Path, ast.Call]]:
    out = subprocess.run(
        ["git", "grep", "-l", "create_suggestion(", "--", "tools/"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    sites: list[tuple[Path, ast.Call]] = []
    for rel in out.stdout.split():
        path = ROOT / rel
        if path.suffix != ".py" or path.name == "suggestion_store.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if name == "create_suggestion":
                    sites.append((path, node))
    return sites


def test_every_runtime_writer_declares_its_origin_kind():
    sites = _create_suggestion_call_sites()
    assert sites, "no create_suggestion call sites found under tools/ — the scan is broken"
    missing = []
    for path, call in sites:
        kws = {kw.arg for kw in call.keywords}
        if "origin_kind" not in kws:
            missing.append(f"{path.relative_to(ROOT)}:{call.lineno}")
    assert not missing, f"create_suggestion callers without origin_kind: {missing}"


def _keyword_literal(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def test_redline_drafter_records_a_basis_it_computed_never_a_literal():
    """dwr-anchor-03 pinned this call to a LITERAL `unanchored`, because the
    drafter was handed an entity LABEL and not a span. dwr-anchor-04 wired the
    span, so the claim inverts and the pin inverts with it: the basis must now
    be whatever `resolve_passage` could PROVE against the live section, and the
    one thing still forbidden is a HARD-CODED basis — `exact` written as a
    literal is the inference both cards refuse. The offsets travel with it: a
    basis with no span is a claim with no evidence."""
    path = ROOT / "tools" / "doc_modernization" / "redline_drafter.py"
    calls = [c for p, c in _create_suggestion_call_sites() if p == path]
    assert len(calls) == 1, f"expected ONE create_suggestion call in redline_drafter, found {len(calls)}"
    call = calls[0]
    assert _keyword_literal(call, "origin_kind") == "docmod_redline"
    assert _keyword_literal(call, "anchor_basis") is None, \
        "anchor_basis must be COMPUTED, never a literal"
    kws = {kw.arg for kw in call.keywords}
    for required in ("anchor_section_id", "anchor_start", "anchor_end",
                     "anchor_text", "anchor_basis", "anchor_content"):
        assert required in kws, f"the drafter's write is missing {required}"


@pytest.mark.parametrize("relpath, expected_origin", [
    ("tools/document_intelligence/blueprint.py", "crowdsource"),
    ("tools/genesis/reflexes/dic_integration.py", "section_draft"),
])
def test_section_writers_declare_their_origin(relpath, expected_origin):
    path = ROOT / relpath
    calls = [c for p, c in _create_suggestion_call_sites() if p == path]
    assert calls, f"no create_suggestion call in {relpath}"
    for call in calls:
        assert _keyword_literal(call, "origin_kind") == expected_origin, \
            f"{relpath}:{call.lineno} origin_kind is not {expected_origin!r}"
