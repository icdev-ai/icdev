#!/usr/bin/env python3
# CUI // SP-CTI
"""Xacta 360 REST API client for CSSP/ATO integration.

Pushes assessment data, evidence, and compliance artifacts to Xacta 360
(system of record for CSSP/ATO). Supports PKI/certificate-based auth
per DoD environment requirements.

Usage:
    from tools.compliance.xacta.xacta_client import XactaClient
    client = XactaClient()
    client.push_system(project_data)
    client.push_assessment(project_id, assessment_results)
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

try:
    import yaml
except ImportError:
    yaml = None

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
DEFAULTS_PATH = BASE_DIR / "args" / "project_defaults.yaml"

logger = logging.getLogger("icdev.xacta")


def _load_xacta_config():
    """Load Xacta configuration from project defaults."""
    defaults = {
        "api_base_url": "https://xacta.govcloud.local/api/v1",
        "auth_method": "pki",
        "client_cert_secret": "xacta/client-cert",
        "client_key_secret": "xacta/client-key",
        "ca_bundle_secret": "xacta/ca-bundle",
        "sync_mode": "hybrid",
        "export_format": "oscal",
        "export_dir": "compliance/xacta-exports",
        "auto_sync": True,
        "timeout": 30,
        "max_retries": 3,
        "retry_backoff": 2,
    }

    if yaml and DEFAULTS_PATH.exists():
        try:
            with open(DEFAULTS_PATH, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            if config and "xacta" in config:
                defaults.update(config["xacta"])
        except Exception:
            pass

    return defaults


def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _log_audit(conn, project_id, action, details=None):
    """Log Xacta sync event to audit trail."""
    conn.execute(
        """INSERT INTO audit_trail
           (project_id, event_type, actor, action, details, classification, created_at)
           VALUES (?, 'xacta_sync', 'icdev-xacta-client', ?, ?, 'CUI', datetime('now'))""",
        (project_id, action, json.dumps(details) if details else None),
    )
    conn.commit()


class XactaClient:
    """REST API client for Xacta 360 GRC platform."""

    def __init__(self, config=None, db_path=None):
        """Initialize Xacta client with configuration.

        Args:
            config: Optional override config dict. If None, loads from project_defaults.yaml
            db_path: Optional database path override.
        """
        self.config = config or _load_xacta_config()
        self.db_path = db_path or DB_PATH
        self.base_url = self.config["api_base_url"].rstrip("/")
        self.timeout = self.config.get("timeout", 30)
        self.max_retries = self.config.get("max_retries", 3)
        self.retry_backoff = self.config.get("retry_backoff", 2)
        self._session = None

    def _get_session(self):
        """Get or create requests session with PKI auth."""
        if self._session is not None:
            return self._session

        if requests is None:
            raise ImportError(
                "requests library required for Xacta API. "
                "Install with: pip install requests"
            )

        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Classification": "CUI",
            "User-Agent": "ICDEV-Compliance-Engine/1.0",
        })

        # PKI/certificate-based auth
        if self.config.get("auth_method") == "pki":
            cert_path = self.config.get("client_cert_path")
            key_path = self.config.get("client_key_path")
            ca_path = self.config.get("ca_bundle_path")

            if cert_path and key_path:
                self._session.cert = (cert_path, key_path)
            if ca_path:
                self._session.verify = ca_path

        return self._session

    def _request(self, method, endpoint, data=None, params=None):
        """Make an API request with retry logic.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE)
            endpoint: API endpoint path (e.g., "/systems")
            data: Request body dict
            params: Query parameters dict

        Returns:
            Response dict or None on failure.
        """
        url = f"{self.base_url}{endpoint}"
        session = self._get_session()
        last_error = None

        for attempt in range(self.max_retries):
            try:
                response = session.request(
                    method=method,
                    url=url,
                    json=data,
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                if response.content:
                    return response.json()
                return {"status": "success", "code": response.status_code}

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "Xacta API attempt %d/%d failed: %s",
                    attempt + 1, self.max_retries, last_error
                )
                if attempt < self.max_retries - 1:
                    wait = self.retry_backoff ** attempt
                    time.sleep(wait)

        logger.error("Xacta API request failed after %d attempts: %s", self.max_retries, last_error)
        return {"status": "error", "error": last_error}

    def push_system(self, project_data):
        """Register or update a system in Xacta 360.

        Args:
            project_data: Dict with project fields (id, name, description, type, classification)

        Returns:
            Dict with Xacta system_id and status.
        """
        payload = {
            "system_name": project_data.get("name", ""),
            "system_id": project_data.get("id", ""),
            "description": project_data.get("description", ""),
            "classification": project_data.get("classification", "CUI"),
            "system_type": project_data.get("type", "webapp"),
            "status": "operational",
            "authorization_boundary": project_data.get("directory_path", ""),
            "impact_level": "Moderate",
            "source": "ICDEV",
        }
        result = self._request("POST", "/systems", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, project_data.get("id"), "push_system", {
                "system_name": payload["system_name"],
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    def push_controls(self, project_id, control_mappings):
        """Push control implementations to Xacta.

        Args:
            project_id: ICDEV project ID
            control_mappings: List of control mapping dicts from project_controls table

        Returns:
            Dict with push result summary.
        """
        payload = {
            "system_id": project_id,
            "controls": [
                {
                    "control_id": m.get("control_id", ""),
                    "implementation_status": m.get("implementation_status", "planned"),
                    "implementation_description": m.get("implementation_description", ""),
                    "responsible_role": m.get("responsible_role", ""),
                    "evidence_path": m.get("evidence_path", ""),
                    "last_assessed": m.get("last_assessed", ""),
                }
                for m in control_mappings
            ],
            "source": "ICDEV",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        result = self._request("POST", f"/systems/{project_id}/controls", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, project_id, "push_controls", {
                "control_count": len(control_mappings),
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    def push_assessment(self, project_id, assessment_results):
        """Push CSSP assessment results to Xacta.

        Args:
            project_id: ICDEV project ID
            assessment_results: List of cssp_assessments dicts

        Returns:
            Dict with push result summary.
        """
        payload = {
            "system_id": project_id,
            "assessment_type": "CSSP",
            "framework": "DoD Instruction 8530.01",
            "assessment_date": datetime.now(timezone.utc).isoformat(),
            "assessor": "icdev-compliance-engine",
            "results": [
                {
                    "requirement_id": r.get("requirement_id", ""),
                    "functional_area": r.get("functional_area", ""),
                    "status": r.get("status", "not_assessed"),
                    "evidence_description": r.get("evidence_description", ""),
                    "evidence_path": r.get("evidence_path", ""),
                    "notes": r.get("notes", ""),
                }
                for r in assessment_results
            ],
            "source": "ICDEV",
        }
        result = self._request("POST", f"/systems/{project_id}/assessments", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, project_id, "push_assessment", {
                "requirement_count": len(assessment_results),
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    def push_findings(self, project_id, findings):
        """Push STIG/security findings to Xacta.

        Args:
            project_id: ICDEV project ID
            findings: List of stig_findings dicts

        Returns:
            Dict with push result summary.
        """
        payload = {
            "system_id": project_id,
            "finding_type": "STIG",
            "findings": [
                {
                    "finding_id": f.get("finding_id", ""),
                    "stig_id": f.get("stig_id", ""),
                    "rule_id": f.get("rule_id", ""),
                    "severity": f.get("severity", ""),
                    "title": f.get("title", ""),
                    "status": f.get("status", "Open"),
                    "comments": f.get("comments", ""),
                }
                for f in findings
            ],
            "source": "ICDEV",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        result = self._request("POST", f"/systems/{project_id}/findings", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, project_id, "push_findings", {
                "finding_count": len(findings),
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    def push_poam(self, project_id, poam_items):
        """Push POA&M items to Xacta.

        Args:
            project_id: ICDEV project ID
            poam_items: List of poam_items dicts

        Returns:
            Dict with push result summary.
        """
        payload = {
            "system_id": project_id,
            "poam_items": [
                {
                    "weakness_id": p.get("weakness_id", ""),
                    "weakness_description": p.get("weakness_description", ""),
                    "severity": p.get("severity", ""),
                    "source": p.get("source", ""),
                    "status": p.get("status", "open"),
                    "corrective_action": p.get("corrective_action", ""),
                    "milestone_date": p.get("milestone_date", ""),
                    "responsible_party": p.get("responsible_party", ""),
                }
                for p in poam_items
            ],
            "source": "ICDEV",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        result = self._request("POST", f"/systems/{project_id}/poam", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, project_id, "push_poam", {
                "poam_count": len(poam_items),
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    def push_evidence(self, project_id, evidence_manifest):
        """Upload evidence artifacts manifest to Xacta.

        Args:
            project_id: ICDEV project ID
            evidence_manifest: Evidence manifest dict from cssp_evidence_collector

        Returns:
            Dict with push result summary.
        """
        payload = {
            "system_id": project_id,
            "evidence": evidence_manifest,
            "source": "ICDEV",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        result = self._request("POST", f"/systems/{project_id}/evidence", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, project_id, "push_evidence", {
                "artifact_count": evidence_manifest.get("metadata", {}).get("total_artifacts", 0),
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    def get_system_status(self, project_id):
        """Pull current ATO status from Xacta.

        Args:
            project_id: ICDEV project ID

        Returns:
            Dict with system status from Xacta.
        """
        return self._request("GET", f"/systems/{project_id}/status")

    def get_certification_status(self, project_id):
        """Pull CSSP certification status from Xacta.

        Args:
            project_id: ICDEV project ID

        Returns:
            Dict with certification details.
        """
        return self._request("GET", f"/systems/{project_id}/certification")

    def test_connection(self):
        """Test connectivity to Xacta 360 API.

        Returns:
            Dict with connection test result.
        """
        result = self._request("GET", "/health")
        if result and result.get("status") != "error":
            return {"connected": True, "endpoint": self.base_url, "details": result}
        return {"connected": False, "endpoint": self.base_url, "error": result.get("error", "Unknown")}

    def close(self):
        """Close the requests session."""
        if self._session:
            self._session.close()
            self._session = None


# CUI // SP-CTI
