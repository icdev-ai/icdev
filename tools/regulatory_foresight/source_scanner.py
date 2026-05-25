
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Regulatory Foresight — source scanner (D352/D-RES-3 pattern).

SOURCE_SCANNERS maps source name → scan function.  Each function fetches
proposed rules/bills via HTTP and returns raw signal dicts whose fields
match the regulatory_foresight_signals schema (migration 066).

Air-gap safe: every network call is wrapped; failures return [] with a
warning log.  No external deps — stdlib urllib only.

CLI:
    python source_scanner.py --scan --all --json
    python source_scanner.py --scan --source federal_register --json
"""

import hashlib
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

logger = get_logger(__name__)

_HTTP_TIMEOUT = 10  # seconds; kept short for air-gap safety


# ── helpers ──────────────────────────────────────────────────────────────────

def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[dict]:
    """Best-effort HTTP GET → parsed JSON.  Returns None on any failure."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        logger.warning("source_scanner: network error fetching %s: %s", url, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("source_scanner: failed to process %s: %s", url, exc)
    return None


def _sid(source: str, doc_id: str) -> str:
    """Deterministic 24-char signal ID for deduplication."""
    return hashlib.sha256(f"{source}:{doc_id}".encode()).hexdigest()[:24]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _raw(
    *,
    source: str,
    doc_id: str,
    title: str,
    url: str,
    proposed_at: Optional[str] = None,
    comment_deadline: Optional[str] = None,
    estimated_mandate_date: Optional[str] = None,
    affected_frameworks: Optional[List[str]] = None,
    icdev_impact_areas: Optional[List[str]] = None,
) -> dict:
    """Build a raw signal dict with all migration-066 fields."""
    return {
        "id": _sid(source, doc_id),
        "source": source,
        "doc_id": doc_id,
        "title": title,
        "url": url,
        "proposed_at": proposed_at,
        "comment_deadline": comment_deadline,
        "estimated_mandate_date": estimated_mandate_date,
        "affected_frameworks": json.dumps(affected_frameworks or []),
        "icdev_impact_areas": json.dumps(icdev_impact_areas or []),
        "time_to_mandate_days": None,   # computed by impact_scorer
        "icdev_impact_score": None,
        "blast_radius_score": None,
        "composite_score": None,
        "status": "new",
        "innovation_signal_id": None,
        "scanned_at": _now(),
        "classification": "CUI // SP-CTI",
    }


# ── Federal Register ──────────────────────────────────────────────────────────

_FR_BASE = "https://www.federalregister.gov/api/v1/documents.json"


def scan_federal_register() -> List[dict]:
    """Proposed rules from the Federal Register public API (no key required)."""
    params = urllib.parse.urlencode(
        {
            "fields[]": [
                "document_number",
                "title",
                "publication_date",
                "comments_close_on",
                "effective_on",
                "html_url",
                "agencies",
            ],
            "type[]": ["Proposed Rule"],
            "per_page": "20",
            "order": "newest",
        },
        doseq=True,
    )
    data = _http_get_json(f"{_FR_BASE}?{params}")
    if not data:
        return []

    signals = []
    for doc in data.get("results", []):
        doc_id = doc.get("document_number", "")
        if not doc_id:
            continue
        agencies = [a.get("name", "") for a in doc.get("agencies", []) if a.get("name")]
        signals.append(
            _raw(
                source="federal_register",
                doc_id=doc_id,
                title=doc.get("title", ""),
                url=doc.get("html_url", ""),
                proposed_at=doc.get("publication_date"),
                comment_deadline=doc.get("comments_close_on"),
                estimated_mandate_date=doc.get("effective_on"),
                affected_frameworks=agencies,
            )
        )
    return signals


# ── Congress Bills ────────────────────────────────────────────────────────────

_CONGRESS_BASE = "https://api.congress.gov/v3/bill"


