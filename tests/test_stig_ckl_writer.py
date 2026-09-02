#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the DISA STIG checklist emitter (rmf-oscal-01).

The acceptance criterion is that a written .ckl ROUND-TRIPS through
``tools/network/stig_import.py``'s parser, so every assertion here runs the
emitter's output back through ``parse_ckl`` -- the real parser the repo has
always had -- rather than through a second reader written to match the writer.

A writer tested against its own reader proves only that two functions in the
same file agree. The point of this pair is that the emitter satisfies a parser
it does not own and cannot change.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.compliance.stig_ckl_writer import (  # noqa: E402
    CAT_TO_SEVERITY,
    CKLB_STATUS,
    STATUS_FROM_NORMALIZED,
    build_ckl,
    build_cklb,
    write_stig_checklist,
)
from tools.network.stig_import import (  # noqa: E402
    CKL_STATUS_MAP,
    SEVERITY_TO_CAT,
    detect_format,
    parse_ckl,
)

# One finding per status and per severity, so the round-trip exercises every
# value in both vocabularies rather than the happy one.
FINDINGS = [
    {
        "stig_id": "RHEL-08-010010",
        "finding_id": "V-230221",
        "rule_id": "SV-230221r743913_rule",
        "severity": "CAT1",
        "title": "RHEL 8 must be a vendor-supported release.",
        # Deliberately carries XML metacharacters: an emitter that concatenated
        # strings instead of serialising a tree produces a file its own parser
        # cannot read, and the failure surfaces only on real STIG prose.
        'description': 'A release is <supported> only while "current" & patched.',
        "check_content": "Verify the version with `cat /etc/redhat-release`.",
        "fix_text": "Upgrade to a supported release.",
        "status": "Open",
        "comments": "Scheduled for the Q3 patch window.",
    },
    {
        "stig_id": "RHEL-08-010020",
        "finding_id": "V-230222",
        "rule_id": "SV-230222r743916_rule",
        "severity": "CAT2",
        "title": "FIPS mode must be enabled.",
        "description": "FIPS 140-3 validated modules are required.",
        "check_content": "Check fips_enabled.",
        "fix_text": "Enable fips-mode-setup.",
        "status": "NotAFinding",
        "comments": "",
    },
    {
        "stig_id": "RHEL-08-010030",
        "finding_id": "V-230223",
        "rule_id": "SV-230223r743919_rule",
        "severity": "CAT3",
        "title": "A Standard Mandatory DoD Notice banner must be displayed.",
        "description": "Users must acknowledge the banner.",
        "check_content": "Inspect /etc/issue.",
        "fix_text": "Install the banner text.",
        "status": "Not_Applicable",
        "comments": "Headless appliance, no interactive logon.",
    },
    {
        "stig_id": "RHEL-08-010040",
        "finding_id": "V-230224",
        "rule_id": "SV-230224r743922_rule",
        "severity": "CAT2",
        "title": "Audit records must be offloaded.",
        "description": "Offload to a central log host.",
        "check_content": "Inspect rsyslog.conf.",
        "fix_text": "Configure a remote target.",
        "status": "Not_Reviewed",
        "comments": "",
    },
]

ASSET = {
    "host_name": "web01",
    "ip": "10.0.0.5",
    "mac": "00:11:22:33:44:55",
    "fqdn": "web01.example.mil",
}
STIG_INFO = {"title": "Red Hat Enterprise Linux 8 STIG", "version": "1"}


@pytest.fixture
def parsed():
    """The findings, written to .ckl and read back by the REAL parser."""
    xml = build_ckl(FINDINGS, asset=ASSET, stig_info=STIG_INFO)
    return xml, parse_ckl(xml)


# --- the acceptance criterion ----------------------------------------------


def test_written_ckl_is_detected_as_ckl(parsed):
    xml, _ = parsed
    assert detect_format(xml) == "ckl"


def test_every_finding_field_survives_the_round_trip(parsed):
    """Each field the parser extracts comes back exactly as it went in."""
    _, result = parsed
    host = result["hosts"]["web01"]
    assert len(host["findings"]) == len(FINDINGS)

    for original, returned in zip(FINDINGS, host["findings"]):
        assert returned["vuln_id"] == original["finding_id"]
        assert returned["rule_id"] == original["rule_id"]
        assert returned["stig_id"] == original["stig_id"]
        assert returned["title"] == original["title"]
        assert returned["comments"] == original["comments"]
        # The parser normalises these two; the emitter must produce the wire
        # values that normalise back to what was recorded.
        assert returned["severity"] == original["severity"]
        assert returned["status"] == CKL_STATUS_MAP[original["status"]]


def test_asset_and_stig_header_survive_the_round_trip(parsed):
    _, result = parsed
    assert result["stig_name"] == STIG_INFO["title"]
    assert result["stig_version"] == STIG_INFO["version"]
    host = result["hosts"]["web01"]
    assert host["ip"] == ASSET["ip"]
    assert host["mac"] == ASSET["mac"]
    assert host["fqdn"] == ASSET["fqdn"]


