# CUI // SP-CTI
"""VOC Engine — orchestrates the full voice-of-customer pipeline.

Scans upload_dir for unprocessed transcripts, ingests them via TranscriptIngestor,
clusters job statements by keyword-overlap category, scores clusters, and emits
creative_feature_gaps signals for clusters that exceed auto_signal_threshold.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _ROOT / "args" / "voc_config.yaml"

_KEYWORD_SEVERITY: dict[str, float] = {
    "frustrated by": 0.90,
    "unable to": 0.85,
    "pain point": 0.85,
    "bug": 0.70,
    "need to": 0.65,
    "issue": 0.60,
    "I want": 0.55,
    "wish it could": 0.55,
    "when I": 0.45,
    "so that": 0.40,
}

_DEFAULT_STRATEGIC_FIT = 0.50
_DEFAULT_UPLOAD_DIR = "tools/voc/uploads/"
_DEFAULT_SCORING = {
    "frequency_weight": 0.40,
    "severity_weight": 0.35,
    "strategic_fit_weight": 0.25,
    "auto_signal_threshold": 0.65,
}


def _load_config() -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import]
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _assign_category(raw_text: str, keywords: list[str]) -> str:
    for kw in keywords:
        if kw.lower() in raw_text.lower():
            return kw.lower().replace(" ", "_")
    return "uncategorized"


def _statement_severity(raw_text: str, keywords: list[str]) -> float:
    matched = [kw for kw in keywords if kw.lower() in raw_text.lower()]
    if not matched:
        return 0.50
    return max(_KEYWORD_SEVERITY.get(kw, 0.50) for kw in matched)


class VOCEngine:
    def __init__(self) -> None:
        cfg = _load_config()
        ingestion = cfg.get("ingestion", {})
        scoring = {**_DEFAULT_SCORING, **cfg.get("scoring", {})}

        upload_dir = ingestion.get("upload_dir", _DEFAULT_UPLOAD_DIR)
        self._upload_dir = (
            Path(upload_dir) if Path(upload_dir).is_absolute()
            else _ROOT / upload_dir
        )
        self._freq_weight: float = float(scoring["frequency_weight"])
        self._sev_weight: float = float(scoring["severity_weight"])
        self._fit_weight: float = float(scoring["strategic_fit_weight"])
        self._threshold: float = float(scoring["auto_signal_threshold"])
        self._keywords: list[str] = (
            cfg.get("extraction", {}).get("job_statement_keywords", list(_KEYWORD_SEVERITY))
        )

    def run(self) -> dict[str, Any]:
        from tools.voc.transcript_ingestor import TranscriptIngestor  # type: ignore[import]

        unprocessed = self._find_unprocessed()
        ingestor = TranscriptIngestor()
        doc_results = [ingestor.ingest(fp) for fp in unprocessed]
        documents_processed = sum(1 for r in doc_results if r.get("document_id"))

        job_statements, signals_created = self._cluster_and_signal()
        return {
            "documents_processed": documents_processed,
            "job_statements": job_statements,
            "signals_created": signals_created,
        }

    def _find_unprocessed(self) -> list[Path]:
        if not self._upload_dir.exists():
            self._upload_dir.mkdir(parents=True, exist_ok=True)
            return []

        supported = {".txt", ".md", ".pdf", ".docx"}
        candidates = [
            fp for fp in sorted(self._upload_dir.iterdir())
            if fp.is_file() and fp.suffix.lower() in supported
        ]
        if not candidates:
            return []

        try:
            from tools.db.storage import get_connection  # type: ignore[import]
            conn = get_connection()
            try:
                rows = conn.execute("SELECT filename FROM voc_documents").fetchall()
                processed = {r[0] for r in rows}
            finally:
                conn.close()
        except Exception:
            processed = set()

        return [fp for fp in candidates if fp.name not in processed]

    def _cluster_and_signal(self) -> tuple[int, int]:
        from tools.db.storage import get_connection  # type: ignore[import]

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, raw_text, job_category FROM voc_job_statements "
                "WHERE creative_gap_id IS NULL"
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return 0, 0

        statements = []
        for sid, raw_text, job_category in rows:
            category = job_category or _assign_category(raw_text, self._keywords)
            severity = _statement_severity(raw_text, self._keywords)
            statements.append({
                "id": sid,
                "raw_text": raw_text,
                "job_category": category,
                "severity": severity,
                "fit": _DEFAULT_STRATEGIC_FIT,
            })

        clusters: dict[str, list[dict[str, Any]]] = {}
        for stmt in statements:
            clusters.setdefault(stmt["job_category"], []).append(stmt)

        max_freq = max(len(v) for v in clusters.values())
        signals_created = 0
        now = datetime.now(timezone.utc).isoformat()

        conn = get_connection()
        try:
            for category, members in clusters.items():
                cluster_size = len(members)
                freq_norm = cluster_size / max_freq
                avg_sev = sum(m["severity"] for m in members) / cluster_size
                avg_fit = sum(m["fit"] for m in members) / cluster_size
                composite = (
                    freq_norm * self._freq_weight
                    + avg_sev * self._sev_weight
                    + avg_fit * self._fit_weight
                )

                for m in members:
                    conn.execute(
                        "UPDATE voc_job_statements SET job_category=?, frequency=?, "
                        "severity_score=?, strategic_fit_score=?, composite_score=? "
                        "WHERE id=?",
                        (category, cluster_size, m["severity"], m["fit"], composite, m["id"]),
                    )

                if composite >= self._threshold:
                    gap_id = str(uuid.uuid4())
                    description = (
                        f"VOC signal: {cluster_size} statement(s) in category "
                        f"'{category}' — composite {composite:.3f}"
                    )
                    metadata = json.dumps({
                        "gap_type": "voc_signal",
                        "category": category,
                        "cluster_size": cluster_size,
                        "composite": round(composite, 6),
                    })
                    conn.execute(
                        "INSERT INTO creative_feature_gaps "
                        "(id, feature_name, description, gap_score, signal_ids, "
                        "metadata, discovered_at, classification) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            gap_id,
                            f"VOC: {category}",
                            description,
                            round(composite, 6),
                            json.dumps([m["id"] for m in members]),
                            metadata,
                            now,
                            "CUI // SP-CTI",
                        ),
                    )
                    for m in members:
                        conn.execute(
                            "UPDATE voc_job_statements SET creative_gap_id=? WHERE id=?",
                            (gap_id, m["id"]),
                        )
                    signals_created += 1

            conn.commit()
        finally:
            conn.close()

        return len(statements), signals_created

    def status(self) -> dict[str, Any]:
        from tools.db.storage import get_connection  # type: ignore[import]

        conn = get_connection()
        try:
            doc_count = conn.execute("SELECT COUNT(*) FROM voc_documents").fetchone()[0]
            stmt_count = conn.execute("SELECT COUNT(*) FROM voc_job_statements").fetchone()[0]
            gap_count = conn.execute(
                "SELECT COUNT(*) FROM creative_feature_gaps "
                "WHERE metadata LIKE '%voc_signal%'"
            ).fetchone()[0]
            pending = len(self._find_unprocessed())
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            conn.close()

        return {
            "documents_ingested": doc_count,
            "job_statements": stmt_count,
            "signals_created": gap_count,
            "files_pending": pending,
        }

    def top_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        from tools.db.storage import get_connection  # type: ignore[import]

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT job_category, COUNT(*) as cnt, AVG(composite_score) as avg_comp "
                "FROM voc_job_statements WHERE job_category IS NOT NULL "
                "GROUP BY job_category ORDER BY avg_comp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()

        return [
            {
                "job_category": r[0],
                "count": r[1],
                "avg_composite": round(r[2] or 0.0, 4),
            }
            for r in rows
        ]


def _cli() -> None:
    parser = argparse.ArgumentParser(description="VOC Engine — voice-of-customer pipeline")
    parser.add_argument("--run", action="store_true", help="Ingest files, cluster, emit signals")
    parser.add_argument("--status", action="store_true", help="Show pipeline status")
    parser.add_argument("--top-jobs", action="store_true", help="List top job categories")
    parser.add_argument("--limit", type=int, default=10, metavar="N", help="Rows for --top-jobs")
    parser.add_argument("--daemon", action="store_true", help="Run continuously (300s interval)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output JSON")
    args = parser.parse_args()

    engine = VOCEngine()

    if args.run:
        result = engine.run()
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            print(f"documents_processed: {result['documents_processed']}")
            print(f"job_statements:      {result['job_statements']}")
            print(f"signals_created:     {result['signals_created']}")

    elif args.status:
        result = engine.status()
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            for k, v in result.items():
                print(f"{k}: {v}")

    elif args.top_jobs:
        rows = engine.top_jobs(limit=args.limit)
        if args.as_json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(f"{r['job_category']:30s}  count={r['count']:4d}  avg_composite={r['avg_composite']:.4f}")

    elif args.daemon:
        logger.info("VOC Engine daemon started (interval=300s)")
        while True:
            try:
                result = engine.run()
                ts = datetime.now(timezone.utc).isoformat()
                if args.as_json:
                    print(json.dumps({"ts": ts, **result}), flush=True)
                else:
                    print(f"[{ts}] {result}", flush=True)
            except Exception as exc:
                logger.error("Daemon run error: %s", exc)
            time.sleep(300)

    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
