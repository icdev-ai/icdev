# CUI // SP-CTI
"""Document Modernization Engine — scan pipeline.

For each DIC document's latest approved version: pull text (rag chunks via
dic_chunk_links, falling back to dic_sections for docs ingested without
chunking), run every enabled domain pack (extract → evaluate → recommend),
and persist findings.

Append-only discipline: findings never mutate. A state change or resolution
is a NEW row whose ``supersedes_id`` points at the superseded row for the
same ``dedupe_key``. ``get_findings()`` in the package __init__ resolves the
latest state per chain.

Incremental sweeps: a document is skipped when neither its approved version
nor the combined pack ``evidence_snapshot()`` hash changed since the last
scan (docmod_doc_scan_state).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

from .base_pack import ChunkRef, DomainPack
from .pack_loader import load_config, load_packs

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


def _evidence_connect():
    """RLS-free connection for evidence reads.

    Evidence spans tables without tenant_id/classification columns
    (mc_net_eol_data, ni_devices, kg corroboration): under the dashboard's
    Flask security context the RLS predicate raises UndefinedColumn on them,
    and any rollback of a SHARED write connection silently discards the
    scan-run row -> FK violations on the first finding insert. Evidence is
    read-only backend context, so the canvas (security_context=None)
    connection is the correct isolation."""
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection()


def _reset_evidence_run() -> None:
    """Re-arm the governed evidence seam for a new scan run. Never fatal."""
    try:
        from .evidence import reset_run_state

        reset_run_state()
    except Exception as exc:  # noqa: BLE001 — the seam must never fail a scan
        logger.debug("docmod: evidence seam reset unavailable: %s", exc)


def _enrich_findings_enabled(config: dict) -> bool:
    """Is per-finding evidence enrichment live?

    TWO conditions, not one: the master ``cortex.enabled`` toggle AND
    ``cortex.enrich_findings``. Separate because they have different blast
    radii — the pack-level lookups swap one store call for a governed one and
    cost a resolution per DISTINCT entity, while enrichment costs one per
    finding and writes into every ``evidence_json`` the corpus holds. A
    deployment must be able to take the first without the second.
    """
    try:
        from .evidence import cortex_config, cortex_enabled

        return bool(
            cortex_enabled(config)
            and cortex_config(config).get("enrich_findings", True)
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("docmod: evidence seam unavailable: %s", exc)
        return False


def dedupe_key(doc_id: str, pack_id: str, entity_label: str, finding_type: str) -> str:
    raw = f"{doc_id}|{pack_id}|{entity_label.lower().strip()}|{finding_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def combined_evidence_hash(packs: dict[str, DomainPack], conn) -> str:
    parts = []
    for pid in sorted(packs):
        try:
            parts.append(f"{pid}:{packs[pid].evidence_snapshot(conn)}")
        except Exception as exc:
            logger.warning("docmod: evidence_snapshot failed for %s: %s", pid, exc)
            try:
                conn.rollback()  # PG: failed statement poisons the transaction
            except Exception:
                pass
            parts.append(f"{pid}:error")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _latest_approved_version(conn, doc_id: str) -> str | None:
    row = conn.execute(
        "SELECT version_id FROM dic_versions WHERE doc_id=%s AND status='approved' "
        "ORDER BY version_no DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    return dict(row)["version_id"] if row else None


def _doc_chunks(conn, doc_id: str, version_id: str) -> list[tuple[str, ChunkRef]]:
    """(text, ChunkRef) pairs — rag chunks first, dic_sections fallback."""
    chunks: list[tuple[str, ChunkRef]] = []
    try:
        rows = conn.execute(
            "SELECT dcl.link_id, dcl.page, dcl.section, rc.content "
            "FROM dic_chunk_links dcl JOIN rag_chunks rc ON dcl.rag_chunk_id = rc.id "
            "WHERE dcl.version_id = %s",
            (version_id,),
        ).fetchall()
    except Exception as exc:
        # PG poisons the whole transaction after a failed statement — roll back
        # or every later query fails with InFailedSqlTransaction. Never swallow
        # silently: this exact pattern hid an UndefinedColumn in production.
        logger.warning("docmod: chunk-link query failed (%s) — falling back to sections", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        rows = []
    for r in rows:
        d = dict(r)
        if d.get("content"):
            chunks.append((
                d["content"],
                ChunkRef(doc_id=doc_id, version_id=version_id,
                         chunk_link_id=str(d.get("link_id") or ""),
                         page=d.get("page"), section=d.get("section")),
            ))
    if chunks:
        return chunks
    # Fallback: section content (e.g. docs imported via the docgen bridge).
    try:
        rows = conn.execute(
            "SELECT section_id, heading, content FROM dic_sections "
            "WHERE version_id = %s ORDER BY section_id",
            (version_id,),
        ).fetchall()
    except Exception as exc:
        logger.warning("docmod: section fallback query failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        rows = []
    for r in rows:
        d = dict(r)
        if d.get("content"):
            chunks.append((
                d["content"],
                ChunkRef(doc_id=doc_id, version_id=version_id,
                         chunk_link_id=None, section=d.get("heading")),
            ))
    return chunks


def _open_findings(conn, doc_id: str) -> dict[str, dict]:
    """Latest-state open findings for a doc, keyed by dedupe_key."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM docmod_findings WHERE doc_id = %s ORDER BY created_at",
        (doc_id,),
    ).fetchall()]
    latest: dict[str, dict] = {}
    for r in rows:  # created_at ascending — later rows supersede earlier ones
        if r.get("dedupe_key"):
            latest[r["dedupe_key"]] = r
    return {k: v for k, v in latest.items() if v.get("state") == "open"}


