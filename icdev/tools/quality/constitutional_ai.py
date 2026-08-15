# CUI // SP-CTI
"""Constitutional AI — per-rule critique & targeted revision (agx-verify-02).

Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT,
Copyright (c) 2025 Fareed Khan). Pattern only; no upstream code vendored.

ICDEV already HAS the constitution — the merge/deployment gate rules in
``args/security_gates.yaml``, the CUI/classification marking rules, and the TRUST
invariants (citation / placeholder / numeric-consistency). What was missing is
the loop that evaluates a drafted artifact **rule-by-rule**, reports WHICH rule
failed and why (with the offending span), and revises **only** what failed —
instead of one monolithic "is this compliant?" prompt. A blended compliance
verdict hides which specific control was violated, which is unacceptable for an
artifact that carries an ATO claim.

Design:
  * Rules are loaded as DATA from the existing ``args/security_gates.yaml``
    (``constitution:`` block) — the single source. We do not author a parallel
    rule list; the block encodes the already-existing invariants with an
    ``applies_to`` filter and a ``severity`` drawn from the gate lists.
  * Each rule is critiqued in its OWN LLM call (not one blob), yielding a 3-value
    ENUM verdict + the offending span (deterministic-picker: the model commits to
    an enum, Python composes the overall pass/fail).
  * Aggregation policy is **any-block-rule-fail fails** (documented below): a
    single failed BLOCK rule fails the artifact; WARN failures are recorded but
    do not block. This mirrors ``merge_gates.block_on`` vs ``warn_on`` and is the
    only defensible policy for an ATO-bearing artifact — you cannot average away
    a violated mandatory control.
  * Failed BLOCK rules get a targeted, bounded revision pass; the loop never
    silently gives up — unresolved failures are returned and recorded.
  * Every per-rule judgment is written to the append-only ``constitutional_audit_log``.

LLM-agnostic: all inference via ``LLMRouter``; no vendor SDK imports, no hardcoded
model IDs. The 3-value vocabulary is small enough for a 7B local model, with a
fail-closed deterministic fallback for malformed structured output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

VOCABULARY_VERSION = "const-1.0"

# Per-rule verdict vocabulary. Unknown/malformed tokens fail CLOSED (see
# classify_rule_verdict) — a judge that returns garbage must never wave a
# mandatory control through.
RULE_VERDICT_VOCAB = ("pass", "fail", "not_applicable")
_BLOCK, _WARN = "block", "warn"


@dataclass
class Rule:
    id: str
    severity: str  # "block" | "warn"
    principle: str
    applies_to: List[str] = field(default_factory=lambda: ["any"])
    source: str = ""

    def matches(self, artifact_type: Optional[str]) -> bool:
        if not artifact_type or "any" in self.applies_to:
            return True
        return artifact_type in self.applies_to


@dataclass
class RuleResult:
    rule_id: str
    severity: str
    verdict: str
    offending_span: str = ""
    rationale: str = ""
    revised: bool = False


def _security_gates_path(config_path: Optional[str]) -> Path:
    if config_path:
        return Path(config_path)
    # Resolve the repo-root args/ file relative to this module, never cwd
    # (worktree-safe — see the RLS/cwd guidance in CLAUDE.md).
    return Path(__file__).resolve().parents[2] / "args" / "security_gates.yaml"


def load_constitution(
    config_path: Optional[str] = None,
    *,
    artifact_type: Optional[str] = None,
) -> List[Rule]:
    """Load constitutional rules as DATA from ``args/security_gates.yaml``.

    Reads the ``constitution.rules`` block (the single-source encoding of the
    existing gate/CUI/TRUST invariants). Returns the rules whose ``applies_to``
    matches ``artifact_type`` (or all when ``artifact_type`` is None). Returns an
    empty list — never raises — when the block is absent, so an un-updated config
    degrades to "nothing to critique" rather than crashing a promote path.
    """
    try:
        import yaml
        path = _security_gates_path(config_path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — config load must not crash callers
        logger.warning("constitutional_ai: failed to load constitution: %s", exc)
        return []

    block = (data or {}).get("constitution") or {}
    rules: List[Rule] = []
    for raw in block.get("rules") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        severity = str(raw.get("severity", _WARN)).strip().lower()
        rule = Rule(
            id=str(raw["id"]),
            severity=_BLOCK if severity == _BLOCK else _WARN,
            principle=str(raw.get("principle", "")).strip(),
            applies_to=[str(a) for a in (raw.get("applies_to") or ["any"])],
            source=str(raw.get("source", "")),
        )
        if rule.principle and rule.matches(artifact_type):
            rules.append(rule)
    return rules


def classify_rule_verdict(token: Any, *, severity: str = _WARN) -> str:
    """Normalize a model verdict to the vocabulary.

    Fail-closed: an unknown/malformed token becomes ``fail`` for a BLOCK rule
    (a mandatory control cannot be waved through by a malformed judge) and
    ``not_applicable`` for a WARN rule (advisory — do not manufacture a failure).
    """
    t = str(token or "").strip().lower()
    if t in RULE_VERDICT_VOCAB:
        return t
    return "fail" if severity == _BLOCK else "not_applicable"


def _critique_contract(severity: str):
    """The declared shape of one rule critique (trust-struct-01).

    The fail-closed sentinel is SEVERITY-DEPENDENT, which is why the contract is
    built per rule rather than declared once: a malformed judge may not wave a
    mandatory (BLOCK) control through, but must not manufacture a failure for an
    advisory (WARN) one. That is the same policy :func:`classify_rule_verdict`
    already states — declared here so the substitution is recorded, not implied.
    """
    from tools.quality.structured_output import OutputContract, enum_field

    return OutputContract(
        {
            "type": "object",
            "required": ["verdict"],
            "properties": {
                "verdict": enum_field(
                    RULE_VERDICT_VOCAB,
                    fail_closed=classify_rule_verdict(None, severity=severity),
                ),
                "offending_span": {"type": "string", "fail_closed": ""},
                "rationale": {"type": "string", "fail_closed": ""},
            },
        },
        name=f"constitutional_ai.critique[{severity}]",
    )


def _content(resp) -> str:
    return (getattr(resp, "content", "") or "").strip()


def critique_rule(
    artifact: str,
    rule: Rule,
    *,
    router,
    function: str = "constitutional_critique",
) -> RuleResult:
    """Critique ``artifact`` against ONE ``rule`` (never a monolithic blob prompt).

    The LLM commits only to a 3-value enum verdict and cites the offending span;
    Python owns the aggregation. Fails closed on malformed output: the shape is
    held to :func:`_critique_contract` (trust-struct-01), and a payload that
    cannot be repaired from a declared sentinel is REJECTED — the rule then
    takes the same severity-dependent fallback verdict it always did, but now
    says in ``rationale`` which defect caused it instead of reporting nothing.
    """
    from tools.llm.provider import LLMRequest
    from tools.quality.structured_output import coerce_or_reject

    prompt = (
        "You are a compliance reviewer. Judge whether the ARTIFACT satisfies this "
        "ONE rule. Do not consider any other rule.\n\n"
        f"RULE ({rule.severity}): {rule.principle}\n\n"
        "Choose exactly one verdict:\n"
        "  pass           = the artifact satisfies the rule\n"
        "  fail           = the artifact violates the rule\n"
        "  not_applicable = the rule does not apply to this artifact\n\n"
        "Return STRICT JSON only:\n"
        '{"verdict": "pass"|"fail"|"not_applicable", "offending_span": '
        '"<verbatim offending text or empty>", "rationale": "<one sentence>"}\n\n'
        f"ARTIFACT:\n{artifact[:4000]}"
    )
    try:
        resp = router.invoke(function, LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300, temperature=0.0,
        ))
        data, findings = coerce_or_reject(
            _content(resp), _critique_contract(rule.severity), mode="coerce"
        )
        if data is None:
            # Unparseable / unrepairable. Same fail-closed verdict as before,
            # with the defect recorded so a reviewer can tell a malformed judge
            # apart from a genuine violation.
            codes = ",".join(sorted({f["code"] for f in findings})) or "unknown"
            return RuleResult(
                rule_id=rule.id, severity=rule.severity,
                verdict=classify_rule_verdict(None, severity=rule.severity),
                rationale=f"contract_violation: {codes}"[:500],
            )
        return RuleResult(
            rule_id=rule.id, severity=rule.severity,
            verdict=classify_rule_verdict(data.get("verdict"), severity=rule.severity),
            offending_span=str(data.get("offending_span", ""))[:500],
            rationale=str(data.get("rationale", ""))[:500],
        )
    except Exception as exc:  # noqa: BLE001 — fail closed, never crash the gate
        if isinstance(exc, (TypeError, ValueError, AttributeError)):
            raise
        return RuleResult(
            rule_id=rule.id, severity=rule.severity,
            verdict=classify_rule_verdict(None, severity=rule.severity),
            rationale=f"critique_error: {type(exc).__name__}",
        )


def compose_overall(results: List[RuleResult]) -> dict:
    """Compose the overall verdict from per-rule enums.

    POLICY = any-block-rule-fail fails. Rationale: an ATO-bearing artifact cannot
    ship with a single mandatory (BLOCK) control violated; a weighted average
    would let strong areas mask a hard violation. WARN failures are recorded but
    do not block. Returns the composed decision + the failed-rule breakdown.
    """
    failed_block = [r.rule_id for r in results if r.severity == _BLOCK and r.verdict == "fail"]
    failed_warn = [r.rule_id for r in results if r.severity == _WARN and r.verdict == "fail"]
    return {
        "passed": not failed_block,
        "failed_block_rules": failed_block,
        "failed_warn_rules": failed_warn,
        "evaluated": len(results),
        "vocabulary_version": VOCABULARY_VERSION,
    }


def _revise_for_rule(
    artifact: str,
    rule: Rule,
    result: RuleResult,
    *,
    router,
    function: str,
) -> str:
    """Targeted single-rule revision. Returns revised text (or the original)."""
    from tools.llm.provider import LLMRequest

    prompt = (
        "Revise the ARTIFACT to satisfy the ONE rule below. Change only what the "
        "rule requires; keep everything else intact. Return only the revised "
        "artifact text.\n\n"
        f"RULE: {rule.principle}\n"
        f"WHY IT CURRENTLY FAILS: {result.rationale}\n"
        f"OFFENDING SPAN: {result.offending_span}\n\n"
        f"ARTIFACT:\n{artifact}"
    )
    try:
        resp = router.invoke(function, LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000, temperature=0.2,
        ))
        revised = _content(resp)
        return revised or artifact
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, (TypeError, ValueError, AttributeError)):
            raise
        logger.warning("constitutional_ai: revision failed for %s: %s", rule.id, exc)
        return artifact


def constitutional_review(
    artifact: str,
    *,
    router=None,
    artifact_type: Optional[str] = None,
    config_path: Optional[str] = None,
    max_revisions: int = 2,
    critique_function: str = "constitutional_critique",
    revise_function: str = "architecture_run",
    persist_audit: bool = True,
    tenant_id: Optional[str] = None,
    classification: str = "CUI",
) -> dict:
    """Evaluate an artifact rule-by-rule and revise failed BLOCK rules (bounded).

    Returns::

        {passed, revised_text, rule_trace[], failed_block_rules, failed_warn_rules,
         revisions_used, unresolved_block_rules, audit_records[], vocabulary_version}

    The loop critiques every applicable rule, then for each failed BLOCK rule runs
    a targeted revision (up to ``max_revisions`` rounds), re-critiquing only the
    revised rules. It never silently gives up: any rule still failing after the
    budget is returned in ``unresolved_block_rules`` and recorded.
    """
    from tools.llm.router import LLMRouter

    router = router or LLMRouter()
    rules = load_constitution(config_path, artifact_type=artifact_type)
    if not rules:
        return {
            "passed": True, "revised_text": artifact, "rule_trace": [],
            "failed_block_rules": [], "failed_warn_rules": [], "revisions_used": 0,
            "unresolved_block_rules": [], "audit_records": [],
            "vocabulary_version": VOCABULARY_VERSION,
        }

    rule_by_id = {r.id: r for r in rules}
    results: Dict[str, RuleResult] = {
        r.id: critique_rule(artifact, r, router=router, function=critique_function)
        for r in rules
    }

    revised_text = artifact
    revisions_used = 0
    # Targeted, bounded revision of failed BLOCK rules only.
    for _ in range(max(0, max_revisions)):
        failing = [rid for rid, res in results.items()
                   if rule_by_id[rid].severity == _BLOCK and res.verdict == "fail"]
        if not failing:
            break
        for rid in failing:
            rule = rule_by_id[rid]
            revised_text = _revise_for_rule(
                revised_text, rule, results[rid], router=router, function=revise_function)
            # Re-critique only this rule against the revised text.
            new_res = critique_rule(revised_text, rule, router=router, function=critique_function)
            new_res.revised = True
            results[rid] = new_res
        revisions_used += 1

    ordered = [results[r.id] for r in rules]
    decision = compose_overall(ordered)
    unresolved = [r.rule_id for r in ordered if r.severity == _BLOCK and r.verdict == "fail"]

    audit_records = _build_audit_records(
        ordered, artifact_type=artifact_type, tenant_id=tenant_id, classification=classification)
    if persist_audit:
        _persist_audit(audit_records)

    return {
        "passed": decision["passed"],
        "revised_text": revised_text,
        "rule_trace": [
            {"rule_id": r.rule_id, "severity": r.severity, "verdict": r.verdict,
             "offending_span": r.offending_span, "rationale": r.rationale,
             "revised": r.revised}
            for r in ordered
        ],
        "failed_block_rules": decision["failed_block_rules"],
        "failed_warn_rules": decision["failed_warn_rules"],
        "revisions_used": revisions_used,
        "unresolved_block_rules": unresolved,
        "audit_records": audit_records,
        "vocabulary_version": VOCABULARY_VERSION,
    }


# ── Append-only audit trail ─────────────────────────────────────────────────
def _build_audit_records(
    results: List[RuleResult],
    *,
    artifact_type: Optional[str],
    tenant_id: Optional[str],
    classification: str,
) -> List[dict]:
    now = datetime.now(timezone.utc).isoformat()
    records = []
    for r in results:
        records.append({
            "id": f"const-{abs(hash((r.rule_id, r.verdict, now, id(r)))) & 0xffffffffffff:x}",
            "artifact_type": artifact_type or "",
            "rule_id": r.rule_id,
            "severity": r.severity,
            "verdict": r.verdict,
            "offending_span": r.offending_span,
            "rationale": r.rationale,
            "revised": 1 if r.revised else 0,
            "vocabulary_version": VOCABULARY_VERSION,
            "tenant_id": tenant_id,
            "classification": classification,
            "recorded_at": now,
        })
    return records


def _persist_audit(records: List[dict]) -> None:
    """Append per-rule judgments to the append-only ``constitutional_audit_log``.

    Best-effort and table-existence-tolerant: an un-migrated checkout simply
    skips persistence rather than crashing the promote path. NEVER updates or
    deletes rows (NIST AU append-only).
    """
    if not records:
        return
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        logger.warning("constitutional_ai: audit DB unavailable: %s", exc)
        return
    try:
        for rec in records:
            try:
                conn.execute(
                    "INSERT INTO constitutional_audit_log "
                    "(id, artifact_type, rule_id, severity, verdict, offending_span, "
                    " rationale, revised, vocabulary_version, tenant_id, classification, recorded_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (rec["id"], rec["artifact_type"], rec["rule_id"], rec["severity"],
                     rec["verdict"], rec["offending_span"], rec["rationale"], rec["revised"],
                     rec["vocabulary_version"], rec["tenant_id"], rec["classification"],
                     rec["recorded_at"]),
                )
            except Exception:  # table absent / dialect quirk — skip this row
                return
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("constitutional_ai: audit persist failed: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass
