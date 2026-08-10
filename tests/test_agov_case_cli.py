# CUI // SP-CTI
"""AGOV CASE — timeline, bundler and the CLI that fronts them (agov-case-04).

The load-bearing test here is the ROUND TRIP: a bundle written by
``case_bundler`` must verify clean under ``bundle_verifier``. Writer and
verifier are separate modules with separate digest code paths, so a test that
only checks "the bundler wrote some files" would pass while the two disagreed
about member paths, JSON canonicalization or line endings — which is exactly the
drift that makes a forensic bundle worthless on the machine it is carried to.

The database here is a hand-built three-table SQLite file rather than the
``icdev_db`` fixture: that fixture costs 30-50s per test and its
MINIMAL_ICDEV_SCHEMA carries no ``hook_events``. Connections go through
``tests._sql_compat.translating`` so the ``%s`` placeholders this repo authors
for PostgreSQL are rewritten exactly as ``StorageConnection`` rewrites them —
a bare ``sqlite3.connect`` would make every statement raise inside the callers'
error handling and the tests would assert against their own no-op.
"""

from __future__ import annotations

import importlib
import json
import sqlite3

import pytest

from tests._sql_compat import translating

bundle_format = importlib.import_module("tools.agent_case.bundle_format")
bundle_verifier = importlib.import_module("tools.agent_case.bundle_verifier")
case_bundler = importlib.import_module("tools.agent_case.case_bundler")
cli = importlib.import_module("tools.agent_case.cli")
session_timeline = importlib.import_module("tools.agent_case.session_timeline")

SESSION = "sess-agov-case-04"
OTHER_SESSION = "sess-someone-else"
SECRET = "test-hmac-secret-not-the-shipped-default"

SCHEMA = """
CREATE TABLE hook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    hook_type TEXT NOT NULL,
    tool_name TEXT,
    project_id TEXT,
    payload TEXT,
    classification TEXT DEFAULT 'CUI',
    signature TEXT,
    created_at TIMESTAMP
);
CREATE TABLE audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    ip_address TEXT,
    session_id TEXT,
    recorded_at TEXT,
    created_at TIMESTAMP,
    hash TEXT,
    previous_hash TEXT
);
"""

# agent_findings is created only by the tests that want it: on main the table
# does not exist yet (agov-det-05 adds it), and "absent" is the case the
# timeline has to handle without failing.
FINDINGS_SCHEMA = """
CREATE TABLE agent_findings (
    finding_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    session_id TEXT,
    actor TEXT,
    project_id TEXT,
    event_ids TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    enforced INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL DEFAULT 'observed',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT
);
"""


def _hook_row(raw, session_id, hook_type, tool_name, created_at, secret=SECRET,
              payload=None, sign=True):
    payload = payload if payload is not None else json.dumps(
        {"tool": tool_name, "session": session_id})
    signature = bundle_format.compute_event_hmac(payload, secret) if sign else None
    raw.execute(
        "INSERT INTO hook_events (session_id, hook_type, tool_name, project_id, "
        "payload, classification, signature, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (session_id, hook_type, tool_name, "proj-1", payload, "CUI", signature,
         created_at),
    )


def _audit_row(raw, session_id, action, created_at, chain=True):
    """Insert an audit row and populate the migration-149 chain columns properly."""
    raw.execute(
        "INSERT INTO audit_trail (project_id, event_type, actor, action, details, "
        "classification, ip_address, session_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("proj-1", "code_change", "agent", action, f"detail for {action}", "CUI",
         "10.0.0.1", session_id, created_at),
    )
    row_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
    if not chain:
        return row_id
    row = dict(zip(
        bundle_format.AUDIT_HASH_FIELDS,
        raw.execute(
            "SELECT id, project_id, event_type, actor, action, details, "
            "classification, ip_address, session_id FROM audit_trail WHERE id = ?",
            (row_id,),
        ).fetchone(),
    ))
    row_hash = bundle_format.compute_audit_row_hash(row)
    if row_id == 1:
        previous = bundle_format.GENESIS_HASH
    else:
        previous = raw.execute("SELECT hash FROM audit_trail WHERE id = ?",
                               (row_id - 1,)).fetchone()[0]
    raw.execute("UPDATE audit_trail SET hash = ?, previous_hash = ? WHERE id = ?",
                (row_hash, previous, row_id))
    return row_id


