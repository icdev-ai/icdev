# CUI // SP-CTI
"""Human-in-the-loop deltas — the delta is the reviewable unit (trust-hitl-01).

Every ``force_*`` override this platform ships records THAT a human overrode a
gate and never WHAT CHANGED. ``response_drafter.approve_draft`` writes

    citation_guard_override: Draft <id> promoted by <reviewer> despite
    3 citation defect(s): [{"item_number": 1, "issue": "missing_citations", ...}]

and the draft text — the thing the reviewer actually approved — appears nowhere.
A reviewer cannot review a count, and an auditor reading that row later cannot
tell whether the override repaired the artifact or waved it through untouched.

This module makes the before/after pair the record. :func:`compute_delta` builds
it, :func:`record_delta` persists it and raises the ask, :func:`pending_deltas`
lists what is still waiting on a human, and :func:`settle_delta` answers one.

Claim-anchored, not a text diff
-------------------------------
The diff runs over :func:`~tools.quality.citation_grounding.decompose_claims`
offsets, so every changed span is a *claim* with a start/end into its own text
and — when sources are supplied — the ``verify_claim`` verdict on each side. The
reviewable question is not "which characters moved", it is "which assertion
changed and is the new one still supported". A raw text diff answers the first
and cannot answer the second.

Those offsets are re-anchored first by :func:`anchored_claims`, because the
sentence splitter and this platform's trailing-citation convention disagree about
where a claim ends and the raw boundaries attribute every citation to the wrong
sentence. See the comment above that function — the compensation is local, and
deliberately so.

A claim whose verdict cannot be established is ``unknown``, never ``supported``.
Absent sources are absence of evidence: scoring them as passing is how a
guardrail that cannot fail gets shipped (see ``kg_grounding``: ``kg_unknown`` is
never ``kg_contradicted``).

Storage split — two tables, two lifetimes
-----------------------------------------
Deliberately the split ``tools/agent_runtime/approval_inbox.py`` already made,
and these rows are meant to be read alongside its rows:

``trust_deltas``    migration 20260815063941. APPEND-ONLY, registered in
                    ``APPEND_ONLY_TABLES``. It is EVIDENCE: this text became
                    that text, under these findings, at this moment. Never
                    UPDATEd, never DELETEd.

``approval_items``  migration 20260809203855. MUTABLE, deliberately, and
                    deliberately NOT in ``APPEND_ONLY_TABLES``. It is the
                    human's DISPOSITION: pending, then resolved/expired/
                    cancelled exactly once. That transition IS an UPDATE.

So this module owns no disposition column. :func:`settle_delta` moves the
``approval_items`` row through :func:`tools.agent_runtime.approval_inbox.resolve`,
which writes the permanent ``agent_approval_log`` entry through the existing
``record_decision``. There is no third decision log.

The join is ``approval_item_id``, written at INSERT time: ``enqueue`` accepts a
caller-supplied ``item_id``, so the id is minted here first. An append-only row
cannot be back-filled with an id produced after the fact, which is precisely why
the ordering below is evidence-then-ask and not the reverse.

A correction is a successor row
-------------------------------
:func:`correct_delta` INSERTs a new row whose ``supersedes_delta_id`` points at
the one being corrected. The predecessor keeps every value it had — not even a
"superseded" flag — because a reviewer may already be looking at the document
that row describes. Whether a row has been superseded is derived at read time by
:func:`revision_chain`. Same rule ``sbom_revision.apply_correction`` follows for
``supersedes_sbom_id``.

Full text lives here, and only here
-----------------------------------
``approval_items`` stores argument KEY NAMES and a digest because its rows are
mirrored out to Slack, Teams, Telegram and email. That constraint is about the
DESTINATION and it still holds: :func:`render_delta_summary` produces an approval
body carrying counts, hashes and the ``delta_id`` — never a line of the artifact.
The text sits in ``trust_deltas`` behind the RLS predicate its ``classification``
column makes it eligible for, read by a reviewer already cleared for the artifact.

Consumers
---------
The panel that renders these rows is trust-hitl-02; the ``force_*`` call sites
that produce them are trust-hitl-03. Until those land the CLI below is the
operable surface — ``--pending``, ``--show``, ``--settle``.

CLI::

    python tools/quality/hitl_delta.py --pending --json
    python tools/quality/hitl_delta.py --pending --artifact-id draft-42
    python tools/quality/hitl_delta.py --show <delta_id> --json
    python tools/quality/hitl_delta.py --show <delta_id> --with-text
    python tools/quality/hitl_delta.py --settle <delta_id> --approve \\
        --reason "verified against the source PDF" --json
    python tools/quality/hitl_delta.py --chain <delta_id> --json
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_runtime.approval_gate import resolve_actor  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.quality.citation_grounding import (  # noqa: E402
    _SOURCE_N_RE,
    _SOURCE_RE,
    claim_gate,
    decompose_claims,
    strip_citations,
    verify_claim,
)

logger = get_logger("icdev.quality.hitl_delta")

TABLE = "trust_deltas"

# --- Vocabularies ----------------------------------------------------------
# Single source of truth. Migration 20260815063941 deliberately ships no CHECK
# constraint over these, for the same reason migrations 20260803002224 and
# 20260809203855 did not: a CHECK is a second hardcoded copy that drifts the
# first time a drafting surface is added, and trust-hitl-03 adds surfaces.
STAGE_DRAFT = "draft"
STAGE_REVIEW = "review"
STAGE_PROMOTE = "promote"
STAGE_EXPORT = "export"
STAGE_CORRECTION = "correction"
STAGES = (STAGE_DRAFT, STAGE_REVIEW, STAGE_PROMOTE, STAGE_EXPORT, STAGE_CORRECTION)

# Span operations. `modified` is a paired before/after claim; `added` and
# `removed` have only one side; `unchanged` carries both and is what makes the
# proportion of the artifact a reviewer must actually read computable.
OP_UNCHANGED = "unchanged"
OP_MODIFIED = "modified"
OP_ADDED = "added"
OP_REMOVED = "removed"
OPS = (OP_UNCHANGED, OP_MODIFIED, OP_ADDED, OP_REMOVED)
CHANGED_OPS = (OP_MODIFIED, OP_ADDED, OP_REMOVED)

# A claim whose verdict could not be established. Never collapsed into
# `supported`: no sources means nothing was checked, not that everything passed.
VERDICT_UNKNOWN = "unknown"

# Below this ratio two claims in the same replace-block are treated as an
# unrelated removal plus addition rather than one edit. Above it they pair into a
# `modified` span whose two sides a reviewer reads side by side. Tuned so a
# reworded sentence pairs and a wholly substituted one does not.
PAIR_SIMILARITY_THRESHOLD = 0.5

DEFAULT_CLASSIFICATION = "CUI"
DEFAULT_INBOX = "trust"

# The approval-inbox origin these asks are raised under. `workflow_hitl` is the
# existing member of ORIGINS for a human-in-the-loop step in a pipeline, which is
# exactly what an override review is — no new origin is needed.
INBOX_ORIGIN = "workflow_hitl"

# Column order is the live schema's order. The INSERT names every column
# explicitly, so this list and migration 20260815063941 must agree — asserted by
# tests/test_hitl_delta.py against the migration's own DDL.
COLUMNS = (
    "delta_id",
    "artifact_id",
    "stage",
    "before_hash",
    "after_hash",
    "before_text",
    "after_text",
    "findings_before",
    "findings_after",
    "spans",
    "actor",
    "rationale",
    "approval_item_id",
    "supersedes_delta_id",
    "session_id",
    "classification",
    "created_at",
)

# Columns that are JSON documents stored as TEXT. Parsed in Python, never with
# json_extract — no runtime query filters or groups inside them, so there is no
# SQLite-dialect JSON to keep portable (CLAUDE.md).
_JSON_COLUMNS = ("findings_before", "findings_after", "spans")


class HitlDeltaUnavailable(RuntimeError):
    """``trust_deltas`` is absent or unreachable.

    Raised by :func:`record_delta` rather than swallowed. An override whose
    evidence could not be persisted must not look like one that was recorded:
    the caller has to treat it as a refusal to override. Reads degrade to empty
    instead — a missing table is not evidence that nothing is pending, it is
    evidence that nothing can be known, and a read has no action to fail closed
    on.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _session_id() -> str:
    for key in ("ICDEV_SESSION_ID", "CLAUDE_SESSION_ID"):
        val = os.environ.get(key)
        if val:
            return val
    return "unknown"


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------
@dataclass
class Delta:
    """One before/after pair for one artifact at one stage.

    Mirrors a row of ``trust_deltas``. ``findings_before`` / ``findings_after``
    are lists in the shared ``{item_number, issue, detail}`` shape every guard in
    ``tools/quality`` emits; ``spans`` is the claim-anchored diff from
    :func:`compute_delta`. All three are serialised to JSON TEXT on write and
    parsed back on read, so a caller never sees a string here.
    """

    delta_id: str
    artifact_id: str
    stage: str
    before_hash: str = ""
    after_hash: str = ""
    before_text: str = ""
    after_text: str = ""
    findings_before: list[dict] = field(default_factory=list)
    findings_after: list[dict] = field(default_factory=list)
    spans: list[dict] = field(default_factory=list)
    actor: str = ""
    rationale: str = ""
    approval_item_id: str = ""
    supersedes_delta_id: str = ""
    session_id: str = ""
    classification: str = DEFAULT_CLASSIFICATION
    created_at: str = ""

    @property
    def is_no_op(self) -> bool:
        """The "override" changed nothing at all.

        The single most useful thing a reviewer can be told, and the one today's
        audit line cannot say: a human was shown findings, overrode the gate, and
        the artifact went out byte-identical. Computed from the hashes so it holds
        even when the text columns have been pruned.
        """
        return bool(self.before_hash) and self.before_hash == self.after_hash

    @property
    def changed_spans(self) -> list[dict]:
        """Only the spans a reviewer has to read."""
        return [s for s in self.spans if s.get("op") in CHANGED_OPS]

    @property
    def findings_resolved(self) -> list[dict]:
        """Findings present before and gone after — what the edit actually fixed."""
        after = {_finding_key(f) for f in self.findings_after}
        return [f for f in self.findings_before if _finding_key(f) not in after]

    @property
    def findings_introduced(self) -> list[dict]:
        """Findings the edit ADDED. A non-empty list means the change made it worse."""
        before = {_finding_key(f) for f in self.findings_before}
        return [f for f in self.findings_after if _finding_key(f) not in before]

    def to_dict(self, *, with_text: bool = True) -> dict[str, Any]:
        """Serialise. ``with_text=False`` drops the artifact text.

        Use that form for anything leaving the reviewer's session — a summary
        line, a log record, a chat notification. The derived counts survive; the
        prose does not.
        """
        out = asdict(self)
        out.update(
            {
                "is_no_op": self.is_no_op,
                "changed_span_count": len(self.changed_spans),
                "findings_resolved": self.findings_resolved,
                "findings_introduced": self.findings_introduced,
            }
        )
        if not with_text:
            out["before_text"] = ""
            out["after_text"] = ""
            out["spans"] = [
                {k: v for k, v in s.items() if not k.endswith("_claim")}
                for s in self.spans
            ]
        return out


