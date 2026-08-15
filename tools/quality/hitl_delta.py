# CUI // SP-CTI
"""The DELTA as the reviewable unit (trust-hitl-01).

Today a ``force_*`` override records THAT a human overrode, never WHAT CHANGED.
``idr_publish_audit``, ``agent_approval_log`` and ``pulse.py``'s mandatory
``force_reason`` all answer *who cleared the gate and what they said about it* —
none of them answers *what text the human actually accepted*. A reviewer
approving a self-corrected draft is approving a diff they have never been shown,
which is unauditable after the fact and indistinguishable from a rubber stamp.

This module makes the diff a first-class, persisted, reviewable object.

## The diff is claim-anchored, never a raw text diff

``citation_grounding.decompose_claims`` returns ORIGINAL-TEXT offsets, and every
guard in the TRUST spine numbers its findings 1-based over exactly that
decomposition (``claim_gate``, ``kg_gate``) — which is what
``self_correct.target_findings`` already relies on. So a delta aligns the
BEFORE claims against the AFTER claims and carries each changed span together
with the findings that were open against it. A line diff would anchor to
nothing: a reviewer would see that a paragraph moved without seeing that the
unsupported claim inside it is the reason.

Alignment is ``difflib.SequenceMatcher`` over the claim strings — stdlib,
deterministic, no LLM, so this runs air-gapped exactly as TRUST stage 1 does.

## Two tables, two lifetimes — do not conflate them

``trust_deltas``    migration 20260815063956. APPEND-ONLY EVIDENCE, registered
                    in ``APPEND_ONLY_TABLES``. A delta is an observation: this
                    text became that text at this instant. Observations are not
                    edited.

``approval_items``  migration 20260809203855. MUTABLE STATE, deliberately NOT
                    append-only. Created ``pending``, moved exactly once to a
                    terminal state. That transition IS an UPDATE.

:func:`record_delta` writes the evidence row and enqueues the ask;
:func:`settle_delta` resolves the ask through the existing
``approval_inbox.resolve`` — which writes the permanent ``agent_approval_log``
row — and then APPENDS a ``settlement`` delta pointing at its predecessor
through ``supersedes_delta_id``. The predecessor is never touched, not even to
flag it settled: that is derived at read time by :func:`delta_chain`, the same
rule ``sbom_revision.apply_correction`` follows and for the identical reason.

## A settlement states its reason

:func:`settle_delta` REFUSES an empty ``rationale``. Not "warns" — refuses, and
returns ``None``. This mirrors ``trust_gate`` invariant 4 and the ``pulse.py``
``force_publish`` precedent that trust-hitl-03 ports to the remaining override
call sites: an unexplained override is unauditable after the fact and
indistinguishable from a bug.

## What this module is not

It does not gate anything, does not call an LLM, and does not decide. It
computes, stores, lists and settles. It never runs ``CREATE TABLE IF NOT
EXISTS``: a missing table raises :class:`DeltaStoreUnavailable` on the write
path, because that statement never ALTERs an existing table and silently
drifting away from the migration is how an INSERT starts failing inside a
swallowed exception (CLAUDE.md). Reads degrade to empty — a missing table is
not evidence that nothing is pending, it is evidence that nothing can be known,
and a read has no action to fail closed on.

CLI::

    python tools/quality/hitl_delta.py --list --json
    python tools/quality/hitl_delta.py --list --disposition pending
    python tools/quality/hitl_delta.py --show <delta_id> --json
    python tools/quality/hitl_delta.py --settle <delta_id> --approve \\
        --rationale "evidence checked against the SSP" --json
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.quality.citation_grounding import decompose_claims  # noqa: E402

logger = get_logger("icdev.quality.hitl_delta")

TABLE = "trust_deltas"

# --- Vocabularies -----------------------------------------------------------
# Single source of truth. Migration 20260815063956 deliberately ships no CHECK
# constraint over these — see its header. Validation happens here, before the
# INSERT.

#: A bounded revise-and-recheck round was accepted (``quality.self_correct``).
STAGE_SELF_CORRECTION = "self_correction"
#: A human cleared a blocking guard with a ``force_*`` (trust-hitl-03).
STAGE_OVERRIDE = "override"
#: A human edited the draft directly.
STAGE_MANUAL_EDIT = "manual_edit"
#: The successor row recording a human's disposition of an earlier delta.
STAGE_SETTLEMENT = "settlement"
DELTA_STAGES = (
    STAGE_SELF_CORRECTION, STAGE_OVERRIDE, STAGE_MANUAL_EDIT, STAGE_SETTLEMENT,
)

DISPOSITION_PENDING = "pending"
DISPOSITION_APPROVED = "approved"
DISPOSITION_DENIED = "denied"
DISPOSITIONS = (DISPOSITION_PENDING, DISPOSITION_APPROVED, DISPOSITION_DENIED)

# Span alignment outcomes. `changed` is a replace, so it carries BOTH texts;
# `removed` and `added` carry one side each.
SPAN_UNCHANGED = "unchanged"
SPAN_CHANGED = "changed"
SPAN_REMOVED = "removed"
SPAN_ADDED = "added"
SPAN_KINDS = (SPAN_UNCHANGED, SPAN_CHANGED, SPAN_REMOVED, SPAN_ADDED)

DEFAULT_CLASSIFICATION = "CUI"

#: The approval_items ``origin`` a delta review is filed under. One of the
#: existing ORIGINS — this is a workflow asking a human to look, which is
#: exactly what that origin already means.
APPROVAL_ORIGIN = "workflow_hitl"
#: The synthetic ``tool_name`` / ``tier`` the ask carries. approval_items has no
#: delta-specific columns and needs none: the ask is a POINTER to the delta, and
#: the evidence lives in trust_deltas.
APPROVAL_TOOL = "trust_delta_review"
APPROVAL_TIER = "review"

# Column order is the live schema's order. The INSERT names every column
# explicitly, so this tuple and migration 20260815063956 must agree — asserted
# by tests/test_hitl_delta.py against the migration's own DDL.
COLUMNS = (
    "delta_id",
    "artifact_id",
    "artifact_type",
    "stage",
    "gate",
    "before_hash",
    "after_hash",
    "before_text",
    "after_text",
    "findings_before",
    "findings_after",
    "findings_before_n",
    "findings_after_n",
    "spans",
    "actor",
    "rationale",
    "disposition",
    "approval_item_id",
    "supersedes_delta_id",
    "session_id",
    "tenant_id",
    "classification",
    "created_at",
)

#: Columns persisted as a JSON string.
_JSON_COLUMNS = ("findings_before", "findings_after", "spans")
#: Columns persisted as an integer.
_INT_COLUMNS = ("findings_before_n", "findings_after_n")


class DeltaStoreUnavailable(RuntimeError):
    """``trust_deltas`` is absent or unreachable.

    Raised by the write path rather than swallowed. A delta that could not be
    persisted must not look like one awaiting review: the reviewable unit simply
    does not exist, and the caller has to treat that as "still blocked".
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
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Delta:
    """One reviewable change. Mirrors a row of ``trust_deltas``."""

    delta_id: str
    artifact_id: str
    stage: str
    before_hash: str = ""
    after_hash: str = ""
    before_text: str = ""
    after_text: str = ""
    findings_before: list = field(default_factory=list)
    findings_after: list = field(default_factory=list)
    spans: list = field(default_factory=list)
    artifact_type: str = ""
    gate: str = ""
    actor: str = ""
    rationale: str = ""
    disposition: str = DISPOSITION_PENDING
    approval_item_id: str = ""
    supersedes_delta_id: str = ""
    session_id: str = ""
    tenant_id: str = ""
    classification: str = DEFAULT_CLASSIFICATION
    created_at: str = ""

    # -- derived, never stored ------------------------------------------------
    @property
    def findings_before_n(self) -> int:
        return len(self.findings_before)

    @property
    def findings_after_n(self) -> int:
        return len(self.findings_after)

    @property
    def net_findings(self) -> int:
        """Negative is an improvement. Stored counts are what the panel sorts on."""
        return self.findings_after_n - self.findings_before_n

    @property
    def is_pending(self) -> bool:
        return self.disposition == DISPOSITION_PENDING

    @property
    def is_noop(self) -> bool:
        """True when nothing actually changed.

        A delta whose hashes match is not a review item — it is an assertion
        that a human looked at nothing. :func:`record_delta` refuses one.
        """
        return self.before_hash == self.after_hash

    @property
    def changed_spans(self) -> list:
        return [s for s in self.spans if s.get("kind") != SPAN_UNCHANGED]

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["findings_before_n"] = self.findings_before_n
        out["findings_after_n"] = self.findings_after_n
        out["net_findings"] = self.net_findings
        out["is_pending"] = self.is_pending
        return out