@pytest.fixture
def db(tmp_path):
    """A three-table SQLite database seeded with two sessions' worth of records.

    Row ids 1-2 belong to OTHER_SESSION on purpose: SESSION's audit slice then
    starts mid-chain, which is the only way to exercise ``chain_context``.
    """
    path = tmp_path / "case.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(SCHEMA)

    _audit_row(raw, OTHER_SESSION, "unrelated-a", "2026-08-09T10:00:00Z")
    _audit_row(raw, OTHER_SESSION, "unrelated-b", "2026-08-09T10:00:01Z")
    _audit_row(raw, SESSION, "wrote file", "2026-08-09T12:00:02Z")
    _audit_row(raw, SESSION, "ran tests", "2026-08-09T12:00:04Z")

    _hook_row(raw, SESSION, "pre_tool_use", "Read", "2026-08-09T12:00:01Z")
    _hook_row(raw, SESSION, "post_tool_use", "Read", "2026-08-09T12:00:03Z")
    _hook_row(raw, OTHER_SESSION, "pre_tool_use", "Bash", "2026-08-09T10:00:00Z")
    raw.commit()
    raw.close()
    return path


@pytest.fixture
def conn(db):
    connection = translating(sqlite3.connect(str(db)))
    yield connection
    connection.close()


@pytest.fixture
def patched_conn(db, monkeypatch):
    """Point every module's ``get_connection`` at the test database.

    Patched per module by attribute, not by patching ``tools.db.storage``: each
    module did ``from tools.db.storage import get_connection``, so it holds its
    own reference and patching the source module would not reach it.
    """
    def _factory(*_args, **_kwargs):
        return translating(sqlite3.connect(str(db)))

    for module in (session_timeline, case_bundler):
        monkeypatch.setattr(module, "get_connection", _factory)
    return db


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------

def test_timeline_joins_only_the_requested_session(conn):
    result = session_timeline.build_timeline(SESSION, conn=conn)

    assert result["counts"]["entries"] == 4
    for entry in result["entries"]:
        record = entry["record"]
        assert record.get("session_id") == SESSION


def test_timeline_is_ordered_by_timestamp_across_sources(conn):
    result = session_timeline.build_timeline(SESSION, conn=conn)

    assert [(e["source"], e["at"]) for e in result["entries"]] == [
        ("hook_events", "2026-08-09T12:00:01Z"),
        ("audit_trail", "2026-08-09T12:00:02Z"),
        ("hook_events", "2026-08-09T12:00:03Z"),
        ("audit_trail", "2026-08-09T12:00:04Z"),
    ]
    assert [e["seq"] for e in result["entries"]] == [1, 2, 3, 4]


def test_timeline_names_the_tables_it_cannot_join(conn):
    result = session_timeline.build_timeline(SESSION, conn=conn)

    limits = " ".join(result["limits"])
    for table in ("agent_executions", "ai_telemetry", "ace_audit_log"):
        assert table in limits, f"{table} must be named as unjoinable, not omitted"


def test_timeline_reports_a_missing_optional_table_rather_than_failing(conn):
    result = session_timeline.build_timeline(SESSION, conn=conn)

    findings = result["sources"]["agent_findings"]
    assert findings["present"] is False
    assert findings["records"] == 0


def test_timeline_includes_agent_findings_once_the_table_exists(db, conn):
    raw = sqlite3.connect(str(db))
    raw.executescript(FINDINGS_SCHEMA)
    raw.execute(
        "INSERT INTO agent_findings (finding_id, rule_id, rule_version, severity, "
        "title, session_id, actor, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("f-1", "secrets.env_file_read", "1", "high", "read .env", SESSION,
         "agent", "2026-08-09T12:00:05Z"),
    )
    raw.commit()
    raw.close()

    result = session_timeline.build_timeline(SESSION, conn=conn)

    assert result["sources"]["agent_findings"]["records"] == 1
    assert result["entries"][-1]["source"] == "agent_findings"
    assert result["entries"][-1]["summary"] == "read .env"


def test_timeline_sorts_undated_records_last_and_counts_them(db, conn):
    raw = sqlite3.connect(str(db))
    _hook_row(raw, SESSION, "notification", "NoTimestamp", None)
    raw.commit()
    raw.close()

    result = session_timeline.build_timeline(SESSION, conn=conn)

    assert result["undated"] == 1
    assert result["entries"][-1]["at"] is None
    assert result["entries"][-1]["summary"] == "NoTimestamp"
    assert any("no timestamp" in limit for limit in result["limits"])


def test_timeline_window_filters_are_applied_in_sql(conn):
    result = session_timeline.build_timeline(
        SESSION, conn=conn, since="2026-08-09T12:00:03Z")

    assert [e["at"] for e in result["entries"]] == [
        "2026-08-09T12:00:03Z", "2026-08-09T12:00:04Z"]


def test_timeline_limit_marks_the_result_truncated(conn):
    result = session_timeline.build_timeline(SESSION, conn=conn, limit=1)

    assert result["sources"]["hook_events"]["truncated"] is True
    assert any("--limit 1" in limit for limit in result["limits"])