def _finding_key(finding: Any) -> str:
    """Identity of a finding for set arithmetic.

    ``issue`` plus ``detail``, not ``item_number``: inserting a sentence shifts
    every later item number, and a finding that merely moved down the document is
    not a new defect. Comparing on item_number would report the same defect as
    simultaneously resolved and introduced.
    """
    if not isinstance(finding, dict):
        return str(finding)
    return json.dumps(
        [finding.get("issue", ""), finding.get("detail", "")],
        sort_keys=True,
        default=str,
    )


# ---------------------------------------------------------------------------
# Claim anchoring
# ---------------------------------------------------------------------------
# `decompose_claims` splits on sentence boundaries, and this platform's citation
# convention puts the tag AFTER the sentence it supports. The two disagree:
# the splitter breaks at the full stop, so the tag lands at the head of the NEXT
# claim. On "A. [source: S] B. [source: S]" it yields
#
#     'A.'                  -> uncited
#     '[source: S] B.'      -> S's support checked against B
#     '[source: S]'         -> a claim consisting of nothing but a pointer
#
# so the first sentence reads unattributed, the second is checked against the
# wrong source, and the trailing bare tag is scored `unsupported` — dropping
# supported_ratio to 0.5 on a document where every sentence is correctly cited.
#
# That is an upstream defect in citation_grounding and fixing it there changes
# what every TRUST gate blocks on, which is trust-cite/trust-spine's call, not
# this module's. What this module cannot do is inherit it: a span whose verdict
# describes the neighbouring sentence makes the side-by-side panel actively
# misleading, which is worse than the audit line it replaces.
#
# So the offsets are re-anchored here, for span boundaries and for the findings
# computed from them, and nothing else. The citation SYNTAX is imported from
# citation_grounding rather than restated — a second copy of that pattern is how
# the two drift. tests/test_hitl_delta.py pins the upstream behaviour so this
# compensation can be deleted the day it is fixed at the source.
_LEADING_CITATION_RE = re.compile(
    r"^(?:" + _SOURCE_RE.pattern + r"|" + _SOURCE_N_RE.pattern + r")\s*",
    re.IGNORECASE,
)


