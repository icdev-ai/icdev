#!/usr/bin/env python3
# CUI // SP-CTI
"""Notify a human that the pipeline has stopped on something it cannot fix.

Everything the pipeline CAN recover is retried automatically and reported in the
Autonomous Recovery panel. This module is for the residue: a task whose rebase
and resume budgets are both spent, which will wait forever until a person acts.

The signal must reach whoever is on shift, and they are not always looking at the
dashboard — so the same queue is delivered three ways from ONE implementation:

  * the terminal, for a CLI session (bell + a block that names the tasks);
  * the OS notification centre, for a backgrounded terminal;
  * the browser, via tools/dashboard/templates/_hitl_popup.html, which calls the
    same /api/hitl/pending endpoint.

WHY NOT A LIBRARY. Desktop notification packages (plyer, win10toast, notify2)
would each drag a dependency into an air-gapped install for one toast. Every
platform already ships a way to do this from the command line, so each branch is
a subprocess call with a list argv and shell=False, and every one of them is
best-effort: a machine with no notification daemon must still get the terminal
output, and a failure here must never propagate to a caller whose real job is
something else.
"""
from __future__ import annotations

import os
import subprocess  # nosec B404 — fixed argv, shell=False, best-effort only
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

HITL_SOURCE_PREFIX = "pr_watcher:hitl:"

#: Kept short deliberately — a toast that overflows is truncated by the OS at an
#: arbitrary point, which is how a task id gets cut in half.
_MAX_TOAST_TASKS = 3


def pending(get_conn: Callable = get_connection) -> List[Dict[str, Any]]:
    """Firing HITL alerts, oldest first. The same rows the dashboard reads."""
    try:
        conn = get_conn()
    except Exception as exc:  # noqa: BLE001
        logger.debug("hitl_notify: no db: %s", exc)
        return []
    try:
        rows = conn.execute(
            "SELECT id, source, title, description, created_at FROM alerts "
            "WHERE status = 'firing' AND source LIKE %s "
            "ORDER BY created_at ASC",
            (HITL_SOURCE_PREFIX + "%",),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("hitl_notify: query failed: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    out = []
    for r in rows:
        d = dict(r)
        out.append({
            "id": d.get("id"),
            "task_id": (d.get("source") or "").rsplit(":", 1)[-1],
            "title": d.get("title"),
            "description": d.get("description"),
            "created_at": str(d.get("created_at") or ""),
        })
    return out


def _toast_argv(title: str, body: str) -> Optional[List[str]]:
    """The platform's own notification command, or None if there isn't one.

    Explicit branches with both sides implemented, per the cross_process_lease
    precedent — never a POSIX-only path that silently no-ops on Windows.
    """
    if os.name == "nt":
        # BurntToast is not present by default, so use the shell's own toast via
        # the WinRT API exposed to PowerShell. Single-quoted here-string style
        # arguments are avoided: the text is passed as a variable, not
        # interpolated into the script, so a task id containing a quote cannot
        # break the command.
        script = (
            "$ErrorActionPreference='Stop';"
            "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
            "ContentType=WindowsRuntime]>$null;"
            "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
            "$n=$t.GetElementsByTagName('text');"
            "$n.Item(0).AppendChild($t.CreateTextNode($env:ICDEV_TOAST_TITLE))>$null;"
            "$n.Item(1).AppendChild($t.CreateTextNode($env:ICDEV_TOAST_BODY))>$null;"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
            "'ICDEV').Show([Windows.UI.Notifications.ToastNotification]::new($t))"
        )
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    if sys.platform == "darwin":
        return ["osascript", "-e",
                f'display notification {body!r} with title {title!r}']
    return ["notify-send", title, body]


def desktop_toast(title: str, body: str, *, runner: Optional[Callable] = None) -> bool:
    """Best-effort OS notification. False when the platform has no way to show one.

    Never raises: a headless box, an air-gapped image with no notification
    daemon, or a locked-down PowerShell policy are all normal, and none of them
    is a reason to fail the caller.
    """
    argv = _toast_argv(title, body)
    if not argv:
        return False
    env = dict(os.environ)
    env["ICDEV_TOAST_TITLE"] = title
    env["ICDEV_TOAST_BODY"] = body
    if runner is None:
        runner = subprocess.run
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=15,
                      shell=False, env=env)  # nosec B603
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("hitl_notify: toast unavailable: %s", exc)
        return False
    return getattr(proc, "returncode", 1) == 0


def render_terminal(items: List[Dict[str, Any]], *, bell: bool = True) -> str:
    """The block a CLI session sees. Empty string when nothing is pending.

    The bell is the point: a CLI notification that only prints is invisible to
    someone whose terminal is behind another window, which is the same failure
    the dashboard popup exists to fix.
    """
    if not items:
        return ""
    lines = ["", "-" * 64]
    lines.append(f"  {len(items)} TASK(S) NEED A HUMAN - the pipeline cannot recover them")
    lines.append("-" * 64)
    for i in items:
        lines.append(f"  {i['task_id']:<22} {(i.get('description') or '')[:120]}")
    lines.append("")
    lines.append("  Act:  python tools/kanban/cli.py --needs-human --json")
    lines.append("        or open /monitoring for Rebase / Requeue / Dismiss")
    lines.append("-" * 64)
    return ("\a" if bell else "") + "\n".join(lines)


def notify(
    *,
    get_conn: Callable = get_connection,
    stream=None,
    toast: bool = True,
    runner: Optional[Callable] = None,
) -> int:
    """Deliver the HITL queue to the terminal and the OS. Returns the count.

    Callers use the count as an exit code (non-zero == a human is needed), which
    is what lets a cron or CI step act without parsing stdout.
    """
    items = pending(get_conn=get_conn)
    if not items:
        return 0
    out = stream if stream is not None else sys.stdout
    try:
        out.write(render_terminal(items) + "\n")
    except Exception:  # noqa: BLE001 — a closed pipe must not lose the toast
        pass
    if toast:
        names = ", ".join(i["task_id"] for i in items[:_MAX_TOAST_TASKS])
        if len(items) > _MAX_TOAST_TASKS:
            names += f" +{len(items) - _MAX_TOAST_TASKS} more"
        desktop_toast(
            f"ICDEV: {len(items)} task(s) need a human",
            f"{names}. Open /monitoring to rebase, requeue or dismiss.",
            runner=runner,
        )
    return len(items)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(1 if notify() else 0)
