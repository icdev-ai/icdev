#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Confabulation Detector — NIST AI 600-1 GAI.1 risk mitigation.

Deterministic confabulation (hallucination) detection for GenAI outputs.
Uses consistency checking, citation verification, and internal
contradiction detection — no LLM required (air-gap safe, D310).

Usage:
    python tools/security/confabulation_detector.py --project-id proj-123 --check-output "text" --json
    python tools/security/confabulation_detector.py --project-id proj-123 --summary --json
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS confabulation_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            check_type TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            result TEXT NOT NULL,
            risk_score REAL DEFAULT 0.0,
            findings_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_confabulation_project
            ON confabulation_checks(project_id);
    """)
    conn.commit()


def check_citation_patterns(text: str) -> List[Dict]:
    """Detect potentially fabricated citations and references."""
    findings = []

    # Check for URL-like references that may be fabricated
    url_pattern = re.compile(r'https?://[^\s\)\"\']+')
    urls = url_pattern.findall(text)
    for url in urls:
        parsed = urlparse(url)
        # Flag URLs with suspicious patterns
        if any(word in parsed.path.lower() for word in [
            "article", "paper", "report", "study"
        ]) and len(parsed.path) > 100:
            findings.append({
                "type": "suspicious_url",
                "detail": f"Long URL path may indicate fabricated reference: {url[:80]}...",
                "severity": "medium",
            })

    # Check for academic citation patterns with specific years
    cite_pattern = re.compile(r'(?:et al\.|(?:[A-Z][a-z]+,?\s+){2,})\(?\d{4}\)?')
    citations = cite_pattern.findall(text)
    if len(citations) > 5:
        findings.append({
            "type": "high_citation_density",
            "detail": f"Found {len(citations)} academic citations — verify provenance",
            "severity": "low",
        })

    # Check for specific document references (RFC, NIST SP, etc.)
    doc_refs = re.findall(r'(?:RFC|NIST SP|OMB M-|EO )\d[\d\-\.]+', text)
    for ref in doc_refs:
        findings.append({
            "type": "document_reference",
            "detail": f"Document reference detected: {ref} — verify accuracy",
            "severity": "info",
        })

    return findings


def check_internal_contradictions(text: str) -> List[Dict]:
    """Detect potential internal contradictions in text."""
    findings = []
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    # Check for negation contradictions
    negation_pairs = [
        ("must", "must not"),
        ("shall", "shall not"),
        ("required", "not required"),
        ("always", "never"),
        ("all", "none"),
        ("enabled", "disabled"),
    ]

    for word, negation in negation_pairs:
        has_positive = any(word in s.lower() and negation not in s.lower() for s in sentences)
        has_negative = any(negation in s.lower() for s in sentences)
        if has_positive and has_negative:
            findings.append({
                "type": "potential_contradiction",
                "detail": f"Text contains both '{word}' and '{negation}' claims — verify consistency",
                "severity": "medium",
            })

    return findings


def check_confidence_indicators(text: str) -> List[Dict]:
    """Detect hedging language that may indicate uncertainty."""
    findings = []

    hedging_patterns = [
        (r'\b(?:I think|I believe|probably|possibly|might|could be|likely)\b', "hedging_language"),
        (r'\b(?:approximately|roughly|around|about)\s+\d', "imprecise_quantification"),
        (r'\b(?:as of my|my training|my knowledge|cutoff)\b', "knowledge_limitation"),
    ]

    for pattern, pattern_type in hedging_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            findings.append({
                "type": pattern_type,
                "detail": f"Found {len(matches)} instances of {pattern_type.replace('_', ' ')}",
                "severity": "low",
                "count": len(matches),
            })

    return findings


def check_output(
    project_id: str,
    text: str,
    db_path: Path = DB_PATH,
) -> Dict:
    """Run all confabulation checks on text output."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        now = datetime.now(timezone.utc).isoformat()
        input_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

        all_findings = []
        all_findings.extend(check_citation_patterns(text))
        all_findings.extend(check_internal_contradictions(text))
        all_findings.extend(check_confidence_indicators(text))

        # Calculate risk score
        severity_weights = {"info": 0.1, "low": 0.2, "medium": 0.5, "high": 0.8, "critical": 1.0}
        total_weight = sum(severity_weights.get(f["severity"], 0.3) for f in all_findings)
        risk_score = min(1.0, total_weight / 5.0)

        result = {
            "project_id": project_id,
            "input_hash": input_hash,
            "input_length": len(text),
            "check_timestamp": now,
            "risk_score": round(risk_score, 3),
            "risk_level": (
                "high" if risk_score >= 0.7
                else "medium" if risk_score >= 0.3
                else "low"
            ),
            "findings": all_findings,
            "findings_count": len(all_findings),
            "checks_performed": [
                "citation_verification",
                "internal_contradiction",
                "confidence_indicators",
            ],
        }

        # Store in DB (append-only)
        conn.execute(
            """INSERT INTO confabulation_checks
               (project_id, check_type, input_hash, result, risk_score, findings_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "full_check", input_hash, json.dumps(result), risk_score, len(all_findings), now),
        )
        conn.commit()

        return result
    finally:
        conn.close()


def get_summary(project_id: str, db_path: Path = DB_PATH) -> Dict:
    """Get confabulation check summary for a project."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        rows = conn.execute(
            """SELECT COUNT(*) as total_checks,
                      AVG(risk_score) as avg_risk,
                      MAX(risk_score) as max_risk,
                      SUM(findings_count) as total_findings
               FROM confabulation_checks WHERE project_id = ?""",
            (project_id,),
        ).fetchone()

        return {
            "project_id": project_id,
            "total_checks": rows["total_checks"] or 0,
            "avg_risk_score": round(rows["avg_risk"] or 0, 3),
            "max_risk_score": round(rows["max_risk"] or 0, 3),
            "total_findings": rows["total_findings"] or 0,
            "detection_active": True,
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Confabulation Detector (NIST AI 600-1)")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--check-output", help="Text to check for confabulation")
    parser.add_argument("--summary", action="store_true", help="Get check summary")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    try:
        if args.summary:
            result = get_summary(args.project_id, db)
        elif args.check_output:
            result = check_output(args.project_id, args.check_output, db)
        else:
            print("ERROR: --check-output or --summary required", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if args.summary:
                print(f"Confabulation Summary for {args.project_id}")
                print(f"  Total checks: {result['total_checks']}")
                print(f"  Avg risk: {result['avg_risk_score']}")
                print(f"  Max risk: {result['max_risk_score']}")
            else:
                print(f"Risk: {result['risk_level']} ({result['risk_score']})")
                print(f"Findings: {result['findings_count']}")
                for f in result["findings"]:
                    print(f"  [{f['severity']}] {f['detail']}")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