# ---------------------------------------------------------------------------
# The diff — claim-anchored, deterministic, LLM-free
# ---------------------------------------------------------------------------
def _finding_field(finding: Any, name: str, default: Any = None) -> Any:
    if isinstance(finding, Mapping):
        return finding.get(name, default)
    return getattr(finding, name, default)


def _normalize_findings(findings: Any) -> list[dict]:
    """Adapt guard findings (``Finding`` dataclass or mapping) to plain dicts.

    Anything unrecognised is kept as its ``repr`` rather than dropped: a finding
    this module cannot parse is still a finding, and losing it would make the
    delta understate what was open against the draft.
    """
    out: list[dict] = []
    for finding in findings or []:
        if isinstance(finding, Mapping) or hasattr(finding, "issue"):
            out.append({
                "guard": str(_finding_field(finding, "guard", "") or ""),
                "issue": str(_finding_field(finding, "issue", "finding") or "finding"),
                "severity": str(_finding_field(finding, "severity", "") or ""),
                "item_number": _finding_field(finding, "item_number", "document"),
                "detail": _finding_field(finding, "detail"),
            })
        else:
            out.append({"guard": "", "issue": str(finding), "severity": "",
                        "item_number": "document", "detail": None})
    return out


def _claim_index(value: Any) -> Optional[int]:
    """1-based claim index, or ``None`` for a document-level finding."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _findings_by_claim(findings: Sequence[Mapping]) -> dict[int, list[dict]]:
    """Bucket findings by the 1-based claim index they were reported against."""
    buckets: dict[int, list[dict]] = {}
    for finding in findings:
        index = _claim_index(finding.get("item_number"))
        if index is not None:
            buckets.setdefault(index, []).append(dict(finding))
    return buckets


def document_findings(findings: Sequence[Mapping]) -> list[dict]:
    """Findings that anchor to no claim — the panel lists them separately.

    Dropping them would be the ``self_correct.target_findings`` mistake in
    reverse: ``placeholder_guard`` and ``citation_guard`` report at document
    level, and a panel that only rendered claim-anchored findings would show a
    blocked draft with nothing wrong with it.
    """
    return [dict(f) for f in findings if _claim_index(f.get("item_number")) is None]


def align_claims(before: str, after: str) -> list[tuple[str, Optional[int], Optional[int]]]:
    """Align BEFORE claims to AFTER claims as ``(kind, before_ix, after_ix)``.

    Indices are 1-based to match the ``item_number`` vocabulary every TRUST
    guard reports in, so a caller can join an alignment entry straight onto the
    findings that were open against it. ``None`` on either side means the claim
    exists only on the other one.

    ``difflib.SequenceMatcher`` over the claim strings: stdlib, deterministic,
    no LLM — this must run air-gapped, exactly as TRUST stage 1 does.
    """
    before_claims = [c for c, _s, _e in decompose_claims(before)]
    after_claims = [c for c, _s, _e in decompose_claims(after)]
    matcher = difflib.SequenceMatcher(a=before_claims, b=after_claims, autojunk=False)

    out: list[tuple[str, Optional[int], Optional[int]]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                out.append((SPAN_UNCHANGED, i1 + offset + 1, j1 + offset + 1))
        elif tag == "replace":
            # Pair positionally as far as both sides go; the tail of the longer
            # side is a pure removal or addition. Pairing is what lets the panel
            # render a true side-by-side row rather than a delete followed by an
            # unrelated-looking insert.
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                out.append((SPAN_CHANGED, i1 + offset + 1, j1 + offset + 1))
            for offset in range(paired, i2 - i1):
                out.append((SPAN_REMOVED, i1 + offset + 1, None))
            for offset in range(paired, j2 - j1):
                out.append((SPAN_ADDED, None, j1 + offset + 1))
        elif tag == "delete":
            for offset in range(i2 - i1):
                out.append((SPAN_REMOVED, i1 + offset + 1, None))
        elif tag == "insert":
            for offset in range(j2 - j1):
                out.append((SPAN_ADDED, None, j1 + offset + 1))
    return out


def compute_delta(
    before_text: str,
    after_text: str,
    *,
    artifact_id: str,
    stage: str = STAGE_SELF_CORRECTION,
    findings_before: Any = None,
    findings_after: Any = None,
    artifact_type: str = "",
    gate: str = "",
    actor: str = "",
    rationale: str = "",
    tenant_id: str = "",
    classification: str = DEFAULT_CLASSIFICATION,
    delta_id: Optional[str] = None,
) -> Delta:
    """Build a :class:`Delta` from two versions of an artifact. Pure — no DB.

    Every changed span carries the findings that were open against it BEFORE and
    the findings still open against it AFTER, so the panel can show a reviewer
    the claim, the defect it carried, and whether the revision actually resolved
    it — rather than a diff and a separate list they have to correlate by eye.

    Args:
        before_text / after_text: the two versions. Both are stored; see the
            migration header for why this table holds the artifact itself.
        artifact_id: what changed. The panel groups a chain by this.
        stage: one of :data:`DELTA_STAGES`.
        findings_before / findings_after: guard findings — ``TrustVerdict``
            ``Finding`` objects or the mapping form both work. These are what
            make the delta reviewable rather than merely visible.
        gate: the blocking guard, when there was one (``TrustVerdict.gate``).

    Raises ``ValueError`` for an unknown ``stage`` — a typo'd stage that
    silently persisted would make the row invisible to a panel filtering on the
    vocabulary.
    """
    if stage not in DELTA_STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {DELTA_STAGES}")
    if not artifact_id:
        raise ValueError("artifact_id is required")

    before_text = before_text or ""
    after_text = after_text or ""
    before = _normalize_findings(findings_before)
    after = _normalize_findings(findings_after)

    before_claims = decompose_claims(before_text)
    after_claims = decompose_claims(after_text)
    before_buckets = _findings_by_claim(before)
    after_buckets = _findings_by_claim(after)

    spans: list[dict] = []
    for kind, b_ix, a_ix in align_claims(before_text, after_text):
        b_claim, b_start, b_end = (
            before_claims[b_ix - 1] if b_ix is not None else ("", None, None)
        )
        a_claim, a_start, a_end = (
            after_claims[a_ix - 1] if a_ix is not None else ("", None, None)
        )
        spans.append({
            "kind": kind,
            "before_index": b_ix,
            "after_index": a_ix,
            "before_claim": b_claim,
            "after_claim": a_claim,
            "before_start": b_start,
            "before_end": b_end,
            "after_start": a_start,
            "after_end": a_end,
            "findings_before": before_buckets.get(b_ix, []) if b_ix else [],
            "findings_after": after_buckets.get(a_ix, []) if a_ix else [],
        })

    return Delta(
        delta_id=delta_id or f"td-{uuid.uuid4().hex[:16]}",
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        stage=stage,
        gate=gate,
        before_hash=_sha256(before_text),
        after_hash=_sha256(after_text),
        before_text=before_text,
        after_text=after_text,
        findings_before=before,
        findings_after=after,
        spans=spans,
        actor=actor,
        rationale=rationale,
        disposition=DISPOSITION_PENDING,
        session_id=_session_id(),
        tenant_id=tenant_id,
        classification=classification or DEFAULT_CLASSIFICATION,
        created_at=_now(),
    )


def delta_from_self_correction(
    report: Mapping[str, Any],
    *,
    artifact_id: str,
    original_text: str,
    findings_before: Any = None,
    findings_after: Any = None,
    **kwargs: Any,
) -> Optional[Delta]:
    """Build a delta from a :func:`tools.quality.self_correct.self_correct` report.

    Returns ``None`` when the loop changed nothing — ``status`` ``clean`` /
    ``unchanged`` / ``unavailable`` / ``unmeasurable``, or a text that came back
    byte-identical. There is no reviewable unit in that case, and manufacturing
    an empty one would put a no-op in a human's queue.

    ``self_correct`` reports counts, not the finding objects themselves, so a
    caller that wants per-claim findings on the delta passes them in; without
    them the delta still renders the claim-level diff, just without the defect
    annotations.
    """
    revised_text = str(report.get("text") or "")
    if not report.get("revised") or revised_text == (original_text or ""):
        return None
    return compute_delta(
        original_text,
        revised_text,
        artifact_id=artifact_id,
        stage=STAGE_SELF_CORRECTION,
        findings_before=findings_before,
        findings_after=findings_after,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Connection plumbing
# ---------------------------------------------------------------------------
def _connect():
    """Open a connection, or raise :class:`DeltaStoreUnavailable`.

    ``get_connection`` (never a raw driver) so the RLS predicate and the
    ``%s`` → ``?`` translation both apply — ``trust_deltas`` carries
    ``tenant_id`` + ``classification`` precisely so it is RLS-eligible.
    """
    try:
        from tools.db.storage import get_connection, table_exists
    except Exception as exc:  # noqa: BLE001
        raise DeltaStoreUnavailable(f"storage layer unavailable: {exc}") from exc
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        raise DeltaStoreUnavailable(f"cannot open a connection: {exc}") from exc
    try:
        if not table_exists(conn, TABLE):
            raise DeltaStoreUnavailable(
                f"{TABLE} is missing — run `python tools/db/migrate.py --up` "
                "(migration 20260815063956_trust_hitl_deltas)"
            )
    except DeltaStoreUnavailable:
        _close(conn)
        raise
    except Exception as exc:  # noqa: BLE001
        _close(conn)
        raise DeltaStoreUnavailable(f"cannot inspect {TABLE}: {exc}") from exc
    return conn


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _decode_json(value: Any) -> list:
    """Decode a JSON column, tolerating an already-decoded PG ``jsonb`` value."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _row_to_delta(row: Any) -> Delta:
    if isinstance(row, dict):
        values = {c: row.get(c) for c in COLUMNS}
    else:
        values = dict(zip(COLUMNS, row))
    kwargs: dict[str, Any] = {}
    for column, value in values.items():
        if column in _INT_COLUMNS:
            # Derived from the lists on the dataclass — never passed to __init__.
            continue
        if column in _JSON_COLUMNS:
            kwargs[column] = _decode_json(value)
        else:
            kwargs[column] = "" if value is None else str(value)
    return Delta(**kwargs)


