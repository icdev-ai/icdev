# CUI // SP-CTI
"""SharePoint REST API via Microsoft Graph wiki strategy."""
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
            return yaml.safe_load(f).get("integrations", {}).get("sharepoint", {})
    except Exception:
        return {}


def _get_access_token(cfg: dict) -> str:
    tenant_id   = os.getenv("WF_SP_TENANT_ID",   cfg.get("tenant_id", ""))
    client_id   = os.getenv("WF_SP_CLIENT_ID",   cfg.get("client_id", ""))
    client_secret = os.getenv("WF_SP_CLIENT_SECRET", cfg.get("client_secret", ""))
    data = (
        f"grant_type=client_credentials"
        f"&client_id={client_id}"
        f"&client_secret={client_secret}"
        f"&scope=https%3A%2F%2Fgraph.microsoft.com%2F.default"
    ).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("access_token", "")


class SharePointStrategy:

    def create_page(self, step: dict) -> str:
        cfg = _cfg()
        if not cfg.get("enabled"):
            logger.info("SharePoint adapter disabled — simulating for step %s", step["id"])
            return f"https://sharepoint.example.com/sites/ICDEV/SIM-{step['id'][:8]}"

        site_url = os.getenv("WF_SP_SITE_URL", cfg.get("site_url", ""))
        try:
            token = _get_access_token(cfg)
        except Exception as exc:
            logger.warning("SharePoint token error: %s", exc)
            return f"sharepoint-token-error/{step['id']}"

        title = f"HITL Review — {step.get('stage_name')} [{step['id'][:8]}]"
        payload = json.dumps({
            "title": title,
            "body": {"content": f"Workflow instance {step['instance_id']} awaiting review."},
        }).encode()

        # Use Graph API to create a SharePoint site page
        site_id_placeholder = site_url.replace("https://", "").replace("/", ",")
        req = urllib.request.Request(
            f"https://graph.microsoft.com/v1.0/sites/{site_id_placeholder}/pages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                return data.get("webUrl", f"{site_url}/pages/{step['id']}")
        except Exception as exc:
            logger.warning("SharePoint page creation failed: %s", exc)
            return f"{site_url}/pages/error/{step['id']}"

    def is_approved(self, step: dict) -> bool:
        return True  # Auto-advance after write
