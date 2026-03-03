#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""OWASP Top 10 Agentic AI Systems (ASI01-ASI10) Assessor.

Assesses compliance with the OWASP Top 10 Risks for Agentic AI
Applications. Maps 10 ASI risks to existing ICDEV controls via
automated checks against DB tables and configuration files.

Pattern: tools/compliance/base_assessor.py (BaseAssessor ABC).
ADR D339: BaseAssessor subclass with 10 automated checks.

Usage:
    python tools/compliance/owasp_asi_assessor.py --project-id proj-123
    python tools/compliance/owasp_asi_assessor.py --project-id proj-123 --gate
    python tools/compliance/owasp_asi_assessor.py --project-id proj-123 --json
"""

import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


class OWASPASIAssessor(BaseAssessor):
    FRAMEWORK_ID = "owasp_asi"
    FRAMEWORK_NAME = "OWASP Top 10 Agentic AI (ASI01-ASI10)"
    TABLE_NAME = "owasp_asi_assessments"
    CATALOG_FILENAME = "owasp_agentic_asi.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """OWASP ASI01-ASI10 automated checks.

        Maps each ASI risk to existing ICDEV controls:
        - ASI-01 (Goal Hijacking) <- prompt_injection_detector
        - ASI-02 (Tool Abuse) <- mcp_tool_authorizer + tool_chain_validator
        - ASI-03 (Identity Abuse) <- RBAC + agent_trust_scorer
        - ASI-04 (Supply Chain) <- AI BOM + marketplace scanning
        - ASI-05 (Code Execution) <- dangerous_pattern_detector
        - ASI-06 (Memory Poisoning) <- behavioral_drift + memory_consolidation
        - ASI-07 (Comms Compromise) <- A2A mTLS + HMAC signing config
        - ASI-08 (Cascading Failures) <- circuit_breaker + retry config
        - ASI-09 (Human Oversight) <- audit trail + HITL gates
        - ASI-10 (Rogue Agents) <- agent_trust_scoring + behavioral_red_team
        """
        results = {}
        conn = None

        try:
            if self.db_path.exists():
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                project_id = project.get("id", "")

                # ASI-01: Goal Hijacking — prompt injection detection active
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM prompt_injection_log
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["ASI-01"] = "satisfied"
                except Exception:
                    pass

                # ASI-02: Tool Abuse — tool chain validation + MCP authorization
                try:
                    chain_rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM tool_chain_events
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if chain_rows and chain_rows["cnt"] > 0:
                        results["ASI-02"] = "satisfied"
                except Exception:
                    pass

                # ASI-03: Identity Abuse — agent trust scores recorded
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM agent_trust_scores
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["ASI-03"] = "satisfied"
                except Exception:
                    pass

                # ASI-04: Supply Chain — AI BOM exists
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_bom
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["ASI-04"] = "satisfied"
                except Exception:
                    pass

                # ASI-06: Memory Poisoning — behavioral drift or memory consolidation
                try:
                    for table in ["ai_telemetry", "memory_consolidation_log"]:
                        rows = conn.execute(
                            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?",
                            (project_id,),
                        ).fetchone()
                        if rows and rows["cnt"] > 0:
                            results["ASI-06"] = "satisfied"
                            break
                except Exception:
                    pass

                # ASI-09: Human Oversight — audit trail records
                try:
                    rows = conn.execute(
                        """SELECT COUNT(*) as cnt FROM audit_trail
                           WHERE project_id = ?""",
                        (project_id,),
                    ).fetchone()
                    if rows and rows["cnt"] > 0:
                        results["ASI-09"] = "satisfied"
                except Exception:
                    pass

                # ASI-10: Rogue Agents — trust scores or red team results
                try:
                    for table in ["agent_trust_scores", "atlas_red_team_results"]:
                        rows = conn.execute(
                            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?",
                            (project_id,),
                        ).fetchone()
                        if rows and rows["cnt"] > 0:
                            results["ASI-10"] = "satisfied"
                            break
                except Exception:
                    pass

        except Exception:
            pass
        finally:
            if conn:
                conn.close()

        # File-based checks (config files in project dir or ICDEV base)
        check_dirs = [Path(project_dir)] if project_dir else []
        check_dirs.append(BASE_DIR)

        for check_dir in check_dirs:
            if not check_dir.exists():
                continue

            # ASI-05: Code Execution — dangerous pattern config exists
            if "ASI-05" not in results:
                code_pattern_config = check_dir / "args" / "code_pattern_config.yaml"
                if code_pattern_config.exists():
                    results["ASI-05"] = "satisfied"

            # ASI-07: Communication Compromise — A2A/HMAC config
            if "ASI-07" not in results:
                agent_config = check_dir / "args" / "agent_config.yaml"
                if agent_config.exists():
                    try:
                        content = agent_config.read_text(encoding="utf-8", errors="ignore").lower()
                        if ("tls" in content or "mtls" in content or "hmac" in content):
                            results["ASI-07"] = "satisfied"
                    except Exception:
                        pass

            # ASI-08: Cascading Failures — resilience config
            if "ASI-08" not in results:
                resilience_config = check_dir / "args" / "resilience_config.yaml"
                if resilience_config.exists():
                    try:
                        content = resilience_config.read_text(encoding="utf-8", errors="ignore").lower()
                        if "circuit_breaker" in content or "retry" in content:
                            results["ASI-08"] = "satisfied"
                    except Exception:
                        pass

        return results


if __name__ == "__main__":
    OWASPASIAssessor().run_cli()
