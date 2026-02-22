#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""MITRE ATLAS v5.4.0 Assessment Engine.

Assesses projects against MITRE ATLAS (Adversarial Threat Landscape for
AI Systems) mitigations. ATLAS catalogs adversarial ML techniques and
maps mitigations to NIST 800-53 controls via the crosswalk engine.

Automated checks verify:
- Prompt injection detection (M0015)
- AI telemetry / model monitoring (M0024)
- BYOK encryption for model keys (M0012)
- Marketplace asset signing (M0013)
- API gateway authentication (M0019)
- Command allowlists for remote execution (M0026)

Usage:
    python tools/compliance/atlas_assessor.py --project-id proj-123
    python tools/compliance/atlas_assessor.py --project-id proj-123 --gate
    python tools/compliance/atlas_assessor.py --project-id proj-123 --json
    python tools/compliance/atlas_assessor.py --project-id proj-123 --human
"""

import json
import sys
from pathlib import Path
from typing import Dict, Optional

# Ensure base module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))
from base_assessor import BaseAssessor

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


class ATLASAssessor(BaseAssessor):
    """MITRE ATLAS v5.4.0 compliance assessor.

    Evaluates AI/ML security posture against ATLAS mitigations.
    Inherits NIST 800-53 implementations via crosswalk where applicable.
    """

    FRAMEWORK_ID = "atlas"
    FRAMEWORK_NAME = "MITRE ATLAS v5.4.0"
    TABLE_NAME = "atlas_assessments"
    CATALOG_FILENAME = "atlas_mitigations.json"

    def get_automated_checks(
        self, project: Dict, project_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """ATLAS-specific automated checks.

        Checks for:
        - M0015: Prompt injection detector exists in project
        - M0024: AI telemetry has recent entries in DB
        - M0012: BYOK encryption configured for model keys
        - M0013: Marketplace asset signing is active
        - M0019: API gateway authentication is enabled
        - M0026: Command allowlists configured for remote execution
        """
        results: Dict[str, str] = {}

        # ---------------------------------------------------------------
        # M0015 — Adversarial Input Detection (Prompt Injection)
        # Check if project contains prompt injection detection tooling
        # ---------------------------------------------------------------
        if project_dir:
            project_path = Path(project_dir)
            found_prompt_detector = False
            search_patterns = ["*.py", "*.yaml", "*.yml", "*.json"]
            for pattern in search_patterns:
                for fpath in project_path.rglob(pattern):
                    try:
                        content = fpath.read_text(
                            encoding="utf-8", errors="ignore"
                        )
                        if any(kw in content.lower() for kw in [
                            "prompt_injection",
                            "prompt injection",
                            "input_validation",
                            "input_sanitiz",
                            "adversarial_input",
                        ]):
                            found_prompt_detector = True
                            break
                    except Exception:
                        continue
                if found_prompt_detector:
                    break

            if found_prompt_detector:
                results["M0015"] = "satisfied"
            else:
                results["M0015"] = "not_satisfied"

        # ---------------------------------------------------------------
        # M0024 — AI Telemetry / Model Monitoring
        # Check if ai_telemetry or agent_token_usage has recent entries
        # ---------------------------------------------------------------
        try:
            conn = self._get_connection()
            try:
                # Check agent_token_usage for recent AI telemetry
                row = conn.execute(
                    """SELECT COUNT(*) as cnt FROM agent_token_usage
                       WHERE project_id = ?
                       AND timestamp >= datetime('now', '-30 days')""",
                    (project.get("id", ""),),
                ).fetchone()
                if row and row["cnt"] > 0:
                    results["M0024"] = "satisfied"
                else:
                    results["M0024"] = "not_satisfied"
            except Exception:
                # Table may not exist
                results["M0024"] = "not_assessed"
            finally:
                conn.close()
        except Exception:
            results["M0024"] = "not_assessed"

        # ---------------------------------------------------------------
        # M0012 — Encrypt ML Artifacts (BYOK encryption)
        # Check if BYOK encryption is configured in dashboard_user_llm_keys
        # or if encryption settings exist in config
        # ---------------------------------------------------------------
        try:
            conn = self._get_connection()
            try:
                # Check for BYOK encryption entries
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM dashboard_user_llm_keys"
                ).fetchone()
                byok_configured = row and row["cnt"] > 0

                # Also check for encryption config files
                encryption_in_config = False
                config_path = BASE_DIR / "args"
                if config_path.exists():
                    for cfg in config_path.glob("*.yaml"):
                        try:
                            content = cfg.read_text(
                                encoding="utf-8", errors="ignore"
                            )
                            if "encrypt" in content.lower() and (
                                "byok" in content.lower()
                                or "fernet" in content.lower()
                                or "aes" in content.lower()
                            ):
                                encryption_in_config = True
                                break
                        except Exception:
                            continue

                if byok_configured or encryption_in_config:
                    results["M0012"] = "satisfied"
                else:
                    results["M0012"] = "not_satisfied"
            except Exception:
                results["M0012"] = "not_assessed"
            finally:
                conn.close()
        except Exception:
            results["M0012"] = "not_assessed"

        # ---------------------------------------------------------------
        # M0013 — Code Signing (Marketplace asset signing)
        # Check if marketplace scan pipeline enforces signing
        # ---------------------------------------------------------------
        try:
            conn = self._get_connection()
            try:
                # Check marketplace_scan_results for signing checks
                row = conn.execute(
                    """SELECT COUNT(*) as cnt FROM marketplace_scan_results
                       WHERE scan_type = 'signature'
                       AND status = 'passed'"""
                ).fetchone()
                if row and row["cnt"] > 0:
                    results["M0013"] = "satisfied"
                else:
                    # Also check if attestation_manager config exists
                    attestation_path = (
                        BASE_DIR / "tools" / "devsecops"
                        / "attestation_manager.py"
                    )
                    if attestation_path.exists():
                        results["M0013"] = "partially_satisfied"
                    else:
                        results["M0013"] = "not_satisfied"
            except Exception:
                results["M0013"] = "not_assessed"
            finally:
                conn.close()
        except Exception:
            results["M0013"] = "not_assessed"

        # ---------------------------------------------------------------
        # M0019 — Access Restriction (API Gateway Auth)
        # Check if API gateway auth is configured
        # ---------------------------------------------------------------
        try:
            conn = self._get_connection()
            try:
                # Check for API key entries in the platform
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM dashboard_api_keys"
                ).fetchone()
                api_keys_configured = row and row["cnt"] > 0

                # Check for auth middleware existence
                auth_middleware = (
                    BASE_DIR / "tools" / "saas" / "auth" / "middleware.py"
                )
                has_auth = auth_middleware.exists()

                if api_keys_configured and has_auth:
                    results["M0019"] = "satisfied"
                elif has_auth:
                    results["M0019"] = "partially_satisfied"
                else:
                    results["M0019"] = "not_satisfied"
            except Exception:
                results["M0019"] = "not_assessed"
            finally:
                conn.close()
        except Exception:
            results["M0019"] = "not_assessed"

        # ---------------------------------------------------------------
        # M0026 — Restrict Command / Query Input (Command Allowlists)
        # Check if remote gateway command allowlists are configured
        # ---------------------------------------------------------------
        allowlist_config = (
            BASE_DIR / "args" / "remote_gateway_config.yaml"
        )
        if allowlist_config.exists():
            try:
                content = allowlist_config.read_text(
                    encoding="utf-8", errors="ignore"
                )
                if "allowlist" in content.lower() or "allow_list" in content.lower():
                    results["M0026"] = "satisfied"
                else:
                    results["M0026"] = "partially_satisfied"
            except Exception:
                results["M0026"] = "not_assessed"
        else:
            # Check for any command restriction in DB
            try:
                conn = self._get_connection()
                try:
                    row = conn.execute(
                        """SELECT COUNT(*) as cnt
                           FROM remote_command_allowlist"""
                    ).fetchone()
                    if row and row["cnt"] > 0:
                        results["M0026"] = "satisfied"
                    else:
                        results["M0026"] = "not_satisfied"
                except Exception:
                    results["M0026"] = "not_assessed"
                finally:
                    conn.close()
            except Exception:
                results["M0026"] = "not_assessed"

        return results


if __name__ == "__main__":
    ATLASAssessor().run_cli()
