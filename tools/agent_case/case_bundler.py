#!/usr/bin/env python3
# CUI // SP-CTI
"""AGOV CASE — write an agent session out as a portable, SHA-256-manifested bundle.

The bundle is the unit of handoff: a directory that can be copied to a machine
that never had the source database and still be verified there. Everything the
verifier needs travels with it, which is why the record files carry raw stored
values (``hook_events.payload``, ``hook_events.signature``,
``audit_trail.hash``) rather than anything re-derived at export time.

Layout, and every well-known path here is the constant from
:mod:`tools.agent_case.bundle_format` rather than a literal, so the writer and
the verifier cannot drift::

    <bundle>/
      manifest.json                    # SHA-256 of every other member
      context.json                     # endpoint/context header + classification
      timeline.json                    # the ordered join (session_timeline)
      records/hook_events.json         # {"table", "records"}
      records/audit_trail.json         # {"table", "records", "chain_context"}
      records/agent_findings.json      # only when the table exists
      records/agent_approval_log.json  # enforcement decisions, excerpts redacted
      artifacts.json                   # paths the session referenced, not their bytes
      provenance/prov.json             # W3C PROV-JSON, only when a project resolves

WHY THIS IS NOT A THIRD BUNDLER. ``tools/compliance/swft_evidence_bundler.py``
bundles DoD SWFT software-supply-chain evidence per PROJECT, and
``tools/observability/provenance/prov_recorder.py`` emits W3C PROV-JSON per
project as well. Neither can express agent BEHAVIOUR, because neither is keyed
by ``session_id``. This module adds that one axis on the same machinery: the
provenance member is ``prov_recorder.export_prov_json`` output carried verbatim,
and the artifact member follows ``swft_evidence_bundler._collect_artifact_evidence``
in recording that evidence EXISTS and where, never copying its bytes.

THREE INVARIANTS THIS MODULE OWNS
---------------------------------

1. **Every member is in the manifest with its SHA-256.** ``build_manifest``
   walks the directory that was actually written, so a member cannot be added
   without being hashed. The manifest never covers itself — it is the root of
   trust and a self-referential digest is not computable.

2. **Identical input produces an identical manifest.** No member carries an
   export wall-clock: ``_VOLATILE_MEMBER_FIELDS`` names the fields stripped on
   the way out (``built_at``), and export time lives ONLY in
   ``manifest.created_at``. So ``bundle_digest`` — a digest over the member
   list — is time-independent, and two exports of unchanged data taken hours
   apart prove equality byte for byte. Members are written with ``newline="\\n"``
   and ``sort_keys=True``, because the manifest hashes raw bytes and CRLF would
   break every digest the moment a Windows-written bundle was verified on Linux.

3. **No raw transcript, by construction rather than by filtering.**
   ``TRANSCRIPT_SOURCES`` is the set of tables that hold conversation text, and
   nothing here ever opens one — the exported sources are a closed allowlist
   (``session_timeline.SOURCES`` plus ``agent_approval_log``), each with its own
   column allowlist. A bundle cannot leak a prompt it never read. Free-text
   fields that an operator can write into (``agent_approval_log.reason`` and
   ``.detail``) are additionally passed through
   ``tools.llm.output_redactor.redact`` before they are written.

The classification marking is resolved through
``tools/compliance/classification_manager.py`` from the markings actually
present on the session's records — the most restrictive one wins — and never
hardcoded here. A session carrying a SECRET audit row produces a SECRET bundle
banner without anyone editing this file.

Usage:
    from tools.agent_case.case_bundler import build_case_bundle
    result = build_case_bundle("sess-abc123", "out/case-sess-abc123")
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Run by path, sys.path[0] is this file's own directory — never the import root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_case.bundle_format import (  # noqa: E402
    AUDIT_TRAIL_MEMBER,
    HOOK_EVENTS_MEMBER,
    MANIFEST_NAME,
    build_manifest,
    write_manifest,
)
from tools.agent_case.session_timeline import (  # noqa: E402
    UNJOINABLE_SOURCES,
    _row_to_dict,
    _table_columns,
    build_timeline,
)
from tools.db.storage import (  # noqa: E402
    get_backend,
    get_connection,
    sql_placeholder,
    table_exists,
)

# --- Members ---------------------------------------------------------------

CONTEXT_MEMBER = "context.json"
TIMELINE_MEMBER = "timeline.json"
FINDINGS_MEMBER = "records/agent_findings.json"
APPROVALS_MEMBER = "records/agent_approval_log.json"
ARTIFACTS_MEMBER = "artifacts.json"
PROVENANCE_MEMBER = "provenance/prov.json"

BUNDLE_SPEC = "icdev.agov.case-bundle"
BUNDLE_SPEC_VERSION = 1
PRODUCER = "tools/agent_case/case_bundler.py"

# timeline source table -> bundle member it is written to.
MEMBER_FOR_SOURCE = {
    "hook_events": HOOK_EVENTS_MEMBER,
    "audit_trail": AUDIT_TRAIL_MEMBER,
    "agent_findings": FINDINGS_MEMBER,
}

# Fields stripped from a member on the way out because they record when the
# EXPORT ran rather than what happened. Leaving one in would make two exports of
# unchanged data differ, which is the difference between a manifest that proves
# something and one that only proves the clock moved.
_VOLATILE_MEMBER_FIELDS = ("built_at",)

# --- The enforcement-decision source ---------------------------------------

# agent_approval_log is the approval gate's own record of what it let through
# and what it stopped (tools/agent_runtime/approval_gate.py). It is NOT in
# session_timeline.SOURCES because it is not one of the tamper-evident forensic
# tables — it carries no HMAC and no hash chain — which is exactly why its
# free-text columns can be redacted in place here without costing anyone a
# verification. Columns are an allowlist so a later ALTER TABLE cannot silently
# widen a forensic export.
APPROVAL_SOURCE = {
    "table": "agent_approval_log",
    "id_column": "id",
    "time_column": "decided_at",
    "columns": (
        "id", "decided_at", "session_id", "actor", "tool_name", "tier", "rule",
        "decision", "reason", "mode", "arg_keys", "input_sha256", "detail",
        "classification", "created_at",
    ),
}

# Operator-writable free text. Everything else in the allowlist is an
# identifier, an enum or a digest — ``arg_keys`` is deliberately key NAMES only
# and ``input_sha256`` is already a digest, so neither needs redacting.
REDACTED_APPROVAL_FIELDS = ("reason", "detail")

# --- What is deliberately NOT exported --------------------------------------

# Tables that carry conversation text, verified against the live DDL. Named here
# so the exclusion is a reviewable decision rather than an absence, and asserted
# by tests/test_agov_case_bundle.py. Nothing in this module reads one.
#
# The first two are the load-bearing entries: they carry a ``session_id`` AND
# raw turn ``content``, so a join one column wider than the allowlist would pick
# them up for exactly the session being exported. Excluding them is a decision,
# not an accident of which tables happen to key on session_id.
TRANSCRIPT_SOURCES = {
    "intake_conversation": ("customer/analyst turn content — has session_id and "
                            "content; would join"),
    "ci_conversation_turns": ("developer/agent turn content — has session_id and "
                              "content; would join"),
    "chat_messages": "chat pane message bodies, keyed by context_id",
    "ace_audit_log": "ACE co-worker action detail, keyed by instance_id",
    # Not conversation text itself, but a pointer to it: agent_executions stores
    # prompt_hash and output_path, never the prompt. Following a stored path is
    # precisely what collect_artifact_paths refuses to do.
    "agent_executions": ("prompt_hash and output_path — a pointer to run output, "
                         "never followed"),
}

# Columns that carry a filesystem path the session touched. Read from the
# records already in the bundle, never from a wider query.
ARTIFACT_PATH_FIELDS = ("affected_files",)


def _write_member(bundle_dir: Path, member: str, payload: dict) -> Path:
    """Write one bundle member as canonical UTF-8/LF JSON."""
    path = bundle_dir / member
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return path


def _bundle_id(session_id: str, created_at: str) -> str:
    """Stable id for this export. Two exports of one session are two bundles."""
    digest = hashlib.sha256(f"{session_id}|{created_at}".encode()).hexdigest()
    return f"case-{digest[:16]}"


def compute_bundle_digest(manifest: dict) -> str:
    """A single digest over the member list — the bundle's time-free identity.

    ``manifest.created_at`` changes on every export; this does not. Two exports
    of unchanged session data have the same ``bundle_digest``, which is what
    lets a recipient say "this is the same evidence I already have" rather than
    only "these bytes differ somewhere".
    """
    lines = sorted(
        f"{m.get('path')}:{m.get('sha256')}" for m in (manifest.get("members") or [])
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# --- Classification ---------------------------------------------------------


def resolve_classification(records: list) -> str:
    """The most restrictive marking present on the session's records.

    Resolved through ``classification_manager.get_clearance_order`` rather than
    a local ranking: the ordering of PUBLIC < CUI < SECRET < TOP SECRET is that
    module's to define, and duplicating it here is how the two drift.
    """
    from tools.compliance.classification_manager import get_clearance_order

    best = "CUI"
    best_order = get_clearance_order(best)
    for record in records:
        marking = (record or {}).get("classification")
        if not marking:
            continue
        order = get_clearance_order(str(marking))
        if order > best_order:
            best, best_order = str(marking), order
    return best


def classification_header(classification: str) -> dict:
    """The bundle's marking block, every field produced by classification_manager.

    Nothing in this dict is a literal. Swap the resolved classification and the
    banner text follows, which is the property
    ``tests/test_agov_case_bundle.py`` pins — a hardcoded ``CUI // SP-CTI``
    would keep saying CUI over a SECRET session.
    """
    from tools.compliance.classification_manager import (
        get_marking_banner,
        get_portion_marking,
    )

    return {
        "classification": classification,
        "banner": get_marking_banner(classification),
        "portion_marking": get_portion_marking(classification),
        "marked_by": "tools/compliance/classification_manager.py",
    }


# --- Sources ----------------------------------------------------------------


def collect_chain_context(conn, audit_rows: list) -> dict:
    """Hashes of audit rows immediately preceding the slice, keyed by row id.

    Only predecessors that are NOT already in the slice are looked up: an
    in-bundle predecessor is verified from the bundle itself, and re-exporting
    its hash from the database would let a tampered slice supply its own anchor.
    """
    if not audit_rows or not table_exists(conn, "audit_trail"):
        return {}

    in_slice = {str(r.get("id")) for r in audit_rows if r.get("id") is not None}
    wanted = set()
    for row in audit_rows:
        row_id = row.get("id")
        if row_id is None:
            continue
        try:
            prev_id = int(row_id) - 1
        except (TypeError, ValueError):
            continue
        if prev_id < 1 or str(prev_id) in in_slice:
            continue
        wanted.add(prev_id)
    if not wanted:
        return {}

    if "hash" not in _table_columns(conn, "audit_trail"):
        # Migration 149 has not run here. Anchoring nothing is correct: the
        # verifier reports the link as not verifiable, which is the truth.
        return {}

    ph = sql_placeholder(conn)
    placeholders = ", ".join([ph] * len(wanted))
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT id, hash FROM audit_trail WHERE id IN ({placeholders})",  # nosec B608 -- placeholders only; ids are ints
        sorted(wanted),
    )
    context = {}
    for row in cursor.fetchall():
        record = _row_to_dict(cursor, row)
        if record.get("hash"):
            context[str(record["id"])] = record["hash"]
    return context


def collect_enforcement_decisions(conn, session_id: str, since: str = None,
                                  until: str = None, limit: int = None) -> dict:
    """Approval-gate decisions for one session, free text redacted in place.

    Returns the member payload, including a ``redaction`` block naming which
    fields were scanned and which patterns fired. A row whose text was changed
    is flagged ``redacted: true`` so a reader can tell "nothing sensitive here"
    from "something was removed" — an unflagged redaction is indistinguishable
    from the operator having typed the placeholder themselves.
    """
    from tools.llm.output_redactor import redact

    table = APPROVAL_SOURCE["table"]
    payload = {
        "table": table,
        "records": [],
        "redaction": {
            "fields": list(REDACTED_APPROVAL_FIELDS),
            "redactor": "tools/llm/output_redactor.py::redact",
            "rows_redacted": 0,
            "patterns_hit": [],
        },
    }
    if not table_exists(conn, table):
        payload["present"] = False
        payload["note"] = "table does not exist in this database"
        return payload
    payload["present"] = True

    available = _table_columns(conn, table)
    columns = [c for c in APPROVAL_SOURCE["columns"] if c in available]
    if not columns:
        payload["note"] = "table exists but carries none of the expected columns"
        return payload

    ph = sql_placeholder(conn)
    # Identifiers come from APPROVAL_SOURCE and the live table's own columns;
    # every caller-supplied value is bound below.
    sql = (f"SELECT {', '.join(columns)} FROM {table} "  # nosec B608 -- identifiers are module constants, values are bound
           f"WHERE session_id = {ph}")
    params = [session_id]
    time_column = APPROVAL_SOURCE["time_column"]
    if since and time_column in available:
        sql += f" AND {time_column} >= {ph}"
        params.append(since)
    if until and time_column in available:
        sql += f" AND {time_column} <= {ph}"
        params.append(until)
    sql += f" ORDER BY {time_column}, {APPROVAL_SOURCE['id_column']}"
    if limit:
        sql += f" LIMIT {int(limit)}"

    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = [_row_to_dict(cursor, row) for row in cursor.fetchall()]

    hits = set()
    for row in rows:
        changed = False
        for field in REDACTED_APPROVAL_FIELDS:
            value = row.get(field)
            if not value:
                continue
            result = redact(str(value))
            if result.changed:
                row[field] = result.redacted_text
                changed = True
                hits.update(result.pattern_hits)
        if changed:
            row["redacted"] = True
            payload["redaction"]["rows_redacted"] += 1
        payload["records"].append(row)

    payload["redaction"]["patterns_hit"] = sorted(hits)
    return payload


def collect_artifact_paths(records_by_source: dict) -> dict:
    """Paths the session referenced, taken from records already in the bundle.

    Following ``swft_evidence_bundler._collect_artifact_evidence``, this records
    that an artifact was REFERENCED and where it lives; it does not copy the
    bytes and does not open the path. Reading a path that came out of a database
    would make the exporter follow attacker-influenced input off the bundle's
    own disk, and an artifact's contents are the one place a transcript could
    re-enter a bundle that never queried for one.
    """
    referenced: dict = {}
    for source, records in records_by_source.items():
        for record in records:
            for field in ARTIFACT_PATH_FIELDS:
                for path in _split_paths(record.get(field)):
                    entry = referenced.setdefault(
                        path, {"path": path, "referenced_by": []})
                    ref = f"{source}#{record.get('id') or record.get('finding_id')}"
                    if ref not in entry["referenced_by"]:
                        entry["referenced_by"].append(ref)
    for entry in referenced.values():
        entry["referenced_by"].sort()
    return {
        "artifacts": [referenced[k] for k in sorted(referenced)],
        "contents_included": False,
        "note": ("Paths are recorded as referenced, not resolved: this exporter "
                 "never opens a path that came out of the database. Collect the "
                 "artifacts themselves through the SWFT evidence bundler."),
    }


def _split_paths(value) -> list:
    """Paths out of an ``affected_files`` cell — JSON list, or comma/newline text."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        candidates = list(value)
    else:
        text = str(value).strip()
        candidates = None
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    candidates = parsed
            except (ValueError, TypeError):
                candidates = None
        if candidates is None:
            candidates = [p for part in text.splitlines() for p in part.split(",")]
    return sorted({str(c).strip() for c in candidates if str(c).strip()})


