# CUI // SP-CTI
"""IQE ext.* adapter — bridges DataBridge connectors into the IQE collection namespace.

Importing this module side-effect-registers the following collections on the
module-level Executor singleton:

  ext.databridge.connectors       — Registry listing of all registered DataBridge
                                    connectors (always available; no credentials needed).

  ext.servicenow.incidents        — ServiceNow ITSM incident records
  ext.servicenow.change_requests  — ServiceNow ITSM change request records
  ext.servicenow.applications     — ServiceNow CMDB application CI records
  ext.servicenow.servers          — ServiceNow CMDB server CI records

  ext.splunk.events               — Splunk search events (security audit log)
  ext.splunk.devices              — Splunk asset lookup (device inventory)
  ext.splunk.alerts               — Splunk notable events / alerts

  ext.tenable.scans               — Tenable scan listing
  ext.tenable.assets              — Tenable managed assets
  ext.tenable.vulnerabilities     — Tenable vulnerability workbench

  ext.gdelt.events                — GDELT Global Knowledge Graph events

Graceful degradation: if a connector is not registered, not configured with
credentials, or the remote is unreachable, the adapter logs a warning and
returns an empty list rather than raising.  This keeps the IQE dispatcher
resilient when external systems are not available in the current environment.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection
from tools.logging.icdev_logger import get_logger

_logger = get_logger("iqe.adapters.ext_databridge")

_DEFAULT_LIMIT = 500


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_fetch(connector_name: str, table: str, limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """Instantiate a DataBridge connector and fetch rows from a logical table.

    Returns an empty list (never raises) when:
    - ``connector_name`` is not in the DataBridge registry
    - The connector requires credentials that are not configured
    - The remote is unreachable or returns a non-ok status
    """
    try:
        from tools.databridge.registry import get_connector_instance  # noqa: PLC0415
        from tools.databridge.connector import ConnectorRequest        # noqa: PLC0415

        connector = get_connector_instance(connector_name)
        if connector is None:
            _logger.debug("ext.%s not registered — returning empty list", connector_name)
            return []

        req = ConnectorRequest(table_name=table, limit=limit)
        resp = connector.read(req)

        if resp.status != "ok":
            _logger.warning(
                "ext.%s.%s: connector returned status=%s errors=%s",
                connector_name, table, resp.status, resp.errors,
            )
            return []

        data = resp.data or []
        rows = list(data) if isinstance(data, (list, tuple)) else []
        if rows and not _apply_governance_gate(connector_name, table):
            return []
        if rows:
            try:
                _record_lineage(connector_name, table, len(rows))
            except Exception:  # noqa: BLE001
                pass
        return rows

    except Exception as exc:  # noqa: BLE001
        _logger.warning("ext.%s.%s fetch failed: %s", connector_name, table, exc)
        return []


def _record_lineage(connector_name: str, table: str, row_count: int) -> None:
    """Fire-and-forget DDC lineage record for a successful external fetch."""
    try:
        from tools.data_canvas.lineage import record_external_fetch  # noqa: PLC0415

        record_external_fetch(connector_name, table, row_count)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("lineage record skipped for ext.%s.%s: %s", connector_name, table, exc)


def _apply_governance_gate(connector_name: str, table: str) -> bool:
    """Return True when the Data Mesh OPA governance gate allows the read.

    Fail-open: any error in the gate itself is logged and the read proceeds.
    """
    try:
        from tools.data_canvas.data_mesh.governance_engine import check_ext_access  # noqa: PLC0415

        decision = check_ext_access(connector_name, table)
        if not decision.get("allowed", True):
            _logger.warning(
                "ext.%s.%s: access DENIED by OPA governance gate — %s",
                connector_name, table, decision.get("reason"),
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.debug("governance gate skipped for ext.%s.%s: %s", connector_name, table, exc)
        return True


# ---------------------------------------------------------------------------
# Registry adapter (always available — no credentials required)
# ---------------------------------------------------------------------------

def connectors_registry_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return all registered DataBridge connectors as queryable rows."""
    try:
        from tools.databridge.registry import list_registered  # noqa: PLC0415
        return [
            {"connector_name": name, "class_name": cls_name, "status": "registered"}
            for name, cls_name in list_registered().items()
        ]
    except Exception as exc:  # noqa: BLE001
        _logger.warning("ext.databridge.connectors registry read failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# ServiceNow ITSM adapters
# ---------------------------------------------------------------------------

def servicenow_incidents_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return ServiceNow ITSM incident records."""
    return _safe_fetch("servicenow_itsm", "incident")


def servicenow_change_requests_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return ServiceNow ITSM change request records."""
    return _safe_fetch("servicenow_itsm", "change_request")


# ---------------------------------------------------------------------------
# ServiceNow CMDB adapters
# ---------------------------------------------------------------------------

def servicenow_applications_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return ServiceNow CMDB application CI records."""
    return _safe_fetch("servicenow_cmdb", "applications")


def servicenow_servers_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return ServiceNow CMDB server CI records."""
    return _safe_fetch("servicenow_cmdb", "servers")


# ---------------------------------------------------------------------------
# Splunk adapters
# ---------------------------------------------------------------------------

def splunk_events_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return Splunk security audit log events."""
    return _safe_fetch("splunk", "events")


def splunk_devices_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return Splunk asset lookup — device inventory."""
    return _safe_fetch("splunk", "devices")


def splunk_alerts_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return Splunk notable events / alerts."""
    return _safe_fetch("splunk", "alerts")


# ---------------------------------------------------------------------------
# Tenable adapters
# ---------------------------------------------------------------------------

def tenable_scans_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return Tenable scan listing."""
    return _safe_fetch("tenable", "scans")


def tenable_assets_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return Tenable managed assets."""
    return _safe_fetch("tenable", "assets")


def tenable_vulnerabilities_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return Tenable vulnerability workbench records."""
    return _safe_fetch("tenable", "vulnerabilities")


# ---------------------------------------------------------------------------
# GDELT adapters
# ---------------------------------------------------------------------------

def gdelt_events_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return GDELT Global Knowledge Graph event records."""
    return _safe_fetch("gdelt", "events")


# ---------------------------------------------------------------------------
# Collection registration — runs at import time
# ---------------------------------------------------------------------------

register_collection("ext.databridge.connectors",      connectors_registry_adapter)

register_collection("ext.servicenow.incidents",        servicenow_incidents_adapter)
register_collection("ext.servicenow.change_requests",  servicenow_change_requests_adapter)
register_collection("ext.servicenow.applications",     servicenow_applications_adapter)
register_collection("ext.servicenow.servers",          servicenow_servers_adapter)

register_collection("ext.splunk.events",               splunk_events_adapter)
register_collection("ext.splunk.devices",              splunk_devices_adapter)
register_collection("ext.splunk.alerts",               splunk_alerts_adapter)

register_collection("ext.tenable.scans",               tenable_scans_adapter)
register_collection("ext.tenable.assets",              tenable_assets_adapter)
register_collection("ext.tenable.vulnerabilities",     tenable_vulnerabilities_adapter)

register_collection("ext.gdelt.events",                gdelt_events_adapter)


# ---------------------------------------------------------------------------
# Messaging channel adapters (Teams, MatterMost, GitHub, GitLab, Skype)
# ---------------------------------------------------------------------------

def teams_messages_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return Teams messages via DataBridge connector."""
    return _safe_fetch("teams", "messages")


def teams_channels_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return Teams channels via DataBridge connector."""
    return _safe_fetch("teams", "channels")


def mattermost_posts_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return MatterMost posts via DataBridge connector."""
    return _safe_fetch("mattermost", "posts")


def mattermost_channels_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return MatterMost channels via DataBridge connector."""
    return _safe_fetch("mattermost", "channels")


def github_issues_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return GitHub issue comments (with !icdev flag) via DataBridge connector."""
    return _safe_fetch("github", "issues")


def github_pull_requests_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return GitHub PR comments via DataBridge connector."""
    return _safe_fetch("github", "pull_requests")


def github_commits_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return GitHub commits via DataBridge connector."""
    return _safe_fetch("github", "commits")


def gitlab_issues_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return GitLab issue notes via DataBridge connector."""
    return _safe_fetch("gitlab", "issues")


def gitlab_merge_requests_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return GitLab MR notes via DataBridge connector."""
    return _safe_fetch("gitlab", "merge_requests")


def gitlab_pipelines_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return GitLab pipeline records via DataBridge connector."""
    return _safe_fetch("gitlab", "pipelines")


def skype_messages_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return Skype messages from local skype_inbox DB table."""
    return _safe_fetch("skype", "messages")


register_collection("ext.teams.messages",             teams_messages_adapter)
register_collection("ext.teams.channels",             teams_channels_adapter)

register_collection("ext.mattermost.posts",           mattermost_posts_adapter)
register_collection("ext.mattermost.channels",        mattermost_channels_adapter)

register_collection("ext.github.issues",              github_issues_adapter)
register_collection("ext.github.pull_requests",       github_pull_requests_adapter)
register_collection("ext.github.commits",             github_commits_adapter)

register_collection("ext.gitlab.issues",              gitlab_issues_adapter)
register_collection("ext.gitlab.merge_requests",      gitlab_merge_requests_adapter)
register_collection("ext.gitlab.pipelines",           gitlab_pipelines_adapter)

register_collection("ext.skype.messages",             skype_messages_adapter)
