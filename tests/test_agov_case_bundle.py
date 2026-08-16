# CUI // SP-CTI
"""AGOV CASE — the portable case bundle and its SHA-256 manifest (agov-case-02).

Four properties are load-bearing here, and each one fails silently in a way that
only shows up on the machine the evidence was carried to:

1. **Manifest completeness.** Every file in the bundle appears in the manifest
   with a digest that matches its bytes. A member that is written but not
   hashed is unverifiable evidence that still looks like evidence.
2. **Determinism.** Two exports of identical input produce an identical
   manifest. A manifest that changes because the clock moved cannot answer "is
   this the same bundle I already have", which is the first question a
   recipient asks.
3. **No raw transcript.** The bundle carries normalized records, never
   conversation text — asserted against the bundle's actual bytes with a
   transcript planted in the database, not against the exporter's intentions.
4. **The classification banner comes from ``classification_manager``.** Pinned
   by changing the marking on the source data and on the manager itself and
   watching the bundle follow. A literal ``CUI // SP-CTI`` passes a
   "banner is present" assertion and fails both of these.

The database is a hand-built SQLite file rather than the ``icdev_db`` fixture:
that fixture costs 30-50s per test and its MINIMAL_ICDEV_SCHEMA carries no
``hook_events``. Connections go through ``tests._sql_compat.translating`` so the
``%s`` placeholders this repo authors for PostgreSQL are rewritten exactly as
``StorageConnection`` rewrites them — a bare ``sqlite3.connect`` would make
every statement raise inside the callers' error handling and the tests would
assert against their own no-op.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3

import pytest

from tests._sql_compat import translating

bundle_format = importlib.import_module("tools.agent_case.bundle_format")
case_bundler = importlib.import_module("tools.agent_case.case_bundler")
classification_manager = importlib.import_module(
    "tools.compliance.classification_manager")
row_hash = importlib.import_module("tools.audit.row_hash")

SESSION = "sess-agov-case-02"
OTHER_SESSION = "sess-not-this-one"

# Planted in every transcript-bearing table the fixture builds. If this string
# reaches the bundle, a conversation leaked into forensic evidence.
TRANSCRIPT_MARKER = "TRANSCRIPT-LEAK-CANARY-4f2a9c"

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
CREATE TABLE agent_session_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    classification TEXT NOT NULL DEFAULT 'CUI',
    correlation_id TEXT
);
CREATE TABLE agent_approval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_at TEXT NOT NULL,
    session_id TEXT,
    actor TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    rule TEXT,
    decision TEXT NOT NULL,
    reason TEXT,
    mode TEXT,
    arg_keys TEXT,
    input_sha256 TEXT,
    detail TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT
);
-- Transcript-bearing tables, with the columns the live DDL actually gives them
-- (tools/db/init_icdev_db.py). They exist so the exporter has the OPPORTUNITY
-- to read them; each is named in case_bundler.TRANSCRIPT_SOURCES as excluded.
--
-- These two are the sharp case: both carry session_id AND content, so a join
-- one column wider than the allowlist would pull this very session's
-- conversation into forensic evidence.
CREATE TABLE intake_conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    content_type TEXT DEFAULT 'text',
    extracted_requirements TEXT,
    metadata TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT
);
CREATE TABLE ci_conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    role TEXT,
    content TEXT NOT NULL,
    content_type TEXT,
    action_taken TEXT,
    comment_id TEXT,
    metadata TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT
);
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    content_type TEXT DEFAULT 'text',
    metadata TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT
);
CREATE TABLE ace_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT,
    coworker_id TEXT,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT 'system',
    classification TEXT NOT NULL DEFAULT 'CUI',
    control_refs TEXT NOT NULL DEFAULT '',
    created_at TEXT
);
"""