def collect_provenance(project_id: str, db_path=None,
                       source_known: bool = True) -> dict:
    """W3C PROV-JSON for the session's project, straight from prov_recorder.

    Carried verbatim rather than reshaped: ``prov_export`` on the observability
    MCP server serves the same structure, and a case bundle that quietly
    reformatted it would be a second provenance format to keep in step. Returns
    a ``{"available": False, "reason": ...}`` stub instead of raising — missing
    provenance is a limit to report, not a failed export.

    ``source_known`` is the integrity guard. ``ProvRecorder`` opens its own
    connection from a PATH and cannot be pointed at an already-open one, so when
    a caller supplies ``conn`` there is no way to prove the recorder would read
    the same database the records came from. Exporting anyway would attribute
    one database's provenance to another database's session — a quiet evidence
    error, and the worst kind. Declining and saying so is the honest answer;
    pass ``db_path`` to opt back in.
    """
    if not project_id:
        return {"available": False,
                "reason": "no project_id on any record for this session"}
    if not source_known:
        return {
            "available": False,
            "project_id": project_id,
            "reason": ("the records came from a caller-supplied connection and "
                       "ProvRecorder reads from a path, so provenance cannot be "
                       "proven to come from the same database; pass db_path to "
                       "export it"),
        }
    try:
        from tools.observability.provenance.prov_recorder import ProvRecorder

        recorder = ProvRecorder(db_path=db_path, project_id=project_id)
        document = recorder.export_prov_json(project_id)
    except Exception as exc:  # noqa: BLE001 - provenance is optional evidence
        return {"available": False, "project_id": project_id,
                "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "project_id": project_id,
        "database": str(db_path) if db_path else "default (tools/db DB_PATH)",
        "format": "W3C PROV-JSON",
        "source": "tools/observability/provenance/prov_recorder.py::export_prov_json",
        "document": document,
    }


