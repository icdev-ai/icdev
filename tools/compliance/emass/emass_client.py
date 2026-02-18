#!/usr/bin/env python3
# CUI // SP-CTI
"""eMASS (Enterprise Mission Assurance Support Service) REST API client.

Pushes compliance data, POA&M items, control implementations, artifacts,
and test results to eMASS (system of record for DoD ATO). Supports
PKI/CAC certificate-based authentication per DoD environment requirements.

eMASS API reference: REST API v3.12 with PKI/CAC auth.

Usage:
    from tools.compliance.emass.emass_client import EMASSClient
    client = EMASSClient()
    client.get_systems()
    client.push_controls(system_id, controls)
"""

import json
import logging
import sqlite3
import time
from datetime import datetime
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

logger = logging.getLogger("icdev.emass")


def _load_emass_config():
    """Load eMASS configuration from project defaults.

    Reads the ``emass:`` section from ``args/project_defaults.yaml``.
    Falls back to sensible defaults if the file is missing or the
    section is absent.

    Returns:
        Dict of eMASS configuration values.
    """
    defaults = {
        "api_base_url": "https://emass.apps.disa.mil/api",
        "api_version": "v3",
        "auth_method": "pki",
        "client_cert_secret": "emass/client-cert",
        "client_key_secret": "emass/client-key",
        "ca_bundle_secret": "emass/ca-bundle",
        "api_key_secret": "emass/api-key",
        "sync_mode": "hybrid",
        "export_format": "csv",
        "export_dir": "compliance/emass-exports",
        "auto_sync": True,
        "timeout": 30,
        "max_retries": 3,
        "retry_backoff": 2,
        "rate_limit_per_minute": 60,
    }

    if yaml and DEFAULTS_PATH.exists():
        try:
            with open(DEFAULTS_PATH, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            if config and "emass" in config:
                defaults.update(config["emass"])
        except Exception:
            pass

    return defaults


def _get_connection(db_path=None):
    """Get a database connection.

    Args:
        db_path: Optional override for database file path.

    Returns:
        sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _log_audit(conn, project_id, action, details=None):
    """Log eMASS sync event to the immutable audit trail.

    Args:
        conn: Active database connection.
        project_id: ICDEV project identifier.
        action: Short action description (e.g., ``push_controls``).
        details: Optional dict of additional context.
    """
    conn.execute(
        """INSERT INTO audit_trail
           (project_id, event_type, actor, action, details, classification, created_at)
           VALUES (?, 'emass_sync', 'icdev-emass-client', ?, ?, 'CUI', datetime('now'))""",
        (project_id, action, json.dumps(details) if details else None),
    )
    conn.commit()


class EMASSClient:
    """REST API client for eMASS (Enterprise Mission Assurance Support Service).

    Mirrors the structure of XactaClient but targets the eMASS v3.12 REST API.
    Supports PKI/CAC certificate authentication, API key header injection,
    exponential-backoff retry, and rate-limit handling (HTTP 429).
    """

    def __init__(self, config=None, db_path=None):
        """Initialize the eMASS client.

        Args:
            config: Optional override config dict. If ``None``, loads from
                    ``args/project_defaults.yaml`` ``emass:`` section.
            db_path: Optional database path override.
        """
        self.config = config or _load_emass_config()
        self.db_path = db_path or DB_PATH
        api_version = self.config.get("api_version", "v3")
        base = self.config["api_base_url"].rstrip("/")
        # Ensure the version segment is present in the base URL.
        if not base.endswith(f"/{api_version}"):
            base = f"{base}/{api_version}"
        self.base_url = base
        self.timeout = self.config.get("timeout", 30)
        self.max_retries = self.config.get("max_retries", 3)
        self.retry_backoff = self.config.get("retry_backoff", 2)
        self.rate_limit_per_minute = self.config.get("rate_limit_per_minute", 60)
        self._session = None
        self._request_timestamps = []

    # ------------------------------------------------------------------
    # Session / transport helpers
    # ------------------------------------------------------------------

    def _get_session(self):
        """Get or create a ``requests.Session`` with PKI auth and headers.

        Returns:
            Configured ``requests.Session``.

        Raises:
            ImportError: If the ``requests`` library is not installed.
        """
        if self._session is not None:
            return self._session

        if requests is None:
            raise ImportError(
                "requests library required for eMASS API. "
                "Install with: pip install requests"
            )

        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Classification": "CUI",
            "User-Agent": "ICDEV-Compliance-Engine/1.0",
        })

        # eMASS API key header (required in addition to PKI).
        api_key = self.config.get("api_key")
        if api_key:
            self._session.headers["api-key"] = api_key

        # PKI/CAC certificate-based authentication.
        if self.config.get("auth_method") == "pki":
            cert_path = self.config.get("client_cert_path")
            key_path = self.config.get("client_key_path")
            ca_path = self.config.get("ca_bundle_path")

            if cert_path and key_path:
                self._session.cert = (cert_path, key_path)
            if ca_path:
                self._session.verify = ca_path

        return self._session

    def _enforce_rate_limit(self):
        """Enforce per-minute rate limiting.

        Blocks (sleeps) if the number of requests in the last 60 seconds
        exceeds ``rate_limit_per_minute``.
        """
        now = time.time()
        # Prune timestamps older than 60 seconds.
        self._request_timestamps = [
            ts for ts in self._request_timestamps if now - ts < 60
        ]
        if len(self._request_timestamps) >= self.rate_limit_per_minute:
            wait = 60 - (now - self._request_timestamps[0])
            if wait > 0:
                logger.info("eMASS rate limit reached, sleeping %.1f seconds", wait)
                time.sleep(wait)

    def _request(self, method, endpoint, data=None, params=None, retry=True):
        """Make an HTTP request to the eMASS API with retry logic.

        Handles:
            - Rate limiting (429) with exponential backoff.
            - Authentication failures (401/403) with certificate hint.
            - Server errors (5xx) with retry up to ``max_retries``.
            - Network errors with retry and backoff.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            endpoint: API endpoint path (e.g., ``/systems``).
            data: Request body dict (sent as JSON).
            params: Query parameters dict.
            retry: Whether to retry on transient failures (default True).

        Returns:
            Response dict, or error dict with ``status: "error"`` on failure.
        """
        url = f"{self.base_url}{endpoint}"
        session = self._get_session()
        last_error = None
        max_attempts = self.max_retries if retry else 1

        for attempt in range(max_attempts):
            self._enforce_rate_limit()
            self._request_timestamps.append(time.time())

            try:
                response = session.request(
                    method=method,
                    url=url,
                    json=data,
                    params=params,
                    timeout=self.timeout,
                )

                # --- Rate limit (429) ---
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", self.retry_backoff ** (attempt + 1)))
                    logger.warning(
                        "eMASS API rate limited (429), retrying after %d seconds (attempt %d/%d)",
                        retry_after, attempt + 1, max_attempts,
                    )
                    time.sleep(retry_after)
                    continue

                # --- Auth failure (401/403) ---
                if response.status_code in (401, 403):
                    error_msg = (
                        f"eMASS authentication failed ({response.status_code}). "
                        "Verify PKI/CAC certificate configuration: client_cert_path, "
                        "client_key_path, ca_bundle_path, and api_key in project_defaults.yaml emass: section."
                    )
                    logger.error(error_msg)
                    return {"status": "error", "code": response.status_code, "error": error_msg}

                # --- Server error (5xx) ---
                if response.status_code >= 500:
                    last_error = f"Server error {response.status_code}: {response.text[:500]}"
                    logger.warning(
                        "eMASS API server error (attempt %d/%d): %s",
                        attempt + 1, max_attempts, last_error,
                    )
                    if attempt < max_attempts - 1:
                        wait = self.retry_backoff ** attempt
                        time.sleep(wait)
                    continue

                # --- Client error (4xx, not 401/403/429) ---
                if response.status_code >= 400:
                    error_body = response.text[:1000]
                    logger.error("eMASS API client error %d: %s", response.status_code, error_body)
                    return {
                        "status": "error",
                        "code": response.status_code,
                        "error": error_body,
                    }

                # --- Success ---
                response.raise_for_status()
                if response.content:
                    return response.json()
                return {"status": "success", "code": response.status_code}

            except requests.exceptions.ConnectionError as exc:
                last_error = f"Connection error: {exc}"
                logger.warning(
                    "eMASS API connection error (attempt %d/%d): %s",
                    attempt + 1, max_attempts, last_error,
                )
                if attempt < max_attempts - 1:
                    wait = self.retry_backoff ** attempt
                    time.sleep(wait)

            except requests.exceptions.Timeout as exc:
                last_error = f"Timeout: {exc}"
                logger.warning(
                    "eMASS API timeout (attempt %d/%d): %s",
                    attempt + 1, max_attempts, last_error,
                )
                if attempt < max_attempts - 1:
                    wait = self.retry_backoff ** attempt
                    time.sleep(wait)

            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "eMASS API unexpected error (attempt %d/%d): %s",
                    attempt + 1, max_attempts, last_error,
                )
                if attempt < max_attempts - 1:
                    wait = self.retry_backoff ** attempt
                    time.sleep(wait)

        logger.error("eMASS API request failed after %d attempts: %s", max_attempts, last_error)
        return {"status": "error", "error": last_error}

    # ------------------------------------------------------------------
    # System endpoints
    # ------------------------------------------------------------------

    def get_system(self, system_id):
        """Retrieve a single system from eMASS by system ID.

        eMASS API: GET /api/systems/{systemId}

        Args:
            system_id: eMASS system identifier (integer or string).

        Returns:
            Dict with system data or error dict.
        """
        return self._request("GET", f"/systems/{system_id}")

    def get_systems(self, params=None):
        """List all systems accessible to the authenticated user.

        eMASS API: GET /api/systems

        Args:
            params: Optional query parameters (e.g., ``{"ditprId": "..."}``).

        Returns:
            Dict with systems list or error dict.
        """
        return self._request("GET", "/systems", params=params)

    def register_system(self, system_data):
        """Register a new system in eMASS.

        eMASS API: POST /api/systems

        Args:
            system_data: Dict with system registration fields:
                - systemName (str): System name
                - systemType (str): e.g., "Major Application"
                - informationType (str): e.g., "CUI // SP-CTI"
                - authorizationBoundary (str): Description of boundary
                - securityObjective (str): e.g., "Confidentiality, Integrity"
                - impactLevel (str): e.g., "Moderate"

        Returns:
            Dict with registered system ID and status.
        """
        payload = {
            "systemName": system_data.get("name", ""),
            "systemType": system_data.get("type", "Major Application"),
            "informationType": system_data.get("classification", "CUI // SP-CTI"),
            "authorizationBoundary": system_data.get("description", ""),
            "securityObjective": "Confidentiality, Integrity, Availability",
            "impactLevel": system_data.get("impact_level", "Moderate"),
            "status": "Under Development",
            "description": system_data.get("description", ""),
        }
        result = self._request("POST", "/systems", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, system_data.get("id", "unknown"), "register_system", {
                "system_name": payload["systemName"],
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    # ------------------------------------------------------------------
    # Controls endpoints
    # ------------------------------------------------------------------

    def get_controls(self, system_id):
        """Retrieve control implementations for a system.

        eMASS API: GET /api/systems/{systemId}/controls

        Args:
            system_id: eMASS system identifier.

        Returns:
            Dict with controls list or error dict.
        """
        return self._request("GET", f"/systems/{system_id}/controls")

    def push_controls(self, system_id, controls):
        """Push control implementations to eMASS.

        eMASS API: PUT /api/systems/{systemId}/controls

        Args:
            system_id: eMASS system identifier.
            controls: List of control dicts with fields:
                - acronym (str): Control acronym (e.g., "AC-2")
                - responsibleEntities (str): Responsible role
                - implementationStatus (str): e.g., "Implemented"
                - controlDesignation (str): e.g., "Common"
                - estimatedCompletionDate (str): ISO date
                - implementationNarrative (str): Description of implementation
                - slcmCriticality (str): Criticality level
                - slcmFrequency (str): Assessment frequency

        Returns:
            Dict with push result summary.
        """
        payload = [
            {
                "acronym": c.get("control_id", c.get("acronym", "")),
                "responsibleEntities": c.get("responsible_role", c.get("responsibleEntities", "")),
                "implementationStatus": _map_impl_status_to_emass(
                    c.get("implementation_status", c.get("implementationStatus", "Planned"))
                ),
                "controlDesignation": c.get("control_designation", c.get("controlDesignation", "Common")),
                "estimatedCompletionDate": c.get("estimated_completion_date", c.get("estimatedCompletionDate", "")),
                "implementationNarrative": c.get(
                    "implementation_description",
                    c.get("implementationNarrative", "Planned"),
                ),
                "slcmCriticality": c.get("slcm_criticality", c.get("slcmCriticality", "")),
                "slcmFrequency": c.get("slcm_frequency", c.get("slcmFrequency", "Annually")),
            }
            for c in controls
        ]
        result = self._request("PUT", f"/systems/{system_id}/controls", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, str(system_id), "push_controls", {
                "control_count": len(controls),
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    # ------------------------------------------------------------------
    # POA&M endpoints
    # ------------------------------------------------------------------

    def get_poam(self, system_id, params=None):
        """Retrieve POA&M items for a system.

        eMASS API: GET /api/systems/{systemId}/poams

        Args:
            system_id: eMASS system identifier.
            params: Optional query parameters (e.g., ``{"scheduledCompletionDateStart": "..."}``).

        Returns:
            Dict with POA&M items or error dict.
        """
        return self._request("GET", f"/systems/{system_id}/poams", params=params)

    def push_poam(self, system_id, poam_items):
        """Create POA&M items in eMASS.

        eMASS API: POST /api/systems/{systemId}/poams

        Args:
            system_id: eMASS system identifier.
            poam_items: List of POA&M dicts with fields:
                - status (str): e.g., "Ongoing", "Completed", "Risk Accepted"
                - vulnerabilityDescription (str): Weakness description
                - sourceIdentVuln (str): Source identifier
                - severity (str): e.g., "Very High", "High", "Moderate", "Low", "Very Low"
                - scheduledCompletionDate (str): ISO date
                - milestones (list): List of milestone dicts
                - pocOrganization (str): Responsible organization
                - resources (str): Required resources

        Returns:
            Dict with push result summary.
        """
        payload = [
            {
                "status": _map_poam_status_to_emass(p.get("status", "Ongoing")),
                "vulnerabilityDescription": p.get(
                    "weakness_description", p.get("vulnerabilityDescription", "")
                ),
                "sourceIdentVuln": p.get("source", p.get("sourceIdentVuln", "")),
                "severity": _map_severity_to_emass(p.get("severity", "Moderate")),
                "scheduledCompletionDate": p.get(
                    "milestone_date", p.get("scheduledCompletionDate", "")
                ),
                "milestones": p.get("milestones", [
                    {
                        "description": p.get("corrective_action", "Remediation planned"),
                        "scheduledCompletionDate": p.get("milestone_date", ""),
                    }
                ]),
                "pocOrganization": p.get("responsible_party", p.get("pocOrganization", "")),
                "resources": p.get("resources_required", p.get("resources", "")),
                "controlAcronym": p.get("control_id", p.get("controlAcronym", "")),
                "office": p.get("office", ""),
            }
            for p in poam_items
        ]
        result = self._request("POST", f"/systems/{system_id}/poams", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, str(system_id), "push_poam", {
                "poam_count": len(poam_items),
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    # ------------------------------------------------------------------
    # Artifacts endpoints
    # ------------------------------------------------------------------

    def push_artifacts(self, system_id, artifacts):
        """Upload artifacts (evidence) to eMASS.

        eMASS API: POST /api/systems/{systemId}/artifacts

        Args:
            system_id: eMASS system identifier.
            artifacts: List of artifact dicts with fields:
                - filename (str): Artifact file name
                - isTemplate (bool): Whether this is a template
                - type (str): Artifact type (e.g., "Procedure", "Diagram", "Policy")
                - category (str): Artifact category (e.g., "Implementation Guidance")
                - description (str): Artifact description
                - expirationDate (str): ISO date when artifact expires
                - lastReviewedDate (str): ISO date of last review

        Returns:
            Dict with push result summary.
        """
        payload = [
            {
                "filename": a.get("filename", a.get("file_name", "")),
                "isTemplate": a.get("isTemplate", a.get("is_template", False)),
                "type": a.get("type", a.get("artifact_type", "Procedure")),
                "category": a.get("category", "Implementation Guidance"),
                "description": a.get("description", ""),
                "expirationDate": a.get("expiration_date", a.get("expirationDate", "")),
                "lastReviewedDate": a.get(
                    "last_reviewed_date",
                    a.get("lastReviewedDate", datetime.utcnow().strftime("%Y-%m-%d")),
                ),
            }
            for a in artifacts
        ]
        result = self._request("POST", f"/systems/{system_id}/artifacts", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, str(system_id), "push_artifacts", {
                "artifact_count": len(artifacts),
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    # ------------------------------------------------------------------
    # Test results endpoints
    # ------------------------------------------------------------------

    def push_test_results(self, system_id, test_results):
        """Upload test/scan results to eMASS.

        eMASS API: POST /api/systems/{systemId}/test-results

        Args:
            system_id: eMASS system identifier.
            test_results: List of test result dicts with fields:
                - cci (str): CCI identifier (e.g., "CCI-000001")
                - testedBy (str): Who performed the test
                - testDate (str): ISO date of test
                - description (str): Test description
                - complianceStatus (str): e.g., "Compliant", "Non-Compliant"

        Returns:
            Dict with push result summary.
        """
        payload = [
            {
                "cci": t.get("cci", t.get("control_id", "")),
                "testedBy": t.get("testedBy", t.get("tested_by", "ICDEV Compliance Engine")),
                "testDate": t.get(
                    "testDate",
                    t.get("test_date", datetime.utcnow().strftime("%Y-%m-%d")),
                ),
                "description": t.get("description", t.get("title", "")),
                "complianceStatus": _map_compliance_status_to_emass(
                    t.get("complianceStatus", t.get("status", "Non-Compliant"))
                ),
            }
            for t in test_results
        ]
        result = self._request("POST", f"/systems/{system_id}/test-results", data=payload)

        conn = _get_connection(self.db_path)
        try:
            _log_audit(conn, str(system_id), "push_test_results", {
                "test_result_count": len(test_results),
                "result": result.get("status") if result else "error",
            })
        finally:
            conn.close()

        return result

    # ------------------------------------------------------------------
    # Authorization / milestones
    # ------------------------------------------------------------------

    def get_authorization_status(self, system_id):
        """Retrieve the authorization (ATO) status for a system.

        Pulls the full system record and extracts authorization fields.

        eMASS API: GET /api/systems/{systemId}

        Args:
            system_id: eMASS system identifier.

        Returns:
            Dict with authorization fields:
                - authorizationStatus
                - authorizationDate
                - authorizationTerminationDate
                - authorizationType
        """
        result = self._request("GET", f"/systems/{system_id}")
        if not result or result.get("status") == "error":
            return result

        # The eMASS system response nests data; extract authorization fields.
        system_data = result.get("data", result)
        if isinstance(system_data, list) and len(system_data) > 0:
            system_data = system_data[0]

        return {
            "status": "success",
            "system_id": system_id,
            "authorizationStatus": system_data.get("authorizationStatus", "Unknown"),
            "authorizationDate": system_data.get("authorizationDate", ""),
            "authorizationTerminationDate": system_data.get("authorizationTerminationDate", ""),
            "authorizationType": system_data.get("authorizationType", ""),
            "systemLifecycle": system_data.get("systemLifecycle", ""),
            "ditprId": system_data.get("ditprId", ""),
        }

    def get_milestones(self, system_id, poam_id):
        """Retrieve milestones for a specific POA&M item.

        eMASS API: GET /api/systems/{systemId}/poams/{poamId}/milestones

        Args:
            system_id: eMASS system identifier.
            poam_id: POA&M item identifier.

        Returns:
            Dict with milestones list or error dict.
        """
        return self._request("GET", f"/systems/{system_id}/poams/{poam_id}/milestones")

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def test_connection(self):
        """Test connectivity to the eMASS API.

        Performs a lightweight GET /systems call to verify credentials
        and network connectivity.

        Returns:
            Dict with connection test result.
        """
        result = self._request("GET", "/systems", params={"pageSize": 1}, retry=False)
        if result and result.get("status") != "error":
            return {"connected": True, "endpoint": self.base_url, "details": result}
        return {
            "connected": False,
            "endpoint": self.base_url,
            "error": result.get("error", "Unknown") if result else "No response",
        }

    def close(self):
        """Close the requests session and release resources."""
        if self._session:
            self._session.close()
            self._session = None


# ======================================================================
# Mapping helpers
# ======================================================================

def _map_impl_status_to_emass(status):
    """Map ICDEV implementation status to eMASS-accepted values.

    eMASS accepts: Planned, Implemented, Inherited, Not Applicable,
    Manually Inherited.

    Args:
        status: ICDEV-internal status string.

    Returns:
        eMASS-compatible implementation status string.
    """
    mapping = {
        "planned": "Planned",
        "implemented": "Implemented",
        "partially_implemented": "Planned",
        "not_implemented": "Planned",
        "inherited": "Inherited",
        "not_applicable": "Not Applicable",
        "manually_inherited": "Manually Inherited",
    }
    return mapping.get(status.lower().strip(), status)


def _map_poam_status_to_emass(status):
    """Map ICDEV POA&M status to eMASS-accepted values.

    eMASS accepts: Ongoing, Completed, Risk Accepted, Delayed, Cancelled.

    Args:
        status: ICDEV-internal POA&M status string.

    Returns:
        eMASS-compatible POA&M status string.
    """
    mapping = {
        "open": "Ongoing",
        "ongoing": "Ongoing",
        "in_progress": "Ongoing",
        "closed": "Completed",
        "completed": "Completed",
        "risk_accepted": "Risk Accepted",
        "delayed": "Delayed",
        "cancelled": "Cancelled",
        "mitigated": "Completed",
    }
    return mapping.get(status.lower().strip(), status)


def _map_severity_to_emass(severity):
    """Map ICDEV severity to eMASS-accepted severity values.

    eMASS accepts: Very High, High, Moderate, Low, Very Low.

    Args:
        severity: ICDEV-internal severity string.

    Returns:
        eMASS-compatible severity string.
    """
    mapping = {
        "critical": "Very High",
        "cat1": "Very High",
        "cat_1": "Very High",
        "very_high": "Very High",
        "high": "High",
        "cat2": "High",
        "cat_2": "High",
        "moderate": "Moderate",
        "medium": "Moderate",
        "cat3": "Moderate",
        "cat_3": "Moderate",
        "low": "Low",
        "very_low": "Very Low",
        "informational": "Very Low",
    }
    return mapping.get(severity.lower().strip(), severity)


def _map_compliance_status_to_emass(status):
    """Map ICDEV test compliance status to eMASS-accepted values.

    eMASS accepts: Compliant, Non-Compliant, Not Applicable.

    Args:
        status: ICDEV-internal compliance/test status string.

    Returns:
        eMASS-compatible compliance status string.
    """
    mapping = {
        "pass": "Compliant",
        "passed": "Compliant",
        "compliant": "Compliant",
        "satisfied": "Compliant",
        "open": "Non-Compliant",
        "fail": "Non-Compliant",
        "failed": "Non-Compliant",
        "non-compliant": "Non-Compliant",
        "non_compliant": "Non-Compliant",
        "not_satisfied": "Non-Compliant",
        "not_applicable": "Not Applicable",
        "n/a": "Not Applicable",
    }
    return mapping.get(status.lower().strip(), status)


# CUI // SP-CTI
