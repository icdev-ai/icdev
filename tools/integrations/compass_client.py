# CUI // SP-CTI
"""Read-only HTTP client for a running Compass backend.

Compass (`C:\\AI\\standalone\\compass`) is a separate standalone app --
LCAT/staffing, document analysis, rate-card automation, and AI-assisted
writing for GovCon teams -- that itself bridges back into ICDEV via
`tools/mcp`'s `dic_search`/`dic_ingest`/`ace_persona_query`/`council_query`
(see Compass's own `tools/integrations/icdev_client.py`, cloned from
idea_lab's identical pattern). This module is the reverse direction: it
lets ICDEV's own CPMP/GovCon modules query Compass's LCAT taxonomy and
staffing data instead of duplicating it (Compass's `tools/govcon/
lcat_mapper.py` and `personnel_manager.py` were themselves the source
Compass vendored its own equivalents from -- this bridge is for querying
Compass's live, evolving copy, not re-deriving it here).

Compass is a plain FastAPI web app, not an MCP server, so this is a simple
HTTP client (via `tools.http.client.request`, ICDEV's standard outbound-HTTP
helper) rather than a stdio/JSON-RPC subprocess bridge. Every public
function degrades to `None` on ANY failure (integration disabled, Compass
unreachable, timeout, non-2xx response, malformed JSON) and never raises --
this integration is entirely optional; no CPMP/GovCon workflow depends on
Compass being present, running, or even installed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from tools.http.client import request as http_request  # noqa: E402


def _load_config() -> dict[str, Any]:
    path = _ROOT / "args" / "compass_integration.yaml"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _compass_url(cfg: dict[str, Any]) -> str | None:
    """Where Compass lives. ``COMPASS_URL`` wins over the config file.

    An address is deployment, not behaviour, and a developer running a second
    instance on another port should not have to edit a tracked YAML file to reach
    it — that is how a local experiment ends up committed.
    """
    if not cfg.get("enabled", False):
        return None
    url = (os.environ.get("COMPASS_URL") or cfg.get("compass_url") or "").strip()
    return url.rstrip("/") or None


def lcat_lookup(text: str) -> dict | None:
    """Look up the best-matching BLS SOC labor category for `text` (a task
    description, resume, etc.) via Compass's live LCAT taxonomy. Returns
    {soc_code, title, confidence, match_score}, or None on any failure."""
    if not (text or "").strip():
        return None
    cfg = _load_config()
    base_url = _compass_url(cfg)
    if base_url is None:
        return None

    try:
        resp = http_request(
            "POST", f"{base_url}/api/staffing/lcat-lookup",
            json={"text": text}, timeout=cfg.get("timeout_seconds", 15.0),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def staffing_summary() -> dict | None:
    """Fetch Compass's current staffing matrix (personnel vs. resume-
    matched LCAT compliance). Returns {rows, person_count, mismatch_count,
    unresolved_count}, or None on any failure."""
    cfg = _load_config()
    base_url = _compass_url(cfg)
    if base_url is None:
        return None

    try:
        resp = http_request(
            "GET", f"{base_url}/api/staffing/matrix",
            timeout=cfg.get("timeout_seconds", 15.0),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ── Project Scheduler ────────────────────────────────────────────────────────
#
# Compass's scheduler was MODELLED ON the lab-tracker spreadsheets this kind of
# programme actually keeps — phases grouping tasks, hierarchical display ids
# ("2.3"), free-text assignees, due dates that are frequently "TBD", and
# semicolon-separated dependencies on other tasks' display ids. It already does
# the forward pass, the slack, the critical path, the dependency cycles and a
# round-trip-stable XLSX export.
#
# So ICDEV does not do any of that. The division is clean and it is the whole
# point of having two products rather than one:
#
#   ICDEV     reads the documents, reconciles the bill of materials, finds what
#             is wrong with it, and knows what that IMPLIES for the plan.
#   COMPASS   schedules.
#
# Writing a second scheduler here — beside a working one, in the same ecosystem,
# behind the same licence — would be the exact duplication this integration
# exists to prevent.
#
# Everything below degrades to None. No ICDEV workflow may depend on Compass
# being installed, let alone running.

_SCHED = "/api/premium/scheduler"


def _sched_request(method: str, path: str, **kwargs) -> dict | None:
    cfg = _load_config()
    base_url = _compass_url(cfg)
    if base_url is None:
        return None
    try:
        resp = http_request(
            method, f"{base_url}{_SCHED}{path}",
            timeout=cfg.get("timeout_seconds", 30.0), **kwargs,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _ascii_safe(name: str) -> str:
    """Keep a project name inside what Compass's export can survive.

    UPSTREAM BUG (compass, /api/premium/scheduler/projects/{id}/export-xlsx): the
    project name goes straight into the Content-Disposition filename header, and
    HTTP headers are latin-1. So ANY non-ASCII character in a project name — an em
    dash, an accented letter, a curly apostrophe — raises inside the response and
    the export returns 500. "Renewal – Phase 2" is an entirely ordinary project
    name and it takes the endpoint down.

    The real fix belongs in Compass (RFC 5987: filename*=UTF-8''...), and this is
    a workaround, not a solution — which is worth saying out loud, because a
    workaround that pretends to be a fix is how a bug survives.

    Naming is ours to control at the point of creation, so we control it here
    rather than letting an em dash silently cost somebody their tracker.
    """
    cleaned = (
        (name or "")
        .replace("—", "-").replace("–", "-")     # em / en dash
        .replace("‘", "'").replace("’", "'")     # curly quotes
        .replace("“", '"').replace("”", '"')
    )
    return cleaned.encode("ascii", "ignore").decode("ascii").strip() or "Project"


def create_project(name: str, description: str = "",
                   funded_value: float | None = None,
                   start_date: str | None = None) -> dict | None:
    """Open a project in Compass's scheduler. Returns {id, name} or None.

    ``start_date`` anchors the schedule. Pass it. Without one, Compass anchors on
    the day the project was created — so the same plan, built tomorrow, produces
    a different set of dates, and nothing in the output says why.
    """
    body: dict[str, Any] = {
        "name": _ascii_safe(name),
        # The description is never put in a header, so it keeps its typography.
        "description": description,
    }
    if funded_value is not None:
        body["funded_value"] = funded_value
    if start_date:
        body["start_date"] = start_date
    return _sched_request("POST", "/projects", json=body)


def import_tracker(project_id: str, xlsx_bytes: bytes, *,
                   filename: str = "tracker.xlsx",
                   dry_run: bool = True) -> dict | None:
    """Load a tracker-shaped workbook into a Compass project.

    ``dry_run=True`` PREVIEWS: it returns the parsed tasks, the phases it found
    and any warnings, and writes nothing. Do that first, always — Compass's own
    importer handles real-world messiness "explicitly, never silently", and the
    warnings it returns (an unresolvable dependency, a date that will not parse)
    are exactly the things somebody needs to see before they are committed.

    NOTE Compass reads the FIRST worksheet. A real tracker workbook has half a
    dozen sheets and the tasks are rarely on the first one, so the caller hands
    over a single-sheet workbook containing just the task grid. That is ICDEV's
    job: it is the one that reads documents.
    """
    import uuid

    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/vnd.openxmlformats-officedocument."
        f"spreadsheetml.sheet\r\n\r\n"
    ).encode() + xlsx_bytes + f"\r\n--{boundary}--\r\n".encode()

    return _sched_request(
        "POST",
        f"/projects/{project_id}/import-xlsx?dry_run={'true' if dry_run else 'false'}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def upsert_task(project_id: str, task: dict) -> dict | None:
    """Add or update one task.

    ``task`` carries the tracker's own vocabulary: {task_id, name, description,
    phase_name, deps, status, assignee, due_date, comment, duration_days}.
    """
    return _sched_request("POST", f"/projects/{project_id}/tasks", json=task)


def schedule(project_id: str) -> dict | None:
    """Compass's forward pass: start, end, duration, slack, critical, at_risk.

    Returns {anchor, project_end, tasks: {task_id: {...}}}. This is the number
    that matters — the critical path is the difference between a plan and a hope,
    and it is computed from the dependency graph rather than asserted.
    """
    return _sched_request("GET", f"/projects/{project_id}/schedule")


def dep_graph(project_id: str) -> dict | None:
    """The dependency graph, plus the cycles and the blocked tasks."""
    return _sched_request("GET", f"/projects/{project_id}/graph")


def rollup(project_id: str) -> dict | None:
    """Progress by phase, and workload by assignee."""
    return _sched_request("GET", f"/projects/{project_id}/rollup")


def export_tracker(project_id: str) -> bytes | None:
    """The tracker back out as a workbook — round-trip stable, same shape in.

    Bytes, not JSON, so this does not go through _sched_request.
    """
    cfg = _load_config()
    base_url = _compass_url(cfg)
    if base_url is None:
        return None
    try:
        resp = http_request(
            "GET", f"{base_url}{_SCHED}/projects/{project_id}/export-xlsx",
            timeout=cfg.get("timeout_seconds", 30.0),
        )
        if resp.status_code != 200:
            return None
        return resp.content
    except Exception:
        return None

# CUI // SP-CTI