def _resolve_project_id(records_by_source: dict) -> str:
    """The project_id the session's records agree on, or None if they do not."""
    seen = {str(r["project_id"]) for records in records_by_source.values()
            for r in records if r.get("project_id")}
    return sorted(seen)[0] if len(seen) == 1 else None


def build_context_header(session_id: str, classification: str, window: dict,
                         included_sources, limits: list) -> dict:
    """The endpoint/context header — where this evidence came from.

    Deliberately free of anything that changes between two exports of the same
    data: no wall-clock (that is ``manifest.created_at``) and no hostname or
    connection string. A DSN can carry a password and a hostname is PII-adjacent
    on a named workstation; the backend NAME is what a recipient needs to know
    to go asking for the source database.
    """
    return {
        "spec": BUNDLE_SPEC,
        "spec_version": BUNDLE_SPEC_VERSION,
        "producer": PRODUCER,
        "session_id": session_id,
        "endpoint": {
            "storage_backend": get_backend(),
            "icdev_env": os.environ.get("ICDEV_ENV") or "unspecified",
        },
        **classification_header(classification),
        "window": window,
        "sources": {
            "included": sorted(included_sources),
            "excluded_transcripts": dict(sorted(TRANSCRIPT_SOURCES.items())),
            "not_joinable": dict(sorted(UNJOINABLE_SOURCES.items())),
        },
        "redaction": {
            "raw_transcripts": ("never included: no transcript table is read, so "
                                "the bundle cannot carry a prompt or a model reply"),
            "excerpts": (f"{', '.join(REDACTED_APPROVAL_FIELDS)} on "
                         f"{APPROVAL_SOURCE['table']} pass through "
                         "tools/llm/output_redactor.py::redact before export"),
            "raw_stored_values": ("records/ carry stored values verbatim because "
                                  "the hook_events HMAC and the migration-149 "
                                  "audit chain are computed over them; redacting "
                                  "one would read as tampering, not as privacy"),
        },
        "limits": list(limits),
    }


