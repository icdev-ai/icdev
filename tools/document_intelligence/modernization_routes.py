# CUI // SP-CTI
"""Document Modernization routes (docmod-ux-02) — bulk legacy-doc ingest.

Registered on ``dic_bp`` via a single import at the bottom of
``tools/document_intelligence/blueprint.py``.

Routes:
  POST /document-intelligence/api/ingest/batch   multi-file upload → daemon
       thread running ingest_orchestrator.ingest_batch, streaming per-file
       progress over the existing single-ingest SSE endpoint
       (/api/ingest/<job_id>/stream) with events shaped
       {stage: "batch_file", file: n, of: total, filename, status, pct}.
"""
from __future__ import annotations

import json
import queue as _queue
import tempfile
import threading
import uuid
from pathlib import Path

from flask import jsonify, request

from tools.document_intelligence.blueprint import (
    _JOB_LOCK,
    _JOB_QUEUES,
    _JOB_RESULTS,
    _conn,
    _now,
    _security_context,
    dic_bp,
    logger,
)


@dic_bp.route("/api/ingest/batch", methods=["POST"])
def api_ingest_batch():
    """Bulk-ingest hundreds of legacy documents in one request.

    Accepts multipart form field ``files`` (repeated), saves each upload to a
    per-job temp directory, then runs ``ingest_batch`` on a daemon thread.
    Per-file progress streams over the existing SSE endpoint.
    """
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify({"error": "no files provided (use multipart field 'files')"}), 400

    collection_id = (request.form.get("collection_id") or "default").strip()
    classification = (request.form.get("classification") or "CUI").strip()
    tenant_id, _ = _security_context()

    # Save all uploads to a dedicated temp dir before the thread starts.
    batch_dir = Path(tempfile.mkdtemp(prefix="dic_batch_"))
    saved: list[Path] = []
    try:
        for f in files:
            # Keep the original name (ingest keys off it) but strip any path parts.
            name = Path(f.filename).name or f"upload_{len(saved)}"
            dest = batch_dir / name
            if dest.exists():  # duplicate names within one batch
                dest = batch_dir / f"{dest.stem}_{len(saved)}{dest.suffix}"
            f.save(str(dest))
            saved.append(dest)
    except Exception as exc:
        return jsonify({"error": f"file save failed: {exc}"}), 500

    job_id = uuid.uuid4().hex
    q: _queue.Queue = _queue.Queue()
    with _JOB_LOCK:
        _JOB_QUEUES[job_id] = q

    # Persist job record (best-effort — mirrors single-ingest behaviour).
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO dic_ingest_jobs (job_id, filename, collection_id, status, tenant_id) "
            "VALUES (%s,%s,%s,%s,%s)",
            (job_id, f"batch:{len(saved)} files", collection_id, "queued", tenant_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    filenames = [p.name for p in saved]
    total = len(saved)

    def _run():
        try:
            def _cb(n: int, of: int, anomalous: list) -> None:
                q.put({
                    "stage": "batch_file",
                    "file": n,
                    "of": of,
                    "filename": filenames[n - 1] if 0 < n <= total else "",
                    "status": "ingested",
                    "pct": int(n * 100 / max(of, 1)),
                })

            from tools.document_intelligence.ingest_orchestrator import ingest_batch
            result = ingest_batch(
                [str(p) for p in saved],
                collection_id,
                tenant_id=tenant_id,
                classification=classification,
                progress_cb=_cb,
            )
            # Terminal event — the SSE stream closes on stage=done + "chunks".
            q.put({
                "stage": "done",
                "chunks": result.succeeded,  # per-file doc count, not chunk count
                "succeeded": result.succeeded,
                "failed": result.failed,
                "total": result.total,
                "per_file": result.per_file,
                "collection_id": collection_id,
                "errors": [e["error"] for e in result.per_file if not e["ok"]],
                "pct": 100,
            })
            with _JOB_LOCK:
                _JOB_RESULTS[job_id] = {
                    "status": "done",
                    "succeeded": result.succeeded,
                    "failed": result.failed,
                    "total": result.total,
                    "per_file": result.per_file,
                    "collection_id": collection_id,
                }
            try:
                c = _conn()
                c.execute(
                    "UPDATE dic_ingest_jobs SET status='done', chunks_total=%s, chunks_done=%s, "
                    "errors_json=%s, updated_at=%s WHERE job_id=%s",
                    (result.total, result.succeeded,
                     json.dumps([e["error"] for e in result.per_file if not e["ok"]]),
                     _now(), job_id),
                )
                c.commit()
                c.close()
            except Exception:
                pass
        except Exception as exc:
            logger.warning("dic: batch ingest thread error: %s", exc)
            q.put({"stage": "error", "message": str(exc), "pct": 0})
            with _JOB_LOCK:
                _JOB_RESULTS[job_id] = {"status": "error", "message": str(exc)}
        finally:
            q.put(None)  # SSE sentinel
            try:
                import shutil
                shutil.rmtree(batch_dir, ignore_errors=True)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()

    return jsonify({
        "job_id": job_id,
        "files": total,
        "filenames": filenames,
        "collection_id": collection_id,
        "stream_url": f"/document-intelligence/api/ingest/{job_id}/stream",
        "result_url": f"/document-intelligence/api/ingest/{job_id}/result",
        "status": "queued",
    }), 202
