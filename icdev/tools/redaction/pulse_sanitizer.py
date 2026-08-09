#!/usr/bin/env python3

# CUI // SP-CTI
"""Pulse Sanitizer — hardened case study sanitization for public articles.

Replaces the minimal _sanitize_title() in R12 Publish with comprehensive
content de-identification:
    - Agency name removal/generalization
    - Program name codename replacement
    - Past performance generalization
    - NAICS code stripping
    - Contract number removal
    - Personnel name detection and removal
    - Financial detail generalization

This is a thin wrapper around GovConSanitizer.sanitize_case_study()
with Pulse-specific convenience methods.

CLI:
    python tools/redaction/pulse_sanitizer.py --sanitize-article --title "T" --body "B" --json
    python tools/redaction/pulse_sanitizer.py --health --json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.redaction.govcon_sanitizer import GovConSanitizer  # noqa: E402

logger = get_logger("icdev.redaction.pulse_sanitizer")


class PulseSanitizer:
    """Sanitizer for Pulse case study articles derived from proposal content."""

    def __init__(self, session_id: Optional[str] = None):
        self._sanitizer = GovConSanitizer(session_id=session_id)

    def sanitize_article(
        self, title: str, body: str, tags: Optional[List[str]] = None, domain: str = ""
    ) -> Dict[str, Any]:
        """Sanitize a case study article for Pulse publication.

        Args:
            title: Article title (may contain solicitation numbers, program names).
            body: Article markdown body.
            tags: Article tags (may contain agency names, NAICS codes).
            domain: Domain category.

        Returns:
            Dict with sanitized title, body, tags, and metadata.
        """
        result = self._sanitizer.sanitize_case_study(
            title=title,
            body=body,
            tags=tags,
        )

        return {
            "title": result["title"],
            "body": result["body"],
            "tags": result["tags"],
            "domain": domain,
            "sanitized": True,
            "session_id": self._sanitizer.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def health(self) -> Dict[str, Any]:
        """Health check."""
        inner = self._sanitizer.health()
        return {
            "status": "ok",
            "sanitizer": inner,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Pulse Sanitizer")
    parser.add_argument("--sanitize-article", action="store_true", help="Sanitize a case study article")
    parser.add_argument("--title", type=str, default="", help="Article title")
    parser.add_argument("--body", type=str, default="", help="Article body")
    parser.add_argument("--tags", type=str, default="", help="Comma-separated tags")
    parser.add_argument("--domain", type=str, default="", help="Domain category")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()

    sanitizer = PulseSanitizer()

    if args.health:
        result = sanitizer.health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Status: {result['status']}")
        if args.gate and result["status"] != "ok":
            sys.exit(1)
        return

    if args.sanitize_article:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        result = sanitizer.sanitize_article(
            title=args.title,
            body=args.body,
            tags=tags,
            domain=args.domain,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Title: {result['title']}")
            print(f"Tags: {result['tags']}")
            print(f"Body length: {len(result['body'])}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
