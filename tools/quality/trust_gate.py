# CUI // SP-CTI
"""TRUST v2 — the two-stage grounding gate (trust-spine-01).

ICDEV already had the pieces. What it did not have was a spine, and the
consequence was measurable: ``claim_gate``, ``verify_claim``, ``bind_claim_span``
and the whole ``constitution:`` rule block in ``args/security_gates.yaml`` had
ZERO production callers, while the only thing actually gating a promote was the
shallow tier — is the ``[source: …]`` tag well-formed and pointing at a retrieved
id? A fluent, well-cited, entirely invented sentence passed.

This module runs the existing guards in two stages and composes ONE verdict:

  STAGE 1 — deterministic. No LLM, ever. Runs air-gapped, runs offline, runs in
  CI. ``structure_guard`` → ``placeholder_guard`` → ``citation_guard`` →
  ``claim_guard`` → ``kg_guard``.

  STAGE 2 — semantic. LLM-backed and optional. ``cove_guard`` (independent
  interrogation of each claim) → ``constitution_guard`` (rule-by-rule critique).

Five invariants hold the thing up. Each maps to a failure this platform has
already shipped, and none of them is decoration:

1. **Stage 1 never requires an LLM.** An air-gapped IL6 deployment must still
   gate. Precedent: ``content_grounding`` reports ``method="no_context"`` rather
   than ``score=0.0`` when there is no evidence.

2. **Unmeasured is not clean.** A guard with nothing to work on returns
   ``unmeasurable``, never ``clean``. The profile decides whether that refuses.
   Same discipline as ``chain_sweep``'s pre_cutover-vs-broken split and
   ``capability_consumption``'s ``telemetry_available: false`` — a check that
   reports "fine" when it did not run is worse than no check.

3. **Stage 2 may never unblock Stage 1.** Findings only ever accumulate. An LLM
   that can talk its way past the deterministic gate is not a gate. Only a human
   ``force_*`` clears a block.

4. **An override states its reason.** ``overrides.require_reason`` — a force with
   an empty reason is ignored and the block stands. Ported from the
   ``dashboard/api/pulse.py`` ``force_publish`` + mandatory ``force_reason``
   precedent.

5. **Stage 2 unavailable is a stated posture, not a silent pass.** Per-profile
   ``stage2.fail_closed``. This is the direct fix for
   ``args/cortex_config.yaml governance.fail_closed: false``, which is how
   ``citation_type="cortex"`` raised ``ValueError`` for its entire lifetime with
   0 of 285 rows persisted and nothing went red.

No citation parsing, claim decomposition, placeholder detection or rule loading
is re-implemented here. This module owns composition and policy only — the TRUST
invariant in CLAUDE.md forbids a second copy of any of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

GATE_VERSION = "trust-2.0"

# Guard order within each stage IS the reporting order: the first blocking guard
# becomes ``TrustVerdict.gate``, which is persisted to idr_publish_audit.gate.
# Placeholder before citation matches the pre-existing docgen ordering.
#
# ``structure_guard`` leads because shape precedes content: if the artifact is
# not the object the caller declared, a citation complaint about its braces is
# noise and the gate a reviewer sees should name the actual defect.
STAGE1_GUARDS: tuple[str, ...] = (
    "structure_guard", "placeholder_guard", "citation_guard", "claim_guard", "kg_guard",
)
STAGE2_GUARDS: tuple[str, ...] = ("cove_guard", "constitution_guard")
ALL_GUARDS: tuple[str, ...] = STAGE1_GUARDS + STAGE2_GUARDS

SEV_BLOCK, SEV_WARN, SEV_SKIP = "block", "warn", "skip"
_SEVERITIES = (SEV_BLOCK, SEV_WARN, SEV_SKIP)

STATUS_CLEAN = "clean"
STATUS_FINDINGS = "findings"
STATUS_UNMEASURABLE = "unmeasurable"   # guard had no evidence to work with
STATUS_UNAVAILABLE = "unavailable"     # guard could not run (no router, import error)
STATUS_SKIPPED = "skipped"             # profile turned it off
#: The guard has nothing to check on THIS artifact — there is no obligation to
#: measure, so there is nothing unmeasured. Distinct from ``unmeasurable``, and
#: the distinction is load-bearing: an SSP narrative declares no output
#: contract, and treating that as "could not verify" would refuse every
#: compliance export under a profile whose whole posture is to refuse on an
#: unmeasurable guard. A compliance check with no applicability gate is 100%
#: false positives, which is the twin of the declared-but-unconsumed defect this
#: framework exists to catch — so applicability is named, not inferred.
STATUS_NOT_APPLICABLE = "not_applicable"

STAGE2_RAN = "ran"
STAGE2_DISABLED = "disabled"
STAGE2_UNAVAILABLE = "unavailable"
STAGE2_SKIPPED_BLOCKED = "skipped_stage1_blocked"

_CONFIG_FILENAME = "trust_gate.yaml"


class TrustGateConfigError(RuntimeError):
    """Raised when a named profile does not exist in args/trust_gate.yaml."""


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Finding:
    """One defect. Shaped to match the guards' native ``{item_number, issue,
    detail}`` payload so no guard needs an adapter."""

    guard: str
    issue: str
    severity: str
    item_number: Any = "document"
    detail: Any = None

    def to_dict(self) -> dict:
        return {
            "guard": self.guard,
            "issue": self.issue,
            "severity": self.severity,
            "item_number": self.item_number,
            "detail": self.detail,
        }


@dataclass
class GuardResult:
    guard: str
    status: str
    severity: str
    findings: List[Finding] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocks(self) -> bool:
        """True when this result refuses the promote.

        ``unmeasurable`` and ``unavailable`` block on the same severity as a
        finding would — invariant 2. A guard that could not run has NOT passed.
        """
        if self.severity != SEV_BLOCK:
            return False
        return self.status in (STATUS_FINDINGS, STATUS_UNMEASURABLE, STATUS_UNAVAILABLE)

    def to_dict(self) -> dict:
        return {
            "guard": self.guard,
            "status": self.status,
            "severity": self.severity,
            "findings": [f.to_dict() for f in self.findings],
            "detail": self.detail,
        }


@dataclass
class TrustVerdict:
    blocked: bool
    gate: Optional[str]
    profile: str
    stage1: Dict[str, GuardResult] = field(default_factory=dict)
    stage2: Dict[str, GuardResult] = field(default_factory=dict)
    stage2_status: str = STAGE2_DISABLED
    overrides: Dict[str, Any] = field(default_factory=dict)
    text: str = ""
    gate_version: str = GATE_VERSION

    @property
    def findings(self) -> List[Finding]:
        out: List[Finding] = []
        for guard in STAGE1_GUARDS:
            if guard in self.stage1:
                out.extend(self.stage1[guard].findings)
        for guard in STAGE2_GUARDS:
            if guard in self.stage2:
                out.extend(self.stage2[guard].findings)
        return out

    def to_dict(self) -> dict:
        return {
            "blocked": self.blocked,
            "gate": self.gate,
            "profile": self.profile,
            "gate_version": self.gate_version,
            "stage1": {k: v.to_dict() for k, v in self.stage1.items()},
            "stage2": {k: v.to_dict() for k, v in self.stage2.items()},
            "stage2_status": self.stage2_status,
            "findings": [f.to_dict() for f in self.findings],
            "overrides": self.overrides,
        }


@dataclass
class Profile:
    name: str
    guards: Dict[str, str]
    unmeasurable: str = SEV_WARN
    stage2_enabled: bool = False
    stage2_fail_closed: bool = False
    description: str = ""

    def severity(self, guard: str) -> str:
        return self.guards.get(guard, SEV_SKIP)


# ─── Config ───────────────────────────────────────────────────────────────────

def _config_path(config_path: Optional[str]) -> Path:
    """Locate args/trust_gate.yaml from either package tree.

    Resolved relative to this module, never cwd — worktree-safe, per the RLS/cwd
    guidance in CLAUDE.md.

    This module ships in both ``tools/quality/`` and the packaged
    ``icdev/tools/quality/`` mirror, so ``parents[2]`` is the repo root for one
    copy and ``icdev/`` for the other. ``icdev/args/`` is only a PARTIAL mirror
    (23 of the args files), and the alternative — shipping a second copy of this
    config — is the documented drift bug that let chains exist only in
    ``icdev/args/llm_config.yaml`` while ``tools.llm.*`` importers silently fell
    through to ``routing.default``. So: walk outward and read the ONE file.
    """
    if config_path:
        return Path(config_path)
    here = Path(__file__).resolve()
    for parent in here.parents[2:5]:
        candidate = parent / "args" / _CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return here.parents[2] / "args" / _CONFIG_FILENAME


def _load_raw(config_path: Optional[str] = None) -> dict:
    try:
        import yaml
        data = yaml.safe_load(_config_path(config_path).read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — config load must not crash a promote path
        logger.warning("trust_gate: failed to load %s: %s", _CONFIG_FILENAME, exc)
        return {}
    return (data or {}).get("trust_gate") or {}


def _norm_severity(value: Any, default: str = SEV_SKIP) -> str:
    sev = str(value or "").strip().lower()
    return sev if sev in _SEVERITIES else default


def load_profile(name: Optional[str] = None, *, config_path: Optional[str] = None) -> Profile:
    """Load one named profile from ``args/trust_gate.yaml``.

    Raises ``TrustGateConfigError`` for an unknown profile rather than silently
    substituting a permissive default — a typo'd profile name that quietly
    degrades to "nothing blocks" is precisely the failure mode this framework
    exists to prevent.
    """
    raw = _load_raw(config_path)
    profiles = raw.get("profiles") or {}
    resolved = name or raw.get("default_profile") or "drafting"
    block = profiles.get(resolved)
    if not isinstance(block, dict):
        available = ", ".join(sorted(profiles)) or "<none>"
        raise TrustGateConfigError(
            f"unknown trust_gate profile {resolved!r} (available: {available})"
        )
    guards = {
        g: _norm_severity(s)
        for g, s in (block.get("guards") or {}).items()
        if g in ALL_GUARDS
    }
    stage2 = block.get("stage2") or {}
    return Profile(
        name=resolved,
        guards=guards,
        unmeasurable=_norm_severity(block.get("unmeasurable"), SEV_WARN),
        stage2_enabled=bool(stage2.get("enabled", False)),
        stage2_fail_closed=bool(stage2.get("fail_closed", False)),
        description=str(block.get("description") or "").strip(),
    )


def list_profiles(*, config_path: Optional[str] = None) -> List[str]:
    return sorted((_load_raw(config_path).get("profiles") or {}).keys())


# ─── Override handling (invariant 4) ──────────────────────────────────────────

def _normalize_force(force: Any, *, require_reason: bool) -> Dict[str, str]:
    """Return ``{guard: reason}`` for overrides that are actually usable.

    Accepts ``{guard: reason}``, ``{guard: True}`` or an iterable of guard names.
    When ``require_reason`` is set, an override carrying no reason is DROPPED and
    the block stands — an unexplained override is unauditable after the fact.
    """
    if not force:
        return {}
    pairs: List[tuple[str, Any]] = []
    if isinstance(force, Mapping):
        pairs = list(force.items())
    elif isinstance(force, (list, tuple, set)):
        pairs = [(g, "") for g in force]
    out: Dict[str, str] = {}
    for guard, reason in pairs:
        guard = str(guard)
        if guard not in ALL_GUARDS:
            continue
        text = "" if reason is True else str(reason or "").strip()
        if require_reason and not text:
            logger.warning(
                "trust_gate: dropping force override for %s — no reason given", guard
            )
            continue
        out[guard] = text
    return out


# ─── Guards ───────────────────────────────────────────────────────────────────

def _findings_from(guard: str, raw: Sequence[Mapping], severity: str) -> List[Finding]:
    """Adapt a guard's native ``{item_number, issue, detail}`` list."""
    out: List[Finding] = []
    for item in raw or []:
        if not isinstance(item, Mapping):
            continue
        out.append(Finding(
            guard=guard,
            issue=str(item.get("issue") or guard),
            severity=severity,
            item_number=item.get("item_number", "document"),
            detail=item.get("detail") if "detail" in item else item.get("placeholders"),
        ))
    return out


