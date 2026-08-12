#!/usr/bin/env python3
# CUI // SP-CTI
"""Whole-chain integrity sweep for ``audit_trail`` — verified / pre-cutover / broken.

``provenance_verifier.verify_audit_integrity`` answers "is row N intact?". That is
the wrong shape for the question an auditor actually asks, which is "has anything
in this table been tampered with?" — you cannot answer it by calling a per-row
verifier 80,000 times. This module walks the chain once and buckets every row.

**The four buckets, and why there are four rather than three.**

``verified``     chained, the digest recomputes, and the link to ``id - 1`` holds.
``pre_cutover``  written before the chain writer existed. Its NULL hash is an
                 absence of evidence, not evidence of tampering.
``unchained``    written *after* cutover, but by a call site that INSERTs into
                 ``audit_trail`` directly instead of going through
                 ``audit_logger.log_event``. 156 files under ``tools/`` do this;
                 :mod:`tools.audit.chain` flagged surfacing the count as this
                 task's job.
``broken``       chained, and either the digest or the link does not hold. This
                 is the tamper signal.

Collapsing ``unchained`` into ``broken`` would put a few hundred rows a week into
the alarm bucket for a known structural gap, and a tamper alarm that cries wolf
is not a tamper alarm. Collapsing it into ``pre_cutover`` would be a lie — those
rows are not old, they are unwritten by the writer. So it gets its own bucket and
the page says which is which.

**Signatures are reported, never a ``broken`` determinant.** With no signing key
configured ``key_manager`` returns ``algorithm: "none"``, and on this deployment
some rows already carry exactly that. Scoring an unsigned row as broken would
paint a healthy chain 100% red the moment a key rotated or a worker started
without the secret. Tamper detection is the hash and the link; the signature is
reported alongside as its own counts.

**Cost is O(chained rows), not O(table).** A row with a NULL hash needs only its
id to bucket — it has no digest to recompute — so those two buckets come from
aggregate ``COUNT(*)``s and only chained rows are read back. On a table of 80,351
rows of which 17 are chained, that is two counts and 17 rows.

Usage:
    python tools/audit/chain_sweep.py --json
    python tools/audit/chain_sweep.py --gate          # exit 1 if any link is broken
    python tools/audit/chain_sweep.py --verify-signatures --json

    from icdev.tools.audit.chain_sweep import sweep_chain
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from icdev.tools.audit.chain import chain_start_id, unfiltered_cursor
from icdev.tools.audit.row_hash import GENESIS_HASH, compute_audit_row_hash

#: Bucket names. The dashboard keys off these strings, so they are API.
STATUS_VERIFIED = "verified"
STATUS_PRE_CUTOVER = "pre_cutover"
STATUS_UNCHAINED = "unchained"
STATUS_BROKEN = "broken"

#: Why a chained row failed. Both mean tampering; they differ in what was touched.
REASON_HASH_MISMATCH = "hash_mismatch"  # the row's own content was altered
REASON_LINK_MISMATCH = "link_mismatch"  # its predecessor was altered or removed

#: Columns the digest is computed over, plus the three chain columns.
_SWEEP_COLUMNS = (
    "id, project_id, event_type, actor, action, details, classification, "
    "ip_address, session_id, hash, previous_hash, signature"
)

#: Chained rows are read in id-ordered batches so a fully-chained table does not
#: have to fit in memory at once.
DEFAULT_BATCH_SIZE = 5000

#: How many broken links to return in full. A tamper event that breaks every row
#: must not return 80,000 samples to a dashboard; the counts stay exact.
DEFAULT_MAX_BROKEN_SAMPLES = 50

#: How many recent links the UI renders in its table.
DEFAULT_MAX_LINKS = 25


def _backend(conn) -> str:
    return getattr(conn, "_backend", "sqlite") or "sqlite"


def _placeholder(conn) -> str:
    """Parameter marker for this backend.

    Derived rather than assumed: the storage layer rewrites ``%s`` to ``?`` only
    on SQLite, so a hardcoded marker works on exactly one of the two backends.
    """
    return "%s" if _backend(conn) == "postgresql" else "?"


def _scalar(row):
    if row is None:
        return None
    try:
        return row[0]
    except (KeyError, IndexError, TypeError):
        return None


def _get(row, field):
    try:
        return row[field]
    except (KeyError, IndexError, TypeError):
        return None


def cutover_boundary(conn) -> tuple:
    """The id at which the chain starts, and how confident we are in it.

    Returns ``(boundary, source)`` where source is:

    ``marker``   read from ``audit_chain_genesis`` — authoritative, written once
                 by the chain writer at cutover.
    ``derived``  the migration has not run here, so the boundary is inferred as
                 ``MIN(id) WHERE hash IS NOT NULL``. Usable, but NOT
                 authoritative: :mod:`tools.audit.chain` records the marker
                 precisely because a derived boundary moves silently if the first
                 chained row is ever removed — which is the event a chain exists
                 to expose. Callers surface this as a caveat rather than
                 presenting a derived boundary as fact.
    ``none``     nothing is chained yet, so there is no boundary and every row is
                 pre-cutover.
    """
    # Ask whether the marker table exists BEFORE querying it. On PostgreSQL a
    # failed statement aborts the entire transaction, so every subsequent read in
    # this sweep would raise "current transaction is aborted" and the panel would
    # report `unavailable` on a perfectly healthy chain. chain_start_id contains
    # its own failure with a SAVEPOINT, but that containment cannot be relied on
    # from a pooled dashboard connection that is already inside a transaction —
    # and on this deployment the table genuinely is absent, so this is the
    # ordinary path, not an edge case. table_exists never raises for a missing
    # table, which is exactly why it is the safe thing to ask first.
    from tools.db.storage import table_exists

    if table_exists(conn, "audit_chain_genesis"):
        marker = chain_start_id(conn)
        if marker is not None:
            return marker, "marker"
    try:
        row = unfiltered_cursor(conn).execute(
            "SELECT MIN(id) FROM audit_trail WHERE hash IS NOT NULL"
        ).fetchone()
        low = _scalar(row)
    except Exception:
        low = None
    if low is None:
        return None, "none"
    return int(low), "derived"


def _signature_algorithm(raw):
    """The algorithm named in a signature blob, or None if it is unreadable."""
    if not raw:
        return None
    try:
        return (json.loads(raw) or {}).get("algorithm")
    except (ValueError, TypeError):
        return None


def _verify_signature(row_hash, raw) -> bool:
    """Cryptographically verify one signature, mirroring verify_audit_integrity."""
    algorithm = _signature_algorithm(raw)
    if not algorithm or algorithm == "none":
        return False
    try:
        from tools.crypto.key_manager import verify_payload

        sig = json.loads(raw)
        return bool(
            verify_payload(
                (row_hash or "").encode("utf-8"),
                sig.get("value", ""),
                sig.get("public_key_fp", ""),
                algorithm=algorithm,
            )
        )
    except Exception:
        return False


def _count(cur, where: str) -> int:
    try:
        return int(_scalar(cur.execute(f"SELECT COUNT(*) FROM audit_trail {where}").fetchone()) or 0)
    except Exception:
        return 0


def _unhashed_counts(conn, boundary) -> tuple:
    """(pre_cutover, unchained) for rows carrying no hash, without reading them.

    A NULL-hash row has no digest to recompute, so bucketing it needs only its id
    against the boundary. Counting in SQL keeps the sweep proportional to the
    number of *chained* rows rather than to the size of the table.
    """
    cur = unfiltered_cursor(conn)
    if boundary is None:
        # Nothing is chained, so nothing can be post-cutover-unchained.
        return _count(cur, "WHERE hash IS NULL"), 0
    ph = _placeholder(conn)
    try:
        pre = int(
            _scalar(
                cur.execute(
                    f"SELECT COUNT(*) FROM audit_trail WHERE hash IS NULL AND id < {ph}",
                    (boundary,),
                ).fetchone()
            )
            or 0
        )
        post = int(
            _scalar(
                cur.execute(
                    f"SELECT COUNT(*) FROM audit_trail WHERE hash IS NULL AND id >= {ph}",
                    (boundary,),
                ).fetchone()
            )
            or 0
        )
    except Exception:
        return 0, 0
    return pre, post


def _classify_chained(row, prev_id, prev_hash) -> tuple:
    """Bucket one chained row. Returns ``(status, reason, expected, actual)``.

    The link rule is the writer's rule restated: ``previous_hash`` must equal the
    hash of the row at ``id - 1``, falling back to GENESIS when that row is
    absent or itself unchained. Writer, per-row verifier and this sweep therefore
    agree by construction — if they did not, a healthy chain would read as broken
    everywhere.
    """
    row_id = int(_get(row, "id"))
    stored_hash = _get(row, "hash")

    expected_hash = compute_audit_row_hash(row)
    if stored_hash != expected_hash:
        return STATUS_BROKEN, REASON_HASH_MISMATCH, expected_hash, stored_hash

    expected_prev = prev_hash if (prev_id == row_id - 1 and prev_hash) else GENESIS_HASH
    stored_prev = _get(row, "previous_hash") or ""
    if stored_prev != expected_prev:
        return STATUS_BROKEN, REASON_LINK_MISMATCH, expected_prev, stored_prev

    return STATUS_VERIFIED, None, expected_hash, stored_hash


def sweep_chain(
    conn=None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_broken_samples: int = DEFAULT_MAX_BROKEN_SAMPLES,
    max_links: int = DEFAULT_MAX_LINKS,
    verify_signatures: bool = False,
    db_path=None,
) -> dict:
    """Walk the whole audit chain once and bucket every row.

    ``conn`` is borrowed when supplied and closed only if this function opened it,
    so a caller inside a transaction does not have its connection shut underneath.
    ``db_path`` points the sweep at a specific database — an evidence copy, or a
    tenant database — instead of the ambient one.
    """
    owns_conn = conn is None
    if owns_conn:
        from tools.db.storage import get_connection

        conn = get_connection(db_path=str(db_path)) if db_path else get_connection()

    try:
        return _sweep(conn, batch_size, max_broken_samples, max_links, verify_signatures)
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:
                pass


def _sweep(conn, batch_size, max_broken_samples, max_links, verify_signatures) -> dict:
    from tools.db.storage import table_exists

    if not table_exists(conn, "audit_trail"):
        return {
            "ok": False,
            "error": "audit_trail does not exist",
            "counts": {
                STATUS_VERIFIED: 0,
                STATUS_PRE_CUTOVER: 0,
                STATUS_UNCHAINED: 0,
                STATUS_BROKEN: 0,
            },
            "total": 0,
            "chain_health": "unavailable",
        }

    boundary, boundary_source = cutover_boundary(conn)
    pre_cutover, unchained = _unhashed_counts(conn, boundary)

    verified = broken = 0
    signed = signature_none = signature_valid = 0
    broken_samples: list = []
    links: list = []

    ph = _placeholder(conn)
    cur = unfiltered_cursor(conn)
    prev_id, prev_hash = None, None
    cursor_id = -1

    while True:
        try:
            rows = cur.execute(
                f"SELECT {_SWEEP_COLUMNS} FROM audit_trail "
                f"WHERE hash IS NOT NULL AND id > {ph} ORDER BY id LIMIT {int(batch_size)}",
                (cursor_id,),
            ).fetchall()
        except Exception as exc:
            return {
                "ok": False,
                "error": f"chain read failed: {exc}",
                "counts": {
                    STATUS_VERIFIED: verified,
                    STATUS_PRE_CUTOVER: pre_cutover,
                    STATUS_UNCHAINED: unchained,
                    STATUS_BROKEN: broken,
                },
                "total": 0,
                "chain_health": "unavailable",
            }

        if not rows:
            break

        for row in rows:
            row_id = int(_get(row, "id"))
            status, reason, expected, actual = _classify_chained(row, prev_id, prev_hash)

            if status == STATUS_VERIFIED:
                verified += 1
            else:
                broken += 1
                if len(broken_samples) < max_broken_samples:
                    broken_samples.append(
                        {
                            "id": row_id,
                            "reason": reason,
                            "expected": expected,
                            "actual": actual,
                        }
                    )

            algorithm = _signature_algorithm(_get(row, "signature"))
            if algorithm and algorithm != "none":
                signed += 1
                if verify_signatures and _verify_signature(
                    _get(row, "hash"), _get(row, "signature")
                ):
                    signature_valid += 1
            else:
                signature_none += 1

            links.append(
                {
                    "id": row_id,
                    "event_type": _get(row, "event_type"),
                    "hash": _get(row, "hash"),
                    "previous_hash": _get(row, "previous_hash"),
                    "status": status,
                    "reason": reason,
                    "signature_algorithm": algorithm or "none",
                    # A run restarts at GENESIS whenever id-1 is missing or
                    # unchained. That is expected on a sparse chain, not a fault,
                    # so the UI labels it rather than flagging it.
                    "chain_start": (_get(row, "previous_hash") or "") == GENESIS_HASH,
                }
            )
            if len(links) > max_links:
                links.pop(0)  # keep the most recent max_links

            prev_id, prev_hash = row_id, _get(row, "hash")
            cursor_id = row_id

    if broken:
        health = "broken"
    elif verified:
        health = "healthy"
    else:
        health = "no_chain"

    return {
        "ok": True,
        "chain_health": health,
        "counts": {
            STATUS_VERIFIED: verified,
            STATUS_PRE_CUTOVER: pre_cutover,
            STATUS_UNCHAINED: unchained,
            STATUS_BROKEN: broken,
        },
        "total": verified + broken + pre_cutover + unchained,
        "cutover": {
            "boundary_id": boundary,
            "source": boundary_source,
            # Only a recorded marker is authoritative. A derived boundary is a
            # best guess that would shift if the first chained row were removed.
            "authoritative": boundary_source == "marker",
        },
        "signatures": {
            "signed": signed,
            "unsigned": signature_none,
            "verified": signature_valid if verify_signatures else None,
            "checked": bool(verify_signatures),
        },
        "broken_samples": broken_samples,
        "broken_samples_truncated": broken > len(broken_samples),
        "links": links,
        "hash_algorithm": "sha256",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the whole audit_trail hash chain (exa-audit-04)."
    )
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="exit 1 if any chained link is broken (for CI / reflex use)",
    )
    parser.add_argument(
        "--verify-signatures",
        action="store_true",
        help="also cryptographically verify each signature (slower)",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--max-links", type=int, default=DEFAULT_MAX_LINKS, help="links to include in output"
    )
    parser.add_argument(
        "--db-path", default=None, help="sweep this database instead of the ambient one"
    )
    args = parser.parse_args()

    report = sweep_chain(
        batch_size=args.batch_size,
        max_links=args.max_links,
        verify_signatures=args.verify_signatures,
        db_path=args.db_path,
    )

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        counts = report.get("counts", {})
        cutover = report.get("cutover", {})
        print(f"Audit chain health: {report.get('chain_health', 'unknown').upper()}")
        print(f"  verified    : {counts.get(STATUS_VERIFIED, 0)}")
        print(f"  pre-cutover : {counts.get(STATUS_PRE_CUTOVER, 0)}  (unverifiable, not tampered)")
        print(f"  unchained   : {counts.get(STATUS_UNCHAINED, 0)}  (post-cutover, writer bypassed)")
        print(f"  BROKEN      : {counts.get(STATUS_BROKEN, 0)}")
        print(f"  cutover id  : {cutover.get('boundary_id')} (source: {cutover.get('source')})")
        if not cutover.get("authoritative") and cutover.get("source") == "derived":
            print("  NOTE: cutover derived, not recorded — run migration 20260812041301.")
        for sample in report.get("broken_samples", []):
            print(f"  BROKEN id={sample['id']} reason={sample['reason']}")
        if report.get("error"):
            print(f"  error: {report['error']}")

    if args.gate and counts_broken(report):
        return 1
    return 0


def counts_broken(report: dict) -> int:
    return int((report.get("counts") or {}).get(STATUS_BROKEN, 0) or 0)


if __name__ == "__main__":
    sys.exit(main())