def test_timeline_for_an_unknown_session_is_empty_not_an_error(conn):
    result = session_timeline.build_timeline("sess-never-existed", conn=conn)

    assert result["counts"]["entries"] == 0
    assert result["limits"], "an empty timeline must still state its limits"


def test_timeline_requires_a_session_id(conn):
    with pytest.raises(ValueError):
        session_timeline.build_timeline("", conn=conn)


@pytest.mark.parametrize("row_factory", [None, sqlite3.Row])
def test_timeline_handles_tuple_and_dict_row_factories(db, row_factory):
    raw = sqlite3.connect(str(db))
    raw.row_factory = row_factory
    connection = translating(raw)
    try:
        result = session_timeline.build_timeline(SESSION, conn=connection)
    finally:
        connection.close()

    assert result["counts"]["entries"] == 4
    assert result["entries"][0]["record"]["tool_name"] == "Read"


# ---------------------------------------------------------------------------
# bundler — and the round trip through the verifier
# ---------------------------------------------------------------------------

def test_bundle_writes_every_member_and_a_manifest(conn, tmp_path):
    out = tmp_path / "bundle"
    result = case_bundler.build_case_bundle(SESSION, out, conn=conn)

    for member in (bundle_format.HOOK_EVENTS_MEMBER,
                   bundle_format.AUDIT_TRAIL_MEMBER,
                   case_bundler.TIMELINE_MEMBER,
                   bundle_format.MANIFEST_NAME):
        assert (out / member).is_file(), f"{member} missing from the bundle"
    assert result["bundle_id"].startswith("case-")
    assert result["counts"]["entries"] == 4


def test_freshly_built_bundle_verifies_clean(conn, tmp_path, monkeypatch):
    """The round trip. Writer and verifier must agree on every digest."""
    monkeypatch.setenv(bundle_format.HMAC_SECRET_ENV, SECRET)
    out = tmp_path / "bundle"
    case_bundler.build_case_bundle(SESSION, out, conn=conn)

    report = bundle_verifier.verify_bundle(out)

    assert report["layers"]["manifest"]["status"] == "PASSED", report["findings"]
    assert report["layers"]["hmac"]["status"] == "PASSED", report["findings"]
    assert report["layers"]["chain"]["status"] == "PASSED", report["findings"]
    assert report["exit_code"] == bundle_verifier.EXIT_OK


def test_bundle_anchors_the_predecessor_outside_the_slice(conn, tmp_path):
    """SESSION's audit rows are 3-4; row 2's hash must travel as an anchor."""
    out = tmp_path / "bundle"
    case_bundler.build_case_bundle(SESSION, out, conn=conn)

    audit = json.loads(
        (out / bundle_format.AUDIT_TRAIL_MEMBER).read_text(encoding="utf-8"))

    assert set(audit["chain_context"]) == {"2"}
    assert len(audit["chain_context"]["2"]) == 64


def test_tampering_with_a_member_is_named_by_path(conn, tmp_path, monkeypatch):
    monkeypatch.setenv(bundle_format.HMAC_SECRET_ENV, SECRET)
    out = tmp_path / "bundle"
    case_bundler.build_case_bundle(SESSION, out, conn=conn)
    target = out / case_bundler.TIMELINE_MEMBER
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")

    report = bundle_verifier.verify_bundle(out, layers=["manifest"])

    assert report["layers"]["manifest"]["status"] == "FAILED"
    assert any(f.get("member") == case_bundler.TIMELINE_MEMBER
               for f in report["findings"])


def test_member_files_use_lf_so_digests_match_across_platforms(conn, tmp_path):
    out = tmp_path / "bundle"
    case_bundler.build_case_bundle(SESSION, out, conn=conn)

    for member in (bundle_format.MANIFEST_NAME, case_bundler.TIMELINE_MEMBER,
                   bundle_format.HOOK_EVENTS_MEMBER):
        assert b"\r\n" not in (out / member).read_bytes(), f"{member} has CRLF"


def test_bundle_carries_the_raw_payload_the_hmac_was_computed_over(db, conn, tmp_path,
                                                                  monkeypatch):
    """Re-serializing the parsed payload would change spacing and break every HMAC."""
    monkeypatch.setenv(bundle_format.HMAC_SECRET_ENV, SECRET)
    payload = '{"tool":"Read",  "spaced":true}'
    raw = sqlite3.connect(str(db))
    _hook_row(raw, SESSION, "pre_tool_use", "Read", "2026-08-09T12:00:09Z",
              payload=payload)
    raw.commit()
    raw.close()
    out = tmp_path / "bundle"

    case_bundler.build_case_bundle(SESSION, out, conn=conn)
    events = json.loads(
        (out / bundle_format.HOOK_EVENTS_MEMBER).read_text(encoding="utf-8"))

    assert payload in [record["payload"] for record in events["records"]]
    # The stored bytes still verify, which is the property that matters.
    assert bundle_verifier.verify_bundle(out, layers=["hmac"])["layers"]["hmac"][
        "status"] == "PASSED"