def _insert_finding(conn, run_id: str, doc_id: str, version_id: str, entity, verdict,
                    replacement, key: str, tenant_id: str | None,
                    classification: str | None, state: str = "open",
                    supersedes_id: str | None = None) -> str:
    fid = f"fnd-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO docmod_findings
           (finding_id, run_id, doc_id, version_id, chunk_link_id, section_heading,
            page, pack_id, entity_label, entity_type, finding_type, currency_verdict,
            severity, rationale, evidence_json, recommended_replacement,
            replacement_evidence_json, confidence, state, supersedes_id,
            dedupe_key, created_at, tenant_id, classification)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            fid, run_id, doc_id, version_id,
            entity.chunk_ref.chunk_link_id, entity.chunk_ref.section,
            entity.chunk_ref.page, entity.pack_id, entity.label, entity.entity_type,
            verdict.finding_type, verdict.currency_verdict, verdict.severity,
            verdict.rationale, json.dumps(verdict.evidence, default=str),
            replacement.label if replacement else None,
            json.dumps(replacement.evidence, default=str) if replacement else None,
            verdict.confidence, state, supersedes_id, key, _now(),
            tenant_id, classification,
        ),
    )
    return fid


def _enrich_evidence(verdict, entity, tenant_id, classification) -> str:
    """Attach the GOVERNED resolution's citations to a finding (cef-di-01).

    The scanner half of the migration, and the half that covers every pack at
    once: a pack keeps whatever hand-written evidence read its verdict needs,
    and the finding it produced additionally carries the citations
    ``cortex.resolve`` gathered for that entity across the currency store, RAG,
    DIC, the knowledge graph and the KB — rungs no pack reaches on its own.

    Three properties, each deliberate:

    * It runs AFTER ``evaluate()`` and touches ``evidence`` only. The verdict,
      the severity, the finding type, the confidence and the ``dedupe_key`` are
      all already fixed, so a toggle-on rescan produces the SAME finding SET as
      a toggle-off one, with more evidence on each row. That is what makes the
      before/after comparison this card asks for a comparison at all.
    * It runs only for entities that produced a FINDING. A corpus sweep names
      thousands of candidates and roughly a hundred become findings; resolving
      every candidate would be a five-backend fan-out per mention for evidence
      no reviewer ever opens.
    * It never raises. An unreachable Cortex, a refused resolution or a dead
      backend leaves the finding exactly as the pack wrote it.

    Returns the ``via`` marker (empty when nothing was attached), so the caller
    can report how many findings the seam actually enriched instead of assuming.
    """
    try:
        from . import evidence as docmod_evidence

        bundle = docmod_evidence.resolve_evidence(
            entity.label,
            entity_type=entity.entity_type,
            tenant_id=tenant_id,
            classification=classification,
        )
    except Exception as exc:  # noqa: BLE001 — evidence enrichment never fails a scan
        logger.warning("docmod: cortex evidence enrichment failed: %s", exc)
        return ""
    if bundle is None or not bundle.citations:
        return ""
    known = {str(e.get("source") or "") for e in (verdict.evidence or [])}
    added = [c for c in bundle.citations if c["source"] not in known]
    if not added:
        return ""
    verdict.evidence = list(verdict.evidence or []) + [
        {**c, "via": "cortex.resolve"} for c in added
    ]
    return "cortex.resolve"