def _is_only_citations(claim: str) -> bool:
    """The claim is a pointer and nothing else — it asserts nothing."""
    stripped = _SOURCE_N_RE.sub("", _SOURCE_RE.sub("", claim or ""))
    return not stripped.strip()


def anchored_claims(text: str) -> list[tuple[str, int, int]]:
    """:func:`decompose_claims`, with trailing citations kept on their own claim.

    Same ``(claim, start, end)`` contract, same offsets into the same text, so
    ``text[start:end].strip() == claim`` still holds. The only difference is
    where a claim ENDS: a citation tag that opens a claim is moved onto its
    predecessor, and a claim that is nothing but tags is absorbed entirely.
    """
    claims = decompose_claims(text or "")
    if not claims:
        return []
    out: list[list[Any]] = []
    for claim, start, end in claims:
        match = _LEADING_CITATION_RE.match(claim)
        if out and (match or _is_only_citations(claim)):
            # Extend the predecessor over the tag. When the whole claim is tags
            # there is nothing left to keep, so it is absorbed and not emitted.
            tag_len = len(claim) if _is_only_citations(claim) else match.end()
            out[-1][2] = start + tag_len
            out[-1][0] = text[out[-1][1]:out[-1][2]].strip()
            if _is_only_citations(claim):
                continue
            start = start + tag_len
            claim = text[start:end].strip()
        out.append([claim, start, end])
    return [(c, s, e) for c, s, e in out if c]


def _ground_anchored(
    text: str, sources: dict, judge: Optional[Callable]
) -> dict[str, Any]:
    """:func:`ground_claims` over :func:`anchored_claims`.

    Same report shape — ``{claims, supported, partial, unsupported, uncited,
    supported_ratio}`` — so :func:`claim_gate` consumes it unchanged and this
    module adds no second copy of the gate's own logic. Only the decomposition
    differs, for the reason stated above.
    """
    verdicts = [verify_claim(c, sources, judge=judge) for c, _s, _e in anchored_claims(text)]
    counts = {"supported": 0, "partial": 0, "unsupported": 0, "uncited": 0}
    for v in verdicts:
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
    cited_total = len(verdicts) - counts["uncited"]
    ratio = (
        (counts["supported"] + 0.5 * counts["partial"]) / cited_total
    ) if cited_total else 1.0
    return {"claims": verdicts, **counts, "supported_ratio": round(ratio, 4)}


# ---------------------------------------------------------------------------
# compute_delta — the claim-anchored diff
# ---------------------------------------------------------------------------
def _norm(claim: str) -> str:
    """Whitespace-normalised claim, for alignment only.

    Deliberately does NOT strip citations. A citation added, removed or
    repointed is exactly the kind of change this module exists to surface, so two
    claims differing only in their ``[source: …]`` tag must align as ``modified``
    and not as ``unchanged``.
    """
    return " ".join((claim or "").split())


