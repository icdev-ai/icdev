# CUI // SP-CTI
"""Tests for web citation type + fetch provenance (oss-cite-01).

Runs under conftest, which forces ICDEV_STORAGE_BACKEND=sqlite. PostgreSQL is
the primary acceptance target for the constraint repair — the PG branch of
``repair_citation_type_constraint`` is exercised there via migration 295 — but
SQLite is a real runtime backend for this table too, so the rebuild path below
is not merely a harness convenience.

No test in this file opens a socket: the fetch path is exercised through a
stub response object, which is also the point of ``capture()`` being separate
from ``fetch_with_provenance()``.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import pytest

from tools.provenance import registry, web_citation
from tools.provenance.registry import (
    CITATION_TYPES,
    citation_type_check_sql,
    repair_citation_type_constraint,
)
from tools.provenance.web_citation import (
    EgressDenied,
    WebFetchProvenance,
    capture,
    content_sha256,
)

# The ten values migration 149 shipped, before oss-cite-01.
_LEGACY_TYPES = (
    "hitl", "rag", "prov_entity", "prov_activity", "canvas_ai",
    "slsa", "sbom", "compliance_evidence", "agent_decision", "manual",
)


@pytest.fixture
def prov_db(tmp_path, monkeypatch):
    """Point get_connection() at a fresh temp SQLite DB with both tables."""
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        f"""
        CREATE TABLE source_citation_registry (
            id TEXT PRIMARY KEY,
            citation_type TEXT NOT NULL CHECK({citation_type_check_sql()}),
            source_table TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            source_doc TEXT,
            source_hash TEXT NOT NULL,
            anchor_hash TEXT,
            merkle_root TEXT,
            blockchain_tx_id TEXT,
            classification TEXT DEFAULT 'CUI',
            project_id TEXT,
            trust_score REAL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(registry, "DB_PATH", db_path)
    web_citation.init_tables()
    return db_path


class _StubResponse:
    """Minimal stand-in for requests.Response — no socket, no requests import."""

    def __init__(self, url, status_code=200, text="", headers=None, history=()):
        self.url = url
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.history = list(history)


# ---------------------------------------------------------------------------
# The citation type itself
# ---------------------------------------------------------------------------


class TestCitationType:
    def test_web_is_an_allowed_citation_type(self):
        assert "web" in CITATION_TYPES

    def test_no_legacy_type_was_dropped(self):
        # Widening the constraint must never narrow it.
        assert set(_LEGACY_TYPES) <= set(CITATION_TYPES)

    def test_check_sql_is_derived_not_hardcoded(self):
        sql = citation_type_check_sql()
        for value in CITATION_TYPES:
            assert f"'{value}'" in sql
        assert sql.startswith("citation_type IN (")

    def test_unknown_type_raises_rather_than_returning_empty(self, prov_db):
        # The pre-oss-cite-01 behaviour was a silent "" — indistinguishable from
        # a registered citation that simply had no id.
        with pytest.raises(ValueError, match="unknown citation_type"):
            registry.register_citation(
                citation_type="crawl",
                source_table="web_fetch_provenance",
                source_record_id="wfp-1",
                source_hash="abc",
            )

    def test_web_citation_actually_persists(self, prov_db):
        reg_id = registry.register_citation(
            citation_type="web",
            source_table="web_fetch_provenance",
            source_record_id="wfp-1",
            source_hash="abc",
            source_doc="https://example.gov/policy",
            project_id="proj-1",
        )
        assert reg_id.startswith("scr-")
        row = registry.get_citation_by_hash("abc")
        assert row is not None
        assert row["citation_type"] == "web"


class TestConstraintRepair:
    """The SQLite rebuild half of repair_citation_type_constraint."""

    def _legacy_db(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        legacy_check = ", ".join(f"'{t}'" for t in _LEGACY_TYPES)
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            f"""
            CREATE TABLE source_citation_registry (
                id TEXT PRIMARY KEY,
                citation_type TEXT NOT NULL CHECK(citation_type IN ({legacy_check})),
                source_table TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                source_doc TEXT,
                source_hash TEXT NOT NULL,
                anchor_hash TEXT,
                merkle_root TEXT,
                blockchain_tx_id TEXT,
                classification TEXT DEFAULT 'CUI',
                project_id TEXT,
                trust_score REAL DEFAULT 0.0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO source_citation_registry
                (id, citation_type, source_table, source_record_id, source_hash)
            VALUES ('scr-old', 'sbom', 'sbom_records', '7', 'deadbeef');
            """
        )
        conn.commit()
        conn.close()
        return db_path

    def test_legacy_table_rejects_web_before_repair(self, tmp_path):
        db_path = self._legacy_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO source_citation_registry "
                "(id, citation_type, source_table, source_record_id, source_hash) "
                "VALUES ('scr-x','web','web_fetch_provenance','wfp-1','abc')"
            )
        conn.close()

    def test_repair_widens_the_constraint_and_preserves_rows(self, tmp_path, monkeypatch):
        db_path = self._legacy_db(tmp_path)
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            result = repair_citation_type_constraint(conn)
        finally:
            conn.close()
        assert result["status"] == "repaired"

        raw = sqlite3.connect(str(db_path))
        # The pre-existing row survived the rebuild.
        assert raw.execute(
            "SELECT COUNT(*) FROM source_citation_registry WHERE id = 'scr-old'"
        ).fetchone()[0] == 1
        # And 'web' is now insertable.
        raw.execute(
            "INSERT INTO source_citation_registry "
            "(id, citation_type, source_table, source_record_id, source_hash) "
            "VALUES ('scr-x','web','web_fetch_provenance','wfp-1','abc')"
        )
        raw.commit()
        raw.close()

    def test_repair_is_idempotent(self, tmp_path, monkeypatch):
        db_path = self._legacy_db(tmp_path)
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            assert repair_citation_type_constraint(conn)["status"] == "repaired"
            assert repair_citation_type_constraint(conn)["status"] == "ok"
        finally:
            conn.close()

    def test_repair_reports_absent_table(self, tmp_path, monkeypatch):
        db_path = tmp_path / "empty.db"
        sqlite3.connect(str(db_path)).close()
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            assert repair_citation_type_constraint(conn)["status"] == "absent"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Fetch provenance capture
# ---------------------------------------------------------------------------