_SELECT = f"SELECT {', '.join(COLUMNS)} FROM {TABLE}"


def _row_values(delta: Delta) -> tuple:
    out: list[Any] = []
    for column in COLUMNS:
        if column in _JSON_COLUMNS:
            out.append(json.dumps(getattr(delta, column) or [], default=str))
        elif column in _INT_COLUMNS:
            out.append(int(getattr(delta, column)))
        else:
            out.append(getattr(delta, column))
    return tuple(out)


def _insert(conn, delta: Delta) -> None:
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    conn.execute(
        f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({placeholders})",
        _row_values(delta),
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def record_delta(delta: Delta, *, enqueue_approval: bool = True) -> Delta:
    """Persist ``delta`` as append-only evidence and queue the human ask.

    The ask is enqueued FIRST and the evidence row written second, so
    ``approval_item_id`` is known before the INSERT and this module never has to
    UPDATE ``trust_deltas``. That ordering is the whole reason the table can be
    honestly append-only: writing the row first would leave the foreign key to
    be filled in afterwards, and an UPDATE — however narrow — on a table
    registered in ``APPEND_ONLY_TABLES`` is an exception that the next change
    widens. The residual risk of this order is a queued ask whose delta failed
    to insert, and that is CANCELLED below rather than left as a dead link in a
    reviewer's inbox.

    A delta whose ask could not be queued is still recorded, with an empty
    ``approval_item_id``. It is complete evidence and the panel still lists it;
    the gap is logged rather than silent.

    Refuses a no-op (``before_hash == after_hash``): asking a human to review
    nothing trains them to approve without looking, which is the failure this
    whole subsystem exists to prevent.

    Raises :class:`DeltaStoreUnavailable` if the row cannot be persisted.
    """
    if delta.stage not in DELTA_STAGES:
        raise ValueError(f"unknown stage {delta.stage!r}; expected one of {DELTA_STAGES}")
    if delta.disposition not in DISPOSITIONS:
        raise ValueError(
            f"unknown disposition {delta.disposition!r}; expected one of {DISPOSITIONS}"
        )
    if delta.is_noop:
        raise ValueError(
            f"delta {delta.delta_id} changes nothing (before_hash == after_hash); "
            "there is nothing to review"
        )
    if not delta.created_at:
        delta.created_at = _now()
    if not delta.session_id:
        delta.session_id = _session_id()

    if enqueue_approval and delta.is_pending and not delta.approval_item_id:
        delta.approval_item_id = _enqueue_ask(delta)

    conn = _connect()
    try:
        _insert(conn, delta)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
        _cancel_ask(delta.approval_item_id, delta.delta_id)
        raise DeltaStoreUnavailable(
            f"could not record delta {delta.delta_id}: {exc}"
        ) from exc
    finally:
        _close(conn)

    logger.info(
        "hitl_delta: recorded %s for %s (%s, %d -> %d findings)",
        delta.delta_id, delta.artifact_id, delta.stage,
        delta.findings_before_n, delta.findings_after_n,
    )
    return delta


def _enqueue_ask(delta: Delta) -> str:
    """Queue the human ask in the mutable inbox. Returns '' if it could not be.

    Never raises: the evidence is already durable, and a delta with no inbox row
    is still reviewable from the panel. Logged at warning so the gap is visible
    rather than silent.
    """
    try:
        from tools.agent_runtime.approval_inbox import enqueue

        item = enqueue(
            tool_name=APPROVAL_TOOL,
            tier=APPROVAL_TIER,
            title=f"[DELTA] {delta.artifact_id} ({delta.stage})",
            origin=APPROVAL_ORIGIN,
            session_id=delta.session_id,
            body=render_ask(delta),
            rule=delta.gate or "trust_delta",
            classification=delta.classification,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "hitl_delta: %s recorded but NOT queued for approval: %s",
            delta.delta_id, exc,
        )
        return ""
    return item.item_id


def render_ask(delta: Delta) -> str:
    """The ask's body — counts and a pointer, never the draft text.

    ``approval_items`` rows are mirrored out to Slack / Teams / Telegram / email,
    so this deliberately carries NO artifact text: that is the same convention
    ``approval_inbox.render_summary`` enforces for tool arguments, and a drafted
    artifact is exactly the CUI it exists to keep out of a chat channel. The
    reviewer follows the link and reads the diff in the panel, behind auth.
    """
    lines = [
        f"Artifact: {delta.artifact_id}",
        f"Stage: {delta.stage}",
        f"Blocking gate: {delta.gate or '(none)'}",
        f"Findings: {delta.findings_before_n} -> {delta.findings_after_n}",
        f"Claims changed: {len(delta.changed_spans)}",
        "",
        "The draft text is deliberately not shown here — it can carry CUI. "
        f"Review the side-by-side diff at /delta-review?delta_id={delta.delta_id}",
    ]
    return "\n".join(lines)


def _cancel_ask(item_id: str, delta_id: str) -> None:
    """Withdraw an ask whose delta failed to persist.

    Without this, the enqueue-then-insert order would leave a reviewer holding
    an item pointing at a delta that does not exist — and a dead link in an
    approval queue is worse than a missing one, because a reviewer who cannot
    see the diff still has an Approve button.
    """
    if not item_id:
        return
    try:
        from tools.agent_runtime.approval_inbox import cancel

        cancel(item_id, reason=f"delta {delta_id} could not be recorded")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "hitl_delta: orphaned approval item %s (delta %s failed to record "
            "and the item could not be cancelled): %s",
            item_id, delta_id, exc,
        )