def scan_document(doc_id: str, conn=None, packs: dict[str, DomainPack] | None = None,
                  run_id: str | None = None, force: bool = False) -> dict:
    """Scan one document. Returns {doc_id, scanned, findings_new, findings_resolved}."""
    own_conn = conn is None
    if own_conn:
        conn = _connect()
    if run_id is None:
        # Top-level scan: re-arm the governed seam's per-run memo cache and
        # outbound budget. Per RUN rather than per process — the cache holds
        # live database state, and memoising it for the lifetime of a dashboard
        # worker would make a catalog edit invisible until restart.
        _reset_evidence_run()
    try:
        packs = packs or load_packs()
        version_id = _latest_approved_version(conn, doc_id)
        if not version_id:
            return {"doc_id": doc_id, "scanned": False, "reason": "no approved version"}

        ev_conn = _evidence_connect()
        evidence_hash = combined_evidence_hash(packs, ev_conn)
        state_row = conn.execute(
            "SELECT last_version_id, last_evidence_hash FROM docmod_doc_scan_state WHERE doc_id=%s",
            (doc_id,),
        ).fetchone()
        if state_row and not force:
            s = dict(state_row)
            if s.get("last_version_id") == version_id and s.get("last_evidence_hash") == evidence_hash:
                return {"doc_id": doc_id, "scanned": False, "reason": "unchanged"}

        doc_row = conn.execute(
            "SELECT tenant_id, classification FROM dic_documents WHERE doc_id=%s", (doc_id,)
        ).fetchone()
        tenant_id = dict(doc_row).get("tenant_id") if doc_row else None
        classification = dict(doc_row).get("classification") if doc_row else None

        own_run = run_id is None
        started_at = _now()
        if own_run:
            # docmod_findings.run_id FK-references docmod_scan_runs, so the run
            # row must exist BEFORE findings insert. Counters get one completion
            # UPDATE below — the sole sanctioned exception to append-only for
            # this table (rows are still never deleted or rewritten otherwise).
            run_id = f"run-{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT INTO docmod_scan_runs
                   (run_id, scope_type, scope_id, pack_ids, evidence_hash,
                    triggered_by, started_at, tenant_id, classification)
                   VALUES (%s,'doc',%s,%s,%s,'manual',%s,%s,%s)""",
                (run_id, doc_id, json.dumps(sorted(packs)), evidence_hash,
                 started_at, tenant_id, classification),
            )

        engine_config = load_config()
        threshold = float(engine_config.get("confidence_threshold", 0.0) or 0.0)
        enrich = _enrich_findings_enabled(engine_config)
        existing_open = _open_findings(conn, doc_id)
        seen_keys: set[str] = set()
        findings_new = 0
        findings_enriched = 0

        # Packs read evidence on the SEPARATE RLS-free connection created above:
        # on PostgreSQL a single failed statement aborts the whole transaction,
        # so a pack's evidence error must never poison the write connection
        # that carries the scan-run row and finding inserts.
        def _ev_rollback():
            try:
                ev_conn.rollback()
            except Exception:
                pass

        # Materialize once: the same (text, ChunkRef) list feeds both the finding
        # scan and the toggle-gated claim extraction below (no second fetch).
        doc_chunks = _doc_chunks(ev_conn, doc_id, version_id)
        try:
            for text, chunk_ref in doc_chunks:
                for pack in packs.values():
                    try:
                        entities = pack.extract(text, chunk_ref)
                    except Exception as exc:
                        logger.warning("docmod: %s.extract failed: %s", pack.pack_id, exc)
                        _ev_rollback()
                        continue
                    for entity in entities:
                        try:
                            verdict = pack.evaluate(entity, ev_conn)
                        except Exception as exc:
                            logger.warning("docmod: %s.evaluate failed: %s", pack.pack_id, exc)
                            _ev_rollback()
                            continue
                        if not verdict.is_finding or verdict.confidence < threshold:
                            continue
                        key = dedupe_key(doc_id, pack.pack_id, entity.label, verdict.finding_type)
                        if key in seen_keys or key in existing_open:
                            seen_keys.add(key)
                            continue
                        seen_keys.add(key)
                        # cef-di-01 — governed evidence, attached AFTER the
                        # verdict is fixed. Adds citations; changes no verdict,
                        # no severity and no dedupe_key.
                        if enrich and _enrich_evidence(
                            verdict, entity, tenant_id, classification
                        ):
                            findings_enriched += 1
                        replacement = None
                        try:
                            replacement = pack.recommend(entity, verdict, ev_conn)
                        except Exception as exc:
                            logger.warning("docmod: %s.recommend failed: %s", pack.pack_id, exc)
                            _ev_rollback()
                        _insert_finding(conn, run_id, doc_id, version_id, entity, verdict,
                                        replacement, key, tenant_id, classification)
                        findings_new += 1
        finally:
            try:
                ev_conn.close()
            except Exception:
                pass

        # Resolve open findings whose entity no longer matches (append supersede row).
        findings_resolved = 0
        for key, row in existing_open.items():
            if key in seen_keys:
                continue
            conn.execute(
                """INSERT INTO docmod_findings
                   (finding_id, run_id, doc_id, version_id, pack_id, entity_label,
                    entity_type, finding_type, currency_verdict, severity, rationale,
                    confidence, state, supersedes_id, dedupe_key, created_at,
                    tenant_id, classification)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'superseded',%s,%s,%s,%s,%s)""",
                (
                    f"fnd-{uuid.uuid4().hex[:12]}", run_id, doc_id, version_id,
                    row["pack_id"], row["entity_label"], row.get("entity_type"),
                    row["finding_type"], "current", row.get("severity") or "medium",
                    "entity no longer present or now current", row.get("confidence"),
                    row["finding_id"], key, _now(), tenant_id, classification,
                ),
            )
            findings_resolved += 1

        open_count = len(_open_findings(conn, doc_id))
        # scan-state is a mutable bookkeeping table (NOT append-only)
        conn.execute("DELETE FROM docmod_doc_scan_state WHERE doc_id=%s", (doc_id,))
        conn.execute(
            """INSERT INTO docmod_doc_scan_state
               (doc_id, last_version_id, last_evidence_hash, last_scanned_at,
                open_findings, tenant_id, classification)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (doc_id, version_id, evidence_hash, _now(), open_count, tenant_id, classification),
        )
        if own_run:
            conn.execute(
                "UPDATE docmod_scan_runs SET docs_scanned=1, findings_new=%s, "
                "findings_resolved=%s, finished_at=%s WHERE run_id=%s",
                (findings_new, findings_resolved, _now(), run_id),
            )
        conn.commit()
        # Semantic claim extraction (dmx-claims-02) — toggle-gated (default OFF).
        # Isolated on its own connection/transaction so a claim failure can never
        # roll back committed findings. Reached only when the doc actually
        # re-scanned (the docmod_doc_scan_state skip above returns early for an
        # unchanged version), so extraction fires once per NEW approved version.
        _maybe_extract_claims(doc_id, version_id, doc_chunks, tenant_id, classification)
        return {"doc_id": doc_id, "scanned": True, "findings_new": findings_new,
                "findings_resolved": findings_resolved, "open_findings": open_count,
                # How many findings the governed seam actually enriched. Reported
                # rather than assumed: `enrich` being on and the seam having
                # answered are different facts, and a scan that enriched nothing
                # must not read like one that enriched everything.
                "findings_enriched": findings_enriched}
    finally:
        if own_conn:
            conn.close()