class TestCapture:
    def test_captures_every_required_field(self):
        resp = _StubResponse(
            "https://example.gov/policy",
            status_code=200,
            text="<html>policy</html>",
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "ETag": 'W/"abc123"',
                "Last-Modified": "Tue, 01 Jul 2026 10:00:00 GMT",
                "Content-Length": "19",
            },
        )
        prov = capture(resp, "https://example.gov/policy", title="Policy")

        assert prov.requested_url == "https://example.gov/policy"
        assert prov.final_url == "https://example.gov/policy"
        assert prov.http_status == 200
        assert prov.fetched_at.endswith("+00:00")
        assert prov.content_hash == content_sha256("<html>policy</html>")
        assert prov.etag == 'W/"abc123"'
        assert prov.last_modified == "Tue, 01 Jul 2026 10:00:00 GMT"
        assert prov.content_type.startswith("text/html")
        assert prov.content_length == 19
        assert prov.title == "Policy"
        assert prov.id.startswith("wfp-")

    def test_requested_and_final_url_are_distinct_after_redirects(self):
        hop1 = _StubResponse("http://example.gov/policy", status_code=301)
        hop2 = _StubResponse("https://example.gov/policy", status_code=301)
        resp = _StubResponse(
            "https://mirror.example.gov/policy", status_code=200, text="body",
            history=[hop1, hop2],
        )
        prov = capture(resp, "http://example.gov/policy")

        assert prov.requested_url == "http://example.gov/policy"
        assert prov.final_url == "https://mirror.example.gov/policy"
        assert prov.redirected is True
        assert prov.redirect_chain == [
            "http://example.gov/policy",
            "https://example.gov/policy",
            "https://mirror.example.gov/policy",
        ]

    def test_no_redirect_leaves_an_empty_chain(self):
        prov = capture(_StubResponse("https://example.gov/a", text="x"),
                       "https://example.gov/a")
        assert prov.redirect_chain == []
        assert prov.redirected is False

    def test_non_2xx_is_captured_not_discarded(self):
        prov = capture(_StubResponse("https://example.gov/gone", status_code=404, text=""),
                       "https://example.gov/gone")
        assert prov.http_status == 404

    def test_missing_revalidators_are_none_not_empty_string(self):
        prov = capture(_StubResponse("https://example.gov/a", text="x"),
                       "https://example.gov/a")
        assert prov.etag is None
        assert prov.last_modified is None

    def test_content_hash_is_stable_and_content_addressed(self):
        a = capture(_StubResponse("https://a.gov/x", text="same"), "https://a.gov/x")
        b = capture(_StubResponse("https://b.gov/y", text="same"), "https://b.gov/y")
        assert a.content_hash == b.content_hash
        assert a.id != b.id