def scan_congress_bills() -> List[dict]:
    """Recent bills from Congress.gov (DEMO_KEY rate-limited; set CONGRESS_API_KEY)."""
    import os

    api_key = os.environ.get("CONGRESS_API_KEY", "DEMO_KEY")
    params = urllib.parse.urlencode(
        {"format": "json", "limit": "20", "sort": "updateDate+desc", "api_key": api_key}
    )
    data = _http_get_json(f"{_CONGRESS_BASE}?{params}")
    if not data:
        return []

    signals = []
    for bill in data.get("bills", []):
        congress = bill.get("congress", "")
        bill_type = bill.get("type", "")
        number = bill.get("number", "")
        doc_id = f"{congress}-{bill_type}-{number}"
        signals.append(
            _raw(
                source="congress_bills",
                doc_id=doc_id,
                title=bill.get("title", ""),
                url=bill.get("url", ""),
                proposed_at=bill.get("introducedDate"),
            )
        )
    return signals


# ── Regulations.gov ───────────────────────────────────────────────────────────

_REGS_BASE = "https://api.regulations.gov/v4/documents"


def scan_regulations_gov() -> List[dict]:
    """Open proposed rules from Regulations.gov (requires REGULATIONS_GOV_API_KEY)."""
    import os

    api_key = os.environ.get("REGULATIONS_GOV_API_KEY", "")
    if not api_key:
        logger.warning("source_scanner: REGULATIONS_GOV_API_KEY not set — skipping regulations_gov")
        return []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    params = urllib.parse.urlencode(
        {
            "filter[documentType]": "Proposed Rule",
            "filter[commentEndDate][gte]": today,
            "sort": "-postedDate",
            "page[size]": "20",
        }
    )
    data = _http_get_json(f"{_REGS_BASE}?{params}", headers={"X-Api-Key": api_key})
    if not data:
        return []

    signals = []
    for doc in data.get("data", []):
        attrs = doc.get("attributes", {})
        doc_id = doc.get("id", "")
        if not doc_id:
            continue
        agency = attrs.get("agencyId", "")
        signals.append(
            _raw(
                source="regulations_gov",
                doc_id=doc_id,
                title=attrs.get("title", ""),
                url=f"https://www.regulations.gov/document/{doc_id}",
                proposed_at=attrs.get("postedDate"),
                comment_deadline=attrs.get("commentEndDate"),
                affected_frameworks=[agency] if agency else [],
            )
        )
    return signals


# ── Registry (D352/D-RES-3 pattern) ──────────────────────────────────────────

SOURCE_SCANNERS: Dict[str, Callable[[], List[dict]]] = {
    "federal_register": scan_federal_register,
    "congress_bills": scan_congress_bills,
    "regulations_gov": scan_regulations_gov,
}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Regulatory Foresight source scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python source_scanner.py --scan --all --json\n"
            "  python source_scanner.py --scan --source federal_register --json\n"
        ),
    )
    parser.add_argument("--scan", action="store_true", help="Run scanner(s)")
    parser.add_argument("--all", action="store_true", help="Scan all sources")
    parser.add_argument("--source", metavar="NAME", help="Single source to scan")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    if not args.scan:
        parser.print_help()
        sys.exit(0)

    if args.all:
        sources = list(SOURCE_SCANNERS)
    elif args.source:
        if args.source not in SOURCE_SCANNERS:
            out = {"error": f"Unknown source: {args.source}", "available": list(SOURCE_SCANNERS)}
            print(json.dumps(out))
            sys.exit(1)
        sources = [args.source]
    else:
        print(json.dumps({"error": "Specify --all or --source <name>"}))
        sys.exit(1)

    signals: List[dict] = []
    for src in sources:
        signals.extend(SOURCE_SCANNERS[src]())

    if args.as_json:
        print(json.dumps({"signals": signals, "count": len(signals)}, indent=2, default=str))
    else:
        for sig in signals:
            print(f"[{sig['source']}] {sig['title'][:80]} — {sig['url']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    _cli()
