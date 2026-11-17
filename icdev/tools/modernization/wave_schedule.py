# CUI // SP-CTI
"""Date an application's waves from a DECLARED, REVIEWED team capacity, deterministically (icx-mdz-05).

``app_wave_planner`` derives the waves and never schedules them; ``apptwin_export.wave_estimates`` sums a planned
wave's tasks' ``estimated_hours``. ICDEV[OS]'s twin watches a wave's ``starts`` and ``ends`` (overdue, late to start)
and reads either as unverified while none is reported. This module gives each wave its estimate and, only when a person
declared AND reviewed how many hours a week the team spends on the application, its dates:

* the capacity is declared in ``args/modernisation_capacity.yaml`` (shipping empty): per application, by its legacy id
  or name, ``hours_per_week``, the ``start`` date the first wave may start and the ``source`` it rests on (a staffing
  plan, a PMO record). An entry with another field, no source, a non-number or a date that cannot be read is refused;
* the entry counts only once a person approved it AT ITS DIGEST through the declared step
  ``decl-wft-mdz-capacity-review`` (the Modernisation page): a changed entry is a new one, reviewed again;
* a wave's estimate is the sum of its tasks' own estimates (the refactor plan's ``migration_tasks.estimated_hours``);
  none (never 0) when no task is on it or one has no estimate, as the exporter already says;
* the team works one wave at a time, in dependency order (a wave starts once every wave it depends on has ended; ties
  by wave number), at ``hours_per_week / 5`` hours on each working day (Monday to Friday) from ``start``. ``starts``
  is the working day its first hour falls on, ``ends`` the working day its last hour falls on (ISO dates);
* no capacity, an unreviewed or refused one, no estimate, or a dependency that is not a wave: no date, and ``reason``
  says why (unmeasured). A wave after an undated one is undated too: when it could start is unknown. Nothing here
  invents a date, and each date carries the capacity digest it was computed from.

Library, no CLI (``app_wave_planner.py --capacity`` takes the file).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from icdev.core.paths import repo_root  # noqa: E402
from tools.modernization.decisions import TEMPLATE_CAPACITY as TEMPLATE  # noqa: E402

_REPO_ROOT = Path(repo_root(__file__)).resolve()

CAPACITY_REL = "args/modernisation_capacity.yaml"
#: The e2e server's capacity file (icx-mdz-05), honoured ONLY for a file under the repository's ``tests/``: a
#: deployment cannot swap in a capacity nobody reviewed through it.
E2E_ENV = "ICDEV_MODERNISATION_CAPACITY_E2E"
FIELDS = ("hours_per_week", "start", "source")
METHOD = ("one wave at a time in dependency order (ties by wave number), each taking its tasks' estimated hours at the "
          "declared hours per week spread over Monday to Friday from the declared start; deterministic")
WORKDAYS = 5


class CapacityError(ValueError):
    pass


def capacity_path(path: str | Path | None = None) -> Path:
    """The declared capacity file: ``path``, else the e2e file under ``tests/`` when named, else the args file."""
    if path:
        return Path(path)
    raw = os.environ.get(E2E_ENV, "").strip()
    if raw:
        tests = (_REPO_ROOT / "tests").resolve()
        p = Path(raw).resolve()
        if tests in p.parents:
            return p
    return _REPO_ROOT / CAPACITY_REL


def shown(path: str | Path | None = None) -> str:
    """The capacity file as the report names it: relative to the repository where it is inside it."""
    p = capacity_path(path).resolve()
    return p.relative_to(_REPO_ROOT).as_posix() if _REPO_ROOT in p.parents else str(p)


def digest(key: str, entry: dict) -> str:
    """SHA-256 over the canonical JSON of one declared entry and the application it is declared for."""
    doc = {"application": str(key), **{k: entry[k] for k in FIELDS}}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _start(v: Any, where: str) -> str:
    if isinstance(v, date):                      # YAML reads an unquoted 2027-01-04 as a date
        return v.isoformat()
    try:
        return date.fromisoformat(str(v).strip()).isoformat()
    except ValueError:
        raise CapacityError(f"{where} start {v!r} is not an ISO date (YYYY-MM-DD)") from None


def declared(path: str | Path | None = None) -> dict[str, dict]:
    """``{application name or legacy id: {hours_per_week, start, source, digest}}`` from the capacity file. Refuses an
    entry that names another field, names no source, gives hours that are not a positive number, or a start that is
    not a date: nothing is guessed or normalised into one. No file: nothing declared."""
    import yaml

    p = capacity_path(path)
    if not p.is_file():
        if path:
            raise CapacityError(f"no capacity file {p}")
        return {}
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    apps = doc.get("applications") or {}
    if not isinstance(apps, dict):
        raise CapacityError(f"{p.name}: 'applications' must be a mapping of application name (or id) to its capacity")
    out = {}
    for key, e in apps.items():
        where = f"{p.name}: {key!r}"
        if not isinstance(e, dict):
            raise CapacityError(f"{where} must be a mapping")
        odd = sorted(set(e) - set(FIELDS))
        if odd:
            raise CapacityError(f"{where} names {odd}, not one of {list(FIELDS)}")
        h = e.get("hours_per_week")
        if isinstance(h, bool) or not isinstance(h, (int, float)) or not h > 0 or math.isinf(h):
            raise CapacityError(f"{where} hours_per_week must be a positive number of hours: YAML read {h!r}")
        if not str(e.get("source") or "").strip():
            raise CapacityError(f"{where} declares a capacity and names no source: say where it is declared")
        entry = {"hours_per_week": float(h), "start": _start(e.get("start"), where), "source": str(e["source"]).strip()}
        out[str(key)] = {**entry, "digest": digest(str(key), entry)}
    return out


def capacity_for(app_id: str, name: str | None, *, path: str | Path | None = None) -> tuple[str | None, dict | None]:
    """The application's declared capacity (by its legacy id, else its name): ``(key, entry)``, or ``(None, None)``."""
    caps = declared(path)
    for key in (app_id, name):
        if key and key in caps:
            return key, caps[key]
    return None, None


