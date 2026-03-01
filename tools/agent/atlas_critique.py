#!/usr/bin/env python3
# CUI // SP-CTI
"""ATLAS Critique Phase — Adversarial Plan Review (Phase 61, Feature 3).

Dispatches the Assemble-phase output to multiple domain agents for
independent, parallel critique.  Findings are classified by severity
and a consensus vote determines GO / NOGO / CONDITIONAL.

Decision D36:  ThreadPoolExecutor for parallel critic dispatch.
Decision D6:   All findings and sessions are append-only (NIST AU).
Decision D26:  Critic configuration is declarative YAML.

CLI:
    python tools/agent/atlas_critique.py --project-id proj-123 \\
        --phase-output "plan text or file path" --json
    python tools/agent/atlas_critique.py --project-id proj-123 \\
        --session-id sess-123 --status --json
    python tools/agent/atlas_critique.py --project-id proj-123 \\
        --history --json
"""

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.compat.datetime_utils import utc_now_iso

DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "atlas_critique_config.yaml"

logger = logging.getLogger("icdev.atlas_critique")

# Valid finding types (must match DB CHECK constraint)
FINDING_TYPES = (
    "security_vulnerability",
    "compliance_gap",
    "architecture_flaw",
    "performance_risk",
    "maintainability_concern",
    "testing_gap",
    "deployment_risk",
    "data_handling_issue",
)

SEVERITY_LEVELS = ("critical", "high", "medium", "low")

SESSION_STATUSES = (
    "in_progress", "go", "nogo", "conditional", "revised", "failed",
)

CONSENSUS_VALUES = ("go", "nogo", "conditional")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    """A single critique finding from one agent."""
    id: str = ""
    session_id: str = ""
    critic_agent: str = ""
    round_number: int = 1
    finding_type: str = ""
    severity: str = ""
    title: str = ""
    description: str = ""
    evidence: str = ""
    suggested_fix: str = ""
    nist_controls: List[str] = field(default_factory=list)
    addressed_in_revision: int = 0
    created_at: str = ""


@dataclass
class CritiqueSession:
    """An ATLAS critique session spanning one or more rounds."""
    id: str = ""
    project_id: str = ""
    workflow_id: str = ""
    phase_input_hash: str = ""
    status: str = "in_progress"
    round_number: int = 1
    max_rounds: int = 3
    consensus: str = ""
    critics_assigned: List[str] = field(default_factory=list)
    total_findings: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    revision_summary: str = ""
    created_at: str = ""
    completed_at: str = ""


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------
def load_config(config_path: Path = None) -> dict:
    """Load atlas_critique_config.yaml and return the atlas_critique section."""
    path = config_path or CONFIG_PATH
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return raw.get("atlas_critique", {})
    except ImportError:
        logger.warning("PyYAML not available — using default config")
        return _default_config()
    except FileNotFoundError:
        logger.warning("Config %s not found — using defaults", path)
        return _default_config()