def _run_structure_guard(
    text: str, severity: str, *, contract: Any, sections: Any, artifact_type: Any
) -> GuardResult:
    """Hold the artifact to a DECLARED shape (trust-struct-03).

    "Structure" has two halves on this platform and this guard carries both,
    dispatching on what the caller declared:

      * ``contract=`` — a machine payload's shape. An ``OutputContract`` (or the
        schema dict for one) from ``structured_output``: is this the object the
        caller asked for?
      * ``sections=`` (+ ``artifact_type=``) — a document's skeleton, via
        ``outline_contract``: does the draft have every section it owes, in
        order, with nothing it invented?

    Both are deterministic, LLM-free and emit the same
    ``{item_number, issue, detail}`` shape, so they compose into one verdict.

    THE APPLICABILITY GATE. Declaring either is what makes the artifact subject
    to structural checking:

      * neither declared  -> ``not_applicable``. Prose has no shape to violate.
        Reporting ``unmeasurable`` here instead would refuse every
        ``compliance_evidence`` export — the profile blocks on unmeasurable —
        for the crime of being a narrative. That is a check with no
        applicability gate, i.e. 100% false positives.
      * declared          -> validated. Not parseable as the declared root type
        is a FINDING, never a pass: this guard exists because a caller that
        asked for an object and got prose was handed the prose back with a note.
      * declared but this platform knows no skeleton for that artifact type ->
        ``unmeasurable``. The caller DID assert the artifact has an outline; we
        simply cannot check which one. That is a different fact from "no outline
        was ever claimed", and ``check_outline`` reports it as ``measurable:
        False`` for exactly this reason.

    ``mode="reject"`` deliberately: ``coerce`` substitutes declared
    ``fail_closed`` sentinels, which is a *repair* decision for the call site
    that owns the fallback policy. A gate does not repair, it reports.
    """
    if contract is None and sections is None:
        return GuardResult(
            guard="structure_guard",
            status=STATUS_NOT_APPLICABLE,
            severity=severity,
            detail={"reason": "no output contract or section outline declared"},
        )

    findings: List[Finding] = []
    detail: Dict[str, Any] = {}

    if contract is not None:
        from tools.quality.structured_output import (
            ContractError, OutputContract, coerce_or_reject,
        )

        if not isinstance(contract, OutputContract):
            try:
                contract = OutputContract(contract, name="trust_gate")
            except ContractError as exc:
                # The CONTRACT is malformed, not the output. That is a defect in
                # the declaring call site, and it must not read as "the artifact
                # passed".
                return GuardResult(
                    guard="structure_guard",
                    status=STATUS_UNAVAILABLE,
                    severity=severity,
                    detail={"reason": f"contract is not a supported schema: {exc}"},
                )
        obj, raw = coerce_or_reject(text, contract, mode="reject")
        findings.extend(_findings_from("structure_guard", _as_gate_findings(raw), severity))
        detail["contract"] = contract.name or ""
        detail["conforms"] = obj is not None

    if sections is not None:
        from tools.quality.outline_contract import check_outline, get_contract

        outline = get_contract(str(artifact_type or ""))
        report = check_outline(list(sections), outline)
        if not report.get("measurable"):
            return GuardResult(
                guard="structure_guard",
                status=STATUS_UNMEASURABLE,
                severity=severity,
                detail={"reason": report.get("reason") or "outline not measurable",
                        "artifact_type": str(artifact_type or "")},
            )
        findings.extend(
            _findings_from("structure_guard", report.get("findings") or [], severity)
        )
        detail["outline"] = {
            k: report[k] for k in ("required", "present", "missing", "unknown", "out_of_order")
        }

    detail["codes"] = sorted({str(f.issue) for f in findings})
    return GuardResult(
        guard="structure_guard",
        status=STATUS_FINDINGS if findings else STATUS_CLEAN,
        severity=severity,
        findings=findings,
        detail=detail,
    )