def _pair_similarity(before: str, after: str) -> float:
    """How alike two claims are, for the purpose of pairing them into one edit.

    Measured on the claims WITHOUT their citation tags, unlike alignment. The
    question here is "is this the same assertion, reworded" — a question about
    prose. Leaving the tags in lets two entirely unrelated sentences that happen
    to cite the same source clear the threshold on the strength of a shared
    ``[source: SRC-1]``, which is how a substituted claim gets presented to a
    reviewer as a light edit of the one it replaced.

    Two claims differing ONLY in their citation therefore score 1.0 and pair,
    which is correct: that is one edit, and it is one a reviewer must see.
    """
    return difflib.SequenceMatcher(
        a=strip_citations(before) or before,
        b=strip_citations(after) or after,
    ).ratio()


def _verdict(claim: str, sources: Optional[dict], judge: Optional[Callable]) -> str:
    """The verdict for one claim, or ``unknown`` when nothing could be checked."""
    if not sources:
        return VERDICT_UNKNOWN
    try:
        return verify_claim(claim, sources, judge=judge).get("verdict") or VERDICT_UNKNOWN
    except Exception as exc:  # noqa: BLE001 — a verifier failure is not a verdict
        logger.warning("hitl_delta: verify_claim failed for a claim: %s", exc)
        return VERDICT_UNKNOWN


def _span(
    op: str,
    before: Optional[tuple[str, int, int]] = None,
    after: Optional[tuple[str, int, int]] = None,
    *,
    before_index: int = -1,
    after_index: int = -1,
    sources: Optional[dict] = None,
    judge: Optional[Callable] = None,
) -> dict:
    """One claim-anchored span. Offsets index the text they came from."""
    out: dict[str, Any] = {"op": op}
    if before is not None:
        claim, start, end = before
        out.update(
            {
                "before_index": before_index,
                "before_start": start,
                "before_end": end,
                "before_claim": claim,
                "before_verdict": _verdict(claim, sources, judge),
            }
        )
    if after is not None:
        claim, start, end = after
        out.update(
            {
                "after_index": after_index,
                "after_start": start,
                "after_end": end,
                "after_claim": claim,
                "after_verdict": _verdict(claim, sources, judge),
            }
        )
    return out