def _maybe_extract_claims(doc_id: str, version_id: str, doc_chunks, tenant_id, classification) -> None:
    """Run toggle-gated claim extraction + lifecycle on an isolated connection.

    A no-op unless ``claims.enabled`` is true (default false): the extractor is
    not even called. Deterministic-first — the LLM only proposes claim structure;
    claims land ``pending_review`` (HITL). Air-gap degrades to the rulebook path.

    Three steps, all on an isolated connection so a claim failure can never roll
    back committed findings (never fatal):
      1. Extract new claims (idempotent per approved version).
      2. Phase D linkage — flag ``active`` claims invalidated by the findings
         just written (the deterministic edge: only a finding row flips a claim).
      3. Phase D verify — auto-``superseded`` claims whose anchor drifted.
    """
    try:
        cfg = load_config()
        if not (cfg.get("claims") or {}).get("enabled"):
            return
        from .claim_extractor import extract_and_persist_claims
        from .claim_lifecycle import link_findings_to_claims, verify_claim_anchors

        claims_conn = _connect()
        try:
            extract_and_persist_claims(
                claims_conn, doc_id, version_id, doc_chunks,
                config=cfg, tenant_id=tenant_id, classification=classification,
            )
            # Deterministic linkage + anchor verification run every scan (not only
            # on a new version): a rulebook/evidence change re-scans this doc,
            # producing the finding that flags an already-active claim.
            link_findings_to_claims(claims_conn, doc_id, version_id)
            chunk_texts = {
                ref.chunk_link_id: text
                for text, ref in doc_chunks
                if getattr(ref, "chunk_link_id", None)
            }
            verify_claim_anchors(claims_conn, doc_id, chunk_texts)
            claims_conn.commit()
        finally:
            claims_conn.close()
    except Exception as exc:  # noqa: BLE001 — claim extraction must never fail a scan
        logger.warning("docmod: claim extraction failed for %s: %s", doc_id, exc)