def _as_gate_findings(raw: Sequence[Mapping]) -> List[dict]:
    """Adapt ``structured_output``'s ``{code, path, message}`` to the guard shape.

    The two validators report in different vocabularies — ``outline_contract``
    already speaks ``{item_number, issue, detail}`` because it was written to
    sit beside ``citation_gate``; ``structured_output`` speaks JSON-pointer.
    Translating here rather than in either module keeps both free of a
    gate-specific shape.
    """
    return [
        {
            "item_number": str(item.get("path") or "$"),
            "issue": str(item.get("code") or "structure_violation"),
            "detail": item.get("message"),
        }
        for item in raw or []
        if isinstance(item, Mapping)
    ]


def _run_placeholder_guard(text: str, severity: str) -> GuardResult:
    from tools.quality.content_grounding import placeholder_findings
    raw = placeholder_findings([{"item_number": "document", "content": text}])
    findings = _findings_from("placeholder_guard", raw, severity)
    return GuardResult(
        guard="placeholder_guard",
        status=STATUS_FINDINGS if findings else STATUS_CLEAN,
        severity=severity,
        findings=findings,
    )


def _run_citation_guard(
    text: str, severity: str, *, allowed_sources: Any, require_citations: bool
) -> GuardResult:
    from tools.quality.citation_grounding import citation_gate
    section: Dict[str, Any] = {"item_number": "document", "content": text}
    if allowed_sources is not None:
        section["allowed_sources"] = allowed_sources
    raw = citation_gate([section], require_citations=require_citations)
    findings = _findings_from("citation_guard", raw, severity)
    return GuardResult(
        guard="citation_guard",
        status=STATUS_FINDINGS if findings else STATUS_CLEAN,
        severity=severity,
        findings=findings,
    )


