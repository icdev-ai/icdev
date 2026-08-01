# CUI // SP-CTI
"""Reproduce-or-drop rule for DYNAMIC security findings (oss-poc-01).

Adapted from STRIX's single load-bearing discipline: *a finding ships with a
working proof-of-concept or it is not a finding*. Concept adopted, independent
implementation, no runtime dependency on STRIX (see ``_ATTRIBUTION_REGISTRY``
in ``tools/agent_toolkit/__init__.py`` for the wording precedent).

The rule
--------
Every DYNAMIC finding — one asserted about a *running* system rather than about
source text — must carry a stored, replayable reproduction: an HTTP
request/response sequence, or an agent action trace. A finding whose
reproduction does not replay is ``unconfirmed``. An ``unconfirmed`` finding is
never promoted to ``confirmed`` and **never blocks a gate**.

This is deliberately narrow. STATIC findings (bandit severity, CVSS lookup,
STIG CAT) are out of scope and pass through untouched — they are claims about
code, not about a live system, and this module has nothing to say about them.

Why a replay and not a second opinion
-------------------------------------
The existing verification machinery grades *text*: the ``adversarial_verifier``
ACE role re-reads an implementation, ``run_agent_loop_with_rubric`` has an LLM
judge a final response. Neither can distinguish "this endpoint fails open to
admin" from "this endpoint looks like it might fail open to admin", because
both are the same sentence. A replay can: it makes the request and reads the
status code.

So the primary decision here is deterministic and LLM-free. The reuse of
``run_agent_loop_with_rubric`` is at the point where it actually helps — see
:func:`make_reproduction_grader`, which supplies the loop's *pluggable code
grader* so an agent claiming a finding is graded by whether its reproduction
fires, not by whether its prose is convincing.

Discrimination is the real bar
------------------------------
A reproduction that runs proves nothing; a reproduction that *discriminates*
proves everything. ``status_in: [200, 403]`` "reproduces" against a vulnerable
target and against a fixed one alike. Two guards:

* :func:`validate_predicate` rejects statically-trivial predicates (too many
  accepted status codes, empty substring, a regex that matches the empty
  string, a bare negative assertion).
* :func:`verify_discrimination` is the empirical proof required by this task:
  the same reproduction must fire against the vulnerable target and must
  **stop** firing once the fix is applied. Only then is ``discriminating`` set.

Scope lock
----------
Replay makes real requests, so the target host must be on the
``target_allowlist`` in ``args/reproduction_policy.yaml`` (default-deny; only
loopback out of the box). A non-allowlisted host is ``refused``, not attempted.

Storage
-------
``dynamic_findings`` (mutable status) + ``finding_replay_attempts``
(append-only evidence trail), migration 303. Persistence is best-effort: an
un-migrated checkout degrades to in-memory classification rather than raising,
because the *rule* must hold even where the tables do not exist yet.

Usage:
    python tools/security/reproduction_validator.py --validate repro.json
    python tools/security/reproduction_validator.py --replay repro.json --json
    python tools/security/reproduction_validator.py --enforce findings.json --gate
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.security.reproduction_validator")


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
class FindingStatus:
    """Lifecycle of a dynamic finding under the reproduce-or-drop rule."""

    unconfirmed = "unconfirmed"
    """No reproduction, or the reproduction did not replay. Reportable as a
    lead; never a gate block."""

    confirmed = "confirmed"
    """A stored reproduction replayed and its vulnerability predicate fired."""

    remediated = "remediated"
    """A previously-confirmed reproduction no longer fires — the fix landed."""


class ReplayOutcome:
    """Result of executing one reproduction."""

    reproduced = "reproduced"
    """Every step ran and the vulnerability predicate evaluated true."""

    not_reproduced = "not_reproduced"
    """Every step ran and the predicate evaluated false. This is the outcome
    that makes a reproduction *useful* — it is what a fix looks like."""

    unavailable = "unavailable"
    """The replay engine for this reproduction kind is not installed (e.g. an
    agent action trace with no registered trace replayer). Explicitly NOT
    ``not_reproduced``: we learned nothing, so the finding stays unconfirmed."""

    error = "error"
    """The reproduction is malformed, or transport failed."""

    refused = "refused"
    """The target host is not on the allowlist. Nothing was sent."""


#: Outcomes that are *evidence of absence* rather than absence of evidence.
_DECISIVE = frozenset({ReplayOutcome.reproduced, ReplayOutcome.not_reproduced})

#: Predicate types understood by :func:`evaluate_predicate`.
PREDICATE_TYPES = frozenset(
    {
        "status_in",
        "body_contains",
        "body_not_contains",
        "body_regex",
        "header_equals",
        "all_of",
    }
)

#: Predicates that only ever *deny* something. A reproduction asserting solely
#: that a marker is absent fires against any error page, so one of these may
#: never stand alone — it must be conjoined with a positive assertion.
_NEGATIVE_ONLY = frozenset({"body_not_contains"})

REPRODUCTION_KINDS = frozenset({"http", "agent_trace"})

DEFAULT_POLICY: dict[str, Any] = {
    "target_allowlist": ["localhost", "127.0.0.1", "::1"],
    "replay": {
        "timeout_seconds": 10,
        "max_steps": 8,
        "max_body_bytes": 65536,
        "allow_redirects": False,
    },
    "predicate": {"max_status_codes": 8},
    "gate": {"blocking_severities": ["critical", "high"], "block_on_unconfirmed": False},
}


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
def _resolve_policy_path() -> Optional[Path]:
    """Find ``args/reproduction_policy.yaml`` from either package tree.

    This module is mirrored to ``icdev/tools/security/``, where
    ``parents[2]`` is ``icdev/`` and has no ``args/``. Walk up instead of
    assuming a fixed depth (cf. ``resolve_llm_config_path``).
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "args" / "reproduction_policy.yaml"
        if candidate.exists():
            return candidate
    return None