def _seed(raw, audit_classification="CUI"):
    """A session with every source populated, plus a decoy session and a transcript."""
    raw.executescript(SCHEMA)

    for seq, (hook_type, tool, at) in enumerate([
        ("pre_tool_use", "Bash", "2026-08-09T10:00:00Z"),
        ("post_tool_use", "Edit", "2026-08-09T10:00:05Z"),
        ("post_tool_use", "Write", "2026-08-09T10:00:09Z"),
    ], start=1):
        payload = json.dumps({"tool_input_keys": ["file_path"], "output_length": seq})
        raw.execute(
            "INSERT INTO hook_events (session_id, hook_type, tool_name, project_id, "
            "payload, classification, signature, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (SESSION, hook_type, tool, "proj-1", payload, "CUI",
             bundle_format.compute_event_hmac(payload, "secret"), at),
        )
    raw.execute(
        "INSERT INTO hook_events (session_id, hook_type, tool_name, project_id, "
        "payload, classification, signature, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (OTHER_SESSION, "post_tool_use", "Bash", "proj-9", "{}", "CUI", "x",
         "2026-08-09T10:00:07Z"),
    )

    raw.execute(
        "INSERT INTO audit_trail (project_id, event_type, actor, action, details, "
        "affected_files, classification, ip_address, session_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("proj-1", "code_change", "agent", "edit_file", "edited two files",
         json.dumps(["tools/foo.py", "tests/test_foo.py"]), audit_classification,
         "10.0.0.1", SESSION, "2026-08-09T10:00:06Z"),
    )
    raw.execute(
        "INSERT INTO audit_trail (project_id, event_type, actor, action, details, "
        "affected_files, classification, ip_address, session_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("proj-1", "code_change", "agent", "write_file", "wrote one file",
         "docs/bar.md", audit_classification, "10.0.0.1", SESSION,
         "2026-08-09T10:00:08Z"),
    )

    # The agent event log (hcx-evt-01). Its payload_json holds the transcript
    # marker, which makes it the sharpest leak path in this fixture: unlike the
    # tables above, this one IS exported — only its payload column is not.
    for seq, (event_type, at, payload) in enumerate([
        ("turn_start", "2026-08-09T10:00:01Z", {"turn": 1}),
        ("request_context", "2026-08-09T10:00:01Z",
         {"messages": [f"system prompt: {TRANSCRIPT_MARKER}"]}),
        ("assistant_message", "2026-08-09T10:00:02Z",
         f"model said: {TRANSCRIPT_MARKER}"),
    ], start=1):
        raw.execute(
            "INSERT INTO agent_session_events (event_id, session_id, seq, "
            "event_type, occurred_at, payload_hash, payload_json, tenant_id, "
            "classification, correlation_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"ase-case02-{seq:04d}", SESSION, seq, event_type, at,
             row_hash.compute_payload_hash(payload),
             json.dumps(payload, sort_keys=True), "default", "CUI", "corr-1"),
        )
    raw.execute(
        "INSERT INTO agent_session_events (event_id, session_id, seq, event_type, "
        "occurred_at, payload_hash, payload_json, tenant_id, classification, "
        "correlation_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("ase-decoy-0001", OTHER_SESSION, 1, "turn_start", "2026-08-09T10:00:01Z",
         row_hash.compute_payload_hash({}), "{}", "default", "CUI", ""),
    )

    # Two enforcement decisions: one allowed, one denied. The denied one carries
    # a live-looking credential and an email in its free text.
    raw.execute(
        "INSERT INTO agent_approval_log (decided_at, session_id, actor, tool_name, "
        "tier, rule, decision, reason, mode, arg_keys, input_sha256, detail, "
        "classification, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-08-09T10:00:04Z", SESSION, "agent", "Edit", "write_local",
         "tier_allows", "allow", "within tier", "unattended",
         json.dumps(["file_path"]), "a" * 64, "no operator note", "CUI",
         "2026-08-09T10:00:04Z"),
    )
    raw.execute(
        "INSERT INTO agent_approval_log (decided_at, session_id, actor, tool_name, "
        "tier, rule, decision, reason, mode, arg_keys, input_sha256, detail, "
        "classification, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-08-09T10:00:10Z", SESSION, "agent", "Bash", "external",
         "irreversible", "deny",
         "operator pasted ghp_" + "a" * 36 + " into the note",
         "unattended", json.dumps(["command"]), "b" * 64,
         "escalate to analyst@example.mil", "CUI", "2026-08-09T10:00:10Z"),
    )

    # The transcript. Present in the database, and — for the first two tables —
    # keyed to this very session by a column the exporter could trivially join.
    raw.execute(
        "INSERT INTO intake_conversation (session_id, turn_number, role, content, "
        "created_at) VALUES (?,?,?,?,?)",
        (SESSION, 1, "customer", f"user said: {TRANSCRIPT_MARKER}",
         "2026-08-09T10:00:03Z"),
    )
    raw.execute(
        "INSERT INTO ci_conversation_turns (session_id, turn_number, role, content, "
        "created_at) VALUES (?,?,?,?,?)",
        (SESSION, 1, "agent", f"assistant replied: {TRANSCRIPT_MARKER}",
         "2026-08-09T10:00:03Z"),
    )
    raw.execute(
        "INSERT INTO chat_messages (context_id, turn_number, role, content, "
        "created_at) VALUES (?,?,?,?,?)",
        ("ctx-1", 1, "user", f"chat body: {TRANSCRIPT_MARKER}",
         "2026-08-09T10:00:03Z"),
    )
    raw.execute(
        "INSERT INTO ace_audit_log (instance_id, coworker_id, action, detail, "
        "created_at) VALUES (?,?,?,?,?)",
        ("inst-1", "cw-1", "turn", f"turn detail: {TRANSCRIPT_MARKER}",
         "2026-08-09T10:00:03Z"),
    )
    raw.commit()