def settle_delta(
    delta_id: str,
    *,
    approved: bool,
    actor: str = "",
    rationale: str,
) -> Optional[Delta]:
    """Record a human's disposition of ``delta_id``.

    Three things happen, in this order, and the order is the point:

    1. The mutable ask in ``approval_items`` moves ``pending`` → ``resolved``
       via the existing :func:`tools.agent_runtime.approval_inbox.resolve`,
       which writes the permanent ``agent_approval_log`` row. That UPDATE is
       conditional on ``state = 'pending'``, so two racing reviewers cannot both
       settle the same delta.
    2. A ``settlement`` delta is APPENDED, pointing at its predecessor through
       ``supersedes_delta_id``.
    3. The predecessor is left EXACTLY as written. Whether it has been settled
       is derived at read time by :func:`delta_chain` — the
       ``sbom_revision.apply_correction`` rule.

    ``rationale`` is MANDATORY. An empty one returns ``None`` and settles
    nothing, mirroring ``trust_gate`` invariant 4: an unexplained disposition is
    unauditable after the fact and indistinguishable from a bug.

    Returns the appended settlement delta, or ``None`` when the delta is absent,
    already settled, or the rationale is empty.
    """
    rationale = (rationale or "").strip()
    if not rationale:
        logger.warning(
            "hitl_delta: refusing to settle %s — no rationale given", delta_id
        )
        return None

    original = get_delta(delta_id)
    if original is None:
        logger.info("hitl_delta: %s not found", delta_id)
        return None
    if not original.is_pending:
        logger.info(
            "hitl_delta: %s is already %s — nothing to settle",
            delta_id, original.disposition,
        )
        return None
    if get_settlement(delta_id) is not None:
        # Belt and braces: the ask may have been resolved out of band (an
        # expiry sweep, a CLI), leaving the predecessor's own disposition
        # column stale. A second settlement row would give the panel two
        # contradictory answers.
        logger.info("hitl_delta: %s already has a settlement row", delta_id)
        return None

    disposition = DISPOSITION_APPROVED if approved else DISPOSITION_DENIED
    actor = actor or ""

    # 1. Settle the mutable ask. Not fatal if it is absent — a delta recorded
    #    while the inbox was unavailable still deserves a reviewable
    #    disposition, and the settlement row below IS the evidence either way.
    if original.approval_item_id:
        try:
            from tools.agent_runtime.approval_inbox import resolve

            item = resolve(
                original.approval_item_id,
                approved=approved,
                resolved_by=actor,
                reason=rationale,
            )
            if item is None:
                logger.warning(
                    "hitl_delta: approval item %s for %s was absent or already settled",
                    original.approval_item_id, delta_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "hitl_delta: could not settle approval item %s: %s",
                original.approval_item_id, exc,
            )

    # 2. APPEND the successor. The settlement's before/after are the SAME two
    #    texts the reviewer was shown — it records a decision about that diff,
    #    not a further change to the artifact.
    settlement = Delta(
        delta_id=f"td-{uuid.uuid4().hex[:16]}",
        artifact_id=original.artifact_id,
        artifact_type=original.artifact_type,
        stage=STAGE_SETTLEMENT,
        gate=original.gate,
        before_hash=original.before_hash,
        after_hash=original.after_hash,
        before_text=original.before_text,
        after_text=original.after_text,
        findings_before=original.findings_before,
        findings_after=original.findings_after,
        spans=original.spans,
        actor=actor,
        rationale=rationale,
        disposition=disposition,
        approval_item_id=original.approval_item_id,
        supersedes_delta_id=original.delta_id,
        session_id=_session_id(),
        tenant_id=original.tenant_id,
        classification=original.classification,
        created_at=_now(),
    )

    conn = _connect()
    try:
        _insert(conn, settlement)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        raise DeltaStoreUnavailable(
            f"could not append settlement for {delta_id}: {exc}"
        ) from exc
    finally:
        _close(conn)

    logger.info(
        "hitl_delta: %s settled %s by %s (successor %s)",
        delta_id, disposition, actor or "(unattributed)", settlement.delta_id,
    )
    return settlement