_POLICY_CACHE: Optional[dict[str, Any]] = None


def load_policy(refresh: bool = False) -> dict[str, Any]:
    """Load ``args/reproduction_policy.yaml``, falling back to DEFAULT_POLICY.

    A missing or unreadable policy file must not disable the rule, so the
    fallback is the same default-deny posture the shipped file encodes.
    """
    global _POLICY_CACHE
    if _POLICY_CACHE is not None and not refresh:
        return _POLICY_CACHE

    policy = json.loads(json.dumps(DEFAULT_POLICY))  # deep copy of literals
    path = _resolve_policy_path()
    if path is not None:
        try:
            import yaml

            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for key, value in loaded.items():
                if isinstance(value, dict) and isinstance(policy.get(key), dict):
                    policy[key].update(value)
                else:
                    policy[key] = value
        except Exception as exc:  # noqa: BLE001 — policy load never blocks the rule
            logger.warning("reproduction policy load failed, using defaults: %s", exc)

    _POLICY_CACHE = policy
    return policy


def _allowed_hosts() -> set[str]:
    """Allowlisted replay targets: policy file plus optional env override."""
    hosts ={str(h).strip().lower() for h in load_policy().get("target_allowlist", []) if str(h).strip()}
    env = os.environ.get("ICDEV_REPRO_TARGET_ALLOWLIST", "")
    hosts |= {h.strip().lower() for h in env.split(",") if h.strip()}
    return hosts


