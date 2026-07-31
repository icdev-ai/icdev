# CUI // SP-CTI
"""Decide when the existing role catalog does not cover a problem's domain.

The problem
-----------
``problem_classifier`` maps a problem onto roles through ``_DOMAIN_ROLES``, a
hand-written table of ~15 domains. Every one of them is software-delivery
shaped — build, devops, monitoring, analytics, compliance, product management —
plus a documentation team for subject-matter work. Anything the table does not
recognise falls back to ``_FALLBACK_SLOTS``: an AI developer and a QA manager.

So a maritime insurance platform, an agricultural supply-chain tracker and a
rare-disease diagnostic all get the same two software roles. The engine is
excellent at *how software is built* and blind to *what the software is about*.

Extending the table would not fix that — it would just move the boundary, and
the next unlisted industry hits the same wall. There is no finite list of
industries to enumerate.

The approach
------------
Detect that the catalog has nothing for this problem, derive the domain **from
the problem text itself**, and let ``sme_registry.ensure_sme`` create an expert
for it. Nothing here names an industry, a role, or a vertical: the domain label
comes from the text via the same LLM normalisation ``persona_generator`` already
uses, and ``ensure_sme`` reuses a near-match from the existing catalog before
generating anything. Adding support for a new industry requires no code change.

Runs PRE-LAUNCH, in the suggestion builder — never inside
``ProblemClassifierLens.propose()``, which executes on the controller's
background thread where a synchronous generation would stall assembly with
nobody able to approve it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Below this classifier confidence the roster is a guess rather than a match.
#: Mirrors the threshold ``ProblemClassifierLens.propose()`` already uses to
#: decide it needs LLM help, so the two agree on what "weak" means.
WEAK_CONFIDENCE = 0.5

#: A problem shorter than this does not carry enough signal to justify creating
#: a specialist. Shares chat_trigger's implicit-trigger floor.
MIN_PROBLEM_CHARS = 200


@dataclass
class SmeGap:
    """A domain the catalog does not cover."""

    domain_description: str
    reason: str
    fallback_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain_description": self.domain_description,
            "reason": self.reason,
            "fallback_only": self.fallback_only,
        }


def _is_fallback_only(manifest: Any) -> bool:
    """True when the classifier produced only its generic fallback roster.

    Compared against the classifier's own ``_FALLBACK_SLOTS`` rather than a
    literal list here, so this keeps agreeing with it if that changes.
    """
    try:
        from icdev.tools.ace.problem_classifier import _FALLBACK_SLOTS

        fallback_ids = {s.role_id for s in _FALLBACK_SLOTS}
    except Exception:  # noqa: BLE001
        return False

    slots = getattr(manifest, "slots", None) or []
    if not slots:
        return True
    return {s.role_id for s in slots} == fallback_ids


#: Words that carry no subject matter — requirement phrasing, delivery verbs,
#: and ordinary English. Filtered out before asking whether any proposed role
#: knows the subject, because they are what a requirements document is MADE of
#: and would otherwise match every role in every industry.
#:
#: Note this is a list of *stopwords*, not of industries. It says nothing about
#: what domains exist, so it never needs extending when a new industry appears.
_NON_DOMAIN_TOKENS: frozenset[str] = frozenset({
    "the", "system", "shall", "must", "should", "will", "need", "needs", "acceptance",
    "criteria", "requirement", "requirements", "user", "story", "stories", "and", "for",
    "with", "that", "this", "from", "into", "every", "each", "all", "any", "per",
    "build", "building", "create", "design", "implement", "deploy", "integrate",
    "generate", "monitor", "capture", "track", "detect", "alert", "analyze", "analyse",
    "process", "correlate", "aggregate", "manage", "support", "provide", "ensure",
    "data", "service", "services", "platform", "application", "app", "tool", "engine",
    "api", "database", "report", "reports", "dashboard", "interface", "module",
    "when", "where", "which", "their", "there", "than", "then", "also", "only",
})


def _content_tokens(text: str) -> set[str]:
    """Subject-matter words in *text*, with requirement scaffolding removed."""
    import re

    tokens = {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 3}
    return tokens - _NON_DOMAIN_TOKENS


def _roster_knows_the_subject(problem_text: str, manifest: Any) -> bool:
    """True when some proposed role's own domain overlaps the problem's subject.

    Compares the problem's content words against each role's ``role_id``,
    ``display_name``, ``personality.domain`` and declared capabilities. Purely
    lexical — no LLM, no industry list, and it works for a domain nobody has
    thought of yet, because the evidence comes from both sides of the comparison
    rather than from a table.

    A false "does not know" is cheap: ``ensure_sme`` dedups against the whole
    catalog and will REUSE an existing role rather than create a duplicate. So
    the failure mode is one wasted lookup, never a spurious specialist.
    """
    subject = _content_tokens(problem_text)
    if not subject:
        return True  # nothing to be expert about

    try:
        from icdev.tools.ace.role_loader import RoleLoader

        catalog = {r.role_id: r for r in RoleLoader().list_roles()}
    except Exception:  # noqa: BLE001
        return True  # cannot tell — do not manufacture a gap

    for slot in getattr(manifest, "slots", None) or []:
        role = catalog.get(slot.role_id)
        if role is None:
            continue
        personality = getattr(role, "personality", None) or {}
        descriptors = " ".join(str(x) for x in [
            slot.role_id.replace("_", " "),
            getattr(role, "display_name", "") or "",
            personality.get("domain", "") if isinstance(personality, dict) else "",
            " ".join(personality.get("capabilities", []) or [])
            if isinstance(personality, dict) else "",
        ])
        if _content_tokens(descriptors) & subject:
            return True

    return False


def detect_sme_gap(
    problem_text: str,
    manifest: Any,
    *,
    max_confidence: float | None = None,
) -> SmeGap | None:
    """Return the domain needing a new SME, or None if the catalog suffices.

    Two independent triggers:

    * the classifier produced only its generic fallback roster, or
    * the roster contains nobody whose own domain relates to the subject.

    The second is the one that matters in practice. Classifier confidence is
    **not** used as the signal: a well-written requirements document scores ~0.80
    whatever industry it describes, because the confidence measures recognition
    of requirement *phrasing*. It is high and uninformative exactly when the
    subject matter is most unfamiliar, so a maritime insurer, an agricultural
    co-op and a veterinary practice all match ``requirements_engineer`` at 0.80
    and would never be seen as gaps.

    ``max_confidence`` is still accepted so a caller that has it can widen the
    net, but nothing depends on it.
    """
    text = (problem_text or "").strip()
    if len(text) < MIN_PROBLEM_CHARS:
        return None

    fallback_only = _is_fallback_only(manifest)
    no_subject_expert = not fallback_only and not _roster_knows_the_subject(text, manifest)
    weak = (
        max_confidence is not None
        and max_confidence < WEAK_CONFIDENCE
        and not (fallback_only or no_subject_expert)
    )

    if not (fallback_only or no_subject_expert or weak):
        return None

    if fallback_only:
        reason = "classifier produced only its generic fallback roster"
    elif no_subject_expert:
        reason = "proposed roles cover the delivery process but not the subject matter"
    else:
        reason = f"classifier confidence {max_confidence:.2f} below {WEAK_CONFIDENCE}"

    # The domain comes from the problem text. No industry list is consulted,
    # because there is no finite list of industries to consult.
    return SmeGap(domain_description=text, reason=reason, fallback_only=fallback_only)


def resolve_gap(gap: SmeGap, *, capability_bundle: str | None = None) -> dict[str, Any] | None:
    """Turn a gap into a usable role id, creating an SME only if needed.

    ``ensure_sme`` normalises the domain, reuses a sufficiently similar existing
    role, and generates both halves (persona + executable role) only when the
    domain is genuinely novel. Returns the ``SmeResult`` dict, or None on any
    failure — a gap that cannot be resolved must degrade to the classifier's
    roster, never block the launch.
    """
    try:
        from icdev.tools.ace.sme_registry import ensure_sme

        return ensure_sme(
            gap.domain_description, capability_bundle=capability_bundle
        ).to_dict()
    except PermissionError as exc:
        # The generated role violated capability policy and was not written.
        logger.warning("sme_gap_detector: generated role refused by policy: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("sme_gap_detector: could not resolve gap: %s", exc)
        return None
