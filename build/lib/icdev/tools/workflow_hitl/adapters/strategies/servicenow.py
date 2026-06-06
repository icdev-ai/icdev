# CUI // SP-CTI
"""ServiceNow Table API ticket strategy."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
import urllib.request

logger = get_logger(__name__)


def _cfg():
    try:
        import yaml
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..",
                                "args", "workflow_hitl_integrations.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            return yaml.safe_load(f).get("integrations", {}).get("servicenow", {})
    except Exception:
        return {}


class ServiceNowStrategy:

    def create_ticket(self, step: dict) -> str:
        cfg = _cfg()
        if not cfg.get("enabled"):
            logger.info("ServiceNow adapter disabled — simulating for step %s", step["id"])
            return f"SNOW-SIM-{step['id'][:8]}"

        instance_url = os.getenv("WF_SNOW_URL", cfg.get("instance_url", ""))
        user = os.getenv("WF_SNOW_USER", cfg.get("username", ""))
        pwd  = os.getenv("WF_SNOW_PASS", cfg.get("password", ""))

        payload = json.dumps({
            "short_description": f"HITL Review — {step.get('stage_name')} [{step['instance_id']}]",
            "description": f"Workflow instance {step['instance_id']} is awaiting review at stage {step.get('stage_name')}.",
            "caller_id": user,
        }).encode()

        import base64
        auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        req = urllib.request.Request(
            f"{instance_url}/api/now/table/incident",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return data.get("result", {}).get("sys_id", "SNOW-UNKNOWN")

    def is_closed(self, step: dict) -> bool:
        cfg = _cfg()
        if not cfg.get("enabled"):
            return False
        sys_id = step.get("external_ref", "")
        if not sys_id:
            return False
        instance_url = os.getenv("WF_SNOW_URL", cfg.get("instance_url", ""))
        user = os.getenv("WF_SNOW_USER", cfg.get("username", ""))
        pwd  = os.getenv("WF_SNOW_PASS", cfg.get("password", ""))
        import base64
        auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        try:
            req = urllib.request.Request(
                f"{instance_url}/api/now/table/incident/{sys_id}?sysparm_fields=state",
                headers={"Authorization": f"Basic {auth}"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                state = data.get("result", {}).get("state", "")
                return str(state) == "6"  # 6 = Resolved/Closed
        except Exception as exc:
            logger.warning("ServiceNow poll failed for %s: %s", sys_id, exc)
            return False