@pytest.fixture()
def case_db(tmp_path):
    """An open, translating connection over a seeded SQLite file."""
    raw = sqlite3.connect(tmp_path / "case.db")
    raw.row_factory = sqlite3.Row
    _seed(raw)
    conn = translating(raw)
    yield conn
    raw.close()


def _build(tmp_path, conn, name="bundle", **kwargs):
    return case_bundler.build_case_bundle(SESSION, tmp_path / name, conn=conn,
                                          **kwargs)


def _bundle_files(bundle_dir):
    """Every file in the bundle except the manifest, as posix-relative paths."""
    return {rel for rel, _ in bundle_format.iter_bundle_files(bundle_dir)}


# --- 1. Manifest completeness ----------------------------------------------


def test_every_file_in_the_bundle_is_in_the_manifest_with_a_matching_digest(
        tmp_path, case_db):
    result = _build(tmp_path, case_db)
    bundle_dir = tmp_path / "bundle"
    manifest = bundle_format.read_manifest(bundle_dir)

    listed = {m["path"] for m in manifest["members"]}
    on_disk = _bundle_files(bundle_dir)

    assert listed == on_disk, (
        f"manifest and bundle disagree; only on disk={on_disk - listed}, "
        f"only in manifest={listed - on_disk}")
    assert on_disk, "the bundle wrote no members at all"

    for member in manifest["members"]:
        path = bundle_dir / member["path"]
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        assert member["sha256"] == expected, f"{member['path']} digest mismatch"
        assert member["bytes"] == path.stat().st_size

    assert manifest["algorithm"] == "sha256"
    # The manifest never hashes itself — a self-referential digest is not
    # computable, and claiming one would be a lie a verifier could not catch.
    assert bundle_format.MANIFEST_NAME not in listed
    assert result["counts"]["members"] == len(on_disk)


