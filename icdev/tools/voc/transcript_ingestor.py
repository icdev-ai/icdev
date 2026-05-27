# CUI // SP-CTI
"""VOC Transcript Ingestor — ingests documents and extracts raw job statements.

Supports .txt, .md (native), .pdf (pdfminer.six), .docx (python-docx).
Extracts sentences containing job-statement keywords from voc_config.yaml
and persists them to voc_documents + voc_job_statements via get_connection().
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import re
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _ROOT / "args" / "voc_config.yaml"

_DEFAULT_KEYWORDS = [
    "when I", "I want", "so that", "need to", "frustrated by",
    "unable to", "wish it could", "pain point", "issue", "bug",
]


def _load_config() -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import]
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _extract_text_plain(file_path: Path) -> tuple[str, None]:
    return file_path.read_text(encoding="utf-8", errors="replace"), None


def _extract_text_pdf(file_path: Path) -> tuple[str | None, str | None]:
    try:
        from pdfminer.high_level import extract_text  # type: ignore[import]
        text = extract_text(str(file_path))
        return text or "", None
    except ImportError:
        warnings.warn("pdfminer.six not installed — skipping PDF extraction")
        return None, "pdfminer.six not installed"
    except Exception as exc:
        return None, f"PDF extraction error: {exc}"


def _extract_text_docx(file_path: Path) -> tuple[str | None, str | None]:
    try:
        from docx import Document  # type: ignore[import]
        doc = Document(str(file_path))
        text = "\n".join(p.text for p in doc.paragraphs)
        return text, None
    except ImportError:
        warnings.warn("python-docx not installed — skipping DOCX extraction")
        return None, "python-docx not installed"
    except Exception as exc:
        return None, f"DOCX extraction error: {exc}"


class TranscriptIngestor:
    def __init__(self) -> None:
        cfg = _load_config()
        self._keywords: list[str] = (
            cfg.get("extraction", {}).get("job_statement_keywords", _DEFAULT_KEYWORDS)
        )

    def ingest(self, file_path: Path) -> dict[str, Any]:
        file_path = Path(file_path)
        suffix = file_path.suffix.lower().lstrip(".")

        if suffix in ("txt", "md"):
            text, skipped_reason = _extract_text_plain(file_path)
        elif suffix == "pdf":
            text, skipped_reason = _extract_text_pdf(file_path)
        elif suffix == "docx":
            text, skipped_reason = _extract_text_docx(file_path)
        else:
            return {
                "document_id": None,
                "job_statement_count": 0,
                "skipped_reason": f"unsupported file type: .{suffix}",
            }

        if text is None:
            return {
                "document_id": None,
                "job_statement_count": 0,
                "skipped_reason": skipped_reason or "extraction failed",
            }

        sentences = _split_sentences(text)
        word_count = len(text.split())
        matching = [
            s for s in sentences
            if any(kw.lower() in s.lower() for kw in self._keywords)
        ]

        doc_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        try:
            from tools.db.storage import get_connection  # type: ignore[import]
            conn = get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO voc_documents
                        (id, filename, source_type, ingested_at, word_count,
                         job_statement_count, classification)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        file_path.name,
                        suffix,
                        now,
                        word_count,
                        len(matching),
                        "CUI // SP-CTI",
                    ),
                )
                for sentence in matching:
                    conn.execute(
                        """
                        INSERT INTO voc_job_statements
                            (id, document_id, raw_text, job_category,
                             frequency, severity_score, strategic_fit_score,
                             composite_score, creative_gap_id, analyzed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            doc_id,
                            sentence,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            now,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.error("DB write failed: %s", exc)
            return {
                "document_id": doc_id,
                "job_statement_count": len(matching),
                "skipped_reason": f"db_error: {exc}",
            }

        return {
            "document_id": doc_id,
            "job_statement_count": len(matching),
            "skipped_reason": None,
        }

    def scan_dir(self, directory: Path) -> list[dict[str, Any]]:
        directory = Path(directory)
        results = []
        for fp in sorted(directory.iterdir()):
            if fp.is_file() and fp.suffix.lower().lstrip(".") in ("txt", "md", "pdf", "docx"):
                results.append({"file": str(fp), **self.ingest(fp)})
        return results


def _cli() -> None:
    parser = argparse.ArgumentParser(description="VOC Transcript Ingestor")
    parser.add_argument("--ingest", metavar="PATH", help="Ingest a single file")
    parser.add_argument("--scan-dir", metavar="DIR", help="Ingest all supported files in a directory")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output JSON")
    args = parser.parse_args()

    ingestor = TranscriptIngestor()

    if args.ingest:
        result = ingestor.ingest(Path(args.ingest))
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            print(f"document_id: {result['document_id']}")
            print(f"job_statement_count: {result['job_statement_count']}")
            if result["skipped_reason"]:
                print(f"skipped_reason: {result['skipped_reason']}")
    elif args.scan_dir:
        results = ingestor.scan_dir(Path(args.scan_dir))
        if args.as_json:
            print(json.dumps(results, indent=2))
        else:
            for r in results:
                print(f"{r['file']}: {r['job_statement_count']} statement(s)")
                if r.get("skipped_reason"):
                    print(f"  skipped: {r['skipped_reason']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