def is_target_allowed(url: str) -> bool:
    """True when *url*'s host is on the allowlist. Default-deny."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001 — an unparseable URL is not allowlisted
        return False
    return bool(host) and host in _allowed_hosts()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Reproduction model
# ---------------------------------------------------------------------------
@dataclass
class Reproduction:
    """A stored, replayable proof that a dynamic finding is real.

    Attributes:
        kind: ``http`` (a request/response sequence) or ``agent_trace`` (an
            ordered action trace from the agent browser primitive).
        target: Base URL replayed against. Must be allowlisted.
        steps: Ordered requests/actions. Multi-step exists for session setup
            (authenticate, then access) — the predicate is evaluated against
            the FINAL step's response only.
        predicate: The *vulnerability* predicate. True means the vulnerable
            behaviour was observed. This is the whole contract: it must be
            false once the bug is fixed.
        description: Free text for the audit trail.
    """

    kind: str = "http"
    target: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    predicate: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "steps": self.steps,
            "predicate": self.predicate,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Reproduction":
        return cls(
            kind=str(data.get("kind", "http")),
            target=str(data.get("target", "")),
            steps=list(data.get("steps") or []),
            predicate=dict(data.get("predicate") or {}),
            description=str(data.get("description", "")),
        )


@dataclass
class ReplayResult:
    """One execution of a reproduction."""

    outcome: str = ReplayOutcome.error
    detail: str = ""
    observations: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def reproduced(self) -> bool:
        return self.outcome == ReplayOutcome.reproduced

    @property
    def decisive(self) -> bool:
        """True when the replay actually told us something either way."""
        return self.outcome in _DECISIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "detail": self.detail,
            "observations": self.observations,
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# Predicate validation — the static half of "must discriminate"
# ---------------------------------------------------------------------------
def validate_predicate(predicate: Any, _depth: int = 0) -> list[str]:
    """Return a list of reasons *predicate* is unusable. Empty means usable.

    Rejects predicates that would fire against any target. These are cheap
    checks that catch the common ways an author accidentally writes a
    tautology; :func:`verify_discrimination` is the expensive, real proof.
    """
    errors: list[str] = []
    if not isinstance(predicate, dict) or not predicate:
        return ["predicate must be a non-empty object"]
    if _depth > 3:
        return ["predicate nesting too deep (max 3)"]

    ptype = str(predicate.get("type", "")).strip()
    if ptype not in PREDICATE_TYPES:
        return [f"unknown predicate type {ptype!r} (expected one of {sorted(PREDICATE_TYPES)})"]

    if _depth == 0 and ptype in _NEGATIVE_ONLY:
        errors.append(
            f"{ptype!r} cannot stand alone — a purely negative assertion fires against "
            "any error page; conjoin it with a positive predicate inside 'all_of'"
        )

    if ptype == "status_in":
        codes = predicate.get("codes")
        if not isinstance(codes, list) or not codes:
            errors.append("status_in requires a non-empty 'codes' list")
        else:
            limit = int(load_policy().get("predicate", {}).get("max_status_codes", 8))
            if len(codes) >= limit:
                errors.append(
                    f"status_in lists {len(codes)} codes (limit {limit}) — too permissive to discriminate"
                )
            if any(not isinstance(c, int) for c in codes):
                errors.append("status_in codes must be integers")

    elif ptype in ("body_contains", "body_not_contains"):
        needle = predicate.get("value")
        if not isinstance(needle, str) or not needle.strip():
            errors.append(f"{ptype} requires a non-empty 'value'")

    elif ptype == "body_regex":
        pattern = predicate.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            errors.append("body_regex requires a non-empty 'pattern'")
        else:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                errors.append(f"body_regex pattern does not compile: {exc}")
            else:
                if compiled.search(""):
                    errors.append("body_regex matches the empty string — it cannot discriminate")

    elif ptype == "header_equals":
        if not str(predicate.get("name", "")).strip():
            errors.append("header_equals requires a 'name'")
        if not isinstance(predicate.get("value"), str):
            errors.append("header_equals requires a string 'value'")

    elif ptype == "all_of":
        clauses = predicate.get("clauses")
        if not isinstance(clauses, list) or not clauses:
            errors.append("all_of requires a non-empty 'clauses' list")
        else:
            if all(
                isinstance(c, dict) and str(c.get("type")) in _NEGATIVE_ONLY for c in clauses
            ):
                errors.append("all_of contains only negative clauses — it cannot discriminate")
            for clause in clauses:
                errors.extend(validate_predicate(clause, _depth + 1))

    return errors


def validate_reproduction(repro: Reproduction, *, effective_target: Optional[str] = None) -> list[str]:
    """Return a list of reasons *repro* cannot be replayed. Empty means valid.

    Args:
        repro: The reproduction to check.
        effective_target: Base URL the caller intends to replay against, when
            it overrides ``repro.target`` (see :func:`verify_discrimination`).
    """
    errors: list[str] = []
    target = effective_target if effective_target is not None else repro.target
    if repro.kind not in REPRODUCTION_KINDS:
        errors.append(f"unknown reproduction kind {repro.kind!r} (expected one of {sorted(REPRODUCTION_KINDS)})")
    if not repro.steps:
        errors.append("reproduction has no steps — there is nothing to replay")
    max_steps = int(load_policy().get("replay", {}).get("max_steps", 8))
    if len(repro.steps) > max_steps:
        errors.append(f"reproduction has {len(repro.steps)} steps (limit {max_steps})")
    if repro.kind == "http":
        if not target:
            errors.append("http reproduction requires a 'target' base URL")
        for i, step in enumerate(repro.steps):
            if not isinstance(step, dict):
                errors.append(f"step {i} is not an object")
                continue
            if not str(step.get("path", "")).strip():
                errors.append(f"step {i} requires a 'path'")
    errors.extend(validate_predicate(repro.predicate))
    return errors


# ---------------------------------------------------------------------------
# Predicate evaluation
# ---------------------------------------------------------------------------
def evaluate_predicate(predicate: dict[str, Any], observation: dict[str, Any]) -> bool:
    """Evaluate a vulnerability predicate against one step observation.

    *observation* carries ``status`` (int), ``body`` (str, already truncated)
    and ``headers`` (dict). Returns True when the vulnerable behaviour was
    observed. Unknown predicate types return False — an unevaluable predicate
    must never confirm a finding.
    """
    ptype = str(predicate.get("type", ""))
    body = observation.get("body") or ""
    headers = {str(k).lower(): str(v) for k, v in (observation.get("headers") or {}).items()}

    if ptype == "status_in":
        return observation.get("status") in set(predicate.get("codes") or [])
    if ptype == "body_contains":
        return str(predicate.get("value", "")) in body
    if ptype == "body_not_contains":
        return str(predicate.get("value", "")) not in body
    if ptype == "body_regex":
        try:
            return re.search(str(predicate.get("pattern", "")), body) is not None
        except re.error:
            return False
    if ptype == "header_equals":
        return headers.get(str(predicate.get("name", "")).lower()) == str(predicate.get("value", ""))
    if ptype == "all_of":
        clauses = predicate.get("clauses") or []
        return bool(clauses) and all(evaluate_predicate(c, observation) for c in clauses)
    return False


# ---------------------------------------------------------------------------
# Replay engines
# ---------------------------------------------------------------------------
#: Pluggable replayer for ``agent_trace`` reproductions. The agent browser
#: primitive (oss-browse-01) registers itself here when it lands. Until then a
#: trace-backed finding replays as ``unavailable`` and therefore stays
#: ``unconfirmed`` — which is the correct answer, not a stub.
_TRACE_REPLAYER: Optional[Callable[[Reproduction], ReplayResult]] = None


def register_trace_replayer(replayer: Callable[[Reproduction], ReplayResult]) -> None:
    """Install the agent-action-trace replay engine."""
    global _TRACE_REPLAYER
    _TRACE_REPLAYER = replayer


def _truncate(body: str, limit: int) -> str:
    return body if len(body) <= limit else body[:limit]


def _replay_http(repro: Reproduction, target: str) -> ReplayResult:
    """Execute an HTTP reproduction against *target* and evaluate the predicate."""
    from tools.http.client import get_session

    policy = load_policy().get("replay", {})
    timeout = float(policy.get("timeout_seconds", 10))
    max_body = int(policy.get("max_body_bytes", 65536))
    default_redirects = bool(policy.get("allow_redirects", False))

    observations: list[dict[str, Any]] = []
    started = time.monotonic()
    session = get_session()
    # A reproduction must be exactly-once: the shared session retries 5xx/429
    # on idempotent methods, which would silently change what was observed.
    from requests.adapters import HTTPAdapter

    session.mount("http://", HTTPAdapter(max_retries=0))
    session.mount("https://", HTTPAdapter(max_retries=0))
    # The shared client applies operator-configured proxies to every scheme.
    # A loopback replay must not be routed through one — the proxy would answer
    # instead of the target and the "observation" would be about the proxy.
    if (urlparse(target).hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}:
        session.proxies.clear()
        session.trust_env = False

    try:
        for index, step in enumerate(repro.steps):
            method = str(step.get("method", "GET")).upper()
            url = target.rstrip("/") + "/" + str(step.get("path", "")).lstrip("/")
            response = session.request(
                method,
                url,
                headers=step.get("headers") or None,
                params=step.get("params") or None,
                json=step.get("json") if step.get("json") is not None else None,
                data=step.get("data") if step.get("data") is not None else None,
                timeout=timeout,
                allow_redirects=bool(step.get("allow_redirects", default_redirects)),
            )
            body = _truncate(response.text or "", max_body)
            observations.append(
                {
                    "step": index,
                    "method": method,
                    "path": str(step.get("path", "")),
                    "status": response.status_code,
                    "body": body,
                    "body_len": len(response.text or ""),
                    # The hash, not the payload: a reproduction needs the
                    # observation to be comparable, not the dump retained.
                    "body_sha256": hashlib.sha256((response.text or "").encode("utf-8")).hexdigest(),
                    "headers": dict(response.headers),
                }
            )
    except Exception as exc:  # noqa: BLE001 — transport failure is an error, not a finding
        return ReplayResult(
            outcome=ReplayOutcome.error,
            detail=f"transport failure on step {len(observations)}: {exc}",
            observations=_redact(observations),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        session.close()

    fired = evaluate_predicate(repro.predicate, observations[-1])
    return ReplayResult(
        outcome=ReplayOutcome.reproduced if fired else ReplayOutcome.not_reproduced,
        detail=(
            "vulnerability predicate fired on the final step"
            if fired
            else "vulnerability predicate did not fire on the final step"
        ),
        observations=_redact(observations),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _redact(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip response bodies from observations before they leave the replay.

    ICDEV is a public repo and these rows are read by dashboards and PR-adjacent
    tooling. The status, length and hash are what make a replay comparable; the
    body itself is not evidence, it is exposure.
    """
    return [{k: v for k, v in obs.items() if k != "body"} for obs in observations]


