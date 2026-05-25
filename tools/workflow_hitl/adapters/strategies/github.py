# CUI // SP-CTI
"""GitHub Issues REST API ticket strategy."""
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
            return yaml.safe_load(f).get("integrations", {}).get("github", {})
    except Exception:
        return {}


class GitHubStrategy:

    def create_ticket(self, step: dict) -> str:
        cfg = _cfg()
        if not cfg.get("enabled"):
            logger.info("GitHub adapter disabled — simulating for step %s", step["id"])
            return f"GH-SIM-{step['id'][:8]}"

        token = os.getenv("WF_GITHUB_TOKEN", cfg.get("token", ""))
        repo  = cfg.get("repo", "org/repo")

        payload = json.dumps({
            "title": f"HITL Review — {step.get('stage_name')} [{step['instance_id']}]",
            "body": f"Workflow instance `{step['instance_id']}` is awaiting review at stage `{step.get('stage_name')}`.",
            "labels": ["hitl-review"],
        }).encode()

        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/issues",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return str(data.get("number", "GH-UNKNOWN"))

    def is_closed(self, step: dict) -> bool:
        cfg = _cfg()
        if not cfg.get("enabled"):
            return False
        issue_number = step.get("external_ref", "")
        if not issue_number:
            return False
        token = os.getenv("WF_GITHUB_TOKEN", cfg.get("token", ""))
        repo  = cfg.get("repo", "org/repo")
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/issues/{issue_number}",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                return data.get("state") == "closed"
        except Exception as exc:
            logger.warning("GitHub poll failed for issue %s: %s", issue_number, exc)
            return False
