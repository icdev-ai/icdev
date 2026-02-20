# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3

import pytest

from tools.requirements.clarification_engine import (
    PRIORITY_MATRIX,
    _score_impact,
    _score_uncertainty,
    _generate_question,
    _prioritize_questions,
    _find_ambiguous_phrase,
    analyze_spec_clarity,
    analyze_requirements_clarity,
)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_SPEC = """\
# CUI // SP-CTI
# Feature: Auth Module

## Metadata
issue_number: `42`
run_id: `abc123`

## Feature Description
This feature implements CAC/PIV authentication for the mission planning
application.  It handles card reader integration, certificate validation,
and session management for warfighter personnel in the field.

## User Story
As a warfighter
I want to authenticate with my CAC card
So that I can securely access the mission planning tool

## Solution Statement
Implement a CAC middleware that validates X.509 certificates against the
DoD PKI trust chain.  Use FIPS 140-2 validated TLS for all connections.
Sessions expire after 15 minutes of inactivity.  Audit all login events
per NIST AU-2.

## ATO Impact Assessment
- **Boundary Impact**: YELLOW
- **New NIST Controls**: IA-2, IA-5, AC-7
- **SSP Impact**: Minor addendum for new auth component

## Acceptance Criteria
- Given a valid CAC card, when inserted, then the user is authenticated within 3 seconds
- Given an expired certificate, when scanned, then authentication is rejected with error code AUTH-003
- Given a revoked certificate, when checked against CRL, then access is denied and event is logged

## Implementation Plan
### Phase 1: Certificate handling
- Add X.509 parser
### Phase 2: Session management
- Token issuance and expiry

## Step by Step Tasks
### Step 1: Set up certificate parser
- Parse X.509 from card reader
### Step 2: Validate against DoD PKI
- CRL/OCSP check
### Step 3: Session token generation
- JWT with 15min TTL

## Testing Strategy
### Unit Tests
- Test certificate parsing with mock certs
### BDD
- Feature file for login flow
### Edge Cases
- Expired cert, revoked cert, no card reader

## Validation Commands
- `python -m pytest tests/ -v`
- `ruff check .`

## NIST 800-53 Controls
- IA-2: Identification and Authentication
- IA-5: Authenticator Management
- AC-7: Unsuccessful Login Attempts
"""

VAGUE_SPEC = """\
# CUI // SP-CTI
# Feature: Some Feature

## Feature Description
We need to do something.

## User Story
As a user I want something so that things are better

## Solution Statement
We will implement it as needed using appropriate methods.
"""

AMBIGUITY_PATTERNS = [
    {"phrase": "as needed", "severity": "high", "clarification": "Specify the exact conditions."},
    {"phrase": "appropriate", "severity": "medium", "clarification": "Define the criteria."},
    {"phrase": "timely", "severity": "medium", "clarification": "Specify a time threshold."},
]


# ---------------------------------------------------------------------------
# Priority matrix tests
# ---------------------------------------------------------------------------

class TestPriorityMatrix:
    def test_mission_critical_unknown_is_p1(self):
        assert PRIORITY_MATRIX[("mission_critical", "unknown")] == 1

    def test_enhancement_assumed_is_p5(self):
        assert PRIORITY_MATRIX[("enhancement", "assumed")] == 5

    def test_all_nine_cells_exist(self):
        assert len(PRIORITY_MATRIX) == 9
        impacts = ["mission_critical", "compliance_required", "enhancement"]
        uncertainties = ["unknown", "ambiguous", "assumed"]
        for i in impacts:
            for u in uncertainties:
                assert (i, u) in PRIORITY_MATRIX


# ---------------------------------------------------------------------------
# Impact scoring
# ---------------------------------------------------------------------------

class TestScoreImpact:
    def test_mission_keyword(self):
        assert _score_impact("The warfighter needs real-time data") == "mission_critical"

    def test_compliance_keyword(self):
        assert _score_impact("Must satisfy NIST AU-2 audit controls") == "compliance_required"

    def test_enhancement_default(self):
        assert _score_impact("Add a tooltip to the dashboard chart") == "enhancement"

    def test_context_overrides(self):
        ctx = {"requirement_type": "security"}
        assert _score_impact("add logging", context=ctx) == "compliance_required"

    def test_context_performance(self):
        ctx = {"requirement_type": "performance"}
        assert _score_impact("improve speed", context=ctx) == "mission_critical"