# ---------------------------------------------------------------------------
# Reads — degrade to empty, never raise
# ---------------------------------------------------------------------------
def _query(sql: str, params: Iterable[Any] = ()) -> list[Delta]:
    try:
        conn = _connect()
    except DeltaStoreUnavailable as exc:
        logger.debug("hitl_delta: read unavailable: %s", exc)
        return []
    try:
        rows = conn.execute(sql, tuple(params)).fetchall() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("hitl_delta: read failed: %s", exc)
        return []
    finally:
        _close(conn)
    return [_row_to_delta(r) for r in rows]


def get_delta(delta_id: str) -> Optional[Delta]:
    """One delta by id, or ``None``."""
    found = _query(f"{_SELECT} WHERE delta_id = %s", (delta_id,))
    return found[0] if found else None


def get_settlement(delta_id: str) -> Optional[Delta]:
    """The settlement row that supersedes ``delta_id``, if one has been written.

    This is how "has it been settled" is DERIVED rather than stored on the
    predecessor — the append-only rule.
    """
    found = _query(
        f"{_SELECT} WHERE supersedes_delta_id = %s AND stage = %s "
        f"ORDER BY created_at ASC",
        (delta_id, STAGE_SETTLEMENT),
    )
    return found[0] if found else None


def list_deltas(
    *,
    disposition: Optional[str] = None,
    artifact_id: Optional[str] = None,
    stage: Optional[str] = None,
    limit: int = 200,
) -> list[Delta]:
    """Deltas matching the given filters, newest first."""
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("disposition", disposition),
        ("artifact_id", artifact_id),
        ("stage", stage),
    ):
        if value:
            clauses.append(f"{column} = %s")
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    # LIMIT is interpolated as an int, not bound. The RLS layer prepends its own
    # predicate params to a SELECT, and a trailing bound LIMIT is the one slot
    # whose position that reordering would silently shift. int() makes the
    # interpolation non-injectable.
    return _query(f"{_SELECT}{where} ORDER BY created_at DESC LIMIT {int(limit)}", params)