def replay(repro: Reproduction, *, target: Optional[str] = None) -> ReplayResult:
    """Execute *repro* once and report whether the vulnerability reproduced.

    Args:
        repro: The stored reproduction.
        target: Optional base-URL override — used by
            :func:`verify_discrimination` to run the same reproduction against
            a vulnerable and a fixed instance.
    """
    # Validate against the EFFECTIVE target: verify_discrimination supplies the
    # base URL per run, so a stored reproduction may legitimately carry none.
    effective = target or repro.target
    errors = validate_reproduction(repro, effective_target=effective)
    if errors:
        return ReplayResult(outcome=ReplayOutcome.error, detail="; ".join(errors))

    if repro.kind == "agent_trace":
        if _TRACE_REPLAYER is None:
            return ReplayResult(
                outcome=ReplayOutcome.unavailable,
                detail="no agent action-trace replayer registered (oss-browse-01 not installed)",
            )
        try:
            return _TRACE_REPLAYER(repro)
        except Exception as exc:  # noqa: BLE001
            return ReplayResult(outcome=ReplayOutcome.error, detail=f"trace replayer failed: {exc}")

    effective = target or repro.target
    if not is_target_allowed(effective):
        return ReplayResult(
            outcome=ReplayOutcome.refused,
            detail=f"target host is not on the replay allowlist: {effective!r}",
        )
    return _replay_http(repro, effective)


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------
@dataclass
class FindingVerdict:
    """The reproduce-or-drop decision for one finding."""

    finding_key: str = ""
    status: str = FindingStatus.unconfirmed
    gate_blocking: bool = False
    reason: str = ""
    replay: ReplayResult = field(default_factory=ReplayResult)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_key": self.finding_key,
            "status": self.status,
            "gate_blocking": self.gate_blocking,
            "reason": self.reason,
            "replay": self.replay.to_dict(),
        }


