# CUI // SP-CTI
"""Confluence REST API v2 wiki strategy."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import os
import urllib.request

logger = get_logger(__name__)


def _cfg():
    try:
        import yaml
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..",
                                "args", "workflow_hitl_integrations.yaml")
        with open(cfg_path, encoding="utf-8") as f:
            return yaml.safe_load(f).get("integrations", {}).get("confluence", {})
    except Exception:
        return {}


class ConfluenceStrategy:

    def create_page(self, step: dict) -> str:
        cfg = _cfg()
        if not cfg.get("enabled"):
            logger.info("Confluence adapter disabled — simulating for step %s", step["id"])
            return f"https://confluence.example.com/pages/SIM-{step['id'][:8]}"

        base_url  = os.getenv("WF_CONFLUENCE_URL", cfg.get("base_url", ""))
        token     = os.getenv("WF_CONFLUENCE_TOKEN", cfg.get("api_token", ""))
        space     = cfg.get("default_space", "ICDEV")
        payload_d = json.loads(step.get("payload_json") or "{}")
        parent_id = payload_d.get("parent_page_id")

        body_content = (
            f"<h2>HITL Review — {step.get('stage_name')}</h2>"
            f"<p>Workflow instance <code>{step['instance_id']}</code> is awaiting review.</p>"
        )

        create_payload = {
            "type": "page",
            "title": f"HITL Review — {step.get('stage_name')} [{step['id'][:8]}]",
            "space": {"key": space},
            "body": {"storage": {"value": body_content, "representation": "storage"}},
        }
        if parent_id:
            create_payload["ancestors"] = [{"id": parent_id}]

        import base64
        auth = base64.b64encode(f"icdev:{token}".encode()).decode()
        req = urllib.request.Request(
            f"{base_url}/rest/api/content",
            data=json.dumps(create_payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            page_id = data.get("id", "")
            links = data.get("_links", {})
            return f"{base_url}{links.get('webui', f'/pages/{page_id}')}"

    def is_approved(self, step: dict) -> bool:
        # Confluence pages auto-advance after creation (no approval gate by default)
        return True