def build_case_bundle(session_id: str, out_dir, conn=None, since: str = None,
                      until: str = None, limit: int = None,
                      overwrite: bool = False, created_at: str = None,
                      prov_db_path=None) -> dict:
    """Write a case bundle for one session and return what was written.

    Args:
        session_id: Session to export.
        out_dir: Bundle directory. Created if absent.
        conn: Optional open connection.
        since / until / limit: Passed to :func:`build_timeline`; a windowed
            bundle records its window in the manifest and the context header so
            a reader can tell a slice from a whole session.
        overwrite: Required to write into a directory that already holds a
            manifest. A bundle is evidence; silently merging a new export over
            an old one would produce a directory whose manifest describes some
            files and not others.
        created_at: Export timestamp. Defaults to now. It lands ONLY in
            ``manifest.json`` — no member embeds it — so a caller that pins it
            gets a byte-identical bundle and a caller that does not still gets
            an identical ``bundle_digest``.
        prov_db_path: Database the W3C PROV export should read. Only needed when
            ``conn`` is supplied: ``ProvRecorder`` opens its own connection from
            a path, so without this the exporter cannot prove the provenance
            came from the same database as the records and declines rather than
            guessing. See :func:`collect_provenance`.

    Returns:
        dict with ``bundle_dir``, ``bundle_id``, ``bundle_digest``, ``members``,
        ``counts``, the resolved ``classification`` and the ``limits`` carried
        through from the timeline.
    """
    if not session_id:
        raise ValueError("session_id is required")
    bundle_dir = Path(out_dir)
    if (bundle_dir / MANIFEST_NAME).exists() and not overwrite:
        raise FileExistsError(
            f"{bundle_dir} already holds a {MANIFEST_NAME}; pass overwrite=True "
            "(--force) to replace that bundle"
        )
    bundle_dir.mkdir(parents=True, exist_ok=True)

    created_at = created_at or datetime.now(timezone.utc).isoformat()
    owns_conn = conn is None
    conn = conn or get_connection()
    members = []
    try:
        timeline = build_timeline(session_id, conn=conn, since=since, until=until,
                                  limit=limit)
        limits = list(timeline["limits"])

        by_source = {}
        for entry in timeline["entries"]:
            by_source.setdefault(entry["source"], []).append(entry["record"])

        # Every joinable source gets a member file, including an empty one: a
        # missing records/hook_events.json makes the verifier report the HMAC
        # layer NOT_VERIFIED, which should mean "not present in the bundle",
        # not "this session had no hook events".
        for source, member in MEMBER_FOR_SOURCE.items():
            if not timeline["sources"].get(source, {}).get("present"):
                continue
            records = by_source.get(source, [])
            payload = {"table": source, "records": records}
            if source == "audit_trail":
                payload["chain_context"] = collect_chain_context(conn, records)
            _write_member(bundle_dir, member, payload)
            members.append({"member": member, "table": source,
                            "records": len(records)})

        approvals = collect_enforcement_decisions(conn, session_id, since=since,
                                                  until=until, limit=limit)
        _write_member(bundle_dir, APPROVALS_MEMBER, approvals)
        members.append({"member": APPROVALS_MEMBER, "table": APPROVAL_SOURCE["table"],
                        "records": len(approvals["records"])})
        if not approvals.get("present"):
            limits.append(
                f"{APPROVAL_SOURCE['table']} does not exist in this database: the "
                "bundle carries no enforcement decisions, which is not the same as "
                "the session having had none"
            )

        record_pool = dict(by_source)
        record_pool[APPROVAL_SOURCE["table"]] = approvals["records"]
        project_id = _resolve_project_id(record_pool)
        classification = resolve_classification(
            [r for records in record_pool.values() for r in records])
    finally:
        if owns_conn:
            conn.close()

    artifacts = collect_artifact_paths(by_source)
    _write_member(bundle_dir, ARTIFACTS_MEMBER, artifacts)
    members.append({"member": ARTIFACTS_MEMBER, "table": None,
                    "records": len(artifacts["artifacts"])})

    provenance = collect_provenance(
        project_id, db_path=prov_db_path,
        source_known=owns_conn or prov_db_path is not None)
    _write_member(bundle_dir, PROVENANCE_MEMBER, provenance)
    members.append({"member": PROVENANCE_MEMBER, "table": None, "records": 0})
    if not provenance.get("available"):
        limits.append(f"provenance not exported: {provenance.get('reason')}")

    # Strip export wall-clock so the member's digest depends only on the
    # session's data. Export time is recorded once, in the manifest.
    for field in _VOLATILE_MEMBER_FIELDS:
        timeline.pop(field, None)
    _write_member(bundle_dir, TIMELINE_MEMBER, timeline)
    members.append({"member": TIMELINE_MEMBER, "table": None,
                    "records": len(timeline["entries"])})

    context = build_context_header(
        session_id, classification, timeline["window"],
        {m["table"] for m in members if m["table"]}, limits)
    _write_member(bundle_dir, CONTEXT_MEMBER, context)
    members.append({"member": CONTEXT_MEMBER, "table": None, "records": 0})

    bundle_id = _bundle_id(session_id, created_at)
    manifest = build_manifest(
        bundle_dir,
        bundle_id=bundle_id,
        session_id=session_id,
        created_at=created_at,
        extra={
            "spec": BUNDLE_SPEC,
            "spec_version": BUNDLE_SPEC_VERSION,
            "producer": PRODUCER,
            "window": timeline["window"],
            "limits": limits,
            # Overrides build_manifest's caller-less default. A case bundle
            # always knows its marking, and it comes from classification_manager.
            **classification_header(classification),
        },
    )
    manifest["bundle_digest"] = compute_bundle_digest(manifest)
    write_manifest(bundle_dir, manifest)

    members.sort(key=lambda m: m["member"])
    return {
        "bundle_dir": str(bundle_dir),
        "bundle_id": bundle_id,
        "bundle_digest": manifest["bundle_digest"],
        "session_id": session_id,
        "created_at": created_at,
        "classification": classification,
        "project_id": project_id,
        "members": members,
        "counts": {
            "members": len(manifest["members"]),
            "entries": timeline["counts"]["entries"],
            "sources_joined": timeline["counts"]["sources_joined"],
            "enforcement_decisions": len(approvals["records"]),
            "artifacts_referenced": len(artifacts["artifacts"]),
            "rows_redacted": approvals["redaction"]["rows_redacted"],
        },
        "limits": limits,
    }