def _is_dynamic(finding: dict[str, Any]) -> bool:
    """Dynamic findings assert something about a *running* system."""
    return str(finding.get("analysis_kind", "dynamic")).lower() == "dynamic"


def classify_finding(
    finding: dict[str, Any],
    *,
    prior_status: str = "",
    replay_result: Optional[ReplayResult] = None,
) -> FindingVerdict:
    """Apply the reproduce-or-drop rule to one finding.

    Args:
        finding: ``{finding_key, severity, analysis_kind, reproduction, ...}``.
        prior_status: The finding's last known status. Only a finding that was
            previously ``confirmed`` can become ``remediated`` — a first-ever
            replay that does not fire means the claim was never established,
            not that something was fixed.
        replay_result: Pre-computed replay (used by tests and by
            :func:`verify_discrimination`); replayed here when omitted.

    Returns:
        A :class:`FindingVerdict`. ``gate_blocking`` is True only for a
        ``confirmed`` finding at a blocking severity — never otherwise.
    """
    key = str(finding.get("finding_key") or finding.get("id") or "")
    severity = str(finding.get("severity", "medium")).lower()

    if not _is_dynamic(finding):
        # Static findings are out of scope: they are claims about code, and
        # this module has no way to replay a claim about code.
        return FindingVerdict(
            finding_key=key,
            status=str(finding.get("status") or FindingStatus.confirmed),
            gate_blocking=_severity_blocks(severity),
            reason="static finding — reproduce-or-drop does not apply",
        )

    raw_repro = finding.get("reproduction")
    if not raw_repro:
        return FindingVerdict(
            finding_key=key,
            status=FindingStatus.unconfirmed,
            gate_blocking=False,
            reason="no stored reproduction — a dynamic finding without a replayable proof is a lead, not a finding",
        )

    repro = raw_repro if isinstance(raw_repro, Reproduction) else Reproduction.from_dict(dict(raw_repro))
    result = replay_result if replay_result is not None else replay(repro)

    if result.outcome == ReplayOutcome.reproduced:
        return FindingVerdict(
            finding_key=key,
            status=FindingStatus.confirmed,
            gate_blocking=_severity_blocks(severity),
            reason="reproduction replayed and the vulnerability predicate fired",
            replay=result,
        )

    if result.outcome == ReplayOutcome.not_reproduced:
        remediated = prior_status == FindingStatus.confirmed
        return FindingVerdict(
            finding_key=key,
            status=FindingStatus.remediated if remediated else FindingStatus.unconfirmed,
            gate_blocking=False,
            reason=(
                "previously-confirmed reproduction no longer fires — treated as remediated"
                if remediated
                else "reproduction replayed but the vulnerability predicate did not fire"
            ),
            replay=result,
        )

    # unavailable / error / refused — we learned nothing. Stay unconfirmed and
    # never block, but do not claim remediation either.
    return FindingVerdict(
        finding_key=key,
        status=FindingStatus.unconfirmed,
        gate_blocking=False,
        reason=f"replay was not decisive ({result.outcome}): {result.detail}",
        replay=result,
    )


