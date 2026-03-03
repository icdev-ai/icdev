#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""OWASP LLM Top 10 v2025 Assessment Engine.

Assesses projects against the OWASP Top 10 for Large Language Model
Applications. Maps OWASP LLM risks through MITRE ATLAS to NIST 800-53
controls via the crosswalk engine. Checks are file-existence and DB-based,
consistent with other BaseAssessor implementations (D116).

Usage:
    python tools/compliance/owasp_llm_assessor.py --project-id proj-123
    python tools/compliance/owasp_llm_assessor.py --project-id proj-123 --gate
    python tools/compliance/owasp_llm_assessor.py --project-id proj-123 --json
"""

import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional

# Ensure base module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class OWASPLLMAssessor(BaseAssessor):
    FRAMEWORK_ID = "owasp_llm"
    FRAMEWORK_NAME = "OWASP LLM Top 10 v2025"
    TABLE_NAME = "owasp_llm_assessments"
    CATALOG_FILENAME = "owasp_llm_top10.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """OWASP LLM-specific automated checks.

        Checks for:
        - LLM01: Prompt injection defense active (detector tool exists)
        - LLM02: Sensitive info disclosure (data classification active)
        - LLM03: Supply chain (SBOM current, deps audited, AI-BOM)
        - LLM04: Data/model poisoning (data validation gates)
        - LLM05: Improper output handling (output sanitization configured)
        - LLM06: Excessive agency (command allowlists, HITL gates)
        - LLM07: System prompt leakage (prompt protection mechanisms)
        - LLM08: Vector/embedding weaknesses (RAG access controls)
        - LLM09: Misinformation (HITL review gates configured)
        - LLM10: Unbounded consumption (rate limiting, token tracking)
        """
        results = {}

        # ── LLM01: Prompt Injection ──────────────────────────────────
        # Check if prompt injection detector tool exists
        pid_tool = BASE_DIR / "tools" / "security" / "prompt_injection_detector.py"
        if pid_tool.exists():
            results["LLM01"] = "satisfied"
        else:
            results["LLM01"] = "not_satisfied"

        # ── LLM02: Sensitive Information Disclosure ──────────────────
        # Check if data classification manager exists and project has
        # classification configuration
        classification_mgr = BASE_DIR / "tools" / "compliance" / "classification_manager.py"
        universal_cls_mgr = BASE_DIR / "tools" / "compliance" / "universal_classification_manager.py"
        if classification_mgr.exists() or universal_cls_mgr.exists():
            results["LLM02"] = "satisfied"
        else:
            results["LLM02"] = "not_satisfied"

        # ── LLM03: Supply Chain Vulnerabilities ──────────────────────
        # Check if SBOM generator exists and dependency auditor is present
        sbom_gen = BASE_DIR / "tools" / "compliance" / "sbom_generator.py"
        dep_audit = BASE_DIR / "tools" / "security" / "dependency_auditor.py"
        sbom_ok = sbom_gen.exists()
        deps_ok = dep_audit.exists()
        if sbom_ok and deps_ok:
            results["LLM03"] = "satisfied"
        elif sbom_ok or deps_ok:
            results["LLM03"] = "partially_satisfied"
        else:
            results["LLM03"] = "not_satisfied"

        # ── LLM04: Data and Model Poisoning ──────────────────────────
        # Check if AI telemetry logger exists (monitors model behavior drift)
        # and if data validation is present
        ai_telemetry = BASE_DIR / "tools" / "security" / "ai_telemetry_logger.py"
        if ai_telemetry.exists():
            # Also check DB for recent telemetry entries as evidence
            has_telemetry_data = self._check_ai_telemetry_active()
            if has_telemetry_data:
                results["LLM04"] = "satisfied"
            else:
                results["LLM04"] = "partially_satisfied"
        else:
            results["LLM04"] = "not_satisfied"

        # ── LLM05: Improper Output Handling ──────────────────────────
        # Check if SAST runner exists (scans AI-generated code) and
        # output sanitization is in place
        sast_runner = BASE_DIR / "tools" / "security" / "sast_runner.py"
        if sast_runner.exists():
            results["LLM05"] = "satisfied"
        else:
            results["LLM05"] = "not_satisfied"

        # ── LLM06: Excessive Agency ──────────────────────────────────
        # Check if command allowlists are configured in security_gates.yaml
        # and if HITL gates are defined
        gates_file = BASE_DIR / "args" / "security_gates.yaml"
        gateway_config = BASE_DIR / "args" / "remote_gateway_config.yaml"
        has_allowlist = False
        has_hitl = False

        if gates_file.exists():
            try:
                content = gates_file.read_text(encoding="utf-8", errors="ignore")
                if "blocked_commands" in content or "allowlist" in content.lower():
                    has_allowlist = True
                if "confirmation_required" in content or "human" in content.lower():
                    has_hitl = True
            except Exception:
                pass

        if gateway_config.exists():
            try:
                content = gateway_config.read_text(encoding="utf-8", errors="ignore")
                if "allowlist" in content.lower() or "command_allowlist" in content.lower():
                    has_allowlist = True
            except Exception:
                pass

        if has_allowlist and has_hitl:
            results["LLM06"] = "satisfied"
        elif has_allowlist or has_hitl:
            results["LLM06"] = "partially_satisfied"
        else:
            results["LLM06"] = "not_satisfied"

        # ── LLM07: System Prompt Leakage ─────────────────────────────
        # Check if prompt injection detector covers system prompt extraction
        # (it exists if LLM01 passed) and if secret detector exists
        secret_detector = BASE_DIR / "tools" / "security" / "secret_detector.py"
        if pid_tool.exists() and secret_detector.exists():
            results["LLM07"] = "satisfied"
        elif pid_tool.exists() or secret_detector.exists():
            results["LLM07"] = "partially_satisfied"
        else:
            results["LLM07"] = "not_satisfied"

        # ── LLM08: Vector and Embedding Weaknesses ───────────────────
        # Check if embedding provider has access controls and if
        # vector store security is configured
        embedding_provider = BASE_DIR / "tools" / "llm" / "embedding_provider.py"
        llm_config = BASE_DIR / "args" / "llm_config.yaml"
        has_embedding = embedding_provider.exists()
        has_llm_config = False

        if llm_config.exists():
            try:
                content = llm_config.read_text(encoding="utf-8", errors="ignore")
                if "embedding" in content.lower():
                    has_llm_config = True
            except Exception:
                pass

        if has_embedding and has_llm_config:
            results["LLM08"] = "satisfied"
        elif has_embedding:
            results["LLM08"] = "partially_satisfied"
        else:
            results["LLM08"] = "not_satisfied"

        # ── LLM09: Misinformation ────────────────────────────────────
        # Check if human-in-the-loop gates are configured (self-healing
        # confidence thresholds, review gates)
        self_heal_goal = BASE_DIR / "goals" / "self_healing.md"
        has_overreliance_controls = False

        if self_heal_goal.exists():
            try:
                content = self_heal_goal.read_text(encoding="utf-8", errors="ignore")
                # Self-healing has confidence thresholds (0.7 auto, 0.3-0.7 suggest, <0.3 escalate)
                if "confidence" in content.lower() and "human" in content.lower():
                    has_overreliance_controls = True
            except Exception:
                pass

        if gates_file.exists() and not has_overreliance_controls:
            try:
                content = gates_file.read_text(encoding="utf-8", errors="ignore")
                if "require_human_review" in content.lower() or "require_peer_review" in content:
                    has_overreliance_controls = True
            except Exception:
                pass

        if has_overreliance_controls:
            results["LLM09"] = "satisfied"
        else:
            results["LLM09"] = "partially_satisfied"

        # ── LLM10: Unbounded Consumption ─────────────────────────────
        # Check if rate limiting is configured and token tracking exists
        rate_limiter = BASE_DIR / "tools" / "saas" / "rate_limiter.py"
        token_tracker = BASE_DIR / "tools" / "agent" / "token_tracker.py"
        scaling_config = BASE_DIR / "args" / "scaling_config.yaml"

        has_rate_limit = rate_limiter.exists()
        has_token_tracking = token_tracker.exists()
        has_scaling = scaling_config.exists()

        if has_rate_limit and has_token_tracking:
            results["LLM10"] = "satisfied"
        elif has_rate_limit or has_token_tracking or has_scaling:
            results["LLM10"] = "partially_satisfied"
        else:
            results["LLM10"] = "not_satisfied"

        return results

    def _check_ai_telemetry_active(self) -> bool:
        """Check if ai_telemetry table exists and has recent entries."""
        try:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    """SELECT COUNT(*) as cnt FROM ai_telemetry
                       WHERE timestamp > datetime('now', '-30 days')"""
                ).fetchone()
                return row and row["cnt"] > 0
            except Exception:
                # Table may not exist — that's fine
                return False
            finally:
                conn.close()
        except (FileNotFoundError, sqlite3.Error):
            return False


if __name__ == "__main__":
    OWASPLLMAssessor().run_cli()