class TestProvenanceRecord:
    def test_citation_tag_round_trips_through_the_shared_parser(self):
        from tools.quality.citation_grounding import parse_citations

        prov = WebFetchProvenance(requested_url="https://example.gov/a")
        text = f"The rule applies annually {prov.citation_tag()}."
        assert parse_citations(text) == [prov.id]

    def test_projects_onto_the_shared_provenance_dataclass(self):
        prov = capture(
            _StubResponse("https://example.gov/a", text="x",
                          headers={"ETag": 'W/"v7"'}),
            "https://example.gov/a",
        )
        shared = prov.to_source_provenance()
        assert shared.source_id == prov.id
        assert shared.sha256 == prov.content_hash
        assert shared.version_ref == 'W/"v7"'
        assert shared.ingest_timestamp == prov.fetched_at

    def test_from_row_round_trip(self):
        prov = capture(
            _StubResponse("https://c.gov/z", status_code=200, text="x",
                          history=[_StubResponse("https://a.gov/z", status_code=302)]),
            "https://a.gov/z",
        )
        row = prov.to_dict()
        row["redirect_chain"] = json.dumps(row["redirect_chain"])
        assert WebFetchProvenance.from_row(row).to_dict() == prov.to_dict()


# ---------------------------------------------------------------------------
# Persistence + the TRUST invariant
# ---------------------------------------------------------------------------


class TestRecord:
    def _record(self, project_id="proj-1", url="https://example.gov/policy"):
        prov = capture(
            _StubResponse(url, status_code=200, text="policy body",
                          headers={"ETag": 'W/"e1"'}),
            url,
            project_id=project_id,
        )
        return web_citation.record(prov)

    def test_record_persists_provenance_and_registers_a_citation(self, prov_db):
        result = self._record()
        assert result["fetch_id"].startswith("wfp-")
        assert result["citation_id"].startswith("scr-")

        row = web_citation.get(result["fetch_id"])
        assert row["final_url"] == "https://example.gov/policy"
        assert row["http_status"] == 200
        assert row["etag"] == 'W/"e1"'
        assert row["content_hash"] == content_sha256("policy body")

    def test_registered_citation_points_back_at_the_fetch_row(self, prov_db):
        result = self._record()
        citations = registry.get_citations_for_project("proj-1")
        match = [c for c in citations if c["source_record_id"] == result["fetch_id"]]
        assert len(match) == 1
        assert match[0]["citation_type"] == "web"
        assert match[0]["source_table"] == "web_fetch_provenance"
        assert match[0]["source_doc"] == "https://example.gov/policy"

    def test_redirect_chain_survives_persistence(self, prov_db):
        prov = capture(
            _StubResponse("https://final.gov/x", status_code=200, text="b",
                          history=[_StubResponse("https://start.gov/x", status_code=301)]),
            "https://start.gov/x",
            project_id="proj-1",
        )
        result = web_citation.record(prov)
        row = web_citation.get(result["fetch_id"])
        assert json.loads(row["redirect_chain"]) == [
            "https://start.gov/x",
            "https://final.gov/x",
        ]
        assert row["requested_url"] != row["final_url"]

    def test_no_register_skips_the_citation(self, prov_db):
        prov = capture(_StubResponse("https://example.gov/a", text="x"),
                       "https://example.gov/a", project_id="proj-2")
        result = web_citation.record(prov, register=False)
        assert result["citation_id"] == ""
        assert web_citation.get(result["fetch_id"]) is not None
        assert registry.get_citations_for_project("proj-2") == []

    def test_repeat_fetches_are_separate_observations(self, prov_db):
        first = self._record()
        second = self._record()
        assert first["fetch_id"] != second["fetch_id"]
        same_content = web_citation.get_by_hash(content_sha256("policy body"))
        assert len(same_content) == 2

    def test_init_tables_is_idempotent(self, prov_db):
        web_citation.init_tables()
        web_citation.init_tables()
        assert web_citation.list_fetches() == []