def _severity_blocks(severity: str) -> bool:
    blocking = {str(s).lower() for s in load_policy().get("gate", {}).get("blocking_severities", [])}
    return severity.lower() in blocking


@dataclass
class EnforcementReport:
    """Outcome of applying the rule across a batch of findings."""

    verdicts: list[FindingVerdict] = field(default_factory=list)

    @property
    def confirmed(self) -> list[FindingVerdict]:
        return [v for v in self.verdicts if v.status == FindingStatus.confirmed]

    @property
    def unconfirmed(self) -> list[FindingVerdict]:
        return [v for v in self.verdicts if v.status == FindingStatus.unconfirmed]

    @property
    def remediated(self) -> list[FindingVerdict]:
        return [v for v in self.verdicts if v.status == FindingStatus.remediated]

    @property
    def blocking(self) -> list[FindingVerdict]:
        return [v for v in self.verdicts if v.gate_blocking]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": len(self.verdicts),
            "confirmed": len(self.confirmed),
            "unconfirmed": len(self.unconfirmed),
            "remediated": len(self.remediated),
            "blocking": len(self.blocking),
            "gate_pass": not self.blocking,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


def enforce(
    findings: list[dict[str, Any]],
    *,
    persist: bool = True,
    tenant_id: str = "default",
    classification: str = "CUI",
) -> EnforcementReport:
    """Apply the reproduce-or-drop rule to *findings* and record the evidence.

    Every dynamic finding is replayed. Confirmed findings may block a gate;
    unconfirmed ones are returned for reporting but are structurally incapable
    of blocking. ``gate.block_on_unconfirmed`` in the policy exists only so
    that re-admitting them is an explicit, visible configuration act.
    """
    report = EnforcementReport()
    block_unconfirmed = bool(load_policy().get("gate", {}).get("block_on_unconfirmed", False))

    for finding in findings:
        prior = str(finding.get("status") or "")
        verdict = classify_finding(finding, prior_status=prior)
        if (
            block_unconfirmed
            and verdict.status == FindingStatus.unconfirmed
            and _severity_blocks(str(finding.get("severity", "medium")))
        ):
            verdict.gate_blocking = True
            verdict.reason += " [block_on_unconfirmed=true overrides the reproduce-or-drop rule]"
        report.verdicts.append(verdict)
        if persist:
            _persist_verdict(finding, verdict, tenant_id=tenant_id, classification=classification)

    return report


# ---------------------------------------------------------------------------
# Discrimination proof — the task's success criterion
# ---------------------------------------------------------------------------
@dataclass
class DiscriminationProof:
    """Evidence that a reproduction distinguishes vulnerable from fixed."""

    discriminating: bool = False
    reason: str = ""
    vulnerable: ReplayResult = field(default_factory=ReplayResult)
    fixed: ReplayResult = field(default_factory=ReplayResult)

    def to_dict(self) -> dict[str, Any]:
        return {
            "discriminating": self.discriminating,
            "reason": self.reason,
            "vulnerable": self.vulnerable.to_dict(),
            "fixed": self.fixed.to_dict(),
        }