def test_the_parsers_own_summary_counts_match_what_was_written(parsed):
    """Four statuses in, one of each out."""
    _, result = parsed
    assert result["hosts"]["web01"]["summary"] == {"pass": 1, "fail": 1, "na": 1, "nr": 1}


def test_xml_metacharacters_in_stig_prose_survive_the_round_trip(parsed):
    """`<`, `&` and `"` in real STIG text must not corrupt the document."""
    _, result = parsed
    xml, _ = parsed
    # The raw document must have escaped them...
    assert "<supported>" not in xml
    assert "&lt;supported&gt;" in xml
    # ...and the parser must give the original text back. Vuln_Discuss is not
    # a field parse_ckl extracts, so assert on the serialised form directly.
    assert "&amp;" in xml


def test_an_empty_finding_set_still_writes_a_parseable_checklist():
    """A system with nothing recorded gets a zero-finding checklist.

    Emitting nothing at all would leave a reader unable to tell "no findings"
    from "the emitter did not run".
    """
    xml = build_ckl([], asset=ASSET, stig_info=STIG_INFO)
    result = parse_ckl(xml)
    assert result["hosts"]["web01"]["findings"] == []
    assert result["hosts"]["web01"]["summary"] == {"pass": 0, "fail": 0, "na": 0, "nr": 0}


def test_a_checklist_with_no_asset_parses_under_the_parsers_own_fallback():
    """No hostname is the parser's 'unknown_host', not a crash."""
    result = parse_ckl(build_ckl(FINDINGS))
    assert "unknown_host" in result["hosts"]
    assert len(result["hosts"]["unknown_host"]["findings"]) == len(FINDINGS)


# --- the vocabularies are inverted from the parser, never restated ----------


def test_severity_table_is_the_exact_inverse_of_the_parsers():
    """A second hand-written copy would drift and downgrade silently."""
    assert CAT_TO_SEVERITY == {cat: word for word, cat in SEVERITY_TO_CAT.items()}
    for word, cat in SEVERITY_TO_CAT.items():
        assert CAT_TO_SEVERITY[cat] == word


def test_status_table_is_the_exact_inverse_of_the_parsers():
    assert STATUS_FROM_NORMALIZED == {norm: raw for raw, norm in CKL_STATUS_MAP.items()}


def test_cklb_status_map_covers_every_ckl_status_the_parser_knows():
    """A CKL status with no .cklb spelling would raise a KeyError at write time."""
    assert set(CKLB_STATUS) == set(CKL_STATUS_MAP)


def test_the_writer_does_not_restate_the_parsers_tables():
    """The inverses must be COMPUTED, not typed out beside the originals.

    Asserting equality above would still pass against a hand-written duplicate;
    this reads the source and requires the derivation.
    """
    source = (_REPO_ROOT / "tools" / "compliance" / "stig_ckl_writer.py").read_text(encoding="utf-8")
    assert "for sev, cat in SEVERITY_TO_CAT.items()" in source
    assert "for raw, norm in CKL_STATUS_MAP.items()" in source


# --- degraded inputs --------------------------------------------------------


def test_an_unknown_status_is_written_not_reviewed_never_notafinding():
    """The one wrong guess here silently clears a finding."""
    xml = build_ckl([dict(FINDINGS[0], status="Mitigated")], asset=ASSET)
    finding = parse_ckl(xml)["hosts"]["web01"]["findings"][0]
    assert finding["status"] == "nr"


def test_a_normalized_status_is_accepted_as_input():
    """A caller holding parser output ('fail') can hand it straight back."""
    xml = build_ckl([dict(FINDINGS[0], status="fail")], asset=ASSET)
    assert parse_ckl(xml)["hosts"]["web01"]["findings"][0]["status"] == "fail"


def test_a_disa_severity_word_is_accepted_as_input():
    xml = build_ckl([dict(FINDINGS[0], severity="low")], asset=ASSET)
    assert parse_ckl(xml)["hosts"]["web01"]["findings"][0]["severity"] == "CAT3"


def test_an_unknown_severity_degrades_the_same_way_the_parser_does():
    """Emitter and parser must agree on the degraded case, not disagree."""
    xml = build_ckl([dict(FINDINGS[0], severity="catastrophic")], asset=ASSET)
    assert parse_ckl(xml)["hosts"]["web01"]["findings"][0]["severity"] == "CAT2"


def test_finding_details_is_empty_because_no_column_feeds_it():
    """Borrowing description/comments would read as evidence of review."""
    result = parse_ckl(build_ckl(FINDINGS, asset=ASSET))
    assert all(f["finding_details"] == "" for f in result["hosts"]["web01"]["findings"])


# --- .cklb (STIG Viewer 3) --------------------------------------------------


def test_cklb_is_json_serialisable_and_carries_every_rule():
    doc = build_cklb(FINDINGS, asset=ASSET, stig_info=STIG_INFO)
    json.dumps(doc)  # must not raise
    stig = doc["stigs"][0]
    assert stig["size"] == len(FINDINGS) == len(stig["rules"])
    assert doc["cklb_version"] == "1.0"
    assert doc["target_data"]["host_name"] == ASSET["host_name"]
    assert doc["target_data"]["ip_address"] == ASSET["ip"]


