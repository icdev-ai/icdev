# CUI // SP-CTI
"""A migration is planned only on ICDEV[OS]'s signed may-plan, fail closed (icx-mdz-02).

``icos_decisions.py may-plan`` verified ICDEV[OS]'s signed portfolio decisions and answered "may a migration be
planned for this application", and nothing asked it: ``monolith_decomposer --create-plan`` wrote a plan and its tasks,
and ``refactor_jobs`` made a CAM project and ran its migrators, for any application. Now both ask here first:

* the signed export (``GET /api/v1/portfolio/decisions/export`` on ICDEV[OS]) and the ICDEV[OS] JWKS the operator
  PINNED come from the caller (``--icos-decisions`` / ``--icos-jwks``) or from ``ICDEV_ICOS_DECISIONS`` /
  ``ICDEV_ICOS_JWKS``. Either missing, an export that does not verify against the pinned keys or comes from a broken
  chain, or a may-plan of "no" (including a strategy other than the one ICDEV[OS] accepted) is a REFUSAL;
* a refusal raises ``PlanRefused`` before anything is written, with the reason, and is recorded in the append-only
  audit trail (``approval_denied``, action ``mdz.may_plan.refused:<app>``), where the 7R page and the modernisation
  twin read it; an allowed plan is recorded too (``approval_granted``, ``mdz.may_plan.allowed:<app>``), naming the
  decision it rests on. An audit write that fails never turns a refusal into a plan.

ICDEV decides nothing here: ICDEV[OS]'s mission owner and AO signed the decision; this only refuses to act without it.
Library, no CLI (the planners' own CLIs take the two options).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.modernization.plan_gate")

DECISIONS_ENV = "ICDEV_ICOS_DECISIONS"
JWKS_ENV = "ICDEV_ICOS_JWKS"
REFUSED = "mdz.may_plan.refused"
ALLOWED = "mdz.may_plan.allowed"


class PlanRefused(PermissionError):
    """No plan may be written: ICDEV[OS]'s may-plan is missing, untrusted or says no. ``answer`` says which and why."""

    def __init__(self, answer: dict):
        super().__init__(answer.get("reason") or "may-plan refused")
        self.answer = answer


def _load(src: Any) -> Any:
    """A JSON document from a dict, a JSON string, or a path to a file."""
    if isinstance(src, dict):
        return src
    text = str(src)
    if text.lstrip().startswith("{"):
        return json.loads(text)
    return json.loads(Path(text).read_text(encoding="utf-8"))


def check(app_id: str, strategy: Optional[str] = None, *, export: Any = None, jwks: Any = None) -> dict:
    """ICDEV[OS]'s may-plan for ``app_id`` (its id in the icos-apptwin bundle: the legacy application's id), with
    ``strategy`` held to the one ICDEV[OS] accepted. Never raises; never writes.

    ``status`` is ``allowed``, ``refused`` (the export verified and says no), ``missing`` (no export or no pinned JWKS)
    or ``untrusted`` (the export does not verify against the pinned keys, or its chain was broken).
    """
    from tools.modernization import icos_decisions

    export = export if export not in (None, "") else (os.environ.get(DECISIONS_ENV, "").strip() or None)
    jwks = jwks if jwks not in (None, "") else (os.environ.get(JWKS_ENV, "").strip() or None)
    base = {"application": app_id, "strategy_asked": strategy or None}
    if export is None:
        return {**base, "allowed": False, "status": "missing",
                "reason": ("no signed ICDEV[OS] decisions export: a migration is planned only on ICDEV[OS]'s may-plan "
                           f"(pass --icos-decisions, or set {DECISIONS_ENV}, to GET /api/v1/portfolio/decisions/export)")}
    if jwks is None:
        return {**base, "allowed": False, "status": "missing",
                "reason": f"no pinned ICDEV[OS] JWKS: pass --icos-jwks, or set {JWKS_ENV}, to the file you pinned"}
    try:
        doc = icos_decisions.verify(_load(export), icos_decisions.pinned(_load(jwks)))
    except (icos_decisions.Untrusted, OSError, ValueError, TypeError, KeyError) as exc:
        return {**base, "allowed": False, "status": "untrusted", "reason": f"the decisions export is not trusted: {exc}"}
    ans = icos_decisions.may_plan(doc, app_id, strategy or None)
    return {**base, **ans, "status": "allowed" if ans["allowed"] else "refused", "issuer": doc.get("issuer")}