def _run_claim_guard(text: str, severity: str, *, sources: Any, judge=None) -> GuardResult:
    """Per-claim span binding + anchor verification.

    ``sources`` must be ``{source_id: source_text}``. Citation IDS alone are not
    enough — this tier binds each claim to a SPAN of the text it cites, so
    without the source bodies there is nothing to bind against. That is an
    ``unmeasurable``, not a pass: the whole point of this guard is catching the
    well-formed citation on an invented sentence, and reporting "clean" because
    we never looked would reintroduce exactly that hole.
    """
    from tools.quality.citation_grounding import claim_gate, ground_claims

    if not isinstance(sources, Mapping) or not sources:
        return GuardResult(
            guard="claim_guard",
            status=STATUS_UNMEASURABLE,
            severity=severity,
            detail={"reason": "no source texts supplied; claim spans cannot be bound"},
        )
    lookup = {str(k): str(v) for k, v in sources.items()}
    report = ground_claims(text, lookup, judge=judge)
    findings = _findings_from("claim_guard", claim_gate(report), severity)
    return GuardResult(
        guard="claim_guard",
        status=STATUS_FINDINGS if findings else STATUS_CLEAN,
        severity=severity,
        findings=findings,
        detail={
            "supported": report.get("supported", 0),
            "partial": report.get("partial", 0),
            "unsupported": report.get("unsupported", 0),
            "uncited": report.get("uncited", 0),
            "supported_ratio": report.get("supported_ratio"),
        },
    )