def subject(key: str) -> str:
    return f"capacity:{key}"


def review_state(key: str, entry: dict, conn: Any = None) -> dict:
    """The review a person took on this entry: ``reviewed`` (approved at this digest), ``rejected`` (at this digest),
    ``stale`` (taken at another digest: the entry changed) or ``unreviewed``; ``unreadable`` when the ledger cannot be
    read (which counts as not reviewed)."""
    from tools.modernization import decisions

    try:
        row = decisions.decided(TEMPLATE, [subject(key)], conn=conn).get(subject(key))
    except Exception as exc:  # noqa: BLE001 -- a ledger that cannot be read never makes a capacity count
        if conn is not None:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        return {"state": "unreadable", "why": str(exc)[:200]}
    if row is None:
        return {"state": "unreviewed"}
    at = (row.get("payload") or {}).get("digest")
    state = ("reviewed" if row["affirmative"] else "rejected") if at == entry["digest"] else "stale"
    return {"state": state, "step_id": row["step_id"], "decision": row["decision"], "by": row["actor"],
            "role": row["actor_role"], "reason": row["reason"], "at": row["created_at"], "digest": at}


def _workday(d: date) -> date:
    while d.weekday() >= WORKDAYS:
        d += timedelta(days=1)
    return d


def _nth_workday(first: date, n: int) -> date:
    """The ``n``-th working day (0-based) counting from ``first`` (itself a working day)."""
    weeks, rest = divmod(n, WORKDAYS)
    d = first + timedelta(weeks=weeks)
    for _ in range(rest):
        d = _workday(d + timedelta(days=1))
    return d


def _order(waves: list[dict]) -> tuple[list[dict], str | None]:
    """The waves in dependency order (ties by wave number), or why there is none: a dependency that names no wave,
    or a cycle."""
    num = {int(w["wave_number"]): w for w in waves}
    for w in waves:
        bad = [d for d in w.get("depends_on") or [] if int(d) not in num]
        if bad:
            return [], f"wave {w['wave_number']} depends on {bad}, which are not waves of this plan"
    done: list[dict] = []
    seen: set[int] = set()
    while len(done) < len(waves):
        ready = [n for n, w in sorted(num.items()) if n not in seen and all(int(d) in seen for d in w.get("depends_on") or [])]
        if not ready:
            return [], "the waves' dependencies form a cycle, so no order exists"
        seen.add(ready[0])
        done.append(num[ready[0]])
    return done, None