def pending_deltas(*, artifact_id: Optional[str] = None, limit: int = 200) -> list[Delta]:
    """Deltas still waiting for a human.

    Filters out any whose settlement row exists but whose own ``disposition``
    column is stale — the predecessor is never rewritten, so "pending" on the
    row is a claim about the moment it was written, and the successor is the
    authority. Reading the column alone is how a settled delta reappears in the
    queue forever.
    """
    candidates = list_deltas(
        disposition=DISPOSITION_PENDING, artifact_id=artifact_id, limit=limit
    )
    if not candidates:
        return []
    settled = _settled_predecessor_ids([d.delta_id for d in candidates])
    return [d for d in candidates if d.delta_id not in settled]


def _settled_predecessor_ids(delta_ids: Sequence[str]) -> set[str]:
    """Which of ``delta_ids`` already have a settlement successor.

    One query, not one per candidate: the panel lists the whole pending queue on
    every page load.
    """
    ids = [d for d in delta_ids if d]
    if not ids:
        return set()
    placeholders = ", ".join(["%s"] * len(ids))
    rows = _query(
        f"{_SELECT} WHERE stage = %s AND supersedes_delta_id IN ({placeholders})",
        [STAGE_SETTLEMENT, *ids],
    )
    return {r.supersedes_delta_id for r in rows if r.supersedes_delta_id}


