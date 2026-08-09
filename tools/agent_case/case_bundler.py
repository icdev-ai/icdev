#!/usr/bin/env python3
# CUI // SP-CTI
"""AGOV CASE — write an agent session out as a portable, SHA-256-manifested bundle.

The bundle is the unit of handoff: a directory that can be copied to a machine
that never had the source database and still be verified there. Everything the
verifier needs travels with it, which is why the record files carry raw stored
values (``hook_events.payload``, ``hook_events.signature``,
``audit_trail.hash``) rather than anything re-derived at export time.

Layout, and every path here is the constant from
:mod:`tools.agent_case.bundle_format` rather than a literal, so the writer and
the verifier cannot drift::

    <bundle>/
      manifest.json              # SHA-256 of every other member
      timeline.json              # the ordered join (session_timeline)
      records/hook_events.json   # {"table", "records"}
      records/audit_trail.json   # {"table", "records", "chain_context"}
      records/agent_findings.json  # only when the table exists

``chain_context`` is the one derived field, and it exists because the
migration-149 hash chain links row N to row N-1: a session's audit rows are a
slice out of the middle of that chain, so the predecessor of the first row is
outside the bundle. Its hash is looked up once at export time and anchored, and
without it the verifier can only report "predecessor outside the slice".

Byte-stability is load-bearing. Members are written with ``newline="\\n"`` and
``sort_keys=True`` so a bundle written on Windows and verified on Linux has
identical member digests — the manifest hashes raw bytes, and CRLF would break
every one of them.

Usage:
    from tools.agent_case.case_bundler import build_case_bundle
    result = build_case_bundle("sess-abc123", "out/case-sess-abc123")
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
    build_manifest,
    write_manifest,
)
from tools.agent_case.session_timeline import (  # noqa: E402
    _row_to_dict,
    build_timeline,
)
from tools.db.storage import (  # noqa: E402
    get_connection,
    sql_placeholder,
    table_exists,
)

TIMELINE_MEMBER = "timeline.json"
FINDINGS_MEMBER = "records/agent_findings.json"

# timeline source table -> bundle member it is written to.
MEMBER_FOR_SOURCE = {
    "hook_events": HOOK_EVENTS_MEMBER,
    "audit_trail": AUDIT_TRAIL_MEMBER,
    "agent_findings": FINDINGS_MEMBER,
}


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

    if "hash" not in _audit_columns(conn):
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


def _audit_columns(conn) -> set:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM audit_trail WHERE 1=0")
    return {d[0] for d in (cursor.description or ())}


def build_case_bundle(session_id: str, out_dir, conn=None, since: str = None,
                      until: str = None, limit: int = None,
                      overwrite: bool = False) -> dict:
    """Write a case bundle for one session and return what was written.

    Args:
        session_id: Session to export.
        out_dir: Bundle directory. Created if absent.
        conn: Optional open connection.
        since / until / limit: Passed to :func:`build_timeline`; a windowed
            bundle records its window in the manifest so a reader can tell a
            slice from a whole session.
        overwrite: Required to write into a directory that already holds a
            manifest. A bundle is evidence; silently merging a new export over
            an old one would produce a directory whose manifest describes some
            files and not others.

    Returns:
        dict with ``bundle_dir``, ``bundle_id``, ``members``, ``counts`` and the
        ``limits`` carried through from the timeline.
    """
    if not session_id:
        raise ValueError("session_id is required")
    bundle_dir = Path(out_dir)
    if (bundle_dir / "manifest.json").exists() and not overwrite:
        raise FileExistsError(
            f"{bundle_dir} already holds a manifest.json; pass overwrite=True "
            "(--force) to replace that bundle"
        )
    bundle_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(timezone.utc).isoformat()
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        timeline = build_timeline(session_id, conn=conn, since=since, until=until,
                                 limit=limit)

        by_source = {}
        for entry in timeline["entries"]:
            by_source.setdefault(entry["source"], []).append(entry["record"])

        members = []
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
            members.append({"member": member, "table": source, "records": len(records)})
    finally:
        if owns_conn:
            conn.close()

    _write_member(bundle_dir, TIMELINE_MEMBER, timeline)
    members.append({"member": TIMELINE_MEMBER, "table": None,
                    "records": len(timeline["entries"])})

    bundle_id = _bundle_id(session_id, created_at)
    manifest = build_manifest(
        bundle_dir,
        bundle_id=bundle_id,
        session_id=session_id,
        created_at=created_at,
        extra={
            "producer": "tools/agent_case/case_bundler.py",
            "window": timeline["window"],
            "limits": timeline["limits"],
        },
    )
    write_manifest(bundle_dir, manifest)

    return {
        "bundle_dir": str(bundle_dir),
        "bundle_id": bundle_id,
        "session_id": session_id,
        "created_at": created_at,
        "classification": "CUI",
        "members": members,
        "counts": {
            "members": len(manifest["members"]),
            "entries": timeline["counts"]["entries"],
            "sources_joined": timeline["counts"]["sources_joined"],
        },
        "limits": timeline["limits"],
    }


def format_bundle(result: dict) -> str:
    """Human-readable summary of a written bundle."""
    lines = ["CUI // SP-CTI",
             f"Bundle: {result['bundle_dir']}",
             f"Bundle id: {result['bundle_id']}",
             f"Session: {result['session_id']}",
             f"Created at: {result['created_at']}",
             ""]
    for member in result["members"]:
        lines.append(f"  {member['member']}  ({member['records']} records)")
    lines.append("  manifest.json")
    lines.append("")
    lines.append(f"{result['counts']['entries']} timeline entries from "
                 f"{result['counts']['sources_joined']} sources")
    if result["limits"]:
        lines.append("")
        lines.append("Limits:")
        for limit in result["limits"]:
            lines.append(f"  * {limit}")
    lines.append("")
    lines.append("Verify with: python tools/agent_case/cli.py verify --bundle "
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