def verify_discrimination(
    repro: Reproduction,
    *,
    vulnerable_target: str,
    fixed_target: str,
) -> DiscriminationProof:
    """Prove a reproduction discriminates, rather than merely running.

    Replays the SAME reproduction against a target that still has the defect
    and against one where the fix is applied. It discriminates only if it
    reproduces on the first and does not reproduce on the second. A
    reproduction that fires on both is a tautology; one that fires on neither
    never established the finding at all.

    This is the bar the reproduce-or-drop rule is worth having: without it,
    "the replay ran" is indistinguishable from "the vulnerability is real".
    """
    vulnerable = replay(repro, target=vulnerable_target)
    fixed = replay(repro, target=fixed_target)

    if not vulnerable.decisive or not fixed.decisive:
        return DiscriminationProof(
            discriminating=False,
            reason=(
                f"indecisive replay (vulnerable={vulnerable.outcome}, fixed={fixed.outcome}) — "
                "discrimination cannot be established"
            ),
            vulnerable=vulnerable,
            fixed=fixed,
        )

    if vulnerable.reproduced and not fixed.reproduced:
        return DiscriminationProof(
            discriminating=True,
            reason="reproduction fired against the vulnerable target and stopped firing once the fix was applied",
            vulnerable=vulnerable,
            fixed=fixed,
        )

    if vulnerable.reproduced and fixed.reproduced:
        reason = "reproduction fires against the fixed target too — it is a tautology, not a proof"
    elif not vulnerable.reproduced and fixed.reproduced:
        reason = "reproduction fires only against the FIXED target — the predicate is inverted"
    else:
        reason = "reproduction fires against neither target — it never established the finding"

    return DiscriminationProof(discriminating=False, reason=reason, vulnerable=vulnerable, fixed=fixed)