def format_bundle(result: dict) -> str:
    """Human-readable summary of a written bundle."""
    lines = [classification_header(result["classification"])["banner"],
             f"Bundle: {result['bundle_dir']}",
             f"Bundle id: {result['bundle_id']}",
             f"Bundle digest: {result['bundle_digest']}",
             f"Session: {result['session_id']}",
             f"Created at: {result['created_at']}",
             ""]
    for member in result["members"]:
        lines.append(f"  {member['member']}  ({member['records']} records)")
    lines.append(f"  {MANIFEST_NAME}")
    lines.append("")
    lines.append(f"{result['counts']['entries']} timeline entries from "
                 f"{result['counts']['sources_joined']} sources, "
                 f"{result['counts']['enforcement_decisions']} enforcement decisions, "
                 f"{result['counts']['artifacts_referenced']} artifacts referenced")
    if result["counts"]["rows_redacted"]:
        lines.append(f"{result['counts']['rows_redacted']} decision rows carry "
                     "redacted free text")
    if result["limits"]:
        lines.append("")
        lines.append("Limits:")
        for limit in result["limits"]:
            lines.append(f"  * {limit}")
    lines.append("")
    lines.append("Verify with: python tools/agent_case/bundle_verifier.py --bundle "
                 f"{result['bundle_dir']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Export an agent session as a portable SHA-256-manifested case bundle.")
    parser.add_argument("--session", required=True, help="session_id to export")
    parser.add_argument("--out", required=True, help="Bundle directory to write")
    parser.add_argument("--since", help="Inclusive ISO-8601 lower bound")
    parser.add_argument("--until", help="Inclusive ISO-8601 upper bound")
    parser.add_argument("--limit", type=int, help="Max rows per source")
    parser.add_argument("--force", action="store_true",
                        help="Replace an existing bundle in --out")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    result = build_case_bundle(args.session, args.out, since=args.since,
                               until=args.until, limit=args.limit,
                               overwrite=args.force)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_bundle(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