class TestCitationValidation:
    """The TRUST invariant: an inline tag validates against a persisted record."""

    def test_a_recorded_fetch_makes_its_citation_valid(self, prov_db):
        prov = capture(_StubResponse("https://example.gov/a", text="x"),
                       "https://example.gov/a", project_id="proj-1")
        result = web_citation.record(prov)
        draft = f"Annual review is required [source: {result['fetch_id']}]."

        report = web_citation.validate_web_citations(draft, project_id="proj-1")
        assert report["valid"] is True
        assert report["cited_count"] == 1

    def test_an_unrecorded_id_is_reported_as_hallucinated(self, prov_db):
        draft = "Annual review is required [source: wfp-0000000000000000]."
        report = web_citation.validate_web_citations(draft, project_id="proj-1")
        assert report["valid"] is False
        assert report["hallucinated_citations"] == ["wfp-0000000000000000"]

    def test_extra_sources_mix_with_web_ids(self, prov_db):
        draft = "See [source: chunk-42] and [source: wfp-0000000000000000]."
        report = web_citation.validate_web_citations(
            draft, project_id="proj-1",
            extra_sources=["chunk-42", "wfp-0000000000000000"],
        )
        assert report["valid"] is True

    def test_gate_flags_a_section_citing_an_unrecorded_page(self, prov_db):
        prov = capture(_StubResponse("https://example.gov/a", text="x"),
                       "https://example.gov/a", project_id="proj-1")
        good = web_citation.record(prov)["fetch_id"]
        sections = [
            {"item_number": "1.1", "content": f"Grounded [source: {good}]."},
            {"item_number": "1.2", "content": "Invented [source: wfp-ffffffffffffffff]."},
            {"item_number": "1.3", "content": "Bare assertion with no tag."},
            {"item_number": "1.4", "content": "Nothing claimed.", "abstained": True},
        ]
        findings = web_citation.web_citation_gate(sections, project_id="proj-1")
        issues = {(f["item_number"], f["issue"]) for f in findings}
        assert ("1.2", "hallucinated_citation") in issues
        assert ("1.3", "missing_citations") in issues
        assert not [f for f in findings if f["item_number"] in ("1.1", "1.4")]


# ---------------------------------------------------------------------------
# Egress
# ---------------------------------------------------------------------------


class TestEgress:
    def test_disabled_by_default(self):
        assert web_citation.check_egress("http://127.0.0.1:5050/x", {}) == (True, "disabled")

    def test_enabled_guard_rejects_a_non_https_url(self):
        allowed, reason = web_citation.check_egress(
            "http://example.gov/x", {"enabled": True}
        )
        assert allowed is False
        assert reason == "scheme_not_https"

    def test_enabled_guard_rejects_the_metadata_address(self):
        allowed, reason = web_citation.check_egress(
            "https://169.254.169.254/latest/meta-data/", {"enabled": True}
        )
        assert allowed is False
        assert reason == "denied_ip_range"

    def test_denylist_beats_allowlist(self):
        cfg = {"enabled": True, "allowlist": ["example.gov"], "denylist": ["example.gov"]}
        allowed, reason = web_citation.check_egress("https://a.example.gov/x", cfg)
        assert allowed is False
        assert reason == "denylisted"

    def test_configured_but_unavailable_guard_fails_closed(self, monkeypatch):
        monkeypatch.setattr(web_citation, "_egress_guard", lambda: None)
        allowed, reason = web_citation.check_egress(
            "https://example.gov/x", {"enabled": True}
        )
        assert allowed is False
        assert reason == "guard_unavailable"

    def test_fetch_raises_before_opening_a_socket(self, monkeypatch):
        monkeypatch.setattr(web_citation, "_egress_config",
                            lambda: {"enabled": True, "denylist": ["example.gov"]})
        with pytest.raises(EgressDenied) as exc:
            web_citation.fetch_with_provenance("https://example.gov/x")
        assert exc.value.reason == "denylisted"