# ---------------------------------------------------------------------------
# Rubric-loop integration — grade an agent by whether its PoC fires
# ---------------------------------------------------------------------------
def parse_agent_reproduction(content: str) -> Optional[Reproduction]:
    """Extract a Reproduction from an agent's final response.

    Accepts a bare JSON object or one inside a fenced code block. Returns None
    when nothing parseable is present — which the grader treats as "no PoC".
    """
    if not content:
        return None
    candidates: list[str] = []
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    candidates.extend(fenced)
    stripped = content.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    # Last resort: the outermost brace-balanced span.
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        candidates.append(content[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and ("steps" in data or "predicate" in data):
            return Reproduction.from_dict(data)
    return None


def make_reproduction_grader(*, target: Optional[str] = None) -> Callable[[Any], Any]:
    """Build a pluggable code grader for :func:`run_agent_loop_with_rubric`.

    ``run_agent_loop_with_rubric`` accepts a ``grader`` callback that REPLACES
    the LLM rubric judge, so verdicts come from real state rather than from a
    model reading prose. This is the honest place to reuse it: an agent
    claiming a dynamic finding is graded on whether the reproduction it wrote
    actually fires. ``needs_revision`` feedback is injected verbatim and the
    loop resumes, so the agent iterates until it has a working PoC or runs out
    of rounds — at which point the finding is, correctly, unconfirmed.

    Returns:
        ``callback(AgentLoopResult) -> RubricGrade``. Import of the LLM stack
        is deferred so this module stays usable with no provider configured.
    """
    from icdev.tools.llm.agent_loop import RubricGrade, RubricVerdict

    def _grade(result: Any) -> Any:
        repro = parse_agent_reproduction(getattr(result, "final_content", "") or "")
        if repro is None:
            return RubricGrade(
                verdict=RubricVerdict.needs_revision,
                feedback=(
                    "No reproduction found. Emit a single JSON object with 'kind', 'target', "
                    "'steps' and 'predicate'. A finding without a replayable proof is not a finding."
                ),
            )

        errors = validate_reproduction(repro)
        if errors:
            return RubricGrade(
                verdict=RubricVerdict.needs_revision,
                feedback="The reproduction is not replayable. Fix these: " + "; ".join(errors),
            )

        outcome = replay(repro, target=target)
        if outcome.reproduced:
            return RubricGrade(
                verdict=RubricVerdict.satisfied,
                feedback=f"Reproduction replayed and the predicate fired: {outcome.detail}",
            )
        if outcome.outcome == ReplayOutcome.not_reproduced:
            return RubricGrade(
                verdict=RubricVerdict.needs_revision,
                feedback=(
                    "The reproduction replayed cleanly but the vulnerability predicate did NOT fire, "
                    f"so the finding is unconfirmed. Observed: {json.dumps(outcome.observations)[:1500]}. "
                    "Either supply a request sequence that demonstrates the defect, or withdraw the finding."
                ),
            )
        return RubricGrade(
            verdict=RubricVerdict.needs_revision,
            feedback=f"Replay was not decisive ({outcome.outcome}): {outcome.detail}",
        )

    return _grade


# ---------------------------------------------------------------------------
# Persistence (best-effort)
# ---------------------------------------------------------------------------
def _persist_verdict(
    finding: dict[str, Any],
    verdict: FindingVerdict,
    *,
    tenant_id: str,
    classification: str,
    purpose: str = "confirm",
) -> bool:
    """Upsert the finding's status and append the replay attempt.

    Never raises: an un-migrated checkout must still get the *rule*, and a
    dropped audit row is preferable to a scanner that crashes mid-sweep.
    """
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        now = _utcnow()
        key = verdict.finding_key or str(uuid.uuid4())
        repro = finding.get("reproduction")
        if isinstance(repro, Reproduction):
            repro = repro.to_dict()

        row = conn.execute(
            "SELECT id FROM dynamic_findings WHERE finding_key = %s AND tenant_id = %s",
            (key, tenant_id),
        ).fetchone()
        finding_id = (row[0] if not isinstance(row, dict) else row["id"]) if row else f"dfn-{uuid.uuid4().hex[:12]}"

        if row:
            conn.execute(
                """
                UPDATE dynamic_findings
                   SET status = %s, last_outcome = %s, last_replayed_at = %s,
                       reproduction = %s, updated_at = %s
                 WHERE id = %s
                """,
                (verdict.status, verdict.replay.outcome, now, json.dumps(repro or {}), now, finding_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO dynamic_findings
                    (id, finding_key, detector, title, severity, target, status,
                     reproduction, discriminating, last_outcome, last_replayed_at,
                     tenant_id, classification, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    finding_id,
                    key,
                    str(finding.get("detector", ""))[:200],
                    str(finding.get("title", ""))[:500],
                    str(finding.get("severity", "medium")).lower(),
                    str(finding.get("target", ""))[:500],
                    verdict.status,
                    json.dumps(repro or {}),
                    0,
                    verdict.replay.outcome,
                    now,
                    tenant_id,
                    classification,
                    now,
                    now,
                ),
            )

        conn.execute(
            """
            INSERT INTO finding_replay_attempts
                (id, finding_id, finding_key, outcome, target, purpose,
                 predicate_json, observations_json, detail, duration_ms,
                 tenant_id, classification, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"fra-{uuid.uuid4().hex[:12]}",
                finding_id,
                key,
                verdict.replay.outcome,
                str((repro or {}).get("target", ""))[:500],
                purpose,
                json.dumps((repro or {}).get("predicate", {})),
                json.dumps(verdict.replay.observations)[:20000],
                verdict.reason[:2000],
                verdict.replay.duration_ms,
                tenant_id,
                classification,
                now,
            ),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        logger.debug("reproduction persistence failed (non-blocking): %s", exc)
        return False


def mark_discriminating(finding_key: str, proof: DiscriminationProof, *, tenant_id: str = "default") -> bool:
    """Record that a finding's reproduction was proven to discriminate."""
    if not proof.discriminating:
        return False
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        conn.execute(
            "UPDATE dynamic_findings SET discriminating = 1, updated_at = %s "
            "WHERE finding_key = %s AND tenant_id = %s",
            (_utcnow(), finding_key, tenant_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("discrimination flag persistence failed (non-blocking): %s", exc)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_json_file(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:  # pragma: no cover - CLI entrypoint
    import argparse

    parser = argparse.ArgumentParser(
        description="Reproduce-or-drop validator for dynamic security findings"
    )
    parser.add_argument("--validate", metavar="FILE", help="validate a reproduction JSON file")
    parser.add_argument("--replay", metavar="FILE", help="replay a reproduction JSON file")
    parser.add_argument("--enforce", metavar="FILE", help="apply the rule to a findings JSON array")
    parser.add_argument("--target", help="override the reproduction's base URL")
    parser.add_argument("--no-persist", action="store_true", help="do not write to the database")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="exit non-zero when any CONFIRMED finding blocks (unconfirmed findings never do)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if args.validate:
        repro = Reproduction.from_dict(_load_json_file(args.validate))
        errors = validate_reproduction(repro)
        payload = {"valid": not errors, "errors": errors}
        print(json.dumps(payload, indent=2) if args.json else ("VALID" if not errors else "\n".join(errors)))
        return 0 if not errors else 1

    if args.replay:
        repro = Reproduction.from_dict(_load_json_file(args.replay))
        result = replay(repro, target=args.target)
        print(json.dumps(result.to_dict(), indent=2) if args.json else f"{result.outcome}: {result.detail}")
        return 0 if result.decisive else 2

    if args.enforce:
        findings = _load_json_file(args.enforce)
        report = enforce(list(findings), persist=not args.no_persist)
        payload = report.to_dict()
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(
                f"total={payload['total']} confirmed={payload['confirmed']} "
                f"unconfirmed={payload['unconfirmed']} remediated={payload['remediated']} "
                f"blocking={payload['blocking']}"
            )
            for verdict in report.verdicts:
                print(f"  [{verdict.status}] {verdict.finding_key}: {verdict.reason}")
        if args.gate and report.blocking:
            return 1
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