def compute_delta(
    before_text: str,
    after_text: str,
    *,
    sources: Optional[dict] = None,
    judge: Optional[Callable] = None,
    require_supported: bool = True,
) -> dict[str, Any]:
    """Decompose a before/after pair into claim-anchored spans.

    Args:
        before_text: the artifact as it was.
        after_text: the artifact as it now is.
        sources: ``{source_id: source_text}``. When given, every span carries the
            ``verify_claim`` verdict on each side and ``findings_before`` /
            ``findings_after`` are derived from :func:`claim_gate`. When omitted
            every verdict is ``unknown`` and both finding lists are empty — this
            function never manufactures a verdict it did not compute.
        judge: optional entailment check, forwarded to ``verify_claim``. Keeps
            this module LLM-free by construction.
        require_supported: forwarded to :func:`claim_gate`.

    Returns ``{spans, before_hash, after_hash, findings_before, findings_after,
    counts, changed, supported_ratio_before, supported_ratio_after}``.

    Alignment runs over the claim sequence, not the character stream: within a
    replaced block, claims are paired positionally and kept as one ``modified``
    span only when they are at least :data:`PAIR_SIMILARITY_THRESHOLD` similar.
    Below that they are an unrelated ``removed`` plus ``added`` pair, because
    presenting two different assertions as one edit invites a reviewer to skim
    the second as a rewording of the first.
    """
    before_claims = anchored_claims(before_text or "")
    after_claims = anchored_claims(after_text or "")
    b_norm = [_norm(c) for c, _s, _e in before_claims]
    a_norm = [_norm(c) for c, _s, _e in after_claims]

    spans: list[dict] = []
    matcher = difflib.SequenceMatcher(a=b_norm, b=a_norm, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                spans.append(
                    _span(
                        OP_UNCHANGED,
                        before=before_claims[i1 + k],
                        after=after_claims[j1 + k],
                        before_index=i1 + k,
                        after_index=j1 + k,
                        sources=sources,
                        judge=judge,
                    )
                )
            continue
        if op == "delete":
            for k in range(i1, i2):
                spans.append(
                    _span(OP_REMOVED, before=before_claims[k], before_index=k,
                          sources=sources, judge=judge)
                )
            continue
        if op == "insert":
            for k in range(j1, j2):
                spans.append(
                    _span(OP_ADDED, after=after_claims[k], after_index=k,
                          sources=sources, judge=judge)
                )
            continue
        # replace — pair positionally, then keep or split each pair on similarity.
        paired = min(i2 - i1, j2 - j1)
        for k in range(paired):
            bi, aj = i1 + k, j1 + k
            ratio = _pair_similarity(b_norm[bi], a_norm[aj])
            if ratio >= PAIR_SIMILARITY_THRESHOLD:
                span = _span(
                    OP_MODIFIED,
                    before=before_claims[bi],
                    after=after_claims[aj],
                    before_index=bi,
                    after_index=aj,
                    sources=sources,
                    judge=judge,
                )
                span["similarity"] = round(ratio, 4)
                spans.append(span)
            else:
                spans.append(
                    _span(OP_REMOVED, before=before_claims[bi], before_index=bi,
                          sources=sources, judge=judge)
                )
                spans.append(
                    _span(OP_ADDED, after=after_claims[aj], after_index=aj,
                          sources=sources, judge=judge)
                )
        for k in range(i1 + paired, i2):
            spans.append(
                _span(OP_REMOVED, before=before_claims[k], before_index=k,
                      sources=sources, judge=judge)
            )
        for k in range(j1 + paired, j2):
            spans.append(
                _span(OP_ADDED, after=after_claims[k], after_index=k,
                      sources=sources, judge=judge)
            )

    counts = {o: 0 for o in OPS}
    for span in spans:
        counts[span["op"]] += 1

    findings_before: list[dict] = []
    findings_after: list[dict] = []
    ratio_before = ratio_after = None
    if sources:
        report_before = _ground_anchored(before_text or "", sources, judge)
        report_after = _ground_anchored(after_text or "", sources, judge)
        findings_before = claim_gate(report_before, require_supported=require_supported)
        findings_after = claim_gate(report_after, require_supported=require_supported)
        ratio_before = report_before.get("supported_ratio")
        ratio_after = report_after.get("supported_ratio")

    return {
        "spans": spans,
        "before_hash": _sha256(before_text or ""),
        "after_hash": _sha256(after_text or ""),
        "findings_before": findings_before,
        "findings_after": findings_after,
        "counts": counts,
        "changed": sum(counts[o] for o in CHANGED_OPS),
        "supported_ratio_before": ratio_before,
        "supported_ratio_after": ratio_after,
    }


# ---------------------------------------------------------------------------
# Rendering — the ONLY sanctioned way to describe a delta outside the panel
# ---------------------------------------------------------------------------
def render_delta_summary(delta: Delta) -> tuple[str, str]:
    """Render ``(title, body)`` for the inbox. Never includes artifact TEXT.

    The body names the artifact, the stage, what changed by count, which findings
    the edit resolved and which it introduced, and the two hashes. That is enough
    for a reviewer to decide whether to open the panel and safe to mirror into a
    chat channel, which is what ``approval_items`` rows are for.

    Do NOT pass ``before_text`` / ``after_text`` into an approval body. The whole
    reason ``approval_items`` stores key names and digests is that its rows leave
    the platform; an artifact under review is exactly the CUI that convention
    exists to keep out of Slack.
    """
    title = f"[{delta.stage}] TRUST delta on {delta.artifact_id}"
    counts: dict[str, int] = {o: 0 for o in OPS}
    for span in delta.spans:
        counts[span.get("op", OP_UNCHANGED)] = counts.get(span.get("op", OP_UNCHANGED), 0) + 1
    lines = [
        f"Artifact: {delta.artifact_id}",
        f"Stage: {delta.stage}",
        f"Actor: {delta.actor}",
        f"Rationale: {delta.rationale}",
        (
            "Claims: "
            f"{counts[OP_MODIFIED]} modified, {counts[OP_ADDED]} added, "
            f"{counts[OP_REMOVED]} removed, {counts[OP_UNCHANGED]} unchanged"
        ),
        (
            "Findings: "
            f"{len(delta.findings_before)} before -> {len(delta.findings_after)} after "
            f"({len(delta.findings_resolved)} resolved, "
            f"{len(delta.findings_introduced)} introduced)"
        ),
    ]
    if delta.is_no_op:
        lines.append(
            "NO-OP: the artifact is byte-identical before and after. This override "
            "changed nothing."
        )
    if delta.findings_introduced:
        lines.append(
            f"WARNING: this change INTRODUCED {len(delta.findings_introduced)} "
            "finding(s) that were not present before."
        )
    if delta.supersedes_delta_id:
        lines.append(f"Corrects: {delta.supersedes_delta_id}")
    lines.append(
        "Artifact text is deliberately not shown — it can carry CUI. Review it in "
        f"the delta panel. before={delta.before_hash[:16]} after={delta.after_hash[:16]}"
    )
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# Connection plumbing
# ---------------------------------------------------------------------------
def _connect():
    """Open a connection, or raise :class:`HitlDeltaUnavailable`.

    ``get_connection`` (never a raw driver) so the RLS predicate and the
    ``%s`` → ``?`` translation both apply — ``trust_deltas`` carries a
    ``classification`` column precisely so it is RLS-eligible.
    """
    try:
        from tools.db.storage import get_connection, table_exists
    except Exception as exc:  # noqa: BLE001
        raise HitlDeltaUnavailable(f"storage layer unavailable: {exc}") from exc
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        raise HitlDeltaUnavailable(f"cannot open a connection: {exc}") from exc
    try:
        if not table_exists(conn, TABLE):
            raise HitlDeltaUnavailable(
                f"{TABLE} is missing — run "
                "`python tools/db/migrate.py --up` (migration "
                "20260815063941_trust_hitl_deltas)"
            )
    except HitlDeltaUnavailable:
        _close(conn)
        raise
    except Exception as exc:  # noqa: BLE001
        _close(conn)
        raise HitlDeltaUnavailable(f"cannot inspect {TABLE}: {exc}") from exc
    return conn


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _row_to_delta(row: Any) -> Delta:
    if isinstance(row, dict):
        values = {c: row.get(c) for c in COLUMNS}
    else:
        values = dict(zip(COLUMNS, row))
    kwargs: dict[str, Any] = {}
    for key, value in values.items():
        if key in _JSON_COLUMNS:
            kwargs[key] = _loads(value)
        else:
            kwargs[key] = "" if value is None else str(value)
    return Delta(**kwargs)


def _loads(value: Any) -> list[dict]:
    """Parse a JSON TEXT column, degrading to empty rather than raising.

    A read has no action to fail closed on, and a row whose ``spans`` column was
    truncated is still evidence that the delta happened — surfacing it with an
    empty span list beats hiding the row behind a decode error.
    """
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        out = json.loads(value)
    except (TypeError, ValueError) as exc:
        logger.warning("hitl_delta: could not parse a JSON column: %s", exc)
        return []
    return out if isinstance(out, list) else []


_SELECT = f"SELECT {', '.join(COLUMNS)} FROM {TABLE}"


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def record_delta(
    *,
    artifact_id: str,
    stage: str,
    before_text: str,
    after_text: str,
    rationale: str,
    actor: str = "",
    sources: Optional[dict] = None,
    judge: Optional[Callable] = None,
    findings_before: Optional[list[dict]] = None,
    findings_after: Optional[list[dict]] = None,
    delta: Optional[dict] = None,
    supersedes_delta_id: str = "",
    session_id: str = "",
    inbox: str = DEFAULT_INBOX,
    classification: str = DEFAULT_CLASSIFICATION,
    delta_id: Optional[str] = None,
    raise_ask: bool = True,
) -> Delta:
    """Persist one delta as evidence and raise the ask that settles it.

    ``rationale`` is mandatory and must be non-empty. An override with no stated
    reason is the record this module was built to replace; refusing it here is
    what makes ``force_reason`` enforceable at the call sites trust-hitl-03 wires.

    ``delta``: a precomputed :func:`compute_delta` result. Omit it and one is
    computed from the two texts. ``findings_before`` / ``findings_after``
    override whatever the computation produced — a ``force_*`` call site already
    holds the exact findings its gate refused on and should pass those rather
    than have them re-derived slightly differently.

    ``raise_ask=False`` records the evidence without queueing an approval item.
    For a delta that is already settled by construction (an after-the-fact
    reconstruction, a correction of a row whose ask was answered long ago) — not
    a way to skip review, because a delta with no inbox row is reported by
    :func:`pending_deltas` as still pending either way.

    Order is EVIDENCE, THEN ASK, and it is not arbitrary. The ``approval_items``
    id is minted here and passed to ``enqueue`` because an append-only row cannot
    be back-filled. If the enqueue then fails, a delta survives with no inbox row
    and shows up as pending — a human sees an unanswered ask. The reverse
    ordering would leave an ask with no evidence: a reviewer asked to approve a
    change they cannot see, which is the defect, restored.

    Raises :class:`HitlDeltaUnavailable` if the evidence cannot be persisted. An
    override whose delta was never written must not be mistaken for a recorded
    one.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    if not artifact_id:
        raise ValueError("artifact_id is required")
    if not rationale or not str(rationale).strip():
        raise ValueError(
            "rationale is required — an override that does not say why is the "
            "record this module replaces"
        )

    computed = delta if delta is not None else compute_delta(
        before_text, after_text, sources=sources, judge=judge
    )
    now = _now()
    record = Delta(
        delta_id=delta_id or f"td-{uuid.uuid4().hex[:16]}",
        artifact_id=artifact_id,
        stage=stage,
        before_hash=computed.get("before_hash") or _sha256(before_text or ""),
        after_hash=computed.get("after_hash") or _sha256(after_text or ""),
        before_text=before_text or "",
        after_text=after_text or "",
        findings_before=list(
            findings_before if findings_before is not None
            else computed.get("findings_before") or []
        ),
        findings_after=list(
            findings_after if findings_after is not None
            else computed.get("findings_after") or []
        ),
        spans=list(computed.get("spans") or []),
        actor=resolve_actor(actor),
        rationale=str(rationale).strip(),
        approval_item_id=f"ai-{uuid.uuid4().hex[:16]}" if raise_ask else "",
        supersedes_delta_id=supersedes_delta_id or "",
        session_id=session_id or _session_id(),
        classification=classification or DEFAULT_CLASSIFICATION,
        created_at=now,
    )

    conn = _connect()
    try:
        placeholders = ", ".join(["%s"] * len(COLUMNS))
        conn.execute(
            f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({placeholders})",
            tuple(_column_value(record, c) for c in COLUMNS),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
        raise HitlDeltaUnavailable(
            f"could not record delta {record.delta_id}: {exc}"
        ) from exc
    finally:
        _close(conn)

    logger.info(
        "hitl_delta: recorded %s for %s (%s) — %d changed span(s), %d finding(s) "
        "resolved, %d introduced%s",
        record.delta_id, record.artifact_id, record.stage,
        len(record.changed_spans), len(record.findings_resolved),
        len(record.findings_introduced),
        " [NO-OP]" if record.is_no_op else "",
    )

    if raise_ask:
        _raise_ask(record, inbox=inbox)
    return record


def _column_value(record: Delta, column: str) -> Any:
    value = getattr(record, column)
    if column in _JSON_COLUMNS:
        return json.dumps(value, default=str)
    return value


def _raise_ask(record: Delta, *, inbox: str) -> None:
    """Queue the approval item that carries this delta's disposition.

    Best-effort by design: the evidence is already durable, and a delta with no
    inbox row is reported as PENDING by :func:`pending_deltas`, so a failure here
    leaves the ask visible rather than silently approved. It is logged at ERROR
    because a reviewer will not have been notified through their channel.
    """
    title, body = render_delta_summary(record)
    try:
        from tools.agent_runtime.approval_inbox import enqueue

        enqueue(
            tool_name=f"trust_delta:{record.stage}",
            tier="trust_delta",
            title=title,
            body=body,
            origin=INBOX_ORIGIN,
            session_id=record.session_id,
            inbox=inbox or DEFAULT_INBOX,
            tool_input=None,
            rule="hitl_delta",
            classification=record.classification,
            item_id=record.approval_item_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "hitl_delta: %s recorded but its approval item %s was NOT queued (%s) — "
            "it stays pending and unnotified",
            record.delta_id, record.approval_item_id, exc,
        )


def correct_delta(
    delta_id: str,
    *,
    before_text: str,
    after_text: str,
    rationale: str,
    **kwargs: Any,
) -> Delta:
    """Correct a recorded delta by APPENDING a successor that supersedes it.

    The predecessor is not touched — not its text, not its findings, not even a
    "superseded" flag — because a reviewer may already hold the document it
    describes. Whether a row has been superseded is derived at read time by
    :func:`revision_chain`, the same rule ``sbom_revision.apply_correction``
    follows for ``supersedes_sbom_id``.

    ``artifact_id`` and ``stage`` default to the predecessor's; pass them to
    override. Raises ``ValueError`` if the predecessor does not exist — a
    correction of nothing is a new delta, and saying so is not this function's
    call to make.
    """
    predecessor = get(delta_id)
    if predecessor is None:
        raise ValueError(
            f"no delta {delta_id!r} to correct — record it as a new delta instead"
        )
    kwargs.setdefault("artifact_id", predecessor.artifact_id)
    kwargs.setdefault("stage", predecessor.stage)
    kwargs.setdefault("classification", predecessor.classification)
    return record_delta(
        before_text=before_text,
        after_text=after_text,
        rationale=rationale,
        supersedes_delta_id=delta_id,
        **kwargs,
    )


def settle_delta(
    delta_id: str,
    *,
    approved: bool,
    actor: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Answer the ask attached to a delta.

    The disposition is written to ``approval_items`` — never here. This function
    issues no UPDATE against ``trust_deltas``; ``approval_inbox.resolve`` moves
    the mutable row and writes the permanent ``agent_approval_log`` entry through
    the existing ``record_decision``.

    Returns ``{delta_id, settled, state, resolution, resolved_by, reason}``.
    ``settled`` is False when the delta is unknown, was recorded with no ask, or
    its item was already terminal — a repeated settlement is an idempotent no-op,
    not a second contradictory decision.
    """
    record = get(delta_id)
    if record is None:
        return {"delta_id": delta_id, "settled": False, "reason": "no such delta"}
    if not record.approval_item_id:
        return {
            "delta_id": delta_id,
            "settled": False,
            "reason": "delta was recorded with no approval item",
        }
    try:
        from tools.agent_runtime.approval_inbox import resolve
    except Exception as exc:  # noqa: BLE001
        return {"delta_id": delta_id, "settled": False, "reason": f"inbox unavailable: {exc}"}

    item = resolve(
        record.approval_item_id,
        approved=approved,
        resolved_by=resolve_actor(actor),
        reason=reason or (
            f"delta {delta_id} approved" if approved else f"delta {delta_id} rejected"
        ),
    )
    if item is None:
        return {
            "delta_id": delta_id,
            "settled": False,
            "reason": "approval item is absent or already settled",
        }
    return {
        "delta_id": delta_id,
        "settled": True,
        "approval_item_id": item.item_id,
        "state": item.state,
        "resolution": item.resolution,
        "resolved_by": item.resolved_by,
        "resolved_at": item.resolved_at,
    }


# ---------------------------------------------------------------------------
# Reads — degrade to empty, never raise
# ---------------------------------------------------------------------------
def get(delta_id: str) -> Optional[Delta]:
    """One delta by id, or ``None``."""
    try:
        conn = _connect()
    except HitlDeltaUnavailable as exc:
        logger.debug("hitl_delta: get(%s) unavailable: %s", delta_id, exc)
        return None
    try:
        row = conn.execute(f"{_SELECT} WHERE delta_id = %s", (delta_id,)).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("hitl_delta: get(%s) failed: %s", delta_id, exc)
        return None
    finally:
        _close(conn)
    return _row_to_delta(row) if row is not None else None


def list_deltas(
    *,
    artifact_id: Optional[str] = None,
    stage: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 200,
) -> list[Delta]:
    """Deltas matching the given filters, newest first."""
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("artifact_id", artifact_id),
        ("stage", stage),
        ("session_id", session_id),
    ):
        if value:
            clauses.append(f"{column} = %s")
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    # LIMIT is interpolated as an int, not bound. The RLS layer prepends its own
    # predicate params to a SELECT, and a trailing bound LIMIT is the one slot
    # whose position that reordering would silently shift. int() makes the
    # interpolation non-injectable.
    sql = f"{_SELECT}{where} ORDER BY created_at DESC LIMIT {int(limit)}"

    try:
        conn = _connect()
    except HitlDeltaUnavailable as exc:
        logger.debug("hitl_delta: list unavailable: %s", exc)
        return []
    try:
        rows = conn.execute(sql, tuple(params)).fetchall() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("hitl_delta: list failed: %s", exc)
        return []
    finally:
        _close(conn)
    return [_row_to_delta(r) for r in rows]