def _run_kg_guard(
    text: str, severity: str, *, conn, graph_id=None, flag_unknown_entities: bool = False
) -> GuardResult:
    """Validate claims against GRAPH FACTS (trust-kg-01).

    Needs a database connection: the whole check is "does the graph attest
    this?", and without a graph there is nothing to attest anything. No
    connection is ``unmeasurable``, never ``clean`` — invariant 2.

    ``schema_source`` is carried through to the caller because it decides how
    much the verdict is worth: only a DECLARED schema (``kg_ontology``, which
    ships empty) licenses a contradiction. Under the observed schema this guard
    can inform but never refuse, and saying so is the difference between a
    conservative check and a broken one.
    """
    from tools.quality.kg_grounding import kg_gate, kg_ground_claims

    if conn is None:
        return GuardResult(
            guard="kg_guard", status=STATUS_UNMEASURABLE, severity=severity,
            detail={"reason": "no graph connection supplied; nothing to validate against"},
        )
    report = kg_ground_claims(text, conn=conn, graph_id=graph_id)
    if report.get("status") != "ok":
        return GuardResult(
            guard="kg_guard", status=STATUS_UNMEASURABLE, severity=severity,
            detail={"reason": report.get("detail") or report.get("status")},
        )
    findings = _findings_from(
        "kg_guard", kg_gate(report, flag_unknown_entities=flag_unknown_entities), severity
    )
    return GuardResult(
        guard="kg_guard",
        status=STATUS_FINDINGS if findings else STATUS_CLEAN,
        severity=severity,
        findings=findings,
        detail={
            "schema_source": report.get("schema_source"),
            "counts": report.get("counts"),
            "unknown_entities": (report.get("unknown_entities") or [])[:10],
        },
    )


def _run_cove_guard(text: str, severity: str, *, sources: Any, router, max_questions: int) -> GuardResult:
    from tools.quality.cove_guard import cove_guard as _cove
    # force_override is deliberately NOT threaded through: an override is a
    # decision this gate composes centrally (invariant 3), not one a guard makes.
    result = _cove(
        text,
        available_sources=sources,
        router=router,
        force_override=False,
        max_questions=max_questions,
    )
    if result.get("method") == "error":
        return GuardResult(
            guard="cove_guard",
            status=STATUS_UNAVAILABLE,
            severity=severity,
            detail={"error": (result.get("decision") or {}).get("error", "")},
        )
    findings: List[Finding] = []
    if result.get("needs_revision"):
        findings.append(Finding(
            guard="cove_guard",
            issue="claim_needs_revision",
            severity=severity,
            detail=result.get("contradicted_claims") or result.get("decision"),
        ))
    return GuardResult(
        guard="cove_guard",
        status=STATUS_FINDINGS if findings else STATUS_CLEAN,
        severity=severity,
        findings=findings,
        detail={"method": result.get("method"), "revised_text": result.get("revised_text")},
    )


def _run_constitution_guard(
    text: str, severity: str, *, router, artifact_type: Optional[str], max_revisions: int
) -> GuardResult:
    from tools.quality.constitutional_ai import constitutional_review
    result = constitutional_review(
        text,
        router=router,
        artifact_type=artifact_type,
        max_revisions=max_revisions,
    )
    findings = [
        Finding(
            guard="constitution_guard",
            issue="constitution_rule_failed",
            severity=severity,
            item_number=rule_id,
            detail=rule_id,
        )
        for rule_id in (result.get("unresolved_block_rules") or [])
    ]
    return GuardResult(
        guard="constitution_guard",
        status=STATUS_FINDINGS if findings else STATUS_CLEAN,
        severity=severity,
        findings=findings,
        detail={
            "failed_warn_rules": result.get("failed_warn_rules") or [],
            "revisions_used": result.get("revisions_used", 0),
            "vocabulary_version": result.get("vocabulary_version"),
            "revised_text": result.get("revised_text"),
        },
    )


# ─── The gate ─────────────────────────────────────────────────────────────────

class TrustGate:
    """Compose the TRUST guards into one promote/export verdict."""

    def __init__(self, profile: Optional[str] = None, *, config_path: Optional[str] = None):
        self._config_path = config_path
        self._raw = _load_raw(config_path)
        self.profile = load_profile(profile, config_path=config_path)

    # -- policy helpers -----------------------------------------------------
    def _stage2_cfg(self) -> dict:
        return self._raw.get("stage2") or {}

    def _require_reason(self) -> bool:
        return bool((self._raw.get("overrides") or {}).get("require_reason", True))

    def _apply_unmeasurable(self, result: GuardResult) -> GuardResult:
        """An unmeasurable/unavailable guard takes the profile's posture.

        A guard the profile only WARNs on cannot be escalated by this; the
        profile's per-guard severity is the ceiling.
        """
        if result.status not in (STATUS_UNMEASURABLE, STATUS_UNAVAILABLE):
            return result
        if result.severity == SEV_BLOCK and self.profile.unmeasurable != SEV_BLOCK:
            result.severity = self.profile.unmeasurable
        return result

    # -- stages -------------------------------------------------------------
    def _run_guard(self, guard: str, severity: str, **kw) -> GuardResult:
        """Dispatch one guard, converting any failure into ``unavailable``.

        A guard that raises must never crash the promote path AND must never be
        mistaken for a pass — that is the whole shape of this bug class.
        """
        try:
            if guard == "structure_guard":
                return _run_structure_guard(
                    kw["text"], severity,
                    contract=kw.get("contract"),
                    sections=kw.get("sections"),
                    artifact_type=kw.get("artifact_type"),
                )
            if guard == "placeholder_guard":
                return _run_placeholder_guard(kw["text"], severity)
            if guard == "citation_guard":
                return _run_citation_guard(
                    kw["text"], severity,
                    allowed_sources=kw.get("allowed_sources"),
                    require_citations=kw.get("require_citations", True),
                )
            if guard == "claim_guard":
                return _run_claim_guard(
                    kw["text"], severity, sources=kw.get("sources"), judge=kw.get("judge")
                )
            if guard == "kg_guard":
                return _run_kg_guard(
                    kw["text"], severity,
                    conn=kw.get("conn"), graph_id=kw.get("graph_id"),
                    flag_unknown_entities=bool(kw.get("flag_unknown_entities")),
                )
            if guard == "cove_guard":
                return _run_cove_guard(
                    kw["text"], severity,
                    sources=kw.get("sources"),
                    router=kw.get("router"),
                    max_questions=int(self._stage2_cfg().get("cove_max_questions", 5)),
                )
            if guard == "constitution_guard":
                return _run_constitution_guard(
                    kw["text"], severity,
                    router=kw.get("router"),
                    artifact_type=kw.get("artifact_type"),
                    max_revisions=int(self._stage2_cfg().get("constitution_max_revisions", 2)),
                )
        except Exception as exc:  # noqa: BLE001 — see docstring
            logger.warning("trust_gate: guard %s unavailable: %s", guard, exc)
            return GuardResult(
                guard=guard, status=STATUS_UNAVAILABLE, severity=severity,
                detail={"error": f"{type(exc).__name__}: {exc}"},
            )
        return GuardResult(guard=guard, status=STATUS_SKIPPED, severity=SEV_SKIP)

    def evaluate(
        self,
        text: str,
        *,
        sources: Optional[Mapping[str, str]] = None,
        allowed_sources: Any = None,
        require_citations: bool = True,
        router: Any = None,
        force: Any = None,
        artifact_type: Optional[str] = None,
        judge: Any = None,
        run_stage2: Optional[bool] = None,
        conn: Any = None,
        graph_id: Optional[str] = None,
        flag_unknown_entities: bool = False,
        contract: Any = None,
        sections: Any = None,
    ) -> TrustVerdict:
        """Evaluate ``text`` and return a composed :class:`TrustVerdict`.

        Args:
            text: the artifact to gate.
            sources: ``{source_id: source_text}``. Required for ``claim_guard``
                and used as CoVe's evidence; without it ``claim_guard`` reports
                ``unmeasurable`` (invariant 2).
            allowed_sources: ids the draft is permitted to cite (int or iterable),
                passed through to ``citation_gate``.
            conn: a database connection for ``kg_guard``. Without it that guard
                reports ``unmeasurable`` rather than passing silently.
            graph_id: scope KG validation to one graph.
            contract: an ``OutputContract`` (or the schema dict for one) for
                ``structure_guard``'s payload half.
            sections: a ``list_sections`` payload for ``structure_guard``'s
                outline half, checked against the skeleton ``artifact_type``
                resolves to. Supplying ``contract`` or ``sections`` is what
                makes the artifact subject to structural checking at all;
                without either that guard reports ``not_applicable`` — prose has
                no declared shape to violate, and refusing it would be a check
                with no applicability gate.
            router: an ``LLMRouter``. Stage 2 cannot run without one.
            force: ``{guard: reason}`` HITL overrides. A reason is mandatory
                unless ``overrides.require_reason`` is disabled (invariant 4).
            run_stage2: override the profile's ``stage2.enabled``.
        """
        text = text or ""
        prof = self.profile
        overrides = _normalize_force(force, require_reason=self._require_reason())
        verdict = TrustVerdict(blocked=False, gate=None, profile=prof.name, text=text)

        common = {
            "text": text, "sources": sources, "allowed_sources": allowed_sources,
            "require_citations": require_citations, "router": router,
            "artifact_type": artifact_type, "judge": judge,
            "conn": conn, "graph_id": graph_id,
            "flag_unknown_entities": flag_unknown_entities,
            "contract": contract, "sections": sections,
        }

        # ── STAGE 1 — deterministic, no LLM, always runs ────────────────────
        for guard in STAGE1_GUARDS:
            severity = prof.severity(guard)
            if severity == SEV_SKIP:
                verdict.stage1[guard] = GuardResult(guard, STATUS_SKIPPED, SEV_SKIP)
                continue
            verdict.stage1[guard] = self._apply_unmeasurable(
                self._run_guard(guard, severity, **common)
            )

        stage1_gate = self._first_blocking(verdict.stage1, STAGE1_GUARDS, overrides)

        # ── STAGE 2 — semantic, LLM, optional ───────────────────────────────
        want_stage2 = prof.stage2_enabled if run_stage2 is None else bool(run_stage2)
        active2 = [g for g in STAGE2_GUARDS if prof.severity(g) != SEV_SKIP]

        if not want_stage2 or not active2:
            verdict.stage2_status = STAGE2_DISABLED
        elif stage1_gate and not self._stage2_cfg().get("run_when_stage1_blocked", False):
            # Spending LLM calls to reach a verdict that cannot change the
            # outcome (invariant 3) is pure cost.
            verdict.stage2_status = STAGE2_SKIPPED_BLOCKED
            for guard in active2:
                verdict.stage2[guard] = GuardResult(guard, STATUS_SKIPPED, SEV_SKIP)
        elif router is None:
            # Invariant 5: no verifier reachable is a STATED posture. Under
            # fail_closed the artifact is refused; otherwise it is recorded.
            verdict.stage2_status = STAGE2_UNAVAILABLE
            for guard in active2:
                verdict.stage2[guard] = self._apply_unmeasurable(GuardResult(
                    guard=guard,
                    status=STATUS_UNAVAILABLE,
                    severity=prof.severity(guard) if prof.stage2_fail_closed else SEV_WARN,
                    detail={"reason": "no LLM router supplied"},
                ))
        else:
            verdict.stage2_status = STAGE2_RAN
            for guard in active2:
                result = self._apply_unmeasurable(
                    self._run_guard(guard, prof.severity(guard), **common)
                )
                if result.status == STATUS_UNAVAILABLE and not prof.stage2_fail_closed:
                    result.severity = SEV_WARN
                verdict.stage2[guard] = result

        # ── DECISION ────────────────────────────────────────────────────────
        # Invariant 3 by construction: stage 2 can only ADD a blocking guard.
        # ``stage1_gate`` is computed before stage 2 runs and wins the `gate`
        # slot, so a stage-2 pass can never clear a stage-1 refusal.
        stage2_gate = self._first_blocking(verdict.stage2, STAGE2_GUARDS, overrides)
        verdict.gate = stage1_gate or stage2_gate
        verdict.blocked = verdict.gate is not None
        verdict.overrides = self._record_overrides(verdict, overrides)
        return verdict

    # -- decision helpers ---------------------------------------------------
    @staticmethod
    def _first_blocking(
        results: Mapping[str, GuardResult],
        order: Sequence[str],
        overrides: Mapping[str, str],
    ) -> Optional[str]:
        for guard in order:
            result = results.get(guard)
            if result is not None and result.blocks and guard not in overrides:
                return guard
        return None

    @staticmethod
    def _record_overrides(
        verdict: TrustVerdict, overrides: Mapping[str, str]
    ) -> Dict[str, Any]:
        """Record only overrides that actually suppressed something.

        An override supplied for a guard that was already clean is noise in the
        audit trail, not evidence.
        """
        out: Dict[str, Any] = {}
        for guard, reason in overrides.items():
            result = verdict.stage1.get(guard) or verdict.stage2.get(guard)
            if result is None or not result.blocks:
                continue
            out[f"{guard}_override"] = {
                "reason": reason,
                "status": result.status,
                "findings": [f.to_dict() for f in result.findings],
            }
        return out


def evaluate(
    text: str,
    *,
    profile: Optional[str] = None,
    config_path: Optional[str] = None,
    **kwargs: Any,
) -> TrustVerdict:
    """Module-level convenience wrapper around :class:`TrustGate`."""
    return TrustGate(profile, config_path=config_path).evaluate(text, **kwargs)