# ---------------------------------------------------------------------------
# Migration 295
# ---------------------------------------------------------------------------

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "tools" / "db" / "migrations" / "295_web_citation_fetch_provenance" / "up.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_295_up", str(_MIGRATION))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigration295:
    def test_up_repairs_the_constraint_and_creates_the_table(self, tmp_path, monkeypatch):
        db_path = tmp_path / "migrate.db"
        legacy_check = ", ".join(f"'{t}'" for t in _LEGACY_TYPES)
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            f"""
            CREATE TABLE source_citation_registry (
                id TEXT PRIMARY KEY,
                citation_type TEXT NOT NULL CHECK(citation_type IN ({legacy_check})),
                source_table TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                source_doc TEXT,
                source_hash TEXT NOT NULL,
                anchor_hash TEXT,
                merkle_root TEXT,
                blockchain_tx_id TEXT,
                classification TEXT DEFAULT 'CUI',
                project_id TEXT,
                trust_score REAL DEFAULT 0.0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.commit()
        conn.close()

        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

        from tools.db.storage import get_connection

        module = _load_migration()
        conn = get_connection()
        try:
            result = module.up(conn)
            assert result["status"] == "ok"
            assert result["citation_type_constraint"]["status"] == "repaired"
            # Second application is a no-op, not a second rebuild.
            assert module.up(conn)["citation_type_constraint"]["status"] == "ok"
        finally:
            conn.close()

        raw = sqlite3.connect(str(db_path))
        try:
            tables = {
                r[0] for r in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            assert "web_fetch_provenance" in tables
            raw.execute(
                "INSERT INTO source_citation_registry "
                "(id, citation_type, source_table, source_record_id, source_hash) "
                "VALUES ('scr-w','web','web_fetch_provenance','wfp-1','abc')"
            )
            raw.commit()
        finally:
            raw.close()


# ---------------------------------------------------------------------------
# Drift guard: the constant must enumerate every type the tree actually uses
# ---------------------------------------------------------------------------


class TestCitationTypeConstantMatchesCallSites:
    """The whole point of deriving the CHECK from CITATION_TYPES is that the
    constant is true. It stopped being true once before, silently: migration
    149 hardcoded ten values while ``tools/cortex/governance.py`` passed
    ``'cortex'`` and ``tools/blockchain/asset_ledger.py`` passed
    ``'asset_token'``. Every one of those inserts failed the CHECK and was
    swallowed by ``except Exception: return ""``, so Cortex reported a
    provenance record it had never written.

    This test re-derives the answer from the source tree instead of trusting a
    list, so the next call site added with an unlisted type fails here rather
    than in production.
    """

    CALL_RE = re.compile(r"""citation_type\s*=\s*["']([a-z_]+)["']""")

    def _literal_types(self) -> dict:
        """Map every literal ``citation_type="..."`` in tools/ to its file."""
        from tools.provenance import registry

        root = Path(registry.__file__).resolve().parent.parent  # tools/
        found: dict[str, set] = {}
        for path in root.rglob("*.py"):
            # Skip the registry's own definition and any migration DDL.
            if path.name == "registry.py" or "migrations" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for value in self.CALL_RE.findall(text):
                found.setdefault(value, set()).add(path.name)
        return found

    def test_every_call_site_type_is_declared(self):
        from tools.provenance.registry import CITATION_TYPES

        used = self._literal_types()
        undeclared = {v: sorted(f) for v, f in used.items() if v not in CITATION_TYPES}
        assert not undeclared, (
            "citation_type values passed by shipped code but missing from "
            f"CITATION_TYPES: {undeclared}. register_citation() now raises on "
            "these, and before it raised they failed the CHECK silently. Add "
            "them to the constant and ship a constraint repair."
        )

    def test_the_two_historically_silent_types_are_declared(self):
        """Regression pin for the specific pair found by oss-cite-01."""
        from tools.provenance.registry import CITATION_TYPES

        assert "cortex" in CITATION_TYPES
        assert "asset_token" in CITATION_TYPES

    def test_derived_check_sql_covers_them(self):
        from tools.provenance.registry import citation_type_check_sql

        sql = citation_type_check_sql()
        assert "'cortex'" in sql
        assert "'asset_token'" in sql
        assert "'web'" in sql

    def test_register_citation_accepts_the_recovered_types(self, prov_db):
        """They must now round-trip, not merely stop raising."""
        from tools.provenance.registry import register_citation

        for citation_type in ("cortex", "asset_token", "web"):
            reg_id = register_citation(
                citation_type=citation_type,
                source_table="t",
                source_record_id="r",
                source_hash="h",
            )
            assert reg_id.startswith("scr-"), (
                f"{citation_type} still does not persist — an empty id means the "
                "INSERT failed the CHECK and was swallowed"
            )