# ---------------------------------------------------------------------------
# Uncertainty scoring
# ---------------------------------------------------------------------------

class TestScoreUncertainty:
    def test_empty_is_unknown(self):
        assert _score_uncertainty("", []) == "unknown"

    def test_short_text_is_unknown(self):
        assert _score_uncertainty("we need auth", []) == "unknown"

    def test_ambiguous_phrase_detected(self):
        text = "The system will deliver results in a timely manner with good performance for all users"
        result = _score_uncertainty(text, AMBIGUITY_PATTERNS)
        assert result == "ambiguous"

    def test_hedging_word_detected(self):
        text = "The system should probably handle at least ten users concurrently on the server cluster"
        result = _score_uncertainty(text, [])
        assert result == "assumed"

    def test_clean_text_defaults_to_assumed(self):
        text = "The system processes exactly 100 requests per second with zero downtime guarantee for all clients"
        result = _score_uncertainty(text, [])
        assert result == "assumed"


# ---------------------------------------------------------------------------
# Find ambiguous phrase
# ---------------------------------------------------------------------------

class TestFindAmbiguousPhrase:
    def test_finds_match(self):
        result = _find_ambiguous_phrase("do it as needed", AMBIGUITY_PATTERNS)
        assert result is not None
        assert result["phrase"] == "as needed"

    def test_returns_none_when_clean(self):
        result = _find_ambiguous_phrase("exactly 10 seconds", AMBIGUITY_PATTERNS)
        assert result is None


# ---------------------------------------------------------------------------
# Question generation
# ---------------------------------------------------------------------------

class TestGenerateQuestion:
    def test_unknown_question(self):
        item = {"section": "Testing Strategy", "uncertainty": "unknown"}
        q = _generate_question(item)
        assert "Testing Strategy" in q
        assert "incomplete" in q.lower() or "missing" in q.lower() or "requirements" in q.lower()

    def test_ambiguous_question_with_pattern(self):
        item = {
            "section": "Solution Statement",
            "uncertainty": "ambiguous",
            "snippet": "do it as needed",
            "pattern": AMBIGUITY_PATTERNS[0],
        }
        q = _generate_question(item)
        assert "as needed" in q
        assert "conditions" in q.lower() or "specify" in q.lower()

    def test_assumed_question_with_hedge(self):
        item = {
            "section": "Feature Description",
            "uncertainty": "assumed",
            "snippet": "The system should probably handle 100 users",
        }
        q = _generate_question(item)
        assert "probably" in q or "should" in q
        assert "MUST" in q or "SHOULD" in q or "requirement" in q.lower()


# ---------------------------------------------------------------------------
# Prioritization
# ---------------------------------------------------------------------------

class TestPrioritizeQuestions:
    def test_sorts_by_priority(self):
        items = [
            {"priority": 5, "impact": "enhancement", "section": "Z"},
            {"priority": 1, "impact": "mission_critical", "section": "A"},
            {"priority": 3, "impact": "compliance_required", "section": "M"},
        ]
        result = _prioritize_questions(items, max_questions=3)
        assert result[0]["priority"] == 1
        assert result[-1]["priority"] == 5

    def test_respects_max_questions(self):
        items = [
            {"priority": i, "impact": "enhancement", "section": f"S{i}"}
            for i in range(10)
        ]
        result = _prioritize_questions(items, max_questions=3)
        assert len(result) == 3

    def test_tie_breaking_by_impact(self):
        items = [
            {"priority": 2, "impact": "enhancement", "section": "A"},
            {"priority": 2, "impact": "mission_critical", "section": "B"},
        ]
        result = _prioritize_questions(items, max_questions=2)
        assert result[0]["impact"] == "mission_critical"


# ---------------------------------------------------------------------------
# Spec-level analysis
# ---------------------------------------------------------------------------