def pending_deltas(
    *,
    artifact_id: Optional[str] = None,
    stage: Optional[str] = None,
    limit: int = 200,
) -> list[Delta]:
    """Deltas still waiting on a human.

    Pending means: not superseded by a correction, and its ``approval_items`` row
    is not terminal. A delta whose item is ABSENT counts as pending — the ask
    failed to queue, or the mutable row was pruned, and in both cases nobody
    answered it. Treating an absent item as settled would turn a failed enqueue
    into a silent approval, which is the same shape as an expiry auto-approving.

    The two tables are joined in Python rather than in SQL because they are
    RLS-eligible independently and a cross-table join under two predicates is how
    a row silently vanishes from a review queue.
    """
    candidates = list_deltas(artifact_id=artifact_id, stage=stage, limit=limit)
    superseded = {d.supersedes_delta_id for d in candidates if d.supersedes_delta_id}

    try:
        from tools.agent_runtime.approval_inbox import STATE_PENDING, get as get_item
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "hitl_delta: approval inbox unavailable (%s) — every unsuperseded "
            "delta is reported pending", exc,
        )
        return [d for d in candidates if d.delta_id not in superseded]

    out: list[Delta] = []
    for record in candidates:
        if record.delta_id in superseded:
            continue
        if not record.approval_item_id:
            out.append(record)
            continue
        item = get_item(record.approval_item_id)
        if item is None or item.state == STATE_PENDING:
            out.append(record)
    return out