def redundancy(app_id: str, *, export: Any = None, jwks: Any = None) -> dict:
    """7R retire's ``functional_redundancy`` input (icx-mdz-04, gap G2): ``app_id``'s measured redundancy from
    ICDEV[OS]'s signed decisions export (the same export and pinned JWKS as may-plan, or their environment variables).
    Never raises; never writes. ``share`` is None unless the export verifies AND ICDEV[OS] measured it: a missing or
    untrusted export, an unmeasured or unreported row all leave the criterion unmeasured (``state`` says which), and
    an unmeasured criterion lowers 7R's confidence -- it is never scored."""
    from tools.modernization import icos_decisions

    export = export if export not in (None, "") else (os.environ.get(DECISIONS_ENV, "").strip() or None)
    jwks = jwks if jwks not in (None, "") else (os.environ.get(JWKS_ENV, "").strip() or None)
    if export is None or jwks is None:
        return {"share": None, "state": "missing",
                "why": "no signed ICDEV[OS] decisions export and pinned JWKS given: redundancy is unmeasured"}
    try:
        doc = icos_decisions.verify(_load(export), icos_decisions.pinned(_load(jwks)))
    except (icos_decisions.Untrusted, OSError, ValueError, TypeError, KeyError) as exc:
        return {"share": None, "state": "untrusted", "why": f"the decisions export is not trusted: {exc}"}
    return icos_decisions.redundancy(doc, app_id)


def require(app_id: str, strategy: Optional[str] = None, *, tool: str, project_id: Optional[str] = None,
            export: Any = None, jwks: Any = None) -> dict:
    """``check``, recorded in the audit trail; raises ``PlanRefused`` unless ICDEV[OS]'s may-plan allows the plan.

    Call it before anything is written: on a refusal the caller writes no plan, no task, no CAM project, no job.
    """
    ans = {**check(app_id, strategy, export=export, jwks=jwks), "tool": tool}
    ans["audit_id"] = _record(ans, tool=tool, project_id=project_id)
    if not ans["allowed"]:
        logger.warning("plan refused for %s (%s): %s", app_id, tool, ans["reason"])
        raise PlanRefused(ans)
    return ans


def _record(ans: dict, *, tool: str, project_id: Optional[str]) -> Optional[int]:
    """The audit row for one may-plan answer; None when it could not be written (the answer stands either way)."""
    try:
        from tools.audit.audit_logger import log_event

        allowed = bool(ans.get("allowed"))
        details = {k: ans.get(k) for k in ("application", "strategy_asked", "status", "reason", "role", "strategy",
                                           "baseline", "baseline_digest", "item", "change", "decisions", "issuer")
                   if ans.get(k) is not None}
        details.update(tool=tool, project_id=project_id)
        entry = log_event("approval_granted" if allowed else "approval_denied", actor=tool,
                          action=f"{ALLOWED if allowed else REFUSED}:{ans.get('application')}", details=details)
        return entry if isinstance(entry, int) and entry > 0 else None
    except Exception as exc:  # noqa: BLE001 -- a lost audit row never turns a refusal into a plan
        logger.warning("may-plan audit write failed for %s: %s", ans.get("application"), exc)
        return None


def refusals(app_ids: Optional[Iterable[str]] = None, *, limit: int = 200, conn: Any = None) -> list[dict]:
    """The recorded may-plan refusals, newest first: ``[{id, application, tool, status, reason, strategy_asked, at}]``.

    Raises when the audit trail cannot be read: a page that shows "no refusal" must know it looked.
    """
    from tools.db.storage import get_canvas_connection

    want = {str(a) for a in app_ids} if app_ids is not None else None
    own = conn is None
    conn = conn or get_canvas_connection()
    try:
        rows = conn.execute(
            "SELECT id, actor, action, details, created_at FROM audit_trail "
            "WHERE event_type = 'approval_denied' AND action LIKE %s ORDER BY id DESC LIMIT %s",
            (f"{REFUSED}:%", int(limit))).fetchall()
    finally:
        if own:
            conn.close()
    out = []
    for r in rows:
        rid, actor, action, details, created = tuple(r)
        app = str(action).split(":", 1)[1] if ":" in str(action) else ""
        if want is not None and app not in want:
            continue
        try:
            d = json.loads(details or "{}")
        except ValueError:
            d = {}
        out.append({"id": int(rid), "application": app, "tool": actor, "status": d.get("status"),
                    "reason": d.get("reason"), "strategy_asked": d.get("strategy_asked"),
                    "project_id": d.get("project_id"), "at": str(created or "")})
    return out
