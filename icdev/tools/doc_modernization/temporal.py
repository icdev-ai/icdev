# CUI // SP-CTI
"""Temporal validity for standards references (dmx-packs-03).

A supersession finding is REACTIVE: it fires only once a successor standard has
been announced and mapped. Temporal validity is PROACTIVE: a rulebook entry may
carry ISO-8601 date fields and the evaluator flags a document that cites the
standard as its sunset approaches or passes — independent of any supersession
map.

Rule date fields (all OPTIONAL — a rule without them behaves exactly as before)::

    effective_date: 2013-04-30   # standard not in force before this date
    sunset_date:    2021-09-23   # withdrawn / no longer valid on/after this date
    review_by:      2026-01-01   # internal cadence to re-check the citation

Deterministic (TRUST rule 1): the verdict is a pure function of the rule dates
and a clock. No LLM. Every datetime is timezone-aware UTC — never naive.

Finding-id / dedupe scheme
--------------------------
The scanner's stable dedupe key is
``sha256(doc_id | pack_id | entity_label | finding_type)`` and drift_bridge
dedups on the resulting ``finding_id``. Temporal PHASE is encoded in
``finding_type`` so the key is phase-aware::

    within window of sunset  -> finding_type "expiring_reference", severity medium
    on/after sunset          -> finding_type "stale_reference",    severity high

Consequences:

  * A re-sweep at the same clock yields the same finding_type -> the same dedupe
    key -> the scanner's ``existing_open`` guard skips it (no duplicate; the
    finding_id and the ``docmod:<finding_id>`` drift key stay stable).
  * When a within-window warning crosses its sunset_date the finding_type flips
    ``expiring_reference`` -> ``stale_reference``: the old key stops being
    produced (the scanner appends an append-only ``superseded`` resolve row) and
    the new HIGH key is inserted with a fresh finding_id — exactly one open
    finding, now HIGH, never a duplicate.

Temporal findings are also independent of the supersession finding
(``superseded_standard``), so a standard that is both superseded and past sunset
yields two distinct, non-colliding findings.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from .base_pack import CandidateEntity, Verdict

# Default proactive warning window if args/docmod/docmod_config.yaml omits one.
DEFAULT_WINDOW_DAYS = 90

# Marker placed in a CandidateEntity's attributes so evaluate() routes it here.
TEMPORAL_KIND = "temporal"

# Phase -> (currency_verdict, finding_type, severity).
_PHASE_PAST = ("retired", "stale_reference", "high")       # on/after sunset
_PHASE_WITHIN = ("deprecated", "expiring_reference", "medium")  # within window


def utcnow() -> datetime:
    """Timezone-aware current time. The single clock source for this module."""
    return datetime.now(timezone.utc)


def _parse_iso_date(raw) -> datetime | None:
    """Parse an ISO-8601 date/datetime to a tz-aware UTC datetime, else None."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if len(s) == 10:  # 'YYYY-MM-DD'
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def has_temporal_dates(rule: dict) -> bool:
    """True when a rule carries any temporal field worth tracking."""
    return bool(
        (rule or {}).get("sunset_date")
        or (rule or {}).get("effective_date")
        or (rule or {}).get("review_by")
    )


def window_days(config: dict | None) -> int:
    """Resolve the proactive warning window from engine config, with a default."""
    try:
        n = int((config or {}).get("sunset_warning_window_days", DEFAULT_WINDOW_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS
    return n if n > 0 else DEFAULT_WINDOW_DAYS


def temporal_verdict(
    rule: dict,
    now: datetime | None = None,
    window: int = DEFAULT_WINDOW_DAYS,
) -> Verdict | None:
    """Deterministic time-bounded verdict for a rule, or None for no finding.

    Returns None (no temporal finding) when:
      * the rule has no ``sunset_date``, or
      * ``now`` is before the rule's ``effective_date`` (not yet in force), or
      * ``sunset_date`` is further than ``window`` days in the future.
    """
    now = now or utcnow()
    if now.tzinfo is None:  # defence — never compare naive datetimes
        now = now.replace(tzinfo=timezone.utc)

    effective = _parse_iso_date((rule or {}).get("effective_date"))
    if effective is not None and now < effective:
        return None  # standard not yet in force -> nothing to sunset

    sunset = _parse_iso_date((rule or {}).get("sunset_date"))
    if sunset is None:
        return None  # no sunset date -> behave exactly as today

    rid = (rule or {}).get("id", "?")
    review_by = (rule or {}).get("review_by")
    days_delta = (sunset - now).days  # negative once past sunset

    if now >= sunset:
        verdict, ftype, sev = _PHASE_PAST
        rationale = (
            f"Cited standard is past its sunset date {sunset.date().isoformat()} "
            f"({abs(days_delta)} day(s) ago); no longer valid for new work."
        )
    elif days_delta <= window:
        verdict, ftype, sev = _PHASE_WITHIN
        rationale = (
            f"Cited standard sunsets {sunset.date().isoformat()} "
            f"({days_delta} day(s) away, within the {window}-day warning window)."
        )
    else:
        return None  # sunset far off -> proactive window not open yet

    detail = rationale
    if review_by:
        detail = f"{rationale} Internal review_by {review_by}."
    return Verdict(
        currency_verdict=verdict,
        finding_type=ftype,
        severity=sev,
        rationale=rationale,
        confidence=1.0,
        evidence=[{
            "source": f"rule:{rid}",
            "detail": detail,
            "date": sunset.date().isoformat(),
        }],
    )


def temporal_entities(rules, text, chunk_ref, *, pack_id: str, entity_type: str):
    """Emit one temporal ``CandidateEntity`` per dated-rule match in ``text``.

    Mirrors a pack extractor but tags ``attributes['kind'] = 'temporal'`` and a
    disjoint ``seen`` namespace, so a temporal entity never collides with the
    pack's primary (supersession / deprecation) entity for the same match.
    Rules without date fields are skipped, so a dateless pack emits nothing and
    behaves exactly as before.
    """
    out: list[CandidateEntity] = []
    seen: set[str] = set()
    for rule in rules:
        if not has_temporal_dates(rule):
            continue
        compiled = rule.get("compiled")
        if compiled is None:
            continue
        for m in compiled.finditer(text or ""):
            label = (m.group(0) or "").strip()
            if not label:
                continue
            key = f"temporal|{rule['id']}|{label.lower()}"
            if key in seen:
                continue
            seen.add(key)
            start = max(0, m.start() - 60)
            out.append(CandidateEntity(
                label=label,
                entity_type=entity_type,
                pack_id=pack_id,
                chunk_ref=chunk_ref,
                raw_match=label,
                context=(text or "")[start:m.end() + 60].strip(),
                attributes={"rule_id": rule["id"], "kind": TEMPORAL_KIND},
            ))
    return out


def evaluate_temporal(entity, rules, now, config) -> Verdict | None:
    """Route a temporal entity to its verdict; return None if it is NOT temporal.

    A non-None result is authoritative: either a temporal finding, or a
    non-finding ``Verdict`` (``current``) when the entity's rule is not in any
    temporal window. ``None`` means "not a temporal entity — evaluate normally".
    """
    if (entity.attributes or {}).get("kind") != TEMPORAL_KIND:
        return None
    rid = (entity.attributes or {}).get("rule_id")
    rule = next((r for r in rules if r.get("id") == rid), None)
    if rule is None:  # rulebook changed between extract and evaluate
        return Verdict(currency_verdict="unknown")
    v = temporal_verdict(rule, now, window_days(config))
    return v if v is not None else Verdict(currency_verdict="current")