def test_bundle_refuses_to_overwrite_without_force(conn, tmp_path):
    out = tmp_path / "bundle"
    case_bundler.build_case_bundle(SESSION, out, conn=conn)

    with pytest.raises(FileExistsError):
        case_bundler.build_case_bundle(SESSION, out, conn=conn)

    case_bundler.build_case_bundle(SESSION, out, conn=conn, overwrite=True)


def test_bundle_writes_an_empty_member_rather_than_omitting_the_source(conn, tmp_path):
    """An absent member means "not exported", not "the session had no events"."""
    out = tmp_path / "bundle"
    case_bundler.build_case_bundle("sess-never-existed", out, conn=conn)

    events = json.loads(
        (out / bundle_format.HOOK_EVENTS_MEMBER).read_text(encoding="utf-8"))
    assert events["records"] == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_timeline_json_is_parseable_and_flags_ok(patched_conn, capsys):
    code = cli.main(["timeline", "--session", SESSION, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["counts"]["entries"] == 4


def test_cli_timeline_text_names_every_entry(patched_conn, capsys):
    code = cli.main(["timeline", "--session", SESSION])

    out = capsys.readouterr().out
    assert code == 0
    assert "CUI // SP-CTI" in out
    assert out.count("hook_events#") == 2
    assert "Limits:" in out


def test_cli_build_then_verify_round_trip(patched_conn, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(bundle_format.HMAC_SECRET_ENV, SECRET)
    out = tmp_path / "cli-bundle"

    assert cli.main(["build", "--session", SESSION, "--out", str(out), "--json"]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["ok"] is True

    assert cli.main(["verify", "--bundle", str(out), "--json"]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["ok"] is True
    assert verified["status"] == "PASSED"


def test_cli_verify_exits_1_when_a_layer_fails(patched_conn, tmp_path, capsys,
                                               monkeypatch):
    monkeypatch.setenv(bundle_format.HMAC_SECRET_ENV, SECRET)
    out = tmp_path / "cli-bundle"
    cli.main(["build", "--session", SESSION, "--out", str(out)])
    capsys.readouterr()
    target = out / case_bundler.TIMELINE_MEMBER
    target.write_text("tampered", encoding="utf-8")

    code = cli.main(["verify", "--bundle", str(out), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == bundle_verifier.EXIT_FAILED
    assert payload["ok"] is False


def test_cli_verify_exits_2_when_the_hmac_key_is_unavailable(patched_conn, tmp_path,
                                                            capsys, monkeypatch):
    monkeypatch.delenv(bundle_format.HMAC_SECRET_ENV, raising=False)
    out = tmp_path / "cli-bundle"
    cli.main(["build", "--session", SESSION, "--out", str(out)])
    capsys.readouterr()

    code = cli.main(["verify", "--bundle", str(out), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == bundle_verifier.EXIT_INCOMPLETE
    assert payload["ok"] is False
    assert payload["status"] == "INCOMPLETE"


def test_cli_verify_exits_3_on_a_missing_bundle(tmp_path, capsys):
    code = cli.main(["verify", "--bundle", str(tmp_path / "nope"), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == bundle_verifier.EXIT_UNREADABLE
    assert payload["ok"] is False


def test_cli_errors_are_json_when_json_was_asked_for(patched_conn, tmp_path, capsys):
    out = tmp_path / "cli-bundle"
    cli.main(["build", "--session", SESSION, "--out", str(out)])
    capsys.readouterr()

    code = cli.main(["build", "--session", SESSION, "--out", str(out), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == bundle_verifier.EXIT_FAILED
    assert payload["ok"] is False
    assert "--force" in payload["error"]


def test_cli_build_empty_session_still_exits_zero(patched_conn, tmp_path, capsys):
    out = tmp_path / "empty-bundle"

    code = cli.main(["build", "--session", "sess-never-existed", "--out", str(out),
                     "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["counts"]["entries"] == 0


def test_cli_every_subcommand_accepts_json(capsys):
    parser = cli.build_parser()
    for argv in (["timeline", "--session", "s"], ["build", "--session", "s",
                 "--out", "d"], ["verify", "--bundle", "d"]):
        assert parser.parse_args(argv + ["--json"]).json is True


def test_cli_ships_no_dashboard_template():
    """agov-case-04 is CLI-only on purpose; a page needs all 8 gate components."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    for root in (repo_root / "tools" / "dashboard" / "templates",
                 repo_root / "icdev" / "tools" / "dashboard" / "templates"):
        assert not (root / "agent_case").exists(), (
            "a CASE dashboard page must ship all 8 completeness-gate components "
            "as its own card, not a lone template here"
        )
