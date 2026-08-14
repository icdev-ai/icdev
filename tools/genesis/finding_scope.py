# CUI // SP-CTI
"""Remedy scope for reflex findings — does ONE fix close all of these, or N?

THE BUG THIS EXISTS FOR
=======================
A reflex that walks N rows and files a card per row is correct exactly when each
row has its own thing to remediate. It is wrong when the finding is a defect in
ICDEV's OWN CODE, because then one change closes every instance at once and the
other N-1 cards are N-1 sessions dispatched onto a bug that is already fixed.

Measured 2026-08-14. `cpmp_monitor` pass 3 filed a `[SUBCON] … ISR/SSR` card per
contract: SEVEN cards for ONE code-level defect (`detect_noncompliance` check #4
had no FAR 19.702(a) applicability gate, so it was 7/7 false positives). Four
sessions then fixed the same bug independently — #1628 landed and #1629, #1633
and #1635 were closed as redundant. Two of those three had even created the same
test file path, so they conflicted with each other as well as with main.

The cost is not one wasted card. It is N branches, N PRs, and mutually
conflicting work a human has to adjudicate.

WHAT THIS MODULE IS
===================
The discriminator, and only the discriminator. It answers one question about a
batch of findings from one check:

    code-level -> ONE card, every affected row carried as EVIDENCE
    data-level -> ONE card per row (today's behaviour, and correct for data)

and hands back `CardSpec`s carrying a DETERMINISTIC dedup key. It writes nothing
and knows nothing about kanban; the calling reflex still owns the INSERT.

HOW IT DECIDES — declaration first, inference second
====================================================
1. The finding declares it (`Finding.declared_scope`). A check that knows its
   remedy is a code change says so. Deterministic, no inference, always wins.

2. `args/finding_scope.yaml` declares it per (source, category). This is the
   operator's lever: the moment a check is discovered to be defective, flipping
   one line collapses its next cycle from N cards to 1, with no code deploy.

3. Failing both, and ONLY for a source that opted in with
   `infer_code_scope: true`: a check that fired on 100% of the population it
   examined, with a BYTE-IDENTICAL remedy on every hit, is describing itself
   rather than the population. That is the shape the measured case had, and the
   shape nobody had declared, because nobody knew yet.

Rule 3 is deliberately narrow — full saturation, not a ratio, and a minimum
population — because it is the only rule that can be wrong. When it fires, the
card SAYS it fired and lists every affected row, so the worst case is one
session verifying an applicability gate instead of seven remediating rows. See
`_INFERRED_PREAMBLE`.

WHY NOT TITLE MATCHING
======================
Because it has already been tried in this codebase and it DROPS findings: cards
whose titles collide are treated as duplicates and all but the first are
discarded, which is how five contracts with noncompliant subcontractors showed
one card (PR #1504). Nothing here matches on a title. The identity of a card is
a deterministic key:

    data-level:  "<subject>:<category>:<instance>"   — one per row, as before
    code-level:  "<category>:__code_defect__"        — one per check, forever

The code-level key deliberately contains NO subject, so it is stable as the
affected population grows or shrinks: seven contracts today and five tomorrow
are the same defect and the same card. And because merging AGGREGATES rather
than discards — every affected row lands in the card's evidence and in its
context data — no finding is lost the way a title dedup loses them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

CODE = "code"
DATA = "data"


def _config_path() -> Path:
    """Locate args/finding_scope.yaml from EITHER copy of this module.

    `tools/` and `icdev/tools/` are two separate module objects, not a shim onto
    one file, and a `Path(__file__).parents[2] / "args"` here would resolve to
    `icdev/args/` in the mirror — a directory holding 19 of the repo's 309 args
    files, so the mirrored copy would silently fall back to built-in defaults
    and behave like an older version of itself. `get_data_path` is the seam that
    already resolves this for both, and for a pip install too.
    """
    try:
        from icdev._paths import get_data_path

        return get_data_path("args") / "finding_scope.yaml"
    except Exception:
        return BASE_DIR / "args" / "finding_scope.yaml"

# Fallbacks used when args/finding_scope.yaml is unreadable. A reflex runs inside
# the Genesis daemon and must not lose its findings because a config file moved,
# so the defaults reproduce TODAY's behaviour exactly: data-level, no inference.
_BUILTIN_DEFAULTS: Dict[str, Any] = {
    "scope": DATA,
    "infer_code_scope": False,
    "min_population": 3,
    "max_evidence_rows": 25,
}

_CODE_KEY_SUFFIX = "__code_defect__"

_INFERRED_PREAMBLE = (
    "NOTE — this card was AGGREGATED, not filed per row. The check fired on "
    "{hits}/{population} of the rows it examined with an identical remedy on "
    "every one, which is the signature of a check missing an applicability "
    "gate rather than {population} independent problems. Verify the check "
    "first; if the finding is genuine, every affected row is listed below."
)


@dataclass(frozen=True)
class Finding:
    """One hit from one check, ready to be scoped.

    subject
        The row the check EXAMINED (e.g. a contract id). Saturation is measured
        over distinct subjects, so this must be the scanned unit, not the
        remediable one.
    category
        The check that produced it. Findings are scoped one category at a time —
        a family of checks over the same population can have one broken member
        and three sound ones, which is exactly what happened.
    dedup_key
        The DATA-level key, supplied by the caller so an existing board keeps
        its existing cards. Ignored when the finding scopes code-level.
    signature
        Identity of the REMEDY: what a human would be told to do. Two findings
        share a signature only when the instruction is literally the same, so a
        finding that names its own subcontractor never merges with another's.
    evidence
        One line naming the affected row, for the aggregated card's body.
    payload
        The raw finding, carried into the card's context data unchanged.
    declared_scope
        CODE or DATA when the producing check knows. Wins over everything.
    """

    subject: str
    category: str
    dedup_key: str
    signature: str
    evidence: str
    payload: Dict[str, Any] = field(default_factory=dict)
    declared_scope: Optional[str] = None


@dataclass(frozen=True)
class CardSpec:
    """One card to file. `findings` is every row the card covers."""

    scope: str
    category: str
    dedup_key: str
    reason: str
    findings: Tuple[Finding, ...]

    @property
    def is_aggregated(self) -> bool:
        return self.scope == CODE


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read args/finding_scope.yaml. Never raises — see `_BUILTIN_DEFAULTS`."""
    target = Path(path) if path else _config_path()
    try:
        import yaml

        with open(target, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _setting(config: Dict[str, Any], source: str, key: str) -> Any:
    """Resolve one setting: per-source override, then defaults, then builtin.

    Every key documented in args/finding_scope.yaml is resolved through here, so
    a key in that file cannot become one nothing reads — the failure this
    platform ships most (see args/liveness_gate.yaml).
    """
    sources = config.get("sources") or {}
    per_source = sources.get(source) or {}
    if isinstance(per_source, dict) and key in per_source:
        return per_source[key]
    defaults = config.get("defaults") or {}
    if isinstance(defaults, dict) and key in defaults:
        return defaults[key]
    return _BUILTIN_DEFAULTS[key]


def _declared_category_scope(
    config: Dict[str, Any], source: str, category: str
) -> Optional[str]:
    """Scope declared for one (source, category) in the config file."""
    sources = config.get("sources") or {}
    per_source = sources.get(source) or {}
    categories = (per_source or {}).get("categories") or {}
    value = categories.get(category)
    return value if value in (CODE, DATA) else None


def classify(
    source: str,
    category: str,
    findings: Sequence[Finding],
    population: int,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Return ``(scope, reason)`` for one category's findings.

    ``population`` is how many rows the check EXAMINED, not how many it hit. A
    check that hit 7 of 7 and one that hit 7 of 700 are different findings, and
    only the first is indistinguishable from a missing applicability gate.
    """
    cfg = config if config is not None else load_config()

    declared = next(
        (f.declared_scope for f in findings if f.declared_scope in (CODE, DATA)),
        None,
    )
    if declared:
        return declared, "declared by the check"

    from_config = _declared_category_scope(cfg, source, category)
    if from_config:
        return from_config, f"declared in args/finding_scope.yaml ({source}.{category})"

    if not _as_bool(_setting(cfg, source, "infer_code_scope")):
        return DATA, "default (inference not enabled for this source)"

    min_population = _as_int(_setting(cfg, source, "min_population"), 3)
    hits = len({f.subject for f in findings})
    if population < min_population or hits < min_population:
        # One or two rows collapse to at most one card saved, and a tiny
        # population makes "fired on everything" meaningless.
        return DATA, f"population {population} below min_population {min_population}"

    if hits < population:
        # Asymmetry is the tell that the check HAS a gate and it is working:
        # it looked at `population` rows and deliberately passed on some.
        return DATA, f"fired on {hits}/{population} rows — not saturated"

    signatures = {f.signature for f in findings}
    if len(signatures) > 1:
        return DATA, f"{len(signatures)} distinct remedies — findings are not uniform"

    return CODE, f"fired on {hits}/{population} rows with one identical remedy"


def group(
    source: str,
    findings: Sequence[Finding],
    population: int,
    config: Optional[Dict[str, Any]] = None,
) -> List[CardSpec]:
    """Turn a whole scan pass's findings into the cards that should be filed.

    Call this ONCE per pass, after the row loop — scoping needs the whole
    population, so a reflex that files inside its loop cannot use it. Categories
    are scoped independently and returned in first-seen order, so a broken check
    collapsing to one card never affects its sound siblings.
    """
    cfg = config if config is not None else load_config()

    by_category: Dict[str, List[Finding]] = {}
    for finding in findings:
        by_category.setdefault(finding.category, []).append(finding)

    specs: List[CardSpec] = []
    for category, group_findings in by_category.items():
        scope, reason = classify(source, category, group_findings, population, cfg)
        if scope == CODE:
            specs.append(
                CardSpec(
                    scope=CODE,
                    category=category,
                    # No subject in the key: the same defect across a changing
                    # population is the same card, this cycle and every later one.
                    dedup_key=f"{category}:{_CODE_KEY_SUFFIX}",
                    reason=reason,
                    findings=tuple(group_findings),
                )
            )
        else:
            specs.extend(
                CardSpec(
                    scope=DATA,
                    category=category,
                    dedup_key=f.dedup_key,
                    reason=reason,
                    findings=(f,),
                )
                for f in group_findings
            )
    return specs


def evidence_block(
    spec: CardSpec,
    population: int,
    source: str = "",
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """The affected-row evidence for an aggregated card.

    Empty for a data-level spec — that card already names its own row.

    Rows beyond ``max_evidence_rows`` are NOT silently dropped: the count that
    did not fit is stated, and the caller carries the full list in the card's
    context data. A truncation nobody reports reads as "that was all of them".
    """
    if spec.scope != CODE:
        return ""

    cfg = config if config is not None else load_config()
    limit = _as_int(_setting(cfg, source, "max_evidence_rows"), 25)

    hits = len({f.subject for f in spec.findings})
    lines = [
        _INFERRED_PREAMBLE.format(hits=hits, population=population),
        "",
        f"Affected rows ({len(spec.findings)}):",
    ]
    shown = spec.findings[:limit]
    lines.extend(f"  - {f.evidence}" for f in shown)
    remaining = len(spec.findings) - len(shown)
    if remaining > 0:
        lines.append(
            f"  … and {remaining} more, listed in full in this card's context data."
        )
    return "\n".join(lines)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
