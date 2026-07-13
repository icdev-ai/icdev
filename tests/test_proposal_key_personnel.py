# CUI // SP-CTI
"""Bid-side key personnel registry (proposal_key_personnel) + the program_bridge rewire.

The gap this closes: pg_lcat_allocations is task->LCAT->FTE and never names a
human, pma_personnel is post-award (contract_id), so program_bridge used to
regex names out of proposal_section_drafts prose.
"""
from __future__ import annotations

import json

import pytest

from tools.db.storage import get_connection
from tools.govcon import key_personnel as kp
from tools.govcon import program_bridge as pb

OPP = "opp-pstaff-test"


@pytest.fixture
def conn(icdev_db):
    """Storage connection on the shared conftest schema (proposal_key_personnel included)."""
    connection = get_connection(db_path=str(icdev_db))
    kp.ensure_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def drafts_conn(conn):
    """Add the legacy scrape source: the free-text drafts program_bridge used to mine.

    Minimal stub — only the columns _scrape_key_personnel actually reads.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS proposal_section_drafts ("
        " id TEXT PRIMARY KEY, opportunity_id TEXT, draft_content TEXT)"
    )
    conn.commit()
    return conn


class TestSchemaAndConstraints:
    def test_rls_columns_exist_from_the_start(self, conn):
        """tenant_id/classification are present at creation — never retrofitted (migration 245's lesson)."""
        person = kp.record_key_person(
            OPP, "Jane Doe", tenant_id="acme", classification="CUI", conn=conn
        )
        assert person["tenant_id"] == "acme"
        assert person["classification"] == "CUI"

    def test_check_constraint_values_come_from_the_python_constants(self):
        ddl = kp._table_sql()
        for verdict in kp.QUALIFICATION_VERDICTS:
            assert f"'{verdict}'" in ddl
        for source in kp.PERSON_SOURCES:
            assert f"'{source}'" in ddl

    @pytest.mark.parametrize("verdict", ["maybe", "", "QUALIFIED"])
    def test_rejects_unknown_verdict(self, conn, verdict):
        with pytest.raises(ValueError):
            kp.record_key_person(OPP, "Jane Doe", qualification_verdict=verdict, conn=conn)

    def test_rejects_unknown_source(self, conn):
        with pytest.raises(ValueError):
            kp.record_key_person(OPP, "Jane Doe", source="linkedin", conn=conn)

    def test_rejects_blank_name(self, conn):
        with pytest.raises(ValueError):
            kp.record_key_person(OPP, "   ", conn=conn)


class TestRecordAndRead:
    def test_person_ref_is_derived_from_name_when_absent(self, conn):
        person = kp.record_key_person(OPP, "Jane Q. Doe", conn=conn)
        assert person["person_ref"] == "jane-q-doe"

    def test_explicit_person_ref_wins(self, conn):
        person = kp.record_key_person(OPP, "Jane Doe", person_ref="emp-4417", conn=conn)
        assert person["person_ref"] == "emp-4417"

    def test_people_are_scoped_to_their_opportunity(self, conn):
        kp.record_key_person(OPP, "Jane Doe", conn=conn)
        kp.record_key_person("opp-other", "John Roe", conn=conn)
        names = [p["name"] for p in kp.list_key_personnel(OPP, conn=conn)]
        assert names == ["Jane Doe"]

    def test_person_carries_lcat_and_evidence(self, conn):
        evidence = [{"source": "resume", "locator": "p.2", "quote": "12 yrs SETA"}]
        kp.record_key_person(
            OPP, "Jane Doe",
            proposed_lcat="Senior Systems Engineer",
            qualification_verdict="qualified",
            evidence=evidence,
            source="resume",
            conn=conn,
        )
        person = kp.get_key_person(OPP, "jane-doe", conn=conn)
        assert person["proposed_lcat"] == "Senior Systems Engineer"
        assert person["qualification_verdict"] == "qualified"
        assert person["evidence"] == evidence
        assert json.loads(person["evidence_json"]) == evidence

    def test_missing_person_is_none(self, conn):
        assert kp.get_key_person(OPP, "nobody", conn=conn) is None


class TestAppendOnly:
    def test_verdict_change_appends_a_revision_and_keeps_history(self, conn):
        kp.record_key_person(
            OPP, "Jane Doe", proposed_lcat="Senior SE",
            qualification_verdict="unverified", conn=conn,
        )
        kp.set_verdict(
            OPP, "jane-doe", "gap",
            evidence=[{"source": "rfp", "locator": "L.3.2", "quote": "requires PMP"}],
            conn=conn,
        )

        trail = kp.history(OPP, "jane-doe", conn=conn)
        assert [r["qualification_verdict"] for r in trail] == ["unverified", "gap"]

        roster = kp.list_key_personnel(OPP, conn=conn)
        assert len(roster) == 1, "latest revision wins — the roster must not double-count"
        assert roster[0]["qualification_verdict"] == "gap"

    def test_verdict_change_carries_the_rest_of_the_record_forward(self, conn):
        kp.record_key_person(
            OPP, "Jane Doe", proposed_lcat="Senior SE", source="resume",
            tenant_id="acme", conn=conn,
        )
        updated = kp.set_verdict(OPP, "jane-doe", "qualified", conn=conn)
        assert updated["proposed_lcat"] == "Senior SE"
        assert updated["source"] == "resume"
        assert updated["tenant_id"] == "acme"
        assert updated["name"] == "Jane Doe"

    def test_withdraw_hides_from_roster_without_deleting(self, conn):
        kp.record_key_person(OPP, "Jane Doe", conn=conn)
        kp.withdraw_key_person(OPP, "jane-doe", notes="declined offer", conn=conn)

        assert kp.list_key_personnel(OPP, conn=conn) == []
        withdrawn = kp.list_key_personnel(OPP, include_withdrawn=True, conn=conn)
        assert [p["status"] for p in withdrawn] == ["withdrawn"]
        assert len(kp.history(OPP, "jane-doe", conn=conn)) == 2

        rows = conn.execute(
            "SELECT COUNT(*) FROM proposal_key_personnel WHERE opportunity_id = %s", (OPP,)
        ).fetchone()
        assert rows[0] == 2, "withdrawal appends; it never deletes"

    def test_set_verdict_on_unknown_person_raises(self, conn):
        with pytest.raises(LookupError):
            kp.set_verdict(OPP, "ghost", "qualified", conn=conn)

    def test_withdraw_unknown_person_raises(self, conn):
        with pytest.raises(LookupError):
            kp.withdraw_key_person(OPP, "ghost", conn=conn)


class TestStaffingSummary:
    def test_summary_counts_verdicts_and_lcat_coverage(self, conn):
        kp.record_key_person(
            OPP, "Jane Doe", proposed_lcat="Senior SE",
            qualification_verdict="qualified", conn=conn,
        )
        kp.record_key_person(
            OPP, "John Roe", proposed_lcat="Junior SE",
            qualification_verdict="gap", conn=conn,
        )
        kp.record_key_person(OPP, "Sam Poe", conn=conn)  # no LCAT, unverified

        summary = kp.staffing_summary(OPP, conn=conn)
        assert summary["total"] == 3
        assert summary["by_verdict"]["qualified"] == 1
        assert summary["by_verdict"]["gap"] == 1
        assert summary["by_verdict"]["unverified"] == 1
        assert summary["with_lcat"] == 2
        assert summary["lcat_coverage_pct"] == pytest.approx(66.7)
        assert summary["unqualified_refs"] == ["john-roe"]
        assert summary["unverified_refs"] == ["sam-poe"]

    def test_empty_roster_summary_does_not_divide_by_zero(self, conn):
        summary = kp.staffing_summary(OPP, conn=conn)
        assert summary["total"] == 0
        assert summary["lcat_coverage_pct"] == 0.0


class TestProgramBridgeRewire:
    """The point of the table: the bridge reads declared people, not regex guesses."""

    def test_bridge_reads_the_roster_not_the_prose(self, drafts_conn):
        drafts_conn.execute(
            "INSERT INTO proposal_section_drafts (id, opportunity_id, draft_content) "
            "VALUES (%s, %s, %s)",
            ("d1", OPP, "Our team will coordinate with Colonel Smith at Fort Belvoir."),
        )
        drafts_conn.commit()
        kp.record_key_person(
            OPP, "Jane Doe", proposed_lcat="Program Manager",
            qualification_verdict="qualified", conn=drafts_conn,
        )

        result = pb._gather_key_personnel(drafts_conn, OPP)
        assert result["scraped"] is False
        assert result["record_count"] == 1
        assert [p["name"] for p in result["data"]] == ["Jane Doe"]
        assert result["data"][0]["proposed_lcat"] == "Program Manager"

    def test_bridge_falls_back_to_the_scrape_only_when_the_roster_is_empty(self, drafts_conn):
        drafts_conn.execute(
            "INSERT INTO proposal_section_drafts (id, opportunity_id, draft_content) "
            "VALUES (%s, %s, %s)",
            ("d1", OPP, "Jane Doe will serve as Program Manager."),
        )
        drafts_conn.commit()

        result = pb._gather_key_personnel(drafts_conn, OPP)
        assert result["scraped"] is True
        scraped = result["data"]
        assert any(p["name"] == "Jane Doe" for p in scraped)
        assert all(p["source"] == "draft_scrape" for p in scraped)
        assert all(p["qualification_verdict"] == "unverified" for p in scraped)

    def test_withdrawn_people_do_not_reach_the_bridge(self, drafts_conn):
        kp.record_key_person(OPP, "Jane Doe", conn=drafts_conn)
        kp.withdraw_key_person(OPP, "jane-doe", conn=drafts_conn)
        result = pb._gather_key_personnel(drafts_conn, OPP)
        assert result["data"] == []

    def test_bridge_survives_a_missing_drafts_table(self, conn):
        """No roster and no drafts table at all — degrade to empty, never raise."""
        result = pb._gather_key_personnel(conn, OPP)
        assert result == {"data": [], "record_count": 0, "scraped": False}


class TestBridgeRendering:
    def test_render_shows_lcat_and_verdict_columns(self):
        markdown = pb._render_key_personnel([
            {
                "person_ref": "jane-doe", "name": "Jane Doe",
                "proposed_lcat": "Senior SE", "qualification_verdict": "qualified",
                "source": "resume",
            },
        ])
        assert "Proposed LCAT" in markdown
        assert "Senior SE" in markdown
        assert "Qualified" in markdown
        assert "Unverified." not in markdown

    def test_render_warns_when_the_names_were_scraped(self):
        markdown = pb._render_key_personnel([
            {
                "person_ref": "jane-doe", "name": "Jane Doe", "proposed_lcat": None,
                "qualification_verdict": "unverified", "source": "draft_scrape",
            },
        ])
        assert "Unverified." in markdown
        assert "not a staffing commitment" in markdown

    def test_render_flags_people_who_miss_the_bar(self):
        markdown = pb._render_key_personnel([
            {
                "person_ref": "john-roe", "name": "John Roe", "proposed_lcat": "Junior SE",
                "qualification_verdict": "gap", "source": "resume",
            },
        ])
        assert "Qualification risk" in markdown
        assert "John Roe" in markdown

    def test_render_empty_roster(self):
        markdown = pb._render_key_personnel([])
        assert "No key personnel recorded" in markdown