def test_cklb_statuses_are_the_v3_spelling_of_the_ckl_statuses():
    doc = build_cklb(FINDINGS, asset=ASSET, stig_info=STIG_INFO)
    statuses = [r["status"] for r in doc["stigs"][0]["rules"]]
    assert statuses == [CKLB_STATUS[f["status"]] for f in FINDINGS]


def test_ckl_and_cklb_cannot_disagree_about_a_severity_or_a_status():
    """Both emitters normalise through the same two functions.

    A .ckl and a .cklb written from one assessment that disagreed would be two
    checklists for one system, and only one of them right.
    """
    parsed_ckl = parse_ckl(build_ckl(FINDINGS, asset=ASSET, stig_info=STIG_INFO))
    cklb = build_cklb(FINDINGS, asset=ASSET, stig_info=STIG_INFO)

    for from_ckl, from_cklb in zip(parsed_ckl["hosts"]["web01"]["findings"], cklb["stigs"][0]["rules"]):
        assert CAT_TO_SEVERITY[from_ckl["severity"]] == from_cklb["severity"]
        assert CKLB_STATUS[STATUS_FROM_NORMALIZED[from_ckl["status"]]] == from_cklb["status"]


# --- the database-backed entry point ---------------------------------------


@pytest.fixture
def stig_db(tmp_path):
    db = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE stig_findings (
             id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
             stig_id TEXT NOT NULL, finding_id TEXT NOT NULL, rule_id TEXT NOT NULL,
             severity TEXT NOT NULL CHECK(severity IN ('CAT1','CAT2','CAT3')),
             title TEXT NOT NULL, description TEXT, check_content TEXT, fix_text TEXT,
             status TEXT DEFAULT 'Open'
               CHECK(status IN ('Open','NotAFinding','Not_Applicable','Not_Reviewed')),
             comments TEXT, target_type TEXT, assessed_by TEXT, assessed_at TIMESTAMP,
             created_at TIMESTAMP, updated_at TIMESTAMP)"""
    )
    for finding in FINDINGS:
        conn.execute(
            """INSERT INTO stig_findings
               (project_id, stig_id, finding_id, rule_id, severity, title,
                description, check_content, fix_text, status, comments)
               VALUES ('p1',?,?,?,?,?,?,?,?,?,?)""",
            (
                finding["stig_id"],
                finding["finding_id"],
                finding["rule_id"],
                finding["severity"],
                finding["title"],
                finding["description"],
                finding["check_content"],
                finding["fix_text"],
                finding["status"],
                finding["comments"],
            ),
        )
    conn.commit()
    conn.close()
    return db


def test_write_stig_checklist_emits_both_dialects_from_the_database(tmp_path, stig_db):
    result = write_stig_checklist(
        "p1", output_dir=str(tmp_path / "out"), db_path=str(stig_db), asset=ASSET
    )

    assert result["findings_count"] == len(FINDINGS)
    assert result["summary"] == {"Open": 1, "NotAFinding": 1, "Not_Applicable": 1, "Not_Reviewed": 1}
    assert set(result["files"]) == {"ckl", "cklb"}

    written = Path(result["files"]["ckl"]).read_text(encoding="utf-8")
    parsed_back = parse_ckl(written)
    returned = parsed_back["hosts"]["web01"]["findings"]
    assert {f["vuln_id"] for f in returned} == {f["finding_id"] for f in FINDINGS}

    cklb = json.loads(Path(result["files"]["cklb"]).read_text(encoding="utf-8"))
    assert cklb["stigs"][0]["size"] == len(FINDINGS)


def test_write_stig_checklist_honours_a_single_format(tmp_path, stig_db):
    result = write_stig_checklist("p1", output_dir=str(tmp_path / "o"), db_path=str(stig_db), fmt="ckl")
    assert set(result["files"]) == {"ckl"}


def test_write_stig_checklist_refuses_an_unknown_format(tmp_path, stig_db):
    """A bad format must raise, never silently write the default pair."""
    with pytest.raises(ValueError, match="Unsupported format"):
        write_stig_checklist("p1", output_dir=str(tmp_path / "o"), db_path=str(stig_db), fmt="xccdf")


def test_a_project_with_no_findings_writes_an_empty_but_valid_checklist(tmp_path, stig_db):
    result = write_stig_checklist(
        "no-such-project", output_dir=str(tmp_path / "o"), db_path=str(stig_db), asset=ASSET
    )
    assert result["findings_count"] == 0
    parsed_back = parse_ckl(Path(result["files"]["ckl"]).read_text(encoding="utf-8"))
    assert parsed_back["hosts"]["web01"]["findings"] == []


def test_a_missing_database_is_refused_not_reported_as_zero_findings(tmp_path):
    """An unreadable database must never read as a clean checklist."""
    with pytest.raises(FileNotFoundError):
        write_stig_checklist("p1", output_dir=str(tmp_path / "o"), db_path=str(tmp_path / "nope.db"))