def schedule(waves: list[dict], estimates: dict[str, float | None], *, key: str | None, entry: dict | None,
             review: dict | None, app: str = "the application", file: str = CAPACITY_REL) -> dict:
    """Pure. Each wave's estimate and, from a reviewed capacity, its dates.

    ``waves``: the planner's (``id``, ``wave_number``, ``depends_on`` as wave numbers); ``estimates``: ``{wave id: hours
    or None}``. Returns ``{"waves": {id: {estimated_hours?, starts?, ends?, undated?}}, "dated", "reason", "capacity",
    "method"}``; ``dated`` is true only when every wave has its dates."""
    per: dict[str, dict] = {str(w["id"]): ({"estimated_hours": round(float(estimates[str(w["id"])]), 2)}
                                           if estimates.get(str(w["id"])) is not None else {}) for w in waves}
    cap = None if entry is None else {"application": key, "file": file,
                                      **{k: entry[k] for k in (*FIELDS, "digest")}, "review": review}

    def undated(reason: str) -> dict:
        for w in waves:
            per[str(w["id"])]["undated"] = reason
        return {"waves": per, "dated": False, "reason": reason, "capacity": cap, "method": METHOD}

    if not waves:
        return {"waves": per, "dated": False, "reason": "no wave was planned", "capacity": cap, "method": METHOD}
    if entry is None:
        return undated(f"no team capacity is declared for {app} in {file}: the waves are not dated "
                       "(unmeasured), never a date made up")
    state = (review or {}).get("state") or "unreviewed"
    if state != "reviewed":
        why = {"rejected": "a person rejected it", "stale": "it changed since a person reviewed it",
               "unreadable": f"the review ledger could not be read ({(review or {}).get('why')})"}.get(
            state, "no person has reviewed it")
        return undated(f"the declared capacity for {app} (sha256 {entry['digest'][:12]}) is not reviewed: {why} "
                       f"({TEMPLATE}); the waves are not dated (unmeasured)")
    order, bad = _order(waves)
    if bad:
        return undated(f"{bad}; the waves are not dated")
    daily = Fraction(str(entry["hours_per_week"])) / WORKDAYS
    first = _workday(date.fromisoformat(entry["start"]))
    ends: dict[int, Fraction] = {}
    team = Fraction(0)
    blocked: str | None = None
    for w in order:
        wid, n = str(w["id"]), int(w["wave_number"])
        hours = estimates.get(wid)
        if blocked is None and hours is None:
            blocked = (f"wave {n} has no estimate (no task of the plan is on its components, or one has no "
                       "estimated hours)")
            per[wid]["undated"] = f"{blocked}: not dated (unmeasured)"
            continue
        if blocked is not None:
            per[wid]["undated"] = f"it comes after wave(s) that are not dated ({blocked}): not dated"
            continue
        start_h = max([team, *(ends[int(d)] for d in w.get("depends_on") or [])])
        end_h = start_h + Fraction(str(hours))
        ends[n] = team = end_h
        s_idx = math.floor(start_h / daily)
        e_idx = max(s_idx, math.ceil(end_h / daily) - 1)
        per[wid].update(starts=_nth_workday(first, s_idx).isoformat(), ends=_nth_workday(first, e_idx).isoformat())
    dated = all("starts" in v for v in per.values())
    basis = (f"{entry['hours_per_week']:g} h/week from {entry['start']} (declared capacity sha256 "
             f"{entry['digest'][:12]}, reviewed by step {(review or {}).get('step_id')})")
    reason = (f"dated from {basis}" if dated else
              f"{sum('starts' in v for v in per.values())} of {len(per)} wave(s) dated from {basis}; {blocked}")
    return {"waves": per, "dated": dated, "reason": reason, "capacity": cap, "method": METHOD}


def plan_estimates(app_id: str, conn, waves: list[dict], names: dict[str, str], *,
                   plan_id: str | None = None) -> tuple[str | None, dict[str, float | None]]:
    """The plan the estimates come from (``plan_id``, else the application's latest) and each wave's sum of its tasks'
    ``estimated_hours`` (``apptwin_export.wave_estimates``). No plan: ``(None, {})`` -- no estimate, so no date."""
    from tools.modernization.apptwin_export import wave_estimates

    try:
        if plan_id:
            row = conn.execute("SELECT id FROM migration_plans WHERE id = %s AND legacy_app_id = %s",
                               (plan_id, app_id)).fetchone()
        else:
            row = conn.execute("SELECT id FROM migration_plans WHERE legacy_app_id = %s ORDER BY created_at DESC "
                               "LIMIT 1", (app_id,)).fetchone()
        if row is None:
            return None, {}
        pid = row["id"]
        tasks = [dict(t) for t in conn.execute("SELECT legacy_component_id, estimated_hours FROM migration_tasks "
                                               "WHERE plan_id = %s", (pid,)).fetchall()]
    except Exception as exc:  # no migration tables here: nothing was planned
        if "no such table" in str(exc) or "does not exist" in str(exc):
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            return None, {}
        raise
    return str(pid), wave_estimates(waves, tasks, names)


def apply(rep: dict, app_id: str, conn, *, names: dict[str, str], app: str, plan_id: str | None = None,
          path: str | Path | None = None) -> dict:
    """``rep`` (the planner's report) with each wave's ``estimated_hours``, ``starts`` and ``ends`` where known (or
    ``undated`` with why) and a ``schedule`` block: dated or not, the reason, the plan the estimates come from and the
    declared capacity with its digest and review."""
    waves = rep.get("waves") or []
    pid, estimates = plan_estimates(app_id, conn, waves, names, plan_id=plan_id)
    try:
        key, entry = capacity_for(app_id, app, path=path)
        error = None
    except CapacityError as exc:          # a declaration that is refused is said, never used
        key, entry, error = None, None, str(exc)
    review = review_state(key, entry, conn) if entry is not None else None
    out = schedule(waves, estimates, key=key, entry=entry, review=review, app=app, file=shown(path))
    if error:
        out["reason"] = f"the capacity file is refused ({error}): the waves are not dated"
        for v in out["waves"].values():
            v["undated"] = out["reason"]
    if pid is None and waves:
        why = "no migration plan yet, so no task estimates: the waves carry no estimate and no date"
        out["reason"] = why if entry is None or error else f"{why} ({out['reason']})"
        for v in out["waves"].values():
            v["undated"] = why
    for w in waves:
        w.update(out["waves"][str(w["id"])])
    return {**rep, "schedule": {"dated": out["dated"], "reason": out["reason"], "plan_id": pid,
                                "capacity": out["capacity"], "method": out["method"],
                                **({"capacity_error": error} if error else {})}}