def test_the_bundle_carries_every_member_the_card_asks_for(tmp_path, case_db):
    result = _build(tmp_path, case_db)
    on_disk = _bundle_files(tmp_path / "bundle")

    for member in (case_bundler.CONTEXT_MEMBER, case_bundler.TIMELINE_MEMBER,
                   bundle_format.HOOK_EVENTS_MEMBER,
                   bundle_format.AUDIT_TRAIL_MEMBER,
                   case_bundler.AGENT_EVENTS_MEMBER,
                   case_bundler.APPROVALS_MEMBER, case_bundler.ARTIFACTS_MEMBER,
                   case_bundler.PROVENANCE_MEMBER):
        assert member in on_disk, f"{member} missing from the bundle"

    approvals = json.loads(
        (tmp_path / "bundle" / case_bundler.APPROVALS_MEMBER).read_text("utf-8"))
    assert [r["decision"] for r in approvals["records"]] == ["allow", "deny"]
    assert all(r["session_id"] == SESSION for r in approvals["records"])

    artifacts = json.loads(
        (tmp_path / "bundle" / case_bundler.ARTIFACTS_MEMBER).read_text("utf-8"))
    assert [a["path"] for a in artifacts["artifacts"]] == [
        "docs/bar.md", "tests/test_foo.py", "tools/foo.py"]
    # Referenced, not resolved: the exporter records where an artifact lives and
    # never opens it.
    assert artifacts["contents_included"] is False
    assert result["counts"]["artifacts_referenced"] == 3

    context = json.loads(
        (tmp_path / "bundle" / case_bundler.CONTEXT_MEMBER).read_text("utf-8"))
    assert context["spec"] == case_bundler.BUNDLE_SPEC
    assert context["session_id"] == SESSION
    assert "storage_backend" in context["endpoint"]


def test_a_decoy_session_never_enters_the_bundle(tmp_path, case_db):
    _build(tmp_path, case_db)
    blob = _bundle_bytes(tmp_path / "bundle")
    assert OTHER_SESSION not in blob
    assert SESSION in blob


# --- 2. Determinism ---------------------------------------------------------


def _bundle_bytes(bundle_dir) -> str:
    """Every byte of every member, concatenated. The assertion of last resort."""
    parts = [path.read_text(encoding="utf-8")
             for _, path in bundle_format.iter_bundle_files(bundle_dir)]
    parts.append((bundle_dir / bundle_format.MANIFEST_NAME).read_text("utf-8"))
    return "\n".join(parts)


def test_rebuilding_from_identical_input_produces_an_identical_manifest(
        tmp_path, case_db):
    at = "2026-08-09T12:00:00+00:00"
    first = _build(tmp_path, case_db, name="a", created_at=at)
    second = _build(tmp_path, case_db, name="b", created_at=at)

    manifest_a = (tmp_path / "a" / bundle_format.MANIFEST_NAME).read_bytes()
    manifest_b = (tmp_path / "b" / bundle_format.MANIFEST_NAME).read_bytes()
    assert manifest_a == manifest_b, "identical input produced differing manifests"
    assert first["bundle_id"] == second["bundle_id"]

    # And the members themselves, byte for byte — an identical manifest over
    # differing files would mean the digests were not computed from the bytes.
    for rel in _bundle_files(tmp_path / "a"):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes()


def test_bundle_digest_is_time_independent(tmp_path, case_db):
    """Export time must not reach any member — only ``manifest.created_at``.

    This is the stronger form of determinism: the same session exported hours
    apart yields the same ``bundle_digest``, so a recipient can recognise
    evidence they already hold without coordinating clocks.
    """
    first = _build(tmp_path, case_db, name="morning",
                   created_at="2026-08-09T08:00:00+00:00")
    second = _build(tmp_path, case_db, name="evening",
                    created_at="2026-08-09T20:30:00+00:00")

    assert first["bundle_digest"] == second["bundle_digest"]
    # Different export instants really are different bundles...
    assert first["bundle_id"] != second["bundle_id"]
    # ...but the difference lives only in the manifest, never in a member.
    for rel in _bundle_files(tmp_path / "morning"):
        assert (tmp_path / "morning" / rel).read_bytes() == \
            (tmp_path / "evening" / rel).read_bytes(), f"{rel} embeds export time"


def test_default_created_at_still_yields_a_stable_bundle_digest(tmp_path, case_db):
    """The determinism property must not depend on the caller pinning the clock."""
    first = _build(tmp_path, case_db, name="p")
    second = _build(tmp_path, case_db, name="q")
    assert first["bundle_digest"] == second["bundle_digest"]


def test_members_are_written_lf_only(tmp_path, case_db):
    """CRLF would break every digest the moment the bundle crossed platforms."""
    _build(tmp_path, case_db)
    bundle_dir = tmp_path / "bundle"
    for rel, path in bundle_format.iter_bundle_files(bundle_dir):
        assert b"\r\n" not in path.read_bytes(), f"{rel} carries CRLF"
    assert b"\r\n" not in (bundle_dir / bundle_format.MANIFEST_NAME).read_bytes()


