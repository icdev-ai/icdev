# CUI // SP-CTI — Twin Core canonical verdict + violation schema
"""Canonical verdict + violation schema shared across all canvas digital twins.

Sequoia-Combine **Pattern 4** (Proactive Violation Dashboard): every canvas
digital twin — network (NDC), pipeline (PDC), boundary/compliance (BDC),
security (SDC), data (DDC), observability (ODC), infrastructure (IDC),
mission — reports a *standardized* verdict and a list of *standardized*
violations, so a cross-canvas observer can aggregate them without knowing each
canvas's native shape.

This module is a **thin, additive wrapper**. It NEVER re-computes a verdict or
invents a severity. It normalizes whatever the underlying twin already produced
and preserves method / provenance (e.g. BDC's ``method='heuristic'`` labeling,
IDC's STIG ``CAT1/2/3`` scale). The eight working twins are not touched; adapters
translate their native output into this schema and carry ``method`` through.

The verdict vocabularies observed in the wild are three families, all mapped
here without loss:

* ``pass`` / ``warn`` / ``fail``            — NDC, PDC, SDC, DDC, ODC
* ``green`` / ``amber`` / ``red`` / ``unknown`` — BDC rating bands
* ``pass`` / ``fail`` gate binaries          — DDC quality_gate, IDC gates

Severity families normalized here:

* ``high`` (most heuristic twins), ``critical`` / ``high`` / ``medium`` (PDC, SDC)
* ``CAT1`` / ``CAT2`` / ``CAT3``             — IDC STIG scale
* ``high`` / ``moderate`` / ``low``          — BDC cATO control-family scale
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

# ── Canonical enums (Sequoia Pattern 4) ───────────────────────────────────────

#: Core gating verdicts. ``unknown`` is a fourth, non-gating sentinel meaning
#: "the twin had nothing to score" (preserves BDC honesty — never fabricates a
#: pass/fail when no evidence exists).
VERDICTS: tuple[str, ...] = ("pass", "warn", "fail")
UNKNOWN_VERDICT = "unknown"

SEVERITIES: tuple[str, ...] = ("blocker", "critical", "high", "medium", "low")

CATEGORIES: tuple[str, ...] = (
    "service_parity",
    "iam",
    "network",
    "compliance",
    "cost",
    "security",
)

#: Recognized deployment targets for ``target_csp`` (``None`` allowed = untargeted).
TARGET_CSPS: tuple[str, ...] = (
    "aws_govcloud",
    "azure_gov",
    "gcp",
    "oci",
    "ibm",
    "local",
)

#: How a twin snapshot's estate was OBSERVED (flx-twin-01).
#:
#: Deliberately the ``ni_devices.source`` vocabulary (rmf-disc-02), where
#: ``synthetic`` is spelled out as "NOT evidence of anything" and the inventory
#: feed excludes it BY NAME. The same rule holds one layer up: a twin that
#: freezes an EMULATOR's state has observed a disposable local container, not a
#: deployed estate, and a reader that cannot tell the two apart will rank an
#: emulator's S3 buckets alongside a real inventory.
#:
#: ``observed`` is the only value that asserts a real estate, and no adapter in
#: this tree writes it today — it exists so that ``emulated`` is a POSITIVE
#: statement rather than the absence of one.
SNAPSHOT_PROVENANCES: tuple[str, ...] = ("observed", "emulated", "synthetic", "unknown")

#: What a snapshot's provenance is when nothing said. ``unknown``, never
#: ``observed``: an unlabelled row is one whose provenance was never recorded,
#: and defaulting it to the strongest claim is how an emulated estate would come
#: to read as a measured one.
DEFAULT_SNAPSHOT_PROVENANCE = "unknown"

#: The provenance a twin over an emulator MUST write.
PROVENANCE_EMULATED = "emulated"

# Severity ordering for aggregation (lower index = worse).
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
# Verdict ordering for aggregation (lower index = worse); unknown is least severe.
_VERDICT_RANK = {"fail": 0, "warn": 1, "pass": 2, UNKNOWN_VERDICT: 3}

# ── Normalization tables ──────────────────────────────────────────────────────

_VERDICT_ALIASES = {
    "pass": "pass", "green": "pass", "ok": "pass", "success": "pass",
    "satisfied": "pass", "clean": "pass", "compliant": "pass",
    "warn": "warn", "warning": "warn", "amber": "warn", "yellow": "warn",
    "partial": "warn", "partially_satisfied": "warn", "degraded": "warn",
    "fail": "fail", "red": "fail", "error": "fail", "failed": "fail",
    "blocked": "fail", "non_compliant": "fail", "noncompliant": "fail",
    "unknown": UNKNOWN_VERDICT, "": UNKNOWN_VERDICT, "none": UNKNOWN_VERDICT,
    "not_assessed": UNKNOWN_VERDICT, "not_applicable": UNKNOWN_VERDICT,
}

_SEVERITY_ALIASES = {
    "blocker": "blocker", "block": "blocker",
    "deployment_blocker": "blocker", "deployment-blocker": "blocker",  # twx-fed-01 air-gap blockers
    "critical": "critical", "crit": "critical", "cat1": "critical", "cat_1": "critical",
    "high": "high", "cat2": "high", "cat_2": "high",
    "medium": "medium", "moderate": "medium", "med": "medium",
    "cat3": "medium", "cat_3": "medium", "warning": "medium", "warn": "medium",
    "low": "low", "info": "low", "informational": "low", "minor": "low",
}


def normalize_verdict(value: Any) -> str:
    """Map any twin's native verdict/rating into ``pass|warn|fail|unknown``.

    Recognizes the three verdict families (see module docstring). Unrecognized
    or empty values become ``unknown`` — never silently ``pass`` — so a twin that
    could not score something is honestly represented rather than green-washed.
    """
    key = str(value).strip().lower() if value is not None else ""
    return _VERDICT_ALIASES.get(key, UNKNOWN_VERDICT)


def normalize_severity(value: Any) -> str:
    """Map any twin's native severity into the canonical 5-level scale.

    Handles STIG ``CAT1/2/3`` (IDC), cATO ``high/moderate/low`` (BDC), and the
    bare ``high`` most heuristic twins emit. Unrecognized values default to
    ``medium`` (neither alarmist nor dismissive).
    """
    key = str(value).strip().lower() if value is not None else ""
    return _SEVERITY_ALIASES.get(key, "medium")


def normalize_csp(value: Any) -> str | None:
    """Normalize a CSP hint to a recognized ``target_csp`` value, else ``None``.

    Untargeted violations (most single-canvas findings) legitimately have no
    CSP; only cross-CSP / service-parity findings set one.
    """
    if value is None:
        return None
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "aws": "aws_govcloud", "aws_govcloud": "aws_govcloud", "govcloud": "aws_govcloud",
        "azure": "azure_gov", "azure_gov": "azure_gov", "azuregov": "azure_gov",
        "gcp": "gcp", "google": "gcp",
        "oci": "oci", "oracle": "oci",
        "ibm": "ibm",
        "local": "local", "on_prem": "local", "onprem": "local", "localstack": "local",
    }
    return aliases.get(key)


# ── Canonical violation factory ───────────────────────────────────────────────

def canonical_violation(
    severity: Any,
    category: str,
    recommendation: str,
    *,
    target_csp: Any = None,
    auto_fixable: bool = False,
    title: str | None = None,
    rule_id: str | None = None,
    source_canvas: str | None = None,
    method: str | None = None,
    detail: str | None = None,
) -> dict:
    """Build one Sequoia-Pattern-4 canonical violation dict.

    Required fields (per the pattern): ``severity``, ``category``,
    ``recommendation``, ``target_csp``, ``auto_fixable``. The wrapper *adds*
    provenance (``method``, ``source_canvas``) — it never obscures it.

    ``category`` is validated against :data:`CATEGORIES`; an unknown category is
    coerced to ``compliance`` (the safe catch-all) rather than dropped.
    """
    cat = category if category in CATEGORIES else "compliance"
    return {
        "severity": normalize_severity(severity),
        "category": cat,
        "target_csp": normalize_csp(target_csp),
        "recommendation": str(recommendation or ""),
        "auto_fixable": bool(auto_fixable),
        "title": title,
        "rule_id": rule_id,
        "source_canvas": source_canvas,
        "method": method,
        "detail": detail,
    }


def worst_severity(violations: Iterable[dict]) -> str | None:
    """Return the most severe canonical severity among ``violations`` (or None)."""
    best = None
    best_rank = len(SEVERITIES)
    for v in violations:
        sev = normalize_severity(v.get("severity"))
        rank = _SEVERITY_RANK.get(sev, len(SEVERITIES))
        if rank < best_rank:
            best_rank = rank
            best = sev
    return best


def worst_verdict(verdicts: Iterable[Any]) -> str:
    """Aggregate many verdicts into the single most-severe one.

    ``fail`` > ``warn`` > ``pass`` > ``unknown``. An all-``unknown`` set returns
    ``unknown``; an empty set returns ``pass`` (nothing wrong observed).
    """
    best = None
    best_rank = 99
    saw_any = False
    for value in verdicts:
        saw_any = True
        v = normalize_verdict(value)
        rank = _VERDICT_RANK.get(v, 99)
        if rank < best_rank:
            best_rank = rank
            best = v
    if not saw_any:
        return "pass"
    return best or UNKNOWN_VERDICT


def summarize_violations(violations: Iterable[dict]) -> dict:
    """Count violations by canonical severity. Returns ``{severity: count}`` for
    all five levels (zero-filled) plus ``total``."""
    counts = {s: 0 for s in SEVERITIES}
    total = 0
    for v in violations:
        counts[normalize_severity(v.get("severity"))] += 1
        total += 1
    counts["total"] = total
    return counts


def derive_verdict_from_violations(violations: Iterable[dict]) -> str:
    """Fallback verdict derivation for observers that only have a violation list.

    ``fail`` if any blocker/critical; ``warn`` if any high/medium; ``pass``
    otherwise. Adapters normally pass the twin's OWN verdict through instead —
    this is only for aggregation surfaces that never saw a native verdict.
    """
    seen_warn = False
    for v in violations:
        sev = normalize_severity(v.get("severity"))
        if sev in ("blocker", "critical"):
            return "fail"
        if sev in ("high", "medium"):
            seen_warn = True
    return "warn" if seen_warn else "pass"


def twin_verdict(
    canvas: str,
    target_id: str,
    verdict: Any,
    violations: list[dict] | None = None,
    *,
    method: str | None = None,
    snapshot_id: str | None = None,
    simulation_id: str | None = None,
    coverage_score: float | None = None,
    extra: dict | None = None,
) -> dict:
    """Wrap a twin's simulate result in the canonical envelope.

    Carries the twin's native (normalized) verdict AND a normalized violation
    list, with provenance (``method``) preserved and a severity roll-up attached.
    Never overrides the twin's verdict with the derived one.
    """
    # Always canonicalize: canonical_violation is idempotent over our fields and
    # fills provenance (method / source_canvas) from the envelope when an adapter
    # left it unset. This guarantees every emitted violation carries method.
    viols = [
        canonical_violation(
            v.get("severity"),
            v.get("category", "compliance"),
            v.get("recommendation") or v.get("detail") or v.get("title") or "",
            target_csp=v.get("target_csp"),
            auto_fixable=v.get("auto_fixable", False),
            title=v.get("title"),
            rule_id=v.get("rule_id") or v.get("id"),
            source_canvas=v.get("source_canvas") or canvas,
            method=v.get("method") or method,
            detail=v.get("detail"),
        )
        for v in (violations or [])
    ]
    payload = {
        "canvas": canvas,
        "target_id": target_id,
        "verdict": normalize_verdict(verdict),
        "method": method,
        "snapshot_id": snapshot_id,
        "simulation_id": simulation_id,
        "coverage_score": coverage_score,
        "violations": viols,
        "counts": summarize_violations(viols),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload["extra"] = extra
    return payload
