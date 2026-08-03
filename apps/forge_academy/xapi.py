# CUI // SP-CTI
"""FORGE Academy → xAPI (Experience API 1.0.3) statement export — aca-trn-05.

Completions and certificates live only in ``fa_*`` tables. If Academy results are
ever meant to count as training of record, they have to leave the platform in a
form an LMS/LRS already understands. This module renders them as xAPI statements.

WHY xAPI AND NOT SCORM
    SCORM's unit of record is a course launch with a single rolled-up
    completion/score. The Academy's unit of record is a *verified step* — a
    submission graded server-side against a stored test, with its own score,
    duration and provenance row. Flattening that to one SCORM cmi.core.score per
    mission discards exactly the granularity that makes the record worth
    exporting. xAPI carries a statement per step, per mission and per
    certificate, each with its own actor/verb/object/result.

    SCORM is deliberately NOT implemented here. It is a packaging format for a
    specific target LMS, and building it before a target exists would mean
    guessing at the manifest, the launch sequence and the rollup rules. When a
    named LMS demands SCORM, wrap this module's statements — the data is already
    the hard part.

WHAT IS AND IS NOT EXPORTED
    An export into a system of record must not publish a completion it cannot
    stand behind. Every statement is therefore matched to its provenance row
    before it is emitted:

        step         fa_xp_ledger  reason='step_pass'       source_type='step'
        mission      fa_xp_ledger  reason='mission_complete' source_type='mission'
        certificate  fa_certificate_evidence rows for that cert_id

    A record with no such row, or with one flagged ``verified=0`` (the 315
    backfill wrote those for pre-ledger history it could only reconstruct), is
    EXCLUDED by default and counted in the ``excluded`` block of the result, so
    the caller sees what was withheld rather than a silently shorter list.
    ``include_unverified=True`` emits them anyway, but stamps each statement with
    a provenance extension carrying ``verified: false`` — an LRS consumer can
    then filter on the statement itself. There is no mode in which an
    unverifiable completion is presented as a verified one.

Statement IDs are deterministic (UUIDv5 over activity + actor + verb +
timestamp), so re-running an export and re-POSTing to the same LRS is idempotent
rather than duplicating the learner's history.

All SQL here is PG-native (``%s`` placeholders, no dialect JSON). The
progress→provenance join is done in Python rather than SQL because the ledger can
hold more than one row per (user, source) — a LEFT JOIN would fan the progress row
out and inflate the export.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IRIs
# ---------------------------------------------------------------------------

#: Base IRI for activities this platform owns. Configurable because an LRS keys
#: activities by IRI: two deployments feeding the same LRS must not both claim
#: ``https://icdev.ai/xapi/forge-academy/mission/m-t1-01``.
DEFAULT_ACTIVITY_BASE = "https://icdev.ai/xapi/forge-academy"

# ADL-registered verbs. Only these three are used; the Academy has no vocabulary
# of its own to invent here and an unregistered verb IRI is not interoperable.
VERB_PASSED = "http://adlnet.gov/expapi/verbs/passed"
VERB_COMPLETED = "http://adlnet.gov/expapi/verbs/completed"
# ADL registers no "earned". This is the Open Badges xAPI verb, which is what
# certificate issuance is in practice and what LRS vocabularies already carry.
VERB_EARNED = "http://specification.openbadges.org/xapi/verbs/earned"

ACTIVITY_ASSESSMENT = "http://adlnet.gov/expapi/activities/assessment"
ACTIVITY_COURSE = "http://adlnet.gov/expapi/activities/course"
ACTIVITY_CERTIFICATE = "http://id.tincanapi.com/activitytype/certificate"

XAPI_VERSION = "1.0.3"

#: UUIDv5 namespace for this exporter. Fixed forever: changing it re-issues every
#: statement ID and an LRS would accept the whole history a second time.
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://icdev.ai/xapi/forge-academy")


def activity_base() -> str:
    return (os.environ.get("ICDEV_XAPI_ACTIVITY_BASE") or DEFAULT_ACTIVITY_BASE).rstrip("/")


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------

def _iso8601(value) -> str | None:
    """Normalise a stored timestamp to ISO-8601 with an explicit UTC offset.

    ``fa_*`` timestamps are written by ``datetime('now')`` on SQLite (naive
    'YYYY-MM-DD HH:MM:SS', UTC by definition) and as a timestamp type on
    PostgreSQL. xAPI requires an offset; a naive value is treated as the UTC it
    already is rather than silently reinterpreted as local time.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _duration(started, completed) -> str | None:
    """ISO-8601 duration between two stored timestamps, or None if unknowable.

    Returned only when both ends parse and the interval is non-negative. A
    fabricated duration is worse than an absent one: an LMS reports it as time on
    task.
    """
    a, b = _iso8601(started), _iso8601(completed)
    if not a or not b:
        return None
    seconds = int(
        (datetime.fromisoformat(b.replace("Z", "+00:00"))
         - datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds()
    )
    if seconds < 0:
        return None
    return f"PT{seconds}S"


def _score(raw) -> dict | None:
    """Academy scores are percentages (grading._verdict, assessment score_pct)."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    value = max(0.0, min(100.0, value))
    return {"raw": value, "min": 0.0, "max": 100.0, "scaled": round(value / 100.0, 4)}


def _row(row) -> dict:
    return dict(row) if row is not None and not isinstance(row, dict) else (row or {})


# ---------------------------------------------------------------------------
# Actor
# ---------------------------------------------------------------------------

def build_actor(user: dict) -> dict | None:
    """An xAPI Agent, or None when the user carries no usable identifier.

    xAPI requires exactly one inverse-functional identifier. Email is preferred
    because it is what an external LMS matches its own accounts on; a username
    falls back to an ``account`` scoped to this platform's homePage, which is
    honest about being a local identity rather than pretending to be an email.
    """
    name = (user.get("display_name") or user.get("username") or "").strip()
    email = (user.get("email") or "").strip()
    if email:
        return {"objectType": "Agent", "name": name or email, "mbox": f"mailto:{email}"}
    username = (user.get("username") or "").strip()
    if username:
        return {
            "objectType": "Agent",
            "name": name or username,
            "account": {"homePage": activity_base(), "name": username},
        }
    return None


def _actor_key(actor: dict) -> str:
    if "mbox" in actor:
        return actor["mbox"]
    return f"{actor['account']['homePage']}#{actor['account']['name']}"


# ---------------------------------------------------------------------------
# Statement assembly
# ---------------------------------------------------------------------------

def _statement_id(activity_iri: str, actor: dict, verb_id: str, timestamp: str | None) -> str:
    return str(uuid.uuid5(_NS, f"{activity_iri}|{_actor_key(actor)}|{verb_id}|{timestamp or ''}"))


def _registration(actor: dict, mission_slug: str) -> str:
    """Groups every statement from one learner's run at one mission."""
    return str(uuid.uuid5(_NS, f"registration|{_actor_key(actor)}|{mission_slug}"))


def _activity(iri: str, name: str, activity_type: str, description: str | None = None) -> dict:
    definition = {"name": {"en-US": name}, "type": activity_type}
    if description:
        definition["description"] = {"en-US": description}
    return {"objectType": "Activity", "id": iri, "definition": definition}


def _provenance_ext(verified: bool, source: str, detail: dict | None = None) -> dict:
    """The claim this statement is making about its own evidence.

    Carried on the statement rather than only in the export envelope so that a
    statement forwarded on from the LRS still says whether the platform could
    verify it.
    """
    ext = {"verified": bool(verified), "source": source}
    if detail:
        ext.update(detail)
    return {f"{activity_base()}/extensions/provenance": ext}


def _mission_iri(slug: str) -> str:
    return f"{activity_base()}/mission/{slug}"


def _step_iri(slug: str, step_num: int) -> str:
    return f"{activity_base()}/mission/{slug}/step/{step_num}"


def _cert_iri(cert_tier: str) -> str:
    return f"{activity_base()}/certificate/{cert_tier}"


def _base_statement(actor: dict, verb_id: str, verb_display: str, obj: dict,
                    timestamp: str | None) -> dict:
    return {
        "id": _statement_id(obj["id"], actor, verb_id, timestamp),
        "actor": actor,
        "verb": {"id": verb_id, "display": {"en-US": verb_display}},
        "object": obj,
        "timestamp": timestamp,
        "version": XAPI_VERSION,
    }


def step_statement(actor: dict, mission: dict, step: dict, progress: dict,
                   provenance: dict | None) -> dict:
    """One verified step submission."""
    slug = mission["slug"]
    obj = _activity(
        _step_iri(slug, step.get("step_num") or 0),
        step.get("title") or f"Step {step.get('step_num')}",
        ACTIVITY_ASSESSMENT,
        f"{mission.get('title') or slug} — step {step.get('step_num')} "
        f"({step.get('step_type') or 'coding'})",
    )
    timestamp = _iso8601(progress.get("completed_at"))
    stmt = _base_statement(actor, VERB_PASSED, "passed", obj, timestamp)
    result = {"success": True, "completion": True}
    score = _score(progress.get("score"))
    if score:
        result["score"] = score
    duration = _duration(progress.get("started_at"), progress.get("completed_at"))
    if duration:
        result["duration"] = duration
    stmt["result"] = result
    stmt["context"] = {
        "registration": _registration(actor, slug),
        "contextActivities": {
            "parent": [_activity(_mission_iri(slug), mission.get("title") or slug,
                                 ACTIVITY_COURSE)],
            "grouping": [_activity(f"{activity_base()}/", "FORGE Academy", ACTIVITY_COURSE)],
        },
        "extensions": _provenance_ext(
            bool(provenance and provenance.get("verified")),
            "fa_xp_ledger",
            {
                "ledger_id": (provenance or {}).get("id"),
                "xp_delta": (provenance or {}).get("xp_delta"),
                "skill_tag": step.get("skill_tag"),
                "hints_used": progress.get("hints_used"),
            },
        ),
    }
    return stmt


def mission_statement(actor: dict, mission: dict, progress: dict,
                      provenance: dict | None) -> dict:
    """One completed mission."""
    slug = mission["slug"]
    obj = _activity(_mission_iri(slug), mission.get("title") or slug, ACTIVITY_COURSE,
                    mission.get("tagline") or None)
    timestamp = _iso8601(progress.get("completed_at"))
    stmt = _base_statement(actor, VERB_COMPLETED, "completed", obj, timestamp)
    result = {"success": True, "completion": True}
    score = _score(progress.get("score"))
    if score:
        result["score"] = score
    duration = _duration(progress.get("started_at"), progress.get("completed_at"))
    if duration:
        result["duration"] = duration
    stmt["result"] = result
    stmt["context"] = {
        "registration": _registration(actor, slug),
        "contextActivities": {
            "grouping": [_activity(f"{activity_base()}/", "FORGE Academy", ACTIVITY_COURSE)],
        },
        "extensions": _provenance_ext(
            bool(provenance and provenance.get("verified")),
            "fa_xp_ledger",
            {
                "ledger_id": (provenance or {}).get("id"),
                "xp_earned": progress.get("xp_earned"),
                "attempts": progress.get("attempts"),
                "tier": mission.get("tier"),
            },
        ),
    }
    return stmt


def certificate_statement(actor: dict, cert: dict, evidence_count: int) -> dict:
    """One issued certificate, carrying the count of evidence rows behind it.

    A certificate with zero rows in ``fa_certificate_evidence`` is an assertion
    with nothing behind it — migration 317 exists because that was the state
    before it. Such a certificate is unverified here and withheld by default.
    """
    obj = _activity(_cert_iri(cert.get("cert_tier") or "unknown"),
                    cert.get("cert_label") or cert.get("cert_tier") or "Certificate",
                    ACTIVITY_CERTIFICATE)
    timestamp = _iso8601(cert.get("issued_at"))
    stmt = _base_statement(actor, VERB_EARNED, "earned", obj, timestamp)
    stmt["result"] = {"success": True, "completion": True}
    stmt["context"] = {
        "extensions": _provenance_ext(
            evidence_count > 0,
            "fa_certificate_evidence",
            {
                "evidence_rows": evidence_count,
                "certificate_token": cert.get("token"),
                "expires_at": _iso8601(cert.get("expires_at")),
            },
        ),
    }
    return stmt


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def _fetch(conn, sql, params=()):
    try:
        return [_row(r) for r in conn.execute(sql, params).fetchall()]
    except Exception as exc:  # table absent on a partially-migrated deployment
        _log.warning("xapi export query failed (%s): %s", sql.split()[3:5], exc)
        return []


def _ledger_index(conn, source_type: str, reason: str) -> dict:
    """(user_id, source_id) → the ledger row backing that award.

    Newest row wins when an award was recorded more than once: a later
    compensating entry is the current statement of what happened.
    """
    rows = _fetch(
        conn,
        "SELECT id, user_id, source_id, xp_delta, verified FROM fa_xp_ledger "
        "WHERE reason=%s AND source_type=%s AND source_id IS NOT NULL ORDER BY id ASC",
        (reason, source_type),
    )
    index = {}
    for r in rows:
        index[(r["user_id"], r["source_id"])] = {
            "id": r["id"],
            "xp_delta": r["xp_delta"],
            "verified": bool(r["verified"]),
        }
    return index


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def build_statements(*, user_id: int | None = None, since: str | None = None,
                     include_unverified: bool = False,
                     tenant_id: str | None = None) -> dict:
    """Render Academy records as xAPI statements.

    Returns ``{"statements": [...], "excluded": {...}, "counts": {...},
    "generated_at": ..., "include_unverified": bool}``. ``excluded`` names what
    was withheld and why, so a caller never mistakes a filtered export for a
    complete one.
    """
    conn = get_connection()
    excluded = {"unverified_step": 0, "unverified_mission": 0,
                "unverified_certificate": 0, "unidentifiable_actor": 0,
                "missing_timestamp": 0}

    users = {u["id"]: u for u in _fetch(
        conn,
        "SELECT id, username, display_name, email, tenant_id FROM fa_users"
        + (" WHERE id=%s" if user_id else ""),
        (user_id,) if user_id else (),
    ) if tenant_id is None or u.get("tenant_id") == tenant_id}
    if not users:
        return {"statements": [], "excluded": excluded,
                "counts": {"step": 0, "mission": 0, "certificate": 0, "learners": 0},
                "generated_at": _iso8601(datetime.now(timezone.utc)),
                "include_unverified": include_unverified}

    actors = {}
    for uid, user in users.items():
        actor = build_actor(user)
        if actor is None:
            excluded["unidentifiable_actor"] += 1
            continue
        actors[uid] = actor

    since_iso = _iso8601(since) if since else None

    def _keep(timestamp: str | None, verified: bool, bucket: str) -> bool:
        if not timestamp:
            excluded["missing_timestamp"] += 1
            return False
        if since_iso and timestamp < since_iso:
            return False
        if not verified and not include_unverified:
            excluded[bucket] += 1
            return False
        return True

    statements: list[dict] = []
    counts = {"step": 0, "mission": 0, "certificate": 0}

    # ── Steps ────────────────────────────────────────────────────────────────
    step_ledger = _ledger_index(conn, "step", "step_pass")
    for row in _fetch(
        conn,
        "SELECT sp.user_id, sp.step_id, sp.status, sp.score, sp.hints_used, "
        "       sp.started_at, sp.completed_at, "
        "       s.step_num, s.title AS step_title, s.step_type, s.skill_tag, "
        "       m.slug, m.title AS mission_title, m.tier "
        "  FROM fa_step_progress sp "
        "  JOIN fa_mission_steps s ON s.id = sp.step_id "
        "  JOIN fa_missions m ON m.id = s.mission_id "
        " WHERE sp.status = 'completed' "
        " ORDER BY sp.completed_at ASC, sp.id ASC",
    ):
        actor = actors.get(row["user_id"])
        if actor is None:
            continue
        prov = step_ledger.get((row["user_id"], row["step_id"]))
        verified = bool(prov and prov.get("verified"))
        if not _keep(_iso8601(row["completed_at"]), verified, "unverified_step"):
            continue
        statements.append(step_statement(
            actor,
            {"slug": row["slug"], "title": row["mission_title"], "tier": row["tier"]},
            {"step_num": row["step_num"], "title": row["step_title"],
             "step_type": row["step_type"], "skill_tag": row["skill_tag"]},
            row, prov,
        ))
        counts["step"] += 1

    # ── Missions ─────────────────────────────────────────────────────────────
    mission_ledger = _ledger_index(conn, "mission", "mission_complete")
    for row in _fetch(
        conn,
        "SELECT mp.user_id, mp.mission_id, mp.score, mp.xp_earned, mp.attempts, "
        "       mp.started_at, mp.completed_at, "
        "       m.slug, m.title, m.tagline, m.tier "
        "  FROM fa_mission_progress mp "
        "  JOIN fa_missions m ON m.id = mp.mission_id "
        " WHERE mp.status = 'completed' "
        " ORDER BY mp.completed_at ASC, mp.id ASC",
    ):
        actor = actors.get(row["user_id"])
        if actor is None:
            continue
        prov = mission_ledger.get((row["user_id"], row["mission_id"]))
        verified = bool(prov and prov.get("verified"))
        if not _keep(_iso8601(row["completed_at"]), verified, "unverified_mission"):
            continue
        statements.append(mission_statement(
            actor,
            {"slug": row["slug"], "title": row["title"],
             "tagline": row["tagline"], "tier": row["tier"]},
            row, prov,
        ))
        counts["mission"] += 1

    # ── Certificates ─────────────────────────────────────────────────────────
    evidence = {}
    for row in _fetch(conn, "SELECT cert_id, COUNT(*) AS n FROM fa_certificate_evidence "
                            "GROUP BY cert_id"):
        evidence[row["cert_id"]] = int(row["n"] or 0)
    for row in _fetch(
        conn,
        "SELECT id, user_id, cert_tier, cert_label, token, issued_at, expires_at "
        "  FROM fa_certificates ORDER BY issued_at ASC, id ASC",
    ):
        actor = actors.get(row["user_id"])
        if actor is None:
            continue
        n = evidence.get(row["id"], 0)
        if not _keep(_iso8601(row["issued_at"]), n > 0, "unverified_certificate"):
            continue
        statements.append(certificate_statement(actor, row, n))
        counts["certificate"] += 1

    counts["learners"] = len({_actor_key(s["actor"]) for s in statements})
    return {
        "statements": statements,
        "excluded": excluded,
        "counts": counts,
        "generated_at": _iso8601(datetime.now(timezone.utc)),
        "include_unverified": include_unverified,
        "activity_base": activity_base(),
    }


# ---------------------------------------------------------------------------
# CLI — batch feed for an LRS/LMS that pulls on a schedule
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Export FORGE Academy completions as xAPI 1.0.3 statements."
    )
    ap.add_argument("--user-id", type=int, help="restrict to one learner")
    ap.add_argument("--since", help="only records completed at/after this ISO timestamp")
    ap.add_argument("--tenant-id", help="restrict to one tenant")
    ap.add_argument("--include-unverified", action="store_true",
                    help="also emit records with no verified provenance row "
                         "(each is stamped verified:false)")
    ap.add_argument("--statements-only", action="store_true",
                    help="emit the bare JSON array an LRS POST expects")
    ap.add_argument("--out", help="write to this file instead of stdout")
    ap.add_argument("--json", action="store_true", help="accepted for CLI consistency")
    args = ap.parse_args(argv)

    result = build_statements(
        user_id=args.user_id, since=args.since, tenant_id=args.tenant_id,
        include_unverified=args.include_unverified,
    )
    payload = result["statements"] if args.statements_only else result
    text = json.dumps(payload, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(json.dumps({"written": args.out, "counts": result["counts"],
                          "excluded": result["excluded"]}, default=str))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