def test_an_existing_bundle_is_not_silently_overwritten(tmp_path, case_db):
    _build(tmp_path, case_db)
    with pytest.raises(FileExistsError):
        _build(tmp_path, case_db)
    _build(tmp_path, case_db, overwrite=True)


# --- 3. No raw transcript ---------------------------------------------------


def test_no_raw_transcript_reaches_the_bundle(tmp_path, case_db):
    """The transcript is in the database, keyed to this session, and stays there."""
    # The join really was available: two transcript tables carry raw content
    # under the very session_id being exported. Without this the test would only
    # prove that unreachable data stayed unreached.
    for table in ("intake_conversation", "ci_conversation_turns"):
        cursor = case_db.cursor()
        cursor.execute(
            f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", [SESSION])  # noqa: S608
        assert cursor.fetchone()[0] == 1, f"{table} fixture did not seed"

    # And the sharpest one: agent_session_events IS exported, with the marker in
    # the payload column the export deliberately does not select.
    cursor = case_db.cursor()
    cursor.execute("SELECT COUNT(*) FROM agent_session_events WHERE session_id = ? "
                   "AND payload_json LIKE ?", [SESSION, f"%{TRANSCRIPT_MARKER}%"])
    assert cursor.fetchone()[0] == 2, "agent_session_events fixture did not seed"

    _build(tmp_path, case_db)
    blob = _bundle_bytes(tmp_path / "bundle")
    assert TRANSCRIPT_MARKER not in blob, (
        "conversation text from a transcript table reached the case bundle")
    for phrase in ("user said:", "assistant replied:", "turn detail:", "chat body:",
                   "system prompt:", "model said:"):
        assert phrase not in blob


def test_transcript_tables_are_excluded_by_construction_and_named(tmp_path, case_db):
    """Exclusion is a declared, reviewable set — not an accident of the SELECTs."""
    _build(tmp_path, case_db)
    context = json.loads(
        (tmp_path / "bundle" / case_bundler.CONTEXT_MEMBER).read_text("utf-8"))

    excluded = context["sources"]["excluded_transcripts"]
    assert set(excluded) == set(case_bundler.TRANSCRIPT_SOURCES)
    for table in ("intake_conversation", "ci_conversation_turns", "chat_messages",
                  "ace_audit_log", "agent_executions"):
        assert table in excluded, f"{table} is not declared as excluded"
        assert excluded[table], "an excluded source must say what it holds"

    # No transcript table may appear in the exported set, and the two lists must
    # not intersect at all.
    assert not set(context["sources"]["included"]) & set(case_bundler.TRANSCRIPT_SOURCES)


def test_the_event_log_is_bundled_without_its_payload_column(tmp_path, case_db):
    """hcx-evt-04's half of invariant 3: an exported table with a withheld column.

    ``TRANSCRIPT_SOURCES`` cannot express this — ``agent_session_events`` IS
    read, and dropping it would lose the one source that records what the model
    was actually handed. So the column allowlist drops ``payload_json`` and the
    hash travels in its place.
    """
    result = _build(tmp_path, case_db)
    events = json.loads(
        (tmp_path / "bundle" / case_bundler.AGENT_EVENTS_MEMBER).read_text("utf-8"))

    assert events["table"] == "agent_session_events"
    assert [r["seq"] for r in events["records"]] == [1, 2, 3]
    assert all(r["session_id"] == SESSION for r in events["records"])
    assert "ase-decoy-0001" not in json.dumps(events)

    for record in events["records"]:
        assert "payload_json" not in record
        assert record["payload_hash"], "the hash must travel in the payload's place"
    # The hash is still the one a holder of the payload recomputes.
    assert events["records"][2]["payload_hash"] == row_hash.compute_payload_hash(
        f"model said: {TRANSCRIPT_MARKER}")

    # 3 hooks + 2 audit rows + 3 events; the events are in the timeline too.
    assert result["counts"]["entries"] == 8
    assert result["counts"]["sources_joined"] == 3


def test_the_withheld_column_is_declared_not_merely_omitted(tmp_path, case_db):
    """An absence nobody wrote down is indistinguishable from an oversight."""
    result = _build(tmp_path, case_db)
    context = json.loads(
        (tmp_path / "bundle" / case_bundler.CONTEXT_MEMBER).read_text("utf-8"))

    excluded = context["sources"]["excluded_columns"]
    assert set(excluded) == set(case_bundler.EXCLUDED_COLUMNS)
    assert "agent_session_events.payload_json" in excluded
    assert excluded["agent_session_events.payload_json"]

    # The exported table is named as included, so a reader can see that the
    # exclusion is a COLUMN and not the whole source.
    assert "agent_session_events" in context["sources"]["included"]
    assert any("payload_json" in limit for limit in result["limits"])
    assert any("payload_json" in limit for limit in context["limits"])


def test_declaring_a_column_excluded_while_still_selecting_it_fails_at_import(
        monkeypatch):
    """The declaration is guarded, or it is only a comment.

    Reload the module with ``payload_json`` put back into the timeline's column
    allowlist: the guard must refuse rather than let the two disagree.
    """
    session_timeline = importlib.import_module("tools.agent_case.session_timeline")
    spec = session_timeline.SOURCES["agent_session_events"]
    monkeypatch.setitem(spec, "columns", tuple(spec["columns"]) + ("payload_json",))

    try:
        with pytest.raises(RuntimeError, match="payload_json"):
            importlib.reload(case_bundler)
    finally:
        # A failed reload leaves the module stripped of everything defined below
        # the guard, so it MUST be restored here or every later test in the file
        # runs against a hollow module.
        monkeypatch.undo()
        importlib.reload(case_bundler)
    assert hasattr(case_bundler, "build_case_bundle")


def test_operator_free_text_is_redacted_in_the_enforcement_decisions(
        tmp_path, case_db):
    result = _build(tmp_path, case_db)
    approvals = json.loads(
        (tmp_path / "bundle" / case_bundler.APPROVALS_MEMBER).read_text("utf-8"))

    denied = [r for r in approvals["records"] if r["decision"] == "deny"][0]
    assert "ghp_" not in denied["reason"], "a GitHub token survived export"
    assert "analyst@example.mil" not in denied["detail"]
    assert "[REDACTED]" in denied["reason"]
    # Flagged, so a reader can tell "nothing sensitive" from "something removed".
    assert denied["redacted"] is True
    assert approvals["redaction"]["rows_redacted"] == 1
    assert result["counts"]["rows_redacted"] == 1
    assert approvals["redaction"]["patterns_hit"]

    # Redaction touches free text only. The decision itself is evidence.
    assert denied["decision"] == "deny"
    assert denied["tier"] == "external"
    assert denied["input_sha256"] == "b" * 64
    allowed = [r for r in approvals["records"] if r["decision"] == "allow"][0]
    assert "redacted" not in allowed


def test_raw_stored_values_survive_for_the_tamper_evident_tables(tmp_path, case_db):
    """Redaction must not touch what the HMAC and the hash chain are computed over.

    ``hook_events.payload`` is signed byte for byte. Rewriting it at export time
    would make an untampered bundle report as tampered, which is a worse failure
    than the one redaction prevents — so the contract redacts excerpts and
    leaves signed records alone, and says so in the header.
    """
    _build(tmp_path, case_db)
    hooks = json.loads(
        (tmp_path / "bundle" / bundle_format.HOOK_EVENTS_MEMBER).read_text("utf-8"))
    for record in hooks["records"]:
        assert record["signature"] == bundle_format.compute_event_hmac(
            record["payload"], "secret")

    context = json.loads(
        (tmp_path / "bundle" / case_bundler.CONTEXT_MEMBER).read_text("utf-8"))
    assert context["redaction"]["raw_transcripts"]
    assert context["redaction"]["excerpts"]
    assert context["redaction"]["raw_stored_values"]


# --- 4. The classification banner comes from classification_manager ---------


def test_the_banner_is_the_one_classification_manager_produces(tmp_path, case_db):
    _build(tmp_path, case_db)
    manifest = bundle_format.read_manifest(tmp_path / "bundle")
    context = json.loads(
        (tmp_path / "bundle" / case_bundler.CONTEXT_MEMBER).read_text("utf-8"))

    expected = classification_manager.get_marking_banner("CUI")
    assert manifest["banner"] == expected
    assert context["banner"] == expected
    assert manifest["classification"] == "CUI"
    assert manifest["portion_marking"] == \
        classification_manager.get_portion_marking("CUI")
    assert manifest["marked_by"] == "tools/compliance/classification_manager.py"


def test_the_banner_follows_the_data_not_a_literal(tmp_path):
    """A SECRET audit row must produce the SECRET banner with no code change.

    This is the assertion a hardcoded ``CUI // SP-CTI`` cannot pass, and the
    reason the marking is resolved rather than written down.
    """
    raw = sqlite3.connect(tmp_path / "secret.db")
    raw.row_factory = sqlite3.Row
    _seed(raw, audit_classification="SECRET")
    conn = translating(raw)
    try:
        result = case_bundler.build_case_bundle(SESSION, tmp_path / "s", conn=conn)
    finally:
        raw.close()

    assert result["classification"] == "SECRET"
    manifest = bundle_format.read_manifest(tmp_path / "s")
    assert manifest["classification"] == "SECRET"
    assert manifest["banner"] == classification_manager.get_marking_banner("SECRET")
    assert "SECRET" in manifest["banner"]
    assert "CONTROLLED UNCLASSIFIED INFORMATION" not in manifest["banner"]


def test_the_banner_is_sourced_from_the_manager_at_call_time(tmp_path, case_db,
                                                             monkeypatch):
    """Patch the manager, and the bundle's banner changes. A literal would not."""
    sentinel = "//// PROVES-THE-CALL-REACHED-THE-MANAGER ////"
    monkeypatch.setattr(classification_manager, "get_marking_banner",
                        lambda *a, **k: sentinel)

    _build(tmp_path, case_db)
    manifest = bundle_format.read_manifest(tmp_path / "bundle")
    context = json.loads(
        (tmp_path / "bundle" / case_bundler.CONTEXT_MEMBER).read_text("utf-8"))

    assert manifest["banner"] == sentinel
    assert context["banner"] == sentinel


def test_the_most_restrictive_marking_present_wins(tmp_path):
    """Read-up, not read-down: one SECRET record marks the whole bundle SECRET."""
    order = classification_manager.get_clearance_order
    assert order("SECRET") > order("CUI")
    assert case_bundler.resolve_classification(
        [{"classification": "CUI"}, {"classification": "SECRET"},
         {"classification": "CUI"}]) == "SECRET"
    # An unmarked record cannot downgrade a bundle, and no records at all still
    # produces a marking — an unmarked forensic export is not an option.
    assert case_bundler.resolve_classification(
        [{"classification": None}, {"classification": "SECRET"}]) == "SECRET"
    assert case_bundler.resolve_classification([]) == "CUI"


# --- Degradation ------------------------------------------------------------


def test_a_missing_approval_table_is_a_reported_limit_not_a_crash(tmp_path):
    """A checkout whose approval-gate migration has not run still exports."""
    raw = sqlite3.connect(tmp_path / "partial.db")
    raw.row_factory = sqlite3.Row
    _seed(raw)
    raw.execute("DROP TABLE agent_approval_log")
    raw.commit()
    conn = translating(raw)
    try:
        result = case_bundler.build_case_bundle(SESSION, tmp_path / "p", conn=conn)
    finally:
        raw.close()

    assert result["counts"]["enforcement_decisions"] == 0
    assert any("agent_approval_log" in limit for limit in result["limits"])
    approvals = json.loads(
        (tmp_path / "p" / case_bundler.APPROVALS_MEMBER).read_text("utf-8"))
    assert approvals["present"] is False
    # The member is still written and still hashed: "not in the bundle" must not
    # be confusable with "the session had no enforcement decisions".
    manifest = bundle_format.read_manifest(tmp_path / "p")
    assert case_bundler.APPROVALS_MEMBER in {m["path"] for m in manifest["members"]}


def test_provenance_reuses_prov_recorder_and_the_symbol_actually_resolves(tmp_path):
    """The reuse must be real, not a name that fails inside a broad ``except``.

    ``collect_provenance`` catches everything so a missing PROV table degrades
    to a reported limit — which also means a misspelled class name degrades to a
    plausible-looking ``reason`` string and nobody notices. Import the symbol
    here so the test fails loudly instead.
    """
    from tools.observability.provenance.prov_recorder import ProvRecorder

    assert hasattr(ProvRecorder, "export_prov_json")

    empty = tmp_path / "no-such.db"
    result = case_bundler.collect_provenance("proj-1", db_path=empty)
    # A missing database is a reported limit, never a raise...
    assert result["project_id"] == "proj-1"
    # ...and the reason must not be an ImportError/AttributeError, which would
    # mean the reuse never happened at all.
    assert "ImportError" not in str(result.get("reason", ""))
    assert "AttributeError" not in str(result.get("reason", ""))


def test_provenance_is_declined_rather_than_taken_from_another_database(
        tmp_path, case_db):
    """Records and provenance must come from the same database, or neither.

    ``ProvRecorder`` reads from a path and cannot be handed an open connection.
    With a caller-supplied ``conn`` the exporter therefore cannot prove the two
    agree, and attributing some other database's provenance to this session
    would be a silent evidence error.
    """
    result = _build(tmp_path, case_db)
    provenance = json.loads(
        (tmp_path / "bundle" / case_bundler.PROVENANCE_MEMBER).read_text("utf-8"))

    assert provenance["available"] is False
    assert "same database" in provenance["reason"]
    assert any("provenance not exported" in limit for limit in result["limits"])


def test_provenance_export_really_produces_a_prov_json_document(tmp_path):
    """The happy path, on a real database — reuse proven, not just claimed.

    Failure-path tests alone would pass against a ``collect_provenance`` that
    could never succeed. This one builds the three PROV tables, exports through
    ``ProvRecorder``, and checks the W3C shape came back intact.
    """
    db = tmp_path / "prov.db"
    raw = sqlite3.connect(db)
    raw.executescript("""
        CREATE TABLE prov_entities (id TEXT PRIMARY KEY, project_id TEXT,
            entity_type TEXT, label TEXT, content_hash TEXT);
        CREATE TABLE prov_activities (id TEXT PRIMARY KEY, project_id TEXT,
            activity_type TEXT, label TEXT, start_time TEXT, end_time TEXT);
        CREATE TABLE prov_relations (id INTEGER PRIMARY KEY, project_id TEXT,
            relation_type TEXT, subject_id TEXT, object_id TEXT);
        INSERT INTO prov_entities VALUES ('e1','proj-1','file','tools/foo.py','abc');
        INSERT INTO prov_activities VALUES ('a1','proj-1','edit','edit_file','t0','t1');
        INSERT INTO prov_relations VALUES (1,'proj-1','wasGeneratedBy','e1','a1');
    """)
    raw.commit()
    raw.close()

    result = case_bundler.collect_provenance("proj-1", db_path=db)
    assert result["available"] is True, result.get("reason")
    assert "prov.db" in str(result["database"])

    document = result["document"]
    # Carried verbatim from prov_recorder, not reshaped into a second format.
    assert document["prefix"]["prov"] == "http://www.w3.org/ns/prov#"
    assert document["entity"]["e1"]["prov:label"] == "tools/foo.py"
    assert document["activity"]["a1"]["prov:type"] == "edit"


def test_a_session_with_no_records_produces_a_valid_empty_bundle(tmp_path, case_db):
    result = case_bundler.build_case_bundle("sess-never-existed", tmp_path / "e",
                                            conn=case_db)
    manifest = bundle_format.read_manifest(tmp_path / "e")
    assert {m["path"] for m in manifest["members"]} == _bundle_files(tmp_path / "e")
    assert result["counts"]["entries"] == 0
    assert manifest["banner"] == classification_manager.get_marking_banner("CUI")


def test_session_id_is_required(tmp_path, case_db):
    with pytest.raises(ValueError):
        case_bundler.build_case_bundle("", tmp_path / "x", conn=case_db)