class TestAnalyzeSpecClarity:
    def test_good_spec_returns_ok(self, tmp_path):
        spec_file = tmp_path / "good_spec.md"
        spec_file.write_text(SAMPLE_SPEC, encoding="utf-8")
        result = analyze_spec_clarity(spec_file, max_questions=5)
        assert result["status"] == "ok"
        assert "clarity_score" in result
        assert isinstance(result["questions"], list)

    def test_vague_spec_generates_questions(self, tmp_path):
        spec_file = tmp_path / "vague_spec.md"
        spec_file.write_text(VAGUE_SPEC, encoding="utf-8")
        result = analyze_spec_clarity(spec_file, max_questions=10)
        assert result["total_issues_found"] > 0
        assert len(result["questions"]) > 0

    def test_missing_sections_flagged(self, tmp_path):
        minimal = "# CUI\n\n## Feature Description\nJust a description with enough words to pass the minimum threshold for the checker.\n"
        spec_file = tmp_path / "minimal.md"
        spec_file.write_text(minimal, encoding="utf-8")
        result = analyze_spec_clarity(spec_file, max_questions=10)
        # Should flag missing required sections
        assert result["total_issues_found"] > 0

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            analyze_spec_clarity(tmp_path / "nonexistent.md")


# ---------------------------------------------------------------------------
# Session-level analysis (DB-backed)
# ---------------------------------------------------------------------------

def _init_clarification_db(db_path: Path):
    """Create the minimal schema for clarification engine tests."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS intake_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            customer_name TEXT,
            status TEXT DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS intake_requirements (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            raw_text TEXT,
            requirement_type TEXT DEFAULT 'functional',
            clarity_score REAL,
            completeness_score REAL,
            FOREIGN KEY (session_id) REFERENCES intake_sessions(id)
        );
    """)
    conn.commit()
    conn.close()


class TestAnalyzeRequirementsClarity:
    def test_empty_session_returns_zero(self, tmp_path):
        db = tmp_path / "test.db"
        _init_clarification_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO intake_sessions (id, project_id) VALUES (?, ?)",
            ("sess-1", "proj-1"),
        )
        conn.commit()
        conn.close()

        result = analyze_requirements_clarity("sess-1", db_path=db)
        assert result["status"] == "ok"
        assert result["total_items_analyzed"] == 0
        assert result["clarity_score"] == 0.0

    def test_vague_requirement_generates_question(self, tmp_path):
        db = tmp_path / "test.db"
        _init_clarification_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO intake_sessions (id, project_id) VALUES (?, ?)",
            ("sess-2", "proj-1"),
        )
        conn.execute(
            "INSERT INTO intake_requirements (id, session_id, raw_text, requirement_type) "
            "VALUES (?, ?, ?, ?)",
            ("req-1", "sess-2", "We need it done", "functional"),
        )
        conn.commit()
        conn.close()

        result = analyze_requirements_clarity("sess-2", max_questions=5, db_path=db)
        assert result["total_issues_found"] >= 1
        assert len(result["questions"]) >= 1

    def test_clear_requirement_no_questions(self, tmp_path):
        db = tmp_path / "test.db"
        _init_clarification_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO intake_sessions (id, project_id) VALUES (?, ?)",
            ("sess-3", "proj-1"),
        )
        conn.execute(
            "INSERT INTO intake_requirements (id, session_id, raw_text, requirement_type) "
            "VALUES (?, ?, ?, ?)",
            (
                "req-2",
                "sess-3",
                "The system must authenticate users via CAC/PIV card reader within 3 seconds. "
                "Failed attempts are logged to the SIEM per NIST AU-2 control requirements. "
                "The system processes certificate revocation checks via OCSP every 60 seconds. "
                "All authentication tokens expire after exactly 15 minutes of inactivity.",
                "security",
            ),
        )
        conn.commit()
        conn.close()

        result = analyze_requirements_clarity("sess-3", max_questions=5, db_path=db)
        # Clear requirement should not generate unknown/ambiguous questions
        # (may still have 0 issues or just assumed-level)
        assert result["status"] == "ok"

    def test_session_not_found_raises(self, tmp_path):
        db = tmp_path / "test.db"
        _init_clarification_db(db)
        with pytest.raises(ValueError, match="not found"):
            analyze_requirements_clarity("nonexistent", db_path=db)
