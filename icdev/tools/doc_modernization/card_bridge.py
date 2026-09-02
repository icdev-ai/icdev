# CUI // SP-CTI
"""Kanban HITL surface — per-document rollup predictions.

Writes one oracle_predictions row per document with open modernization
findings (insert shape mirrors tools/awareness/drift_detector.py) so
tools/awareness/suggested_card_writer.py promotes them to kanban_tasks
status='suggested' with its existing dedup/consolidation/auto-dismiss —
no changes to the card writer.

Sub-threshold findings (confidence < docmod_config.confidence_threshold)
never contribute to a rollup (plan risk rule). Nor does a document in a
collection DECLARED not to be a live corpus (args/docmod/demo_collections.yaml)
-- see demo_collections() for why that is a declaration and not a heuristic. reconcile() closes predictions
whose findings are all resolved so the board never shows stale cards.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from icdev.core.paths import repo_root

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# lens_name MUST be 'internal_awareness' — tools/awareness/suggested_card_writer.py
# promotes rows WHERE lens_name = <awareness_config oracle.lens_name>; a custom
# lens_name would never reach the kanban board. lens_id carries the docmod marker.
LENS_ID = "doc_modernization"
LENS_NAME = "internal_awareness"
PREDICTION_PREFIX = "modernization::"

_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


# --------------------------------------------------------------------------- #
# demo corpus (cdh-seed-01)
# --------------------------------------------------------------------------- #
# A DECLARATION, never a heuristic. Matching 'demo'/'test' in a collection name
# would have missed `Politics` and `col1` -- two of the four collections that
# actually filed cards -- and would wrongly exclude a real collection somebody
# called 'Test Estate'. A declaration is a claim somebody made and can be read
# back; a heuristic is one nobody checked.
#
# A DENYLIST, so an UNDECLARED collection still files cards. An allowlist of
# live collections would go silent the first time somebody forgot to declare
# one, and silently dropping a real finding is the worse failure for a queue
# whose whole job is surfacing them.
# repo_root(), never Path(__file__).parents[N]: this module ships in BOTH
# tools/ and icdev/tools/, at two different depths, so a counted walk is
# right in one copy and silently wrong in the other (xit-decl-03).
_DEMO_COLLECTIONS_PATH = (
    repo_root(__file__) / "args" / "docmod" / "demo_collections.yaml"
)


def demo_collections(path=None) -> frozenset:
    """Collection ids declared NOT to be a live corpus.

    FAILS OPEN. A missing, unreadable or malformed declaration returns the empty
    set, so the seeder keeps filing every card it would have filed before. A
    findings queue that goes quiet because a config file broke is this card's
    own defect one level up.
    """
    try:
        import yaml
        raw = yaml.safe_load(
            Path(path or _DEMO_COLLECTIONS_PATH).read_text(encoding="utf-8")
        ) or {}
        entries = raw.get("collections") or {}
        return frozenset(str(k) for k in entries)
    except Exception as exc:  # noqa: BLE001 - fail open, but never silently
        logger.warning("demo-collection declaration unreadable (%s); denying nothing", exc)
        return frozenset()


def resolve_demo_collection_ids(conn, declared=None) -> frozenset:
    """The declared strings, plus the collection_id of every collection they NAME.

    WHY THIS IS NEEDED, and it is not a hypothetical. `dic_documents.collection_id`
    holds an opaque id; the human-readable label lives in `dic_collections.name`.
    Measured on the live board 2026-09-01, after the id-only version merged:

        801d27077c1444ddd4864757 -> 'Politics'  (constitution.pdf, ArtOfWar.pdf, and
                                                 'The First Amendment to the United
                                                 States Constitution')
        d92716e5c128623f0e9fd1b1 -> 'test'      (tmp9x41vmaz)

    Two of the four declared entries were names, so the gate skipped 19 findings
    and let 108 through -- including the two documents the card was written about.
    The unit tests could not have caught it: they passed an id in and asserted set
    membership, which is the mechanism working. The defect was in what a
    DECLARATION means.

    A LOOKUP, NOT A HEURISTIC. Asking the database which collection somebody named
    is not pattern-matching a name for 'test'. Matching is EXACT -- 'test' must
    never drag in 'citation-test-coll'. A name shared by two collections resolves
    to BOTH: a denylist naming a collection means all of it, and picking one id
    would half-apply the declaration.

    FAILS OPEN. An unreadable or absent `dic_collections` resolves nothing extra
    and the declared strings still match by id, which is exactly what shipped
    first -- never fewer matches than before.
    """
    names = frozenset(declared if declared is not None else demo_collections())
    if not names:
        return frozenset()
    resolved = set(names)
    try:
        rows = conn.execute(
            "SELECT collection_id, name FROM dic_collections"
        ).fetchall()
        for row in rows:
            r = dict(row)
            if r.get("name") in names and r.get("collection_id"):
                resolved.add(str(r["collection_id"]))
    except Exception as exc:  # noqa: BLE001 - fail open, but never silently
        logger.warning("could not resolve demo collection names (%s); matching by id only", exc)
    return frozenset(resolved)


def _is_demo_document(collection_id, resolved=None) -> bool:
    """True only for a collection the declaration NAMES (by id or by name).

    An empty or unknown collection is False -- not knowing which corpus a
    document belongs to is a reason to file the card, not to drop it.

    `resolved` is the set from resolve_demo_collection_ids(); omitting it falls
    back to id-only matching, which is correct but weaker, so emit_rollups always
    passes one.
    """
    if not collection_id:
        return False
    return str(collection_id) in (resolved if resolved is not None else demo_collections())


# 'Awaiting review' includes drafted redlines — a finding with a pending
# redline still needs a human; its kanban card must stay open.
_AWAITING_STATES = ("open", "redline_drafted")


def emit_rollups(conn=None) -> dict:
    """One prediction per document with awaiting-review, above-threshold findings."""
    from tools.doc_modernization import get_findings
    from tools.doc_modernization.pack_loader import load_config

    own = conn is None
    if own:
        conn = _connect()
    try:
        threshold = float(load_config().get("confidence_threshold", 0.7) or 0.7)
        open_findings = [
            f for f in get_findings(conn=conn)
            if f.get("state") in _AWAITING_STATES
            and (f.get("confidence") or 0) >= threshold
        ]
        by_doc: dict[str, list[dict]] = {}
        for f in open_findings:
            by_doc.setdefault(f["doc_id"], []).append(f)

        created, skipped, demo_skipped = [], 0, []
        # Resolved ONCE per run: one read of dic_collections, not one per document.
        demo_ids = resolve_demo_collection_ids(conn)
        for doc_id, findings in by_doc.items():
            # prediction-level dedup: one open rollup per document
            existing = conn.execute(
                "SELECT id FROM oracle_predictions WHERE lens_id = %s "
                "AND subject_id = %s AND prediction_type LIKE %s "
                "AND (outcome IS NULL OR outcome = '' OR outcome = 'pending')",
                (LENS_ID, doc_id, PREDICTION_PREFIX + "%"),
            ).fetchone()
            if existing:
                skipped += 1
                continue

            counts: dict[str, int] = {}
            for f in findings:
                counts[f["finding_type"]] = counts.get(f["finding_type"], 0) + 1
            dominant = max(counts, key=counts.get)
            severity = max(
                (f.get("severity") or "medium" for f in findings),
                key=lambda s: _SEVERITY_RANK.get(s, 2),
            )
            try:
                title_row = conn.execute(
                    "SELECT title, collection_id FROM dic_documents WHERE doc_id = %s",
                    (doc_id,),
                ).fetchone()
            except Exception as exc:  # noqa: BLE001 - fail open on an older schema
                logger.warning("could not read %s's collection (%s)", doc_id, exc)
                title_row = None
            row = dict(title_row) if title_row else {}
            if _is_demo_document(row.get("collection_id"), demo_ids):
                demo_skipped.append(doc_id)
                continue
            title = row.get("title") or doc_id
            summary = ", ".join(f"{v}× {k}" for k, v in sorted(counts.items()))
            text = (
                f"Document '{title}' has {len(findings)} open modernization finding(s): "
                f"{summary}. Review at /document-intelligence/doc/{doc_id}"
            )
            pred_id = f"pred-{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO oracle_predictions "
                "(id, lens_id, lens_name, prediction_text, confidence, "
                " created_at, subject_type, subject_id, prediction_type, "
                " severity, horizon_days, evidence_json, classification) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    pred_id, LENS_ID, LENS_NAME, text,
                    max(float(f.get("confidence") or 0) for f in findings),
                    _now(), "dic_document", doc_id,
                    f"{PREDICTION_PREFIX}{dominant}", severity, 0,
                    json.dumps({
                        "finding_ids": [f["finding_id"] for f in findings],
                        "counts": counts,
                        "doc_link": f"/document-intelligence/doc/{doc_id}",
                    }, ensure_ascii=False),
                    findings[0].get("classification") or "CUI // SP-CTI",
                ),
            )
            created.append(pred_id)
        conn.commit()
        # demo_skipped is REPORTED, never silent: a seeder that quietly drops
        # documents cannot be told apart from one that found nothing.
        return {"created": len(created), "skipped_existing": skipped,
                "demo_skipped": len(demo_skipped),
                "demo_skipped_docs": demo_skipped,
                "docs_with_findings": len(by_doc), "prediction_ids": created}
    finally:
        if own:
            conn.close()


def reconcile(conn=None) -> dict:
    """Close open doc_modernization predictions whose documents no longer have
    open findings (accepted/rejected/superseded) so suggested cards go stale-free."""
    from tools.doc_modernization import get_findings

    own = conn is None
    if own:
        conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, subject_id FROM oracle_predictions WHERE lens_id = %s "
            "AND prediction_type LIKE %s "
            "AND (outcome IS NULL OR outcome = '' OR outcome = 'pending')",
            (LENS_ID, PREDICTION_PREFIX + "%"),
        ).fetchall()]
        closed = []
        for r in rows:
            still_open = [f for f in get_findings(doc_id=r["subject_id"], conn=conn)
                          if f.get("state") in _AWAITING_STATES]
            if not still_open:
                # 'confirmed' per the oracle outcome vocabulary — the predicted
                # modernization need was addressed.
                conn.execute(
                    "UPDATE oracle_predictions SET outcome = 'confirmed', "
                    "outcome_at = %s WHERE id = %s",
                    (_now(), r["id"]),
                )
                closed.append(r["id"])
        conn.commit()
        return {"open_rollups": len(rows), "closed": len(closed)}
    finally:
        if own:
            conn.close()
