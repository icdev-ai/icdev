#!/usr/bin/env python3
# CUI // SP-CTI
"""Review Board Notifier — push findings to dashboard, gateway, desktop (D-RB-11).

Three notification sinks:
    1. SSE broadcast → dashboard /events page (real-time)
    2. Agent mailbox → gateway channels (Slack/Teams/Mattermost)
    3. Windows toast → desktop notification (optional, graceful degradation)

Severity routing:
    CRITICAL → all 3 sinks (immediate)
    HIGH     → SSE + mailbox
    MEDIUM   → SSE only
    LOW/INFO → audit trail only (no push)
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def notify_findings(findings: List[Dict], reflex_name: str = "") -> Dict[str, Any]:
    """Route findings to appropriate notification sinks based on severity."""
    results = {"sse": 0, "mailbox": 0, "toast": 0, "skipped": 0}

    critical = [f for f in findings if f.get("severity") == "critical"]
    high = [f for f in findings if f.get("severity") == "high"]
    medium = [f for f in findings if f.get("severity") == "medium"]

    # CRITICAL → all 3 sinks
    if critical:
        summary = _build_summary("CRITICAL", critical, reflex_name)
        _broadcast_sse(summary, "critical")
        results["sse"] += 1
        _send_mailbox(summary, priority=9)
        results["mailbox"] += 1
        _send_toast(f"CRITICAL: {len(critical)} findings", summary["text"])
        results["toast"] += 1

    # HIGH → SSE + mailbox
    if high:
        summary = _build_summary("HIGH", high, reflex_name)
        _broadcast_sse(summary, "high")
        results["sse"] += 1
        _send_mailbox(summary, priority=7)
        results["mailbox"] += 1

    # MEDIUM → SSE only
    if medium:
        summary = _build_summary("MEDIUM", medium, reflex_name)
        _broadcast_sse(summary, "medium")
        results["sse"] += 1

    # LOW/INFO → skip push (already in audit trail)
    low_info = len(findings) - len(critical) - len(high) - len(medium)
    results["skipped"] = low_info

    return results


def notify_remediation(remediation_result: Dict[str, Any]) -> Dict[str, Any]:
    """Notify about remediation outcomes."""
    auto_fixed = remediation_result.get("auto_fixed", 0)
    failed = remediation_result.get("failed", 0)

    if auto_fixed == 0 and failed == 0:
        return {"notified": False}

    text_parts = []
    if auto_fixed:
        text_parts.append(f"{auto_fixed} auto-fixed")
    if failed:
        text_parts.append(f"{failed} FAILED")

    summary = {
        "source": "review_board.remediation",
        "text": "Remediation: " + ", ".join(text_parts),
        "details": remediation_result,
    }

    _broadcast_sse(summary, "info")

    if failed > 0:
        _send_mailbox(summary, priority=7)
        _send_toast("Remediation Failed", f"{failed} fixes failed")

    return {"notified": True, "text": summary["text"]}


def notify_health_score(score: float, trend: str, details: Dict) -> None:
    """Push health score update to dashboard."""
    _broadcast_sse(
        {
            "source": "review_board.health",
            "score": score,
            "trend": trend,
            "text": f"Codebase health: {score:.0f}/100 ({trend})",
            "details": details,
        },
        "health_update",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _build_summary(level: str, findings: List[Dict], reflex_name: str) -> Dict:
    """Build a notification summary from findings."""
    titles = [f.get("title", "Untitled") for f in findings[:5]]
    return {
        "source": f"review_board.{reflex_name}" if reflex_name else "review_board",
        "level": level,
        "count": len(findings),
        "text": f"[{level}] {len(findings)} finding(s) from {reflex_name or 'review board'}: " + "; ".join(titles[:3]),
        "findings": [
            {"severity": f.get("severity"), "title": f.get("title"), "category": f.get("category")}
            for f in findings[:10]
        ],
    }


def _broadcast_sse(data: Dict, severity: str) -> None:
    """Push event to dashboard SSE stream."""
    try:
        from tools.dashboard.sse_manager import sse_manager

        sse_manager.broadcast(
            {
                "event": "review_board_finding",
                "severity": severity,
                **data,
            },
            event_type="review_board_update",
        )
    except Exception:
        pass  # Dashboard may not be running


def _send_mailbox(data: Dict, priority: int = 5) -> None:
    """Send notification via agent mailbox → gateway channels."""
    try:
        from tools.agent.mailbox import send

        send(
            from_agent_id="review-board-daemon",
            to_agent_id="gateway-agent",
            message_type="notification",
            subject=data.get("text", "Review Board Notification"),
            body=json.dumps(data),
            priority=priority,
        )
    except Exception:
        pass  # Gateway may not be running


def _send_toast(title: str, message: str) -> None:
    """Send Windows desktop toast notification (graceful degradation)."""
    if sys.platform != "win32":
        return
    try:
        # Use PowerShell BurntToast module or fallback to basic toast
        ps_script = (
            f"Add-Type -AssemblyName System.Windows.Forms; "
            f"$n = New-Object System.Windows.Forms.NotifyIcon; "
            f"$n.Icon = [System.Drawing.SystemIcons]::Information; "
            f"$n.Visible = $true; "
            f'$n.ShowBalloonTip(5000, "{_escape_ps(title)}", '
            f'"{_escape_ps(message)}", '
            f"[System.Windows.Forms.ToolTipIcon]::Warning); "
            f"Start-Sleep -Seconds 6; $n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # Non-critical — graceful degradation


def _escape_ps(text: str) -> str:
    """Escape string for PowerShell double-quoted context."""
    return text.replace('"', '`"').replace("'", "''")[:200]
