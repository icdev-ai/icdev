# CUI // SP-CTI
"""From a FIXED INCIDENT to a STANDING CLAIM (autonomy-lrn-01).

THE DEFECT. Every defect this week was fixed, tested and documented — and the
test is a fixture-based unit test pinning ONE function. When the same defect
exists at a second site the test still passes, the card is marked done, and the
bug keeps firing under a green test. hgx-park-01 made the Studio approval park
atomic in ``workflow_runner._park_for_approval`` and pinned it with structural
tests that read THAT function's source; ``mcp_executor.open_approval_gate`` had
the identical two-commit defect, went on failing for weeks, and was read as
Windows flake until rem-hyg-19.

A claim in ``tools.awareness.claims`` does not have that blind spot, because it
is asserted against the PRIMARY DATA and not against a function: a gate row
parked under a run that still reads ``running`` is the same finding whichever
site wrote it. So the conversion INCIDENT -> CLAIM is what gives a fix a live
regression guard. It was manual, and mostly did not happen — measured
2026-08-21: 4 claims against a week with 58 done ``fix`` cards (completed or
last touched inside 7 days) — 53 of them with no standing claim.

This module is that path, and the measurement of it:

  * every :class:`~tools.awareness.claim_verifier.Claim` cites an
    :class:`~tools.awareness.claim_verifier.Incident` by kanban task id;
  * :func:`verify_incident` checks the citation is a VERIFIED FACT — the card
    is ``done`` on the board AND the id landed on the default branch — using
    ``tools.kanban.landed_check``, the one implementation of "is it on main";
  * :func:`coverage_report` names which of a window's fixed incidents have a
    standing claim and which do not. NAMES, not a count: a count can be held
    constant while the set churns.

LEARN FROM VERIFIED FACT, NEVER FROM REPETITION. An incident cited by two
claims is ONE incident; a claim citing two task ids (the same defect fixed at
two sites) is one claim guarding TWO incidents. Both are counted as distinct
ids, never as rows — the same rule ``independent_observations`` applies to
evidence.

UNMEASURABLE IS NEVER A CLEAN ZERO. A board with no done fixes in the window
(a fresh worktree database, an ephemeral CI database) reports ``unmeasurable``,
not "every incident is guarded". Report only — no ``--gate``, and nothing here
seeds a claim automatically: a claim needs two derivations that share no code,
and that is authored, with the incident cited, by whoever fixed the defect.

A library. CLI: ``python tools/awareness/claim_verifier.py --incidents``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from tools.awareness.claim_verifier import Claim, Incident

#: A card-shaped id: ``<prefix><epic>-<n>``. A claim must cite a CARD, because
#: the board and the default branch are where the fact is verified.
_TASK_ID = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+$")


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def cited_incidents(registry: Iterable[Claim]) -> Dict[str, List[str]]:
    """``{task_id: [claim_id, ...]}`` — the incidents the registry stands on.

    Keyed by task id so a task cited twice is one entry with two guards, and a
    claim citing two tasks contributes two entries. Distinct ids, never rows.
    """
    out: Dict[str, List[str]] = {}
    for claim in registry:
        if not claim.incident:
            continue
        for task_id in claim.incident.task_ids:
            out.setdefault(task_id, []).append(claim.claim_id)
    return out


def claims_without_incident(registry: Iterable[Claim]) -> List[str]:
    """Claims citing nothing. These are the ones that could be 'reports clean
    because it does nothing' — the registry's tests refuse them."""
    return [c.claim_id for c in registry
            if not c.incident or not c.incident.task_ids]


def incident_is_well_formed(incident: Optional[Incident]) -> bool:
    """Card-shaped ids and an ISO observation date. A malformed citation is
    refused by the tests, not at import — a typo must not take the verifier
    down with it."""
    if not incident or not incident.task_ids:
        return False
    if not all(_TASK_ID.match(t or "") for t in incident.task_ids):
        return False
    try:
        datetime.strptime(incident.observed_on, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return True


# --------------------------------------------------------------------------- #
# Is the cited incident a VERIFIED FACT?
# --------------------------------------------------------------------------- #
def board_status(task_ids: Iterable[str]) -> Optional[Dict[str, Optional[str]]]:
    """``{task_id: status}`` from the board; a missing id maps to None.

    Returns None — UNMEASURABLE — when the board cannot be read at all, which
    is not the same as every id being absent.
    """
    ids = [str(t) for t in task_ids]
    if not ids:
        return {}
    try:
        conn = _conn()
    except Exception:  # noqa: BLE001
        return None
    try:
        marks = ",".join(["%s"] * len(ids))
        rows = conn.execute(
            f"SELECT id, status FROM kanban_tasks WHERE id IN ({marks})",
            tuple(ids),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    found = {str(dict(r)["id"]): dict(r).get("status") for r in rows}
    return {t: found.get(t) for t in ids}


def landed_status(task_ids: Iterable[str], repo_root=None) -> Optional[Dict[str, Optional[str]]]:
    """``{task_id: confidence}`` for ids on the default branch; None per id
    when not landed. None overall when git could not answer (``checked`` False
    on every report — a shallow clone, no repository): fail-OPEN is never a
    clean answer, so it is reported as unmeasurable rather than as 'not landed'.
    """
    ids = [str(t) for t in task_ids]
    if not ids:
        return {}
    try:
        from tools.kanban.landed_check import check_landed_bulk
        reports = check_landed_bulk(ids, repo_root=repo_root)
    except Exception:  # noqa: BLE001
        return None
    if not any(r.get("checked") for r in reports.values()):
        return None
    return {t: (reports.get(t, {}).get("confidence")
                if reports.get(t, {}).get("landed") else None) for t in ids}


#: "Not supplied" — distinct from None, which means "could not be read".
_UNSET: Any = object()


def verify_incident(incident: Incident, repo_root=None, *,
                    board: Any = _UNSET, landed: Any = _UNSET) -> Dict[str, Any]:
    """Is this citation a fact? ``verified`` is True | False | None.

    True requires EVERY cited id to be ``done`` on the board AND landed on the
    default branch — a card in ``pr_opened`` is a fix that has not happened yet,
    and a card ``done`` with nothing on main is the 'board says done but it is
    not on main' bug this repo already gates against. None when either source
    could not be read; it is never folded into True.

    ``board`` / ``landed`` may be supplied (tests, or a caller that already
    holds them for many incidents) so one sweep does not re-query per claim.
    Passing None for either means "that source could not be read" and yields
    ``verified: None`` — it is NOT treated as "please look it up".
    """
    ids = list(incident.task_ids or [])
    if board is _UNSET:
        board = board_status(ids)
    if landed is _UNSET:
        landed = landed_status(ids, repo_root=repo_root)
    result: Dict[str, Any] = {
        "task_ids": ids,
        "observed_on": incident.observed_on,
        "well_formed": incident_is_well_formed(incident),
        "board": board,
        "landed": landed,
        "verified": None,
        "reason": "",
    }
    if not result["well_formed"]:
        result.update(verified=False, reason="citation is malformed")
        return result
    if board is None or landed is None:
        result["reason"] = "board unreadable" if board is None else "git could not answer"
        return result
    not_done = [t for t in ids if board.get(t) != "done"]
    not_landed = [t for t in ids if not landed.get(t)]
    if not_done or not_landed:
        bits = []
        if not_done:
            bits.append("not done on the board: " + ", ".join(
                f"{t}={board.get(t) or 'absent'}" for t in not_done))
        if not_landed:
            bits.append("not on the default branch: " + ", ".join(not_landed))
        result.update(verified=False, reason="; ".join(bits))
        return result
    result.update(verified=True)
    return result


# --------------------------------------------------------------------------- #
# Which fixed incidents have a standing claim?
# --------------------------------------------------------------------------- #
def fixed_incidents(window_days: int = 7) -> Optional[List[Dict[str, Any]]]:
    """Done ``fix`` cards completed inside the window — the population a claim
    could have been learned from. None when the board cannot be read."""
    cut = (datetime.now(timezone.utc) - timedelta(days=int(window_days))).isoformat()
    try:
        conn = _conn()
    except Exception:  # noqa: BLE001
        return None
    try:
        rows = conn.execute(
            "SELECT id, title, COALESCE(completed_at, updated_at) AS finished_at "
            "FROM kanban_tasks WHERE task_type = 'fix' AND status = 'done' "
            "AND COALESCE(completed_at, updated_at) >= %s "
            "ORDER BY COALESCE(completed_at, updated_at) DESC",
            (cut,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    seen, out = set(), []
    for r in rows:
        record = dict(r)
        task_id = str(record.get("id"))
        if task_id in seen:          # distinct ids, never rows
            continue
        seen.add(task_id)
        out.append({"id": task_id, "title": record.get("title"),
                    "finished_at": str(record.get("finished_at") or "")})
    return out


def coverage_report(registry: Iterable[Claim], window_days: int = 7,
                    repo_root=None, *, fixed: Optional[List[Dict[str, Any]]] = None,
                    verify: bool = True) -> Dict[str, Any]:
    """The measurement: of the window's fixed incidents, which have a claim?

    state: unmeasurable | measured | error.
      unmeasurable  the board holds no done fix in the window — a fresh or
                    ephemeral database. ``guarded``/``unguarded`` are None,
                    NEVER 0 — a count of zero unguarded incidents over a board
                    with no incidents is the reassurance this module exists
                    to refuse.
      measured      rows exist; both lists are NAMED.
      error         the board could not be read.
    Independent of the window, ``claims_without_incident`` and
    ``unverified_incidents`` report the registry's own discipline.
    """
    claims = list(registry)
    cited = cited_incidents(claims)
    report: Dict[str, Any] = {
        "state": "measured",
        "window_days": int(window_days),
        "claims": len(claims),
        "incidents_cited": len(cited),
        "claims_without_incident": claims_without_incident(claims),
        "fixed": None, "guarded": None, "unguarded": None,
        "guarded_ids": [], "unguarded_ids": [],
        "unverified_incidents": [],
        "incidents": {},
    }
    if fixed is None:
        fixed = fixed_incidents(window_days)
    if fixed is None:
        report["state"] = "error"
        report["detail"] = "the board could not be read"
    elif not fixed:
        report["state"] = "unmeasurable"
        report["detail"] = (f"no done fix card completed in the last {window_days} "
                            "days — a database with no operating history, not a "
                            "board with every incident guarded")
    else:
        guarded = [f for f in fixed if f["id"] in cited]
        unguarded = [f for f in fixed if f["id"] not in cited]
        report.update(
            fixed=len(fixed), guarded=len(guarded), unguarded=len(unguarded),
            guarded_ids=[{**f, "claims": cited[f["id"]]} for f in guarded],
            unguarded_ids=unguarded,
        )
    if verify:
        ids = sorted(cited)
        board = board_status(ids)
        landed = landed_status(ids, repo_root=repo_root)
        for claim in claims:
            if not claim.incident:
                continue
            v = verify_incident(claim.incident, repo_root=repo_root,
                                board=board, landed=landed)
            report["incidents"][claim.claim_id] = v
            if v["verified"] is not True:
                report["unverified_incidents"].append(
                    {"claim_id": claim.claim_id, "task_ids": v["task_ids"],
                     "verified": v["verified"], "reason": v["reason"]})
    return report


def render(report: Dict[str, Any]) -> str:
    out = [f"Incident -> claim coverage — last {report['window_days']} day(s)  "
           f"[{report['state']}]"]
    out.append(f"  claims {report['claims']} citing {report['incidents_cited']} "
               f"distinct incident(s)")
    if report["state"] != "measured":
        out.append(f"  {report.get('detail', '')}")
    else:
        out.append(f"  fixed {report['fixed']} · guarded {report['guarded']} · "
                   f"UNGUARDED {report['unguarded']}")
        for f in report["guarded_ids"]:
            out.append(f"    ok   {f['id']:28} <- {', '.join(f['claims'])}")
        for f in report["unguarded_ids"]:
            out.append(f"    --   {f['id']:28} {str(f.get('title') or '')[:60]}")
    if report["claims_without_incident"]:
        out.append("  claims citing NO incident: "
                   + ", ".join(report["claims_without_incident"]))
    for u in report["unverified_incidents"]:
        mark = "??" if u["verified"] is None else "!!"
        out.append(f"  {mark} {u['claim_id']} cites {','.join(u['task_ids'])}: "
                   f"{u['reason'] or 'unverifiable'}")
    return "\n".join(out)