def revision_chain(delta_id: str) -> list[Delta]:
    """The correction chain containing ``delta_id``, oldest first.

    Walks back through ``supersedes_delta_id`` to the original and forward to the
    head. "Superseded" is derived here rather than stored, so no predecessor is
    ever written to after the fact.
    """
    record = get(delta_id)
    if record is None:
        return []
    chain = [record]
    seen = {record.delta_id}
    cursor = record
    while cursor.supersedes_delta_id and cursor.supersedes_delta_id not in seen:
        prior = get(cursor.supersedes_delta_id)
        if prior is None:
            break
        chain.insert(0, prior)
        seen.add(prior.delta_id)
        cursor = prior
    cursor = record
    while True:
        successors = [
            d for d in list_deltas(artifact_id=record.artifact_id, limit=500)
            if d.supersedes_delta_id == cursor.delta_id and d.delta_id not in seen
        ]
        if not successors:
            break
        successors.sort(key=lambda d: d.created_at)
        cursor = successors[0]
        chain.append(cursor)
        seen.add(cursor.delta_id)
    return chain


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspect and settle HITL trust deltas (trust-hitl-01)."
    )
    parser.add_argument("--pending", action="store_true", help="deltas awaiting a human")
    parser.add_argument("--list", action="store_true", help="list deltas")
    parser.add_argument("--show", metavar="DELTA_ID", help="show one delta")
    parser.add_argument("--chain", metavar="DELTA_ID", help="show the correction chain")
    parser.add_argument("--settle", metavar="DELTA_ID", help="settle one delta's ask")
    parser.add_argument("--approve", action="store_true", help="with --settle: approve")
    parser.add_argument("--reject", action="store_true", help="with --settle: reject")
    parser.add_argument("--reason", default="", help="with --settle: recorded reason")
    parser.add_argument("--artifact-id", help="filter by artifact")
    parser.add_argument("--stage", help="filter by stage")
    parser.add_argument("--limit", type=int, default=50, help="max rows")
    parser.add_argument(
        "--with-text",
        action="store_true",
        help="include the artifact text (CUI) in the output; omitted by default",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    def emit(payload: Any) -> None:
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(payload)

    def line(record: Delta) -> str:
        flag = " NO-OP" if record.is_no_op else ""
        return (
            f"{record.delta_id}  {record.stage:<10}  {record.artifact_id:<24}  "
            f"{len(record.changed_spans)} changed  "
            f"{len(record.findings_before)}->{len(record.findings_after)} findings{flag}"
        )

    if args.show:
        record = get(args.show)
        if record is None:
            emit({"error": f"no delta {args.show}"})
            return 1
        emit(record.to_dict(with_text=args.with_text))
        return 0

    if args.chain:
        chain = revision_chain(args.chain)
        if not chain:
            emit({"error": f"no delta {args.chain}"})
            return 1
        if args.json:
            emit([d.to_dict(with_text=args.with_text) for d in chain])
        else:
            for d in chain:
                print(line(d))
        return 0

    if args.settle:
        if args.approve == args.reject:
            # Neither or both. An approval must be an explicit, unambiguous act.
            parser.error("--settle requires exactly one of --approve / --reject")
        result = settle_delta(
            args.settle,
            approved=bool(args.approve),
            reason=args.reason,
        )
        emit(result)
        return 0 if result.get("settled") else 1

    if args.pending or args.list:
        records = (
            pending_deltas(
                artifact_id=args.artifact_id, stage=args.stage, limit=args.limit
            )
            if args.pending
            else list_deltas(
                artifact_id=args.artifact_id, stage=args.stage, limit=args.limit
            )
        )
        if args.json:
            emit([d.to_dict(with_text=args.with_text) for d in records])
        else:
            for d in records:
                print(line(d))
            print(f"({len(records)} deltas)")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
