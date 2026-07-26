#!/usr/bin/env python3
# CUI // SP-CTI
"""Reproduce-or-drop for dynamic findings (oss-poc-01).

STRIX's discipline: **a finding ships with a working proof-of-concept or it is
not a finding.** Nothing in the tree did that. Severity came from static taxonomy
(bandit severity, CVSS lookup, STIG CAT) and gates fired on counts — so an
unreproducible dynamic claim could block a merge with exactly the same weight as
a demonstrated one.

The rule
--------
Every DYNAMIC finding must carry a stored, replayable reproduction. Without one
it is ``unconfirmed``: recordable, reportable as a lead, and **never** a
``finding``, never a gate block.

The part that is easy to get wrong
----------------------------------
A replay that merely *runs* proves nothing. The success criterion is that the
reproduction **discriminates**: it must confirm against the vulnerable state and
then FAIL once the fix is applied. A "reproduction" that passes both before and
after is not evidence of a vulnerability — it is evidence that the check is
insensitive to the thing it claims to detect.

This is the same failure this card kept hitting elsewhere. ``oss-meas-01``'s
RAPTOR verdict was measured on a golden set with no headroom, so it *could not*
have detected an improvement; the number was real and meaningless.
:func:`verify_discriminates` is the equivalent guard here, and
:meth:`Reproduction.confirm` refuses to promote a finding without it.

Statuses
--------
``unconfirmed``       recorded, no replay attempted or none stored — never gates
``confirmed``         replay reproduced it AND was shown to discriminate
``not_reproducible``  replay ran and did not reproduce — actively downgraded
``fixed``             previously confirmed; the discriminating replay now fails,
                      which is what "fixed" should mean rather than someone
                      closing a ticket
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.security.reproduction")

UNCONFIRMED = "unconfirmed"
CONFIRMED = "confirmed"
NOT_REPRODUCIBLE = "not_reproducible"
FIXED = "fixed"

#: Only a confirmed finding may block a gate. This is the whole point: an
#: unreproducible claim must not carry the same weight as a demonstrated one.
GATE_BLOCKING_STATUSES = frozenset({CONFIRMED})

STATUSES = frozenset({UNCONFIRMED, CONFIRMED, NOT_REPRODUCIBLE, FIXED})


@dataclass
class Reproduction:
    """A stored, replayable demonstration of a dynamic finding.

    ``kind`` is either ``http`` (a request/response pair) or ``agent_trace``
    (an action trace from the oss-browse-01 agent browser). Both are replayable;
    neither is a screenshot or a prose description, because those cannot be
    re-run to check whether a fix landed.
    """

    kind: str                                   # "http" | "agent_trace"
    steps: List[Dict[str, Any]] = field(default_factory=list)
    expectation: str = ""                       # what proves the finding present
    captured_at: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.captured_at:
            self.captured_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_replayable(self) -> bool:
        """A reproduction with no steps or no expectation cannot be replayed.

        Guarded explicitly because an empty record is the most likely way an
        unreproducible finding sneaks into ``confirmed``.
        """
        return bool(self.steps) and bool(self.expectation.strip())

    def fingerprint(self) -> str:
        """Stable id for dedup across runs."""
        payload = json.dumps(
            {"kind": self.kind, "steps": self.steps, "expectation": self.expectation},
            sort_keys=True, default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    """A dynamic finding and its evidence.

    Starts ``unconfirmed`` by construction. Nothing can set it to ``confirmed``
    except :meth:`confirm`, which requires a discriminating replay.
    """

    title: str
    detail: str = ""
    severity: str = "medium"
    target: str = ""
    reproduction: Optional[Reproduction] = None
    status: str = UNCONFIRMED
    discriminated: bool = False
    id: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"dyn-{uuid.uuid4().hex[:12]}"

    @property
    def blocks_gate(self) -> bool:
        """Only a confirmed finding may block. Unconfirmed leads never gate."""
        return self.status in GATE_BLOCKING_STATUSES

    def confirm(self, discriminated: bool) -> str:
        """Promote to ``confirmed`` — only with a discriminating reproduction.

        Refuses on a missing, unreplayable, or non-discriminating reproduction.
        The refusal is the feature: "the replay ran" is not evidence, and a
        check that cannot fail cannot confirm.
        """
        if self.reproduction is None or not self.reproduction.is_replayable:
            logger.info("finding %s stays unconfirmed: no replayable reproduction", self.id)
            self.status = UNCONFIRMED
            return self.status
        if not discriminated:
            logger.info(
                "finding %s stays unconfirmed: reproduction does not discriminate "
                "(it passes with the vulnerability absent, so it proves nothing)",
                self.id,
            )
            self.status = UNCONFIRMED
            self.discriminated = False
            return self.status
        self.status = CONFIRMED
        self.discriminated = True
        return self.status

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["blocks_gate"] = self.blocks_gate
        return out


def verify_discriminates(
    replay: Callable[[], bool],
    apply_fix: Callable[[], None],
    revert_fix: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """Prove a reproduction detects the vulnerability rather than merely running.

    Runs the replay against the vulnerable state, applies the fix, runs it again,
    and requires the result to FLIP. A replay that reports the finding present
    both before and after is insensitive to the thing it claims to detect, and
    confirming on it would be exactly the mistake ``oss-meas-01`` made: a real
    number from an instrument that could not have produced a different one.

    Args:
        replay: Runs the reproduction; True means "finding still present".
        apply_fix: Puts the system into the fixed state.
        revert_fix: Restores the vulnerable state. Best-effort; a failure here is
            reported but does not change the verdict.

    Returns:
        ``{discriminates, before, after, reason}``.
    """
    try:
        before = bool(replay())
    except Exception as exc:  # noqa: BLE001
        return {"discriminates": False, "before": None, "after": None,
                "reason": f"replay failed against the vulnerable state: {exc}"}

    if not before:
        return {"discriminates": False, "before": False, "after": None,
                "reason": "replay did not reproduce the finding at all"}

    try:
        apply_fix()
    except Exception as exc:  # noqa: BLE001
        return {"discriminates": False, "before": True, "after": None,
                "reason": f"could not apply the fix to test discrimination: {exc}"}

    try:
        after = bool(replay())
    except Exception:
        # A replay that ERRORS once the fix is in has still stopped reporting the
        # finding, which is the discrimination we are testing for.
        after = False
    finally:
        if revert_fix is not None:
            try:
                revert_fix()
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not revert the fix after discrimination check: %s", exc)

    discriminates = before and not after
    return {
        "discriminates": discriminates,
        "before": before,
        "after": after,
        "reason": "" if discriminates else (
            "the replay reports the finding present even with the fix applied — "
            "it is insensitive to the vulnerability and proves nothing"
        ),
    }


def triage(
    findings: List[Finding],
    replay_for: Optional[Callable[[Finding], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Apply reproduce-or-drop across a batch.

    ``replay_for`` returns a :func:`verify_discriminates` result per finding.
    Without it nothing can be confirmed — which is the correct default: a run
    that cannot replay produces leads, not findings.
    """
    for f in findings:
        if replay_for is None:
            f.status = UNCONFIRMED
            continue
        try:
            result = replay_for(f)
        except Exception as exc:  # noqa: BLE001
            logger.warning("replay harness failed for %s: %s", f.id, exc)
            f.status = UNCONFIRMED
            continue
        if result.get("before") is False and result.get("after") is None:
            f.status = NOT_REPRODUCIBLE
            continue
        f.confirm(bool(result.get("discriminates")))
        f.evidence["discrimination"] = result

    confirmed = [f for f in findings if f.status == CONFIRMED]
    return {
        "total": len(findings),
        "confirmed": len(confirmed),
        "unconfirmed": sum(1 for f in findings if f.status == UNCONFIRMED),
        "not_reproducible": sum(1 for f in findings if f.status == NOT_REPRODUCIBLE),
        "gate_blocking": [f.id for f in confirmed],
        "findings": [f.to_dict() for f in findings],
    }


def gate_blocking_findings(findings: List[Finding]) -> List[Finding]:
    """Only the findings entitled to block a gate."""
    return [f for f in findings if f.blocks_gate]
