# CUI // SP-CTI
"""Jira REST API v3 ticket strategy."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
import urllib.error
import urllib.request

logger = get_logger(__name__)


def _cfg():
    try:
        import yaml
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..",
                                "args", "workflow_hitl_integrations.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            return yaml.safe_load(f).get("integrations", {}).get("jira", {})
    except Exception:
        return {}


class JiraStrategy:

    def create_ticket(self, step: dict) -> str:
        cfg = _cfg()
        if not cfg.get("enabled"):
            logger.info("Jira adapter disabled — simulating ticket creation for step %s", step["id"])
            return f"JIRA-SIM-{step['id'][:8]}"

        base_url = os.getenv("WF_JIRA_URL", cfg.get("base_url", ""))
        token    = os.getenv("WF_JIRA_TOKEN", cfg.get("api_token", ""))
        project  = step.get("payload_json") and json.loads(step["payload_json"]).get("project_key") or cfg.get("default_project", "ICDEV")
        issue_type = (json.loads(step.get("payload_json") or "{}").get("issue_type") or "Task")

        payload = json.dumps({
            "fields": {
                "project": {"key": project},
                "summary": f"HITL Review — {step.get('stage_name', 'unknown')} [{step['instance_id']}]",
                "description": {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [
                        {"type": "text", "text": f"Workflow instance {step['instance_id']} is awaiting review at stage {step.get('stage_name')}."}
                    ]}],
                },
                "issuetype": {"name": issue_type},
            }
        }).encode()

        import base64
        auth = base64.b64encode(f"icdev:{token}".encode()).decode()
        req = urllib.request.Request(
            f"{base_url}/rest/api/3/issue",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return data.get("key", "JIRA-UNKNOWN")

    def is_closed(self, step: dict) -> bool:
        cfg = _cfg()
        if not cfg.get("enabled"):
            return False
        external_ref = step.get("external_ref", "")
        if not external_ref:
            return False
        base_url = os.getenv("WF_JIRA_URL", cfg.get("base_url", ""))
        token    = os.getenv("WF_JIRA_TOKEN", cfg.get("api_token", ""))
        import base64
        auth = base64.b64encode(f"icdev:{token}".encode()).decode()
        try:
            req = urllib.request.Request(
                f"{base_url}/rest/api/3/issue/{external_ref}",
                headers={"Authorization": f"Basic {auth}"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                status = data.get("fields", {}).get("status", {}).get("name", "")
                return status.lower() in ("done", "closed", "resolved")
        except Exception as exc:
            logger.warning("Jira poll failed for %s: %s", external_ref, exc)
            return False