def delta_chain(artifact_id: str, *, limit: int = 200) -> list[dict]:
    """Every delta for one artifact, oldest first, with derived settlement state.

    Each entry is ``{delta, settlement, settled}``. ``settled`` is DERIVED from
    the presence of a successor — it is never read off the predecessor's own
    ``disposition``, because that column records what was true when the row was
    written and nothing ever updates it. Same shape and same reasoning as
    ``sbom_revision.revision_chain``.
    """
    rows = sorted(
        list_deltas(artifact_id=artifact_id, limit=limit),
        key=lambda d: d.created_at,
    )
    settlements = {
        d.supersedes_delta_id: d
        for d in rows
        if d.stage == STAGE_SETTLEMENT and d.supersedes_delta_id
    }
    out: list[dict] = []
    for delta in rows:
        if delta.stage == STAGE_SETTLEMENT:
            continue
        successor = settlements.get(delta.delta_id)
        out.append({
            "delta": delta,
            "settlement": successor,
            "settled": successor is not None,
        })
    return out


def summary(*, limit: int = 500) -> dict[str, Any]:
    """Counts for the panel's stat row.

    ``telemetry_available`` is False when the table cannot be reached, so an
    unmigrated database reads as "nothing can be known" rather than as a
    confident zero — the ``capability_consumption`` discipline.
    """
    try:
        _close(_connect())
        available = True
    except DeltaStoreUnavailable:
        available = False

    if not available:
        return {
            "telemetry_available": False, "total": 0, "pending": 0,
            "approved": 0, "denied": 0, "by_stage": {},
        }

    rows = list_deltas(limit=limit)
    settled = _settled_predecessor_ids(
        [d.delta_id for d in rows if d.disposition == DISPOSITION_PENDING]
    )
    counts = {DISPOSITION_PENDING: 0, DISPOSITION_APPROVED: 0, DISPOSITION_DENIED: 0}
    by_stage: dict[str, int] = {}
    for delta in rows:
        if delta.stage == STAGE_SETTLEMENT:
            counts[delta.disposition] = counts.get(delta.disposition, 0) + 1
            continue
        by_stage[delta.stage] = by_stage.get(delta.stage, 0) + 1
        if delta.disposition == DISPOSITION_PENDING and delta.delta_id not in settled:
            counts[DISPOSITION_PENDING] += 1
    return {
        "telemetry_available": True,
        "total": len(rows),
        "pending": counts[DISPOSITION_PENDING],
        "approved": counts[DISPOSITION_APPROVED],
        "denied": counts[DISPOSITION_DENIED],
        "by_stage": by_stage,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspect and settle TRUST HITL deltas (trust-hitl-01)."
    )
    parser.add_argument("--list", action="store_true", help="list deltas")
    parser.add_argument("--show", metavar="DELTA_ID", help="show one delta")
    parser.add_argument("--chain", metavar="ARTIFACT_ID", help="full chain for an artifact")
    parser.add_argument("--settle", metavar="DELTA_ID", help="settle one pending delta")
    parser.add_argument("--approve", action="store_true", help="with --settle: approve")
    parser.add_argument("--deny", action="store_true", help="with --settle: deny")
    parser.add_argument("--rationale", default="", help="with --settle: MANDATORY reason")
    parser.add_argument("--actor", default="", help="with --settle: who decided")
    parser.add_argument("--disposition", help="with --list: filter by disposition")
    parser.add_argument("--stage", help="with --list: filter by stage")
    parser.add_argument("--summary", action="store_true", help="counts for the panel")
    parser.add_argument("--limit", type=int, default=50, help="with --list: max rows")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    def emit(payload: Any) -> None:
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(payload)

    if args.show:
        delta = get_delta(args.show)
        if delta is None:
            emit({"error": f"no delta {args.show}"})
            return 1
        emit(delta.to_dict())
        return 0

    if args.chain:
        chain = [
            {
                "delta": entry["delta"].to_dict(),
                "settlement": entry["settlement"].to_dict() if entry["settlement"] else None,
                "settled": entry["settled"],
            }
            for entry in delta_chain(args.chain)
        ]
        emit(chain)
        return 0

    if args.settle:
        if args.approve == args.deny:
            # Neither or both. A disposition must be an explicit, unambiguous act.
            parser.error("--settle requires exactly one of --approve / --deny")
        if not args.rationale.strip():
            parser.error("--settle requires a non-empty --rationale")
        settlement = settle_delta(
            args.settle,
            approved=bool(args.approve),
            actor=args.actor,
            rationale=args.rationale,
        )
        if settlement is None:
            emit({"error": f"{args.settle} is absent, already settled, or had no rationale"})
            return 1
        emit(settlement.to_dict())
        return 0

    if args.summary:
        emit(summary())
        return 0

    if args.list:
        deltas = list_deltas(
            disposition=args.disposition, stage=args.stage, limit=args.limit
        )
        if args.json:
            emit([d.to_dict() for d in deltas])
        else:
            for d in deltas:
                print(
                    f"{d.delta_id}  {d.disposition:<9}  {d.stage:<16}  "
                    f"{d.findings_before_n}->{d.findings_after_n}  {d.artifact_id}"
                )
            print(f"({len(deltas)} deltas)")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
