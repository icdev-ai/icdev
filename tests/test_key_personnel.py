#!/usr/bin/env python3
# CUI // SP-CTI
"""prem-pstaff-01 — the bid side gets a real person -> LCAT registry.

Before this, ``program_bridge._gather_key_personnel`` regex-scraped capitalised
bigrams out of proposal prose and fed them to the "Key Personnel & Staffing Plan"
section of a real bid. The pattern matches "Program Manager" and "Technical Approach"
as readily as it matches a person, and the result carried no LCAT, no qualification
verdict, and no evidence.

The rule this file exists to pin: **an unevidenced person -> LCAT mapping is REFUSED,
not stored empty.** A person proposed for a labour category with nothing behind the
claim reaches the customer as an assertion nobody can defend at debrief. It is the same
defect class as an uncited win theme.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.govcon.key_personnel import (  # noqa: E402
    PERSON_SOURCES,
    QUALIFICATION_VERDICTS,
    list_key_personnel,
    normalize_evidence,
    register_person,
    table_ddl,
)

EVIDENCE = [{"claim": "12 yrs systems engineering on DoD C2 programs", "source": "resume p2"}]


@pytest.fixture()
def conn(icdev_db):
    from tools.db.storage import get_connection

    c = get_connection(db_path=str(icdev_db))
    yield c
    try:
        c.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Evidence is mandatory — the whole point
# ---------------------------------------------------------------------------


def test_an_evidenced_person_is_registered(conn):
    r = register_person(
        opportunity_id="opp-1", person_ref="p-1", name="Dana Reeves",
        proposed_lcat="Senior Systems Engineer", qualification_verdict="qualified",
        evidence=EVIDENCE, conn=conn,
    )
    assert r["status"] == "registered"
    assert r["evidence_count"] == 1

    people = list_key_personnel("opp-1", conn=conn)
    assert len(people) == 1
    assert people[0]["name"] == "Dana Reeves"
    assert people[0]["evidence"][0]["claim"].startswith("12 yrs")


@pytest.mark.parametrize("empty", [None, "", "   ", [], {}, [{"source": "resume"}]])
def test_an_unevidenced_person_is_REFUSED_and_stores_nothing(conn, empty):
    """Every shape of 'no evidence', including a citation row with no claim."""
    r = register_person(
        opportunity_id="opp-1", person_ref="p-2", name="Sam Vance",
        proposed_lcat="Program Manager", qualification_verdict="qualified",
        evidence=empty, conn=conn,
    )
    assert r["status"] == "refused"
    assert "NO evidence" in r["reason"]
    assert list_key_personnel("opp-1", conn=conn) == []


def test_a_refusal_does_not_block_the_evidenced_people(conn):
    """A compass push of 30 people must not be lost because one has a thin resume."""
    register_person(opportunity_id="opp-1", person_ref="p-good", name="Dana Reeves",
                    proposed_lcat="SSE", qualification_verdict="qualified",
                    evidence=EVIDENCE, conn=conn)
    register_person(opportunity_id="opp-1", person_ref="p-bad", name="Sam Vance",
                    proposed_lcat="PM", qualification_verdict="qualified",
                    evidence=None, conn=conn)

    people = list_key_personnel("opp-1", conn=conn)
    assert [p["person_ref"] for p in people] == ["p-good"]


def test_the_DB_refuses_an_unevidenced_row_even_if_the_code_is_bypassed(conn):
    """Belt and braces: register_person() refuses AND a CHECK constraint refuses.

    A future writer that INSERTs directly must not be able to sneak an unevidenced
    mapping past the rule. The constraint cannot be forgotten.
    """
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO proposal_key_personnel (id, opportunity_id, person_ref, name, "
            "proposed_lcat, qualification_verdict, evidence_json, tenant_id, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            ("x", "opp-1", "p-x", "Nobody", "PM", "qualified", "[]", "default", "CUI"),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Verdicts + sources are constrained
# ---------------------------------------------------------------------------


def test_a_verdict_compass_does_not_produce_is_refused(conn):
    r = register_person(
        opportunity_id="opp-1", person_ref="p-3", name="Dana Reeves",
        proposed_lcat="SSE", qualification_verdict="probably_fine",
        evidence=EVIDENCE, conn=conn,
    )
    assert r["status"] == "refused"
    assert "qualification_verdict" in r["reason"]


@pytest.mark.parametrize("verdict", QUALIFICATION_VERDICTS)
def test_every_compass_verdict_is_accepted(conn, verdict):
    r = register_person(
        opportunity_id="opp-1", person_ref=f"p-{verdict}", name="Dana Reeves",
        proposed_lcat="SSE", qualification_verdict=verdict, evidence=EVIDENCE, conn=conn,
    )
    assert r["status"] == "registered"


def test_reregistering_a_person_updates_rather_than_duplicates(conn):
    for verdict in ("gap", "qualified"):
        register_person(opportunity_id="opp-1", person_ref="p-1", name="Dana Reeves",
                        proposed_lcat="SSE", qualification_verdict=verdict,
                        evidence=EVIDENCE, conn=conn)
    people = list_key_personnel("opp-1", conn=conn)
    assert len(people) == 1
    assert people[0]["qualification_verdict"] == "qualified"


# ---------------------------------------------------------------------------
# normalize_evidence — the two shapes a capture tool actually has
# ---------------------------------------------------------------------------


def test_rendered_text_evidence_is_accepted():
    assert normalize_evidence("12 yrs on DoD C2") == [{"claim": "12 yrs on DoD C2", "source": ""}]


def test_a_citation_row_with_no_claim_cites_nothing():
    assert normalize_evidence([{"source": "resume p2"}]) == []


# ---------------------------------------------------------------------------
# Anti-drift: the Python constants and the SQL that restates them must agree
# ---------------------------------------------------------------------------


def _checked_values(sql: str, column: str) -> set:
    """Pull the ``<column> IN (...)`` list out of a chunk of DDL.

    Matches the bare form and the ``IS NULL OR <column> IN (...)`` form that the
    nullable `source` column uses.
    """
    m = re.search(rf"{column}\s+IN\s*\(([^)]*)\)", sql, re.I | re.S)
    assert m, f"no `{column} IN (...)` found in DDL chunk"
    return {v.strip().strip("'") for v in m.group(1).split(",")}


def test_the_generated_ddl_derives_its_CHECKs_from_the_python_constants():
    ddl = table_ddl()
    assert _checked_values(ddl, "qualification_verdict") == set(QUALIFICATION_VERDICTS)


def test_the_sqlite_bootstrap_ddl_agrees_with_the_python_constants():
    """init_icdev_db.py restates the CHECK in literal SQL (it is the bootstrap file).

    Restating a rule is allowed; letting the two copies DRIFT is not. This is the guard
    — change QUALIFICATION_VERDICTS without mirroring it here and the suite goes red.
    """
    text = (ROOT / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8", errors="replace")
    start = text.index("CREATE TABLE IF NOT EXISTS proposal_key_personnel")
    chunk = text[start:start + 1400]
    assert _checked_values(chunk, "qualification_verdict") == set(QUALIFICATION_VERDICTS)
    assert _checked_values(chunk, "source") == set(PERSON_SOURCES)


def test_a_fresh_postgres_bootstrap_would_have_the_table():
    """bootstrap_pg marks migrations as already-applied on a fresh DB, so migration 266
    alone is NOT enough — the table has to be in the consolidated schema too, or a fresh
    PostgreSQL install silently lacks it."""
    text = (ROOT / "tools" / "db" / "schema" / "pg_consolidated.sql").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "CREATE TABLE IF NOT EXISTS public.proposal_key_personnel" in text
    assert "proposal_key_personnel_evidence_check" in text


# ---------------------------------------------------------------------------
# The gap this closes
# ---------------------------------------------------------------------------


def test_program_bridge_prefers_the_registry_over_the_regex_scrape(conn, monkeypatch):
    """The payoff. With registered people, the Key Personnel section is built from
    evidenced person->LCAT rows, not from a regex over prose."""
    from tools.govcon import program_bridge

    register_person(opportunity_id="opp-1", person_ref="p-1", name="Dana Reeves",
                    proposed_lcat="Senior Systems Engineer",
                    qualification_verdict="qualified", evidence=EVIDENCE, conn=conn)

    out = program_bridge._gather_key_personnel(conn, "opp-1")
    assert out["source"] == "proposal_key_personnel"
    assert out["record_count"] == 1
    person = out["data"][0]
    assert person["name"] == "Dana Reeves"
    assert person["proposed_lcat"] == "Senior Systems Engineer"
    assert person["qualification_verdict"] == "qualified"
    assert person["evidence"]  # <- the thing the regex could never supply


def test_scraped_names_are_marked_as_scraped_not_passed_off_as_evidenced(conn):
    """The regex fallback still runs for pre-registry opportunities — but it must SAY
    so. Silently mixing guessed names in with evidenced ones would be worse than either
    alone: a reader could not tell which names are defensible."""
    from tools.govcon import program_bridge

    conn.execute(
        "INSERT INTO proposal_section_drafts (id, opportunity_id, draft_content) "
        "VALUES (%s, %s, %s)",
        ("d-1", "opp-legacy", "Our lead is Dana Reeves, supported by Program Manager oversight."),
    )
    conn.commit()

    out = program_bridge._gather_key_personnel(conn, "opp-legacy")
    assert out["source"] == "regex_scrape_legacy"
    for person in out["data"]:
        assert person["source"] == "scraped"
        assert person["evidence"] == []
        assert person["qualification_verdict"] == ""


# ---------------------------------------------------------------------------
# Gaps travel WITH the verdict
# ---------------------------------------------------------------------------


def test_a_gap_verdict_carries_its_gaps(conn):
    """A person with a gap can still be the right person to bid — but the bid side has
    to SEE the gap when they make that call and price the risk, not discover it at the
    debrief. A verdict of "gap" with the gaps thrown away is barely better than no
    verdict at all."""
    gaps = [{"kind": "clearance", "item": "TS/SCI required, holds Secret"},
            {"kind": "experience", "item": "8 yrs required, has 6"}]
    register_person(
        opportunity_id="opp-1", person_ref="p-gap", name="Ada Kwan",
        proposed_lcat="Senior Systems Engineer", qualification_verdict="gap",
        evidence=EVIDENCE, gaps=gaps, key_person=True, conn=conn,
    )
    person = list_key_personnel("opp-1", conn=conn)[0]
    assert person["qualification_verdict"] == "gap"
    assert person["key_person"] is True
    assert [g["kind"] for g in person["gaps"]] == ["clearance", "experience"]


def test_gaps_default_to_empty_and_key_person_to_false(conn):
    register_person(
        opportunity_id="opp-1", person_ref="p-1", name="Dana Reeves",
        proposed_lcat="SSE", qualification_verdict="qualified",
        evidence=EVIDENCE, conn=conn,
    )
    person = list_key_personnel("opp-1", conn=conn)[0]
    assert person["gaps"] == []
    assert person["key_person"] is False