def _default_config() -> dict:
    """Minimal fallback config when YAML is unavailable."""
    return {
        "enabled": True,
        "max_rounds": 3,
        "critics": [
            {"agent": "security-agent", "role": "security_reviewer",
             "focus": ["security_vulnerability", "data_handling_issue", "deployment_risk"],
             "prompt_context": "Review the plan for security issues."},
            {"agent": "compliance-agent", "role": "compliance_reviewer",
             "focus": ["compliance_gap", "data_handling_issue"],
             "prompt_context": "Review the plan for compliance gaps."},
            {"agent": "knowledge-agent", "role": "patterns_reviewer",
             "focus": ["architecture_flaw", "performance_risk",
                       "maintainability_concern", "testing_gap"],
             "prompt_context": "Review the plan for quality and pattern issues."},
        ],
        "consensus_rules": {
            "go": {"max_critical": 0, "max_high": 0},
            "conditional": {"max_critical": 0},
        },
        "revision_prompt": (
            "Revise the plan to address ALL critical and high findings.\n\n"
            "Findings:\n{findings}\n\nOriginal plan:\n{original_plan}"
        ),
    }


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _get_db(db_path: Path = None) -> sqlite3.Connection:
    """Open a connection with row_factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(db_path: Path = None):
    """Create atlas_critique_sessions and atlas_critique_findings if absent."""
    conn = _get_db(db_path)
    try:
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS atlas_critique_sessions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                workflow_id TEXT,
                phase_input_hash TEXT NOT NULL,
                status TEXT DEFAULT 'in_progress'
                    CHECK(status IN {SESSION_STATUSES!r}),
                round_number INTEGER DEFAULT 1,
                max_rounds INTEGER DEFAULT 3,
                consensus TEXT CHECK(consensus IN {CONSENSUS_VALUES!r} OR consensus IS NULL),
                critics_assigned TEXT DEFAULT '[]',
                total_findings INTEGER DEFAULT 0,
                critical_count INTEGER DEFAULT 0,
                high_count INTEGER DEFAULT 0,
                medium_count INTEGER DEFAULT 0,
                low_count INTEGER DEFAULT 0,
                revision_summary TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS atlas_critique_findings (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL
                    REFERENCES atlas_critique_sessions(id),
                critic_agent TEXT NOT NULL,
                round_number INTEGER DEFAULT 1,
                finding_type TEXT NOT NULL
                    CHECK(finding_type IN {FINDING_TYPES!r}),
                severity TEXT NOT NULL
                    CHECK(severity IN {SEVERITY_LEVELS!r}),
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                evidence TEXT,
                suggested_fix TEXT,
                nist_controls TEXT DEFAULT '[]',
                addressed_in_revision INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_critique_session_project
                ON atlas_critique_sessions(project_id);
            CREATE INDEX IF NOT EXISTS idx_critique_finding_session
                ON atlas_critique_findings(session_id);
            CREATE INDEX IF NOT EXISTS idx_critique_finding_severity
                ON atlas_critique_findings(severity);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Audit trail helper
# ---------------------------------------------------------------------------
def _audit(event_type: str, actor: str, action: str,
           project_id: str = None, details: dict = None,
           db_path: Path = None):
    """Best-effort append-only audit logging."""
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type=event_type,
            actor=actor,
            action=action,
            project_id=project_id,
            details=details,
            classification="CUI",
            db_path=db_path,
        )
    except Exception as exc:
        logger.debug("Audit logging failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------
class AtlasCritique:
    """Orchestrates the adversarial critique phase of ATLAS.

    Usage::

        critique = AtlasCritique(db_path=tmp_path / "test.db")
        result = critique.run_critique("proj-123", "Plan text here...")
        print(result["consensus"])  # "go", "nogo", or "conditional"
    """

    def __init__(self, db_path: Path = None, config_path: Path = None):
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._config = load_config(config_path)
        self._llm_router = None
        ensure_tables(self._db_path)

    # ------------------------------------------------------------------
    # LLM Router (lazy init)
    # ------------------------------------------------------------------
    def _get_llm_router(self):
        """Return cached LLMRouter, creating if needed."""
        if self._llm_router is None:
            try:
                from tools.llm.router import LLMRouter
                self._llm_router = LLMRouter()
            except Exception:
                self._llm_router = None
        return self._llm_router

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run_critique(
        self,
        project_id: str,
        phase_output: str,
        workflow_id: str = None,
        max_rounds: int = None,
    ) -> dict:
        """Run the full critique loop.

        Args:
            project_id:   Project identifier.
            phase_output: The Assemble-phase output text (plan).
            workflow_id:  Optional workflow correlation ID.
            max_rounds:   Override max revision rounds (default from config).

        Returns:
            dict with session details, findings, consensus, and round info.
        """
        if not self._config.get("enabled", True):
            return {
                "status": "skipped",
                "reason": "atlas_critique.enabled is false in config",
            }

        effective_max = max_rounds or self._config.get("max_rounds", 3)
        session = self._create_session(
            project_id, phase_output, workflow_id, effective_max,
        )

        current_plan = phase_output
        all_findings: List[dict] = []

        for round_num in range(1, effective_max + 1):
            session.round_number = round_num

            # 1. Dispatch critics in parallel
            critic_results = self._dispatch_critics(current_plan)

            # 2. Collect and classify findings
            findings = self._collect_findings(
                critic_results, session.id, round_num,
            )
            all_findings.extend(findings)

            # 3. Store findings in DB
            self._store_findings(findings)

            # 4. Compute severity counts
            counts = self._count_severities(findings)

            # 5. Compute consensus
            consensus = self._compute_consensus(counts)

            # 6. Update session
            session.consensus = consensus
            session.total_findings += len(findings)
            session.critical_count += counts["critical"]
            session.high_count += counts["high"]
            session.medium_count += counts["medium"]
            session.low_count += counts["low"]

            if consensus == "go":
                session.status = "go"
                session.completed_at = utc_now_iso()
                self._update_session(session)
                _audit(
                    "critique_completed", "atlas-critique",
                    f"ATLAS critique GO after round {round_num}",
                    project_id=project_id,
                    details={"session_id": session.id, "consensus": "go",
                             "rounds": round_num},
                    db_path=self._db_path,
                )
                break

            if consensus == "nogo":
                session.status = "nogo"
                session.completed_at = utc_now_iso()
                self._update_session(session)
                _audit(
                    "critique_completed", "atlas-critique",
                    f"ATLAS critique NOGO — {counts['critical']} critical findings",
                    project_id=project_id,
                    details={"session_id": session.id, "consensus": "nogo",
                             "critical_count": counts["critical"]},
                    db_path=self._db_path,
                )
                break

            # consensus == "conditional"
            if round_num < effective_max:
                # Request revision from architect
                revision = self._request_revision(findings, current_plan)
                current_plan = revision.get("revised_plan", current_plan)
                session.status = "revised"
                session.revision_summary = revision.get("summary", "")
                self._update_session(session)
                _audit(
                    "critique_revision_requested", "atlas-critique",
                    f"ATLAS critique revision round {round_num}",
                    project_id=project_id,
                    details={"session_id": session.id, "round": round_num},
                    db_path=self._db_path,
                )
            else:
                # Exhausted max rounds — remain conditional
                session.status = "conditional"
                session.completed_at = utc_now_iso()
                self._update_session(session)
                _audit(
                    "critique_completed", "atlas-critique",
                    f"ATLAS critique CONDITIONAL — max rounds ({effective_max}) exhausted",
                    project_id=project_id,
                    details={"session_id": session.id, "consensus": "conditional",
                             "high_count": session.high_count},
                    db_path=self._db_path,
                )

        return {
            "session_id": session.id,
            "project_id": project_id,
            "status": session.status,
            "consensus": session.consensus,
            "rounds_completed": session.round_number,
            "max_rounds": effective_max,
            "total_findings": session.total_findings,
            "critical_count": session.critical_count,
            "high_count": session.high_count,
            "medium_count": session.medium_count,
            "low_count": session.low_count,
            "revision_summary": session.revision_summary or None,
            "findings": all_findings,
        }

    def get_session_status(self, session_id: str) -> dict:
        """Return the status of a specific critique session."""
        conn = _get_db(self._db_path)
        try:
            row = conn.execute(
                "SELECT * FROM atlas_critique_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return {"error": f"Session {session_id} not found"}
            result = dict(row)
            # Fetch associated findings
            findings = conn.execute(
                "SELECT * FROM atlas_critique_findings WHERE session_id = ? "
                "ORDER BY round_number, severity",
                (session_id,),
            ).fetchall()
            result["findings"] = [dict(f) for f in findings]
            return result
        finally:
            conn.close()

    def get_history(self, project_id: str, limit: int = 20) -> dict:
        """Return critique session history for a project."""
        conn = _get_db(self._db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM atlas_critique_sessions "
                "WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
            sessions = [dict(r) for r in rows]
            return {
                "project_id": project_id,
                "total_sessions": len(sessions),
                "sessions": sessions,
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------
    def _create_session(
        self,
        project_id: str,
        phase_output: str,
        workflow_id: str = None,
        max_rounds: int = 3,
    ) -> CritiqueSession:
        """Create a new critique session and persist it."""
        session = CritiqueSession(
            id=f"crit-{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            workflow_id=workflow_id or "",
            phase_input_hash=hashlib.sha256(
                phase_output.encode("utf-8")
            ).hexdigest()[:16],
            status="in_progress",
            round_number=1,
            max_rounds=max_rounds,
            critics_assigned=[
                c["agent"] for c in self._config.get("critics", [])
            ],
            created_at=utc_now_iso(),
        )

        conn = _get_db(self._db_path)
        try:
            conn.execute(
                """INSERT INTO atlas_critique_sessions
                   (id, project_id, workflow_id, phase_input_hash, status,
                    round_number, max_rounds, critics_assigned, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session.id, session.project_id, session.workflow_id,
                 session.phase_input_hash, session.status,
                 session.round_number, session.max_rounds,
                 json.dumps(session.critics_assigned), session.created_at),
            )
            conn.commit()
        finally:
            conn.close()

        _audit(
            "critique_session_created", "atlas-critique",
            f"Created critique session {session.id}",
            project_id=project_id,
            details={"session_id": session.id,
                      "critics": session.critics_assigned},
            db_path=self._db_path,
        )
        return session

    def _update_session(self, session: CritiqueSession):
        """Update a session row (mutable fields only)."""
        conn = _get_db(self._db_path)
        try:
            conn.execute(
                """UPDATE atlas_critique_sessions
                   SET status = ?, round_number = ?, consensus = ?,
                       total_findings = ?, critical_count = ?,
                       high_count = ?, medium_count = ?, low_count = ?,
                       revision_summary = ?, completed_at = ?
                   WHERE id = ?""",
                (session.status, session.round_number, session.consensus,
                 session.total_findings, session.critical_count,
                 session.high_count, session.medium_count, session.low_count,
                 session.revision_summary, session.completed_at,
                 session.id),
            )
            conn.commit()
        finally:
            conn.close()

    def _dispatch_critics(self, phase_output: str) -> List[dict]:
        """Dispatch critique requests to all configured critic agents in parallel.

        Each critic returns a list of findings dicts.  Uses ThreadPoolExecutor
        (Decision D36) for parallel invocation.

        Returns:
            List of dicts, each with ``agent``, ``role``, and ``findings`` keys.
        """
        critics = self._config.get("critics", [])
        results: List[dict] = []

        def _invoke_critic(critic_cfg: dict) -> dict:
            agent = critic_cfg["agent"]
            role = critic_cfg.get("role", "reviewer")
            prompt_context = critic_cfg.get("prompt_context", "")
            focus_areas = critic_cfg.get("focus", [])

            prompt = (
                f"{prompt_context}\n\n"
                f"Focus on these finding types: {', '.join(focus_areas)}\n\n"
                f"Plan to review:\n{phase_output}\n\n"
                "Return your findings as a JSON array.  Each finding must have:\n"
                '  "finding_type": one of ' + str(FINDING_TYPES) + "\n"
                '  "severity": one of ' + str(SEVERITY_LEVELS) + "\n"
                '  "title": short title\n'
                '  "description": detailed description\n'
                '  "evidence": supporting evidence (optional)\n'
                '  "suggested_fix": recommended fix (optional)\n'
                '  "nist_controls": list of NIST control IDs (optional)\n'
            )

            findings = self._call_agent(agent, prompt, focus_areas)
            return {"agent": agent, "role": role, "findings": findings}

        with ThreadPoolExecutor(max_workers=len(critics) or 1) as executor:
            futures = {
                executor.submit(_invoke_critic, c): c for c in critics
            }
            for future in as_completed(futures):
                critic_cfg = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    logger.error(
                        "Critic %s failed: %s", critic_cfg["agent"], exc,
                    )
                    results.append({
                        "agent": critic_cfg["agent"],
                        "role": critic_cfg.get("role", "reviewer"),
                        "findings": [],
                        "error": str(exc),
                    })
        return results

    def _call_agent(
        self, agent_id: str, prompt: str, focus_areas: List[str],
    ) -> List[dict]:
        """Invoke an agent for critique via LLM router.

        Falls back to an empty findings list if the router is unavailable.
        """
        router = self._get_llm_router()
        if router is None:
            logger.warning("LLM router unavailable — returning empty findings for %s", agent_id)
            return []

        try:
            response = router.invoke(
                prompt=prompt,
                function="plan_review",
            )
            text = response.get("text", "") if isinstance(response, dict) else str(response)
            return self._parse_findings(text, focus_areas)
        except Exception as exc:
            logger.error("Agent %s invocation failed: %s", agent_id, exc)
            return []

    def _parse_findings(
        self, text: str, focus_areas: List[str],
    ) -> List[dict]:
        """Parse LLM response text into structured findings.

        Tries JSON parsing first, then falls back to extracting code blocks.
        Filters to only include valid finding types within focus areas.
        """
        findings = []

        # Try direct JSON parse
        parsed = _try_parse_json(text)
        if isinstance(parsed, list):
            findings = parsed
        elif isinstance(parsed, dict) and "findings" in parsed:
            findings = parsed["findings"]

        # Validate each finding
        valid = []
        for f in findings:
            if not isinstance(f, dict):
                continue
            ft = f.get("finding_type", "")
            sev = f.get("severity", "")
            title = f.get("title", "")
            if ft not in FINDING_TYPES or sev not in SEVERITY_LEVELS or not title:
                continue
            # Filter by focus area if specified
            if focus_areas and ft not in focus_areas:
                continue
            valid.append(f)
        return valid

    def _collect_findings(
        self,
        critic_results: List[dict],
        session_id: str,
        round_number: int,
    ) -> List[dict]:
        """Flatten critic results into a unified findings list with IDs."""
        collected = []
        now = utc_now_iso()
        for result in critic_results:
            agent = result["agent"]
            for f in result.get("findings", []):
                finding = {
                    "id": f"find-{uuid.uuid4().hex[:12]}",
                    "session_id": session_id,
                    "critic_agent": agent,
                    "round_number": round_number,
                    "finding_type": f.get("finding_type", "architecture_flaw"),
                    "severity": f.get("severity", "medium"),
                    "title": f.get("title", "Untitled finding"),
                    "description": f.get("description", ""),
                    "evidence": f.get("evidence", ""),
                    "suggested_fix": f.get("suggested_fix", ""),
                    "nist_controls": json.dumps(
                        f.get("nist_controls", [])
                    ),
                    "addressed_in_revision": 0,
                    "created_at": now,
                }
                collected.append(finding)
        return collected

    def _store_findings(self, findings: List[dict]):
        """Persist findings to the database (append-only)."""
        if not findings:
            return
        conn = _get_db(self._db_path)
        try:
            for f in findings:
                conn.execute(
                    """INSERT INTO atlas_critique_findings
                       (id, session_id, critic_agent, round_number,
                        finding_type, severity, title, description,
                        evidence, suggested_fix, nist_controls,
                        addressed_in_revision, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f["id"], f["session_id"], f["critic_agent"],
                     f["round_number"], f["finding_type"], f["severity"],
                     f["title"], f["description"], f["evidence"],
                     f["suggested_fix"], f["nist_controls"],
                     f["addressed_in_revision"], f["created_at"]),
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _count_severities(findings: List[dict]) -> Dict[str, int]:
        """Count findings by severity level."""
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for f in findings:
            sev = f.get("severity", "low")
            if sev in counts:
                counts[sev] += 1
        return counts

    def _compute_consensus(self, counts: Dict[str, int]) -> str:
        """Determine GO / NOGO / CONDITIONAL from severity counts.

        Rules (from config consensus_rules):
            - GO:          0 critical AND 0 high
            - CONDITIONAL: 0 critical but >0 high
            - NOGO:        >0 critical
        """
        rules = self._config.get("consensus_rules", {})

        # NOGO if any critical
        if counts["critical"] > 0:
            return "nogo"

        # GO if 0 critical AND 0 high
        go_rules = rules.get("go", {"max_critical": 0, "max_high": 0})
        if (counts["critical"] <= go_rules.get("max_critical", 0) and
                counts["high"] <= go_rules.get("max_high", 0)):
            return "go"

        # Otherwise CONDITIONAL
        return "conditional"

    def _request_revision(
        self, findings: List[dict], original_plan: str,
    ) -> dict:
        """Ask the architect agent to revise the plan based on findings.

        Returns:
            dict with ``revised_plan`` and ``summary`` keys.
        """
        # Build findings summary for the revision prompt
        high_and_critical = [
            f for f in findings
            if f.get("severity") in ("critical", "high")
        ]
        findings_text = "\n".join(
            f"- [{f.get('severity', 'unknown').upper()}] {f.get('title', 'N/A')}: "
            f"{f.get('description', '')}"
            for f in high_and_critical
        )

        revision_template = self._config.get(
            "revision_prompt",
            "Revise the plan.\n\nFindings:\n{findings}\n\nOriginal plan:\n{original_plan}",
        )
        prompt = revision_template.format(
            findings=findings_text,
            original_plan=original_plan,
        )

        router = self._get_llm_router()
        if router is None:
            return {
                "revised_plan": original_plan,
                "summary": "LLM router unavailable — plan unchanged",
            }

        try:
            response = router.invoke(
                prompt=prompt,
                function="plan_revision",
            )
            text = response.get("text", "") if isinstance(response, dict) else str(response)
            return {
                "revised_plan": text,
                "summary": f"Revised to address {len(high_and_critical)} findings",
            }
        except Exception as exc:
            logger.error("Revision request failed: %s", exc)
            return {
                "revised_plan": original_plan,
                "summary": f"Revision failed: {exc}",
            }


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------
def _try_parse_json(text: str):
    """Attempt to parse JSON from text, handling markdown code blocks."""
    text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting from ```json ... ``` blocks
    import re
    match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # Try finding first [ ... ] or { ... }
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                pass

    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="ATLAS Critique Phase — adversarial plan review",
    )
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument(
        "--phase-output",
        help="Assemble-phase output text or file path to review",
    )
    parser.add_argument("--workflow-id", help="Optional workflow correlation ID")
    parser.add_argument("--session-id", help="Session ID for status lookup")
    parser.add_argument("--max-rounds", type=int, help="Max revision rounds")
    parser.add_argument("--status", action="store_true", help="Get session status")
    parser.add_argument("--history", action="store_true", help="Get project critique history")
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output as JSON",
    )
    parser.add_argument("--db-path", type=Path, help="Override database path")
    parser.add_argument("--config-path", type=Path, help="Override config path")

    args = parser.parse_args()

    critique = AtlasCritique(
        db_path=args.db_path,
        config_path=args.config_path,
    )

    if args.status and args.session_id:
        result = critique.get_session_status(args.session_id)
    elif args.history:
        result = critique.get_history(args.project_id)
    elif args.phase_output:
        # Read from file if path exists, otherwise treat as text
        phase_text = args.phase_output
        phase_path = Path(args.phase_output)
        if phase_path.is_file():
            phase_text = phase_path.read_text(encoding="utf-8")

        result = critique.run_critique(
            project_id=args.project_id,
            phase_output=phase_text,
            workflow_id=args.workflow_id,
            max_rounds=args.max_rounds,
        )
    else:
        parser.print_help()
        sys.exit(1)

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        # Human-readable output
        try:
            from tools.cli.output_formatter import format_banner, format_table
            print(format_banner("ATLAS Critique Phase"))
        except ImportError:
            print("=" * 60)
            print("  ATLAS Critique Phase")
            print("=" * 60)

        if "error" in result:
            print(f"\nError: {result['error']}")
        elif "sessions" in result:
            print(f"\nProject: {result['project_id']}")
            print(f"Total sessions: {result['total_sessions']}")
            for s in result["sessions"]:
                print(
                    f"  {s['id']}  status={s['status']}  "
                    f"consensus={s.get('consensus', 'N/A')}  "
                    f"findings={s.get('total_findings', 0)}  "
                    f"created={s.get('created_at', '')}"
                )
        else:
            print(f"\nSession:   {result.get('session_id', 'N/A')}")
            print(f"Status:    {result.get('status', 'N/A')}")
            print(f"Consensus: {result.get('consensus', 'N/A')}")
            print(f"Rounds:    {result.get('rounds_completed', 0)}/{result.get('max_rounds', 3)}")
            print(
                f"Findings:  {result.get('total_findings', 0)} "
                f"(C:{result.get('critical_count', 0)} "
                f"H:{result.get('high_count', 0)} "
                f"M:{result.get('medium_count', 0)} "
                f"L:{result.get('low_count', 0)})"
            )


if __name__ == "__main__":
    main()
