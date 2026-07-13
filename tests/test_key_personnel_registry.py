# CUI // SP-CTI
"""proposal_key_personnel storage layer (prem-pstaff-01/02).

The REST tests mock register_person to pin the endpoint's refusal contract; these
exercise the real table, so the upsert, the verdict CHECK, and the evidence CHECK
are proven rather than assumed.
"""
from __future__ import annotations

import json

import pytest

from tools.govcon import key_personnel


@pytest.fixture
def registry(icdev_db, monkeypatch):
    """Point storage at the temp SQLite DB from the shared conftest schema."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    return icdev_db


EVIDENCE = [{"claim": "11 years RMF/ATO packages", "source": "resume p.2"}]


def test_evidenced_mapping_round_trips(registry):
    result = key_personnel.register_person(
        "opp-1", "Dana Whitfield", "Information Security Analyst, Senior",
        EVIDENCE, person_ref="compass:emp-4417",
        qualification_verdict="exceeds", tenant_id="compass")

    assert result["status"] == "ok"
    assert result["evidence_count"] == 1

    people = key_personnel.list_key_personnel("opp-1")
    assert len(people) == 1
    person = people[0]
    assert person["name"] == "Dana Whitfield"
    assert person["proposed_lcat"] == "Information Security Analyst, Senior"
    assert person["qualification_verdict"] == "exceeds"
    # The evidence comes back as citations, not as a lossy blob of prose.
    assert person["evidence"] == [{"claim": "11 years RMF/ATO packages",
                                   "source": "resume p.2"}]


def test_unevidenced_mapping_is_never_written(registry):
    result = key_personnel.register_person(
        "opp-1", "Alex Rivera", "Software Developer, Senior", [])

    assert result["status"] == "refused"
    assert "no qualifying evidence" in result["reason"]
    assert key_personnel.list_key_personnel("opp-1") == []


def test_repushing_a_person_updates_in_place(registry):
    """compass re-runs qualification; the bid must not grow duplicate people."""
    key_personnel.register_person(
        "opp-1", "Dana Whitfield", "Analyst, Mid", EVIDENCE,
        person_ref="compass:emp-4417", qualification_verdict="gap")
    key_personnel.register_person(
        "opp-1", "Dana Whitfield", "Analyst, Senior", EVIDENCE,
        person_ref="compass:emp-4417", qualification_verdict="qualified")

    people = key_personnel.list_key_personnel("opp-1")
    assert len(people) == 1
    assert people[0]["proposed_lcat"] == "Analyst, Senior"
    assert people[0]["qualification_verdict"] == "qualified"


def test_a_caller_without_person_ref_still_converges_on_one_row(registry):
    for lcat in ("Analyst, Mid", "Analyst, Senior"):
        key_personnel.register_person("opp-1", "Dana Whitfield", lcat, EVIDENCE)

    people = key_personnel.list_key_personnel("opp-1")
    assert len(people) == 1
    assert people[0]["proposed_lcat"] == "Analyst, Senior"


def test_bad_verdict_is_rejected_before_the_db(registry):
    result = key_personnel.register_person(
        "opp-1", "Dana Whitfield", "Analyst", EVIDENCE,
        qualification_verdict="vibes")

    assert result["status"] == "error"
    assert key_personnel.list_key_personnel("opp-1") == []


def test_the_db_check_refuses_empty_evidence_even_if_the_guard_is_bypassed(registry):
    """Defense in depth: the table itself will not hold an unevidenced mapping,
    so a future caller that skips register_person cannot create one."""
    import sqlite3

    conn = sqlite3.connect(str(registry))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO proposal_key_personnel "
            "(id, opportunity_id, person_ref, name, proposed_lcat, evidence_json) "
            "VALUES ('pkp-x', 'opp-1', 'ref', 'Ghost', 'Analyst', '[]')")
    conn.close()


def test_evidence_normalization_drops_rows_that_cite_nothing():
    assert key_personnel.normalize_evidence([{"source": "resume.pdf"}]) == []
    assert key_personnel.normalize_evidence("") == []
    assert key_personnel.normalize_evidence(None) == []
    assert key_personnel.normalize_evidence("11 years RMF") == [
        {"claim": "11 years RMF", "source": ""}]
    assert key_personnel.normalize_evidence(
        {"claim": "CISSP", "source": "cert"}) == [
        {"claim": "CISSP", "source": "cert"}]


def test_evidence_is_stored_as_parseable_json(registry):
    key_personnel.register_person("opp-1", "Dana", "Analyst", EVIDENCE)

    import sqlite3
    conn = sqlite3.connect(str(registry))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT evidence_json FROM proposal_key_personnel").fetchone()
    conn.close()

    assert json.loads(row["evidence_json"])[0]["source"] == "resume p.2"