def scan_collection(collection_id: str | None = None, trigger: str = "manual",
                    force: bool = False) -> dict:
    """Scan every document in a collection (or the whole corpus when None)."""
    # Re-arm the governed evidence seam once for the whole sweep, so an entity
    # named by twenty documents costs ONE resolution rather than twenty.
    _reset_evidence_run()
    conn = _connect()
    try:
        packs = load_packs()
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        started_at = _now()
        ev = _evidence_connect()
        try:
            evidence_hash = combined_evidence_hash(packs, ev)
        finally:
            try:
                ev.close()
            except Exception:
                pass
        # Run row first — findings FK-reference it (see scan_document note).
        conn.execute(
            """INSERT INTO docmod_scan_runs
               (run_id, scope_type, scope_id, pack_ids, evidence_hash,
                triggered_by, started_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, "collection" if collection_id else "all", collection_id,
             json.dumps(sorted(packs)), evidence_hash, trigger, started_at),
        )
        if collection_id:
            rows = conn.execute(
                "SELECT doc_id FROM dic_documents WHERE collection_id=%s", (collection_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT doc_id FROM dic_documents").fetchall()
        doc_ids = [dict(r)["doc_id"] for r in rows]

        scanned = new = resolved = 0
        for doc_id in doc_ids:
            result = scan_document(doc_id, conn=conn, packs=packs, run_id=run_id, force=force)
            if result.get("scanned"):
                scanned += 1
                new += result.get("findings_new", 0)
                resolved += result.get("findings_resolved", 0)
        conn.execute(
            "UPDATE docmod_scan_runs SET docs_scanned=%s, findings_new=%s, "
            "findings_resolved=%s, finished_at=%s WHERE run_id=%s",
            (scanned, new, resolved, _now(), run_id),
        )
        conn.commit()
        return {"run_id": run_id, "docs_total": len(doc_ids), "docs_scanned": scanned,
                "findings_new": new, "findings_resolved": resolved}
    finally:
        conn.close()
