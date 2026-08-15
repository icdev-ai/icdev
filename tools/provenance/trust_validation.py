#!/usr/bin/env python3
# CUI // SP-CTI
"""TRUST validation records — a gate verdict made anchorable (trust-anchor-02).

A ``force_*`` override tells you a human let an artifact past a TRUST gate. It
does not let anyone later prove *which* artifact, under *which* findings, after
*which* chain of edits, and on *whose* authority — the audit row is local, and a
local row is exactly what someone with write access to the database would edit.

This module composes those four facts into one Merkle leaf::

    leaf = sha256(artifact_hash | findings_hash | delta_chain_hash | approver)

and registers it in ``source_citation_registry`` as ``citation_type =
'trust_validation'``. From there the EXISTING 30-minute
``tools/genesis/reflexes/govchain_anchor.py`` reflex picks it up:
``ChainAnchor.periodic_anchor`` sweeps ``merkle_root IS NULL`` and batches
whatever it finds. No new reflex, no new schedule, no new table — which is the
point. A capability that ships with its own dormant reflex is this platform's
signature defect, and the fix is to land on a wheel that is already turning.

The four components
-------------------
``artifact_hash``     SHA-256 of the exact bytes that were published. Binds the
                      record to one artifact and makes "the approved text was
                      later swapped" detectable.
``findings_hash``     SHA-256 over the canonicalised finding set the approver was
                      shown. Binds the record to what the human actually saw —
                      an override of three defects must not later read as an
                      override of none.
``delta_chain_hash``  SHA-256 fold over this artifact's ``trust_deltas`` rows.
                      Binds the record to the edit history behind it, so a delta
                      inserted, removed or rewritten after the fact changes the
                      leaf and breaks the anchor.
``approver``          Who decided. A leaf without an actor is not evidence of a
                      decision.

Why the leaf is recomputed rather than trusted
----------------------------------------------
``source_doc`` carries the four components as JSON, so
:func:`recompute_leaf` can rebuild the leaf from them at anchor time.
``ChainAnchor`` refuses to anchor a row whose stored ``source_hash`` disagrees
with its own components. Anchoring a leaf nobody re-derived would put a
tamper-evident wrapper around an unverified value — worse than no anchor,
because it reads as proof.

Empty is not unknown
--------------------
An artifact with no recorded deltas yields :data:`EMPTY_HASH` — the fold over an
empty sequence, a well-defined value. An artifact whose ``trust_deltas`` table
could not be READ yields :class:`DeltaChainUnavailable`, and registration
refuses. Collapsing the second into the first would let an unreadable evidence
chain hash identically to an empty one, which is the "unmeasured scored as
clean" failure the whole TRUST framework exists to prevent.

Usage:
    from tools.provenance.trust_validation import record_validation

    record = record_validation(
        artifact_id=session_id,
        artifact_text=doc_text,
        findings=verdict_findings,
        approver=reviewer,
    )
    # record["registry_id"] is now an unanchored citation; the govchain reflex
    # anchors it within 30 minutes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("provenance.trust_validation")

#: The citation type these records are registered under. Must be in
#: CITATION_TYPES and in the shipped CHECK constraint — migration
#: 20260815095725_trust_validation_citation_type.
CITATION_TYPE = "trust_validation"

#: ``source_table`` on the registry row. Points at the evidence table the
#: delta_chain_hash folds, which is where a reader goes next.
SOURCE_TABLE = "trust_deltas"

#: The leaf recipe's field separator, and the order the fields appear in.
LEAF_SEPARATOR = "|"
LEAF_FIELDS = ("artifact_hash", "findings_hash", "delta_chain_hash", "approver")

#: SHA-256 of the empty byte string — the fold over an empty sequence. Means
#: "there were none", NEVER "we could not look"; the latter raises.
EMPTY_HASH = hashlib.sha256(b"").hexdigest()

_HEX_DIGITS = set("0123456789abcdef")


class TrustValidationError(RuntimeError):
    """A validation record could not be composed or persisted."""


class DeltaChainUnavailable(TrustValidationError):
    """``trust_deltas`` could not be read, so the chain hash is unknown.

    Distinct from an empty chain on purpose. A caller must not register a
    validation record whose delta component it could not establish — the leaf
    would be indistinguishable from one computed over a genuinely empty history.
    """


# ---------------------------------------------------------------------------
# Hashing primitives
# ---------------------------------------------------------------------------
def sha256_text(text: str) -> str:
    """SHA-256 hex of *text*. The artifact_hash recipe."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _is_hex64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value.lower()) <= _HEX_DIGITS
    )


def _finding_identity(finding: Any) -> str:
    """Canonical identity of one finding.

    Delegates to ``hitl_delta._finding_key`` so this module cannot drift from
    the definition the delta evidence already uses — ``issue`` plus ``detail``,
    deliberately NOT ``item_number``, because inserting a sentence renumbers
    every later item and a finding that merely moved is not a different finding.
    The TRUST invariant in CLAUDE.md forbids a second copy of that rule, and a
    second copy here would silently change what a leaf means.

    Falls back to a local canonicalisation only when ``hitl_delta`` cannot be
    imported at all (its import pulls in the whole grounding stack, which a
    minimal air-gapped install may not carry). The fallback is byte-identical
    for the ``{issue, detail}`` shape every guard in ``tools/quality`` emits.
    """
    try:
        from tools.quality.hitl_delta import _finding_key

        return _finding_key(finding)
    except Exception:  # noqa: BLE001 — see docstring
        if not isinstance(finding, dict):
            return str(finding)
        return json.dumps(
            [finding.get("issue", ""), finding.get("detail", "")],
            sort_keys=True,
            default=str,
        )


def findings_hash(findings: Optional[Iterable[Any]]) -> str:
    """SHA-256 over the canonicalised finding SET.

    Sorted, so the same set of findings hashes identically no matter which guard
    emitted them first — guard order is a reporting decision and must not change
    what a reviewer is recorded as having seen. An empty or absent list yields
    :data:`EMPTY_HASH`: a clean verdict is a real verdict.
    """
    keys = sorted(_finding_identity(f) for f in (findings or []))
    if not keys:
        return EMPTY_HASH
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def fold_delta_chain(deltas: Sequence[Mapping[str, Any]]) -> str:
    """Fold an ordered sequence of delta rows into one hash.

    Each row contributes ``delta_id:before_hash:after_hash``. The three columns
    together are what makes a rewrite detectable: changing which text a delta
    describes changes a hash, and changing which delta it was changes the id.
    ``before_text``/``after_text`` are deliberately NOT folded — they may be
    pruned for retention while the hashes stay, and a fold that breaks when
    evidence is legitimately pruned is a fold nobody can verify later.

    Ordering is the caller's; :func:`delta_chain_hash` supplies
    ``(created_at, delta_id)``, which is total because ``delta_id`` is the
    primary key.
    """
    if not deltas:
        return EMPTY_HASH
    lines = [
        "{}:{}:{}".format(
            row.get("delta_id") or "",
            row.get("before_hash") or "",
            row.get("after_hash") or "",
        )
        for row in deltas
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def delta_chain_hash(artifact_id: str, *, db_path: Optional[Path] = None) -> str:
    """Fold every ``trust_deltas`` row for *artifact_id* into one hash.

    Raises:
        DeltaChainUnavailable: the storage layer, the connection or the table
            could not be reached. NOT the same as an empty chain — see the
            module docstring.
    """
    try:
        from tools.db.storage import get_connection, table_exists
    except Exception as exc:  # noqa: BLE001
        raise DeltaChainUnavailable(f"storage layer unavailable: {exc}") from exc

    try:
        conn = get_connection(db_path=str(db_path)) if db_path else get_connection()
    except Exception as exc:  # noqa: BLE001
        raise DeltaChainUnavailable(f"cannot open a connection: {exc}") from exc

    try:
        if not table_exists(conn, SOURCE_TABLE):
            raise DeltaChainUnavailable(
                f"{SOURCE_TABLE} is missing — run `python tools/db/migrate.py --up` "
                "(migration 20260815063941_trust_hitl_deltas)"
            )
        rows = conn.execute(
            f"SELECT delta_id, before_hash, after_hash FROM {SOURCE_TABLE} "
            "WHERE artifact_id = %s ORDER BY created_at, delta_id",
            (artifact_id,),
        ).fetchall()
    except DeltaChainUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DeltaChainUnavailable(f"cannot read {SOURCE_TABLE}: {exc}") from exc
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    return fold_delta_chain([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# The leaf
# ---------------------------------------------------------------------------
def validation_leaf(
    artifact_hash: str,
    findings_hash_: str,
    delta_chain_hash_: str,
    approver: str,
) -> str:
    """Compose the Merkle leaf. THE recipe — never inline a second copy.

        sha256(artifact_hash | findings_hash | delta_chain_hash | approver)

    Raises:
        ValueError: a hash component is not 64 hex digits, the approver is
            empty, or any component contains :data:`LEAF_SEPARATOR`.

    That last check is not pedantry. ``"a|b"`` joined with ``"c"`` and ``"a"``
    joined with ``"b|c"`` produce the same string, so an approver name carrying
    a pipe would let two different validations render one leaf — a collision an
    attacker chooses rather than finds. Refusing the input is cheaper than a
    length-prefixed encoding and keeps the recipe exactly as specified.
    """
    parts = {
        "artifact_hash": artifact_hash,
        "findings_hash": findings_hash_,
        "delta_chain_hash": delta_chain_hash_,
        "approver": approver,
    }
    for name in ("artifact_hash", "findings_hash", "delta_chain_hash"):
        if not _is_hex64(parts[name]):
            raise ValueError(
                f"{name} must be 64 lowercase hex digits, got {parts[name]!r}"
            )
    approver = str(parts["approver"] or "").strip()
    if not approver:
        raise ValueError("approver is required — a leaf with no actor is not evidence")
    parts["approver"] = approver

    for name, value in parts.items():
        if LEAF_SEPARATOR in str(value):
            raise ValueError(
                f"{name} contains the leaf separator {LEAF_SEPARATOR!r}, which would "
                "make the composition ambiguous"
            )

    payload = LEAF_SEPARATOR.join(str(parts[f]) for f in LEAF_FIELDS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def components_of(row: Any) -> Optional[dict]:
    """Parse the four leaf components out of a registry row's ``source_doc``.

    Returns ``None`` when the payload is absent, unparseable or incomplete — a
    row whose components cannot be read is one whose leaf cannot be verified,
    and the caller must treat that as a refusal rather than a pass.
    """
    # Subscript FIRST. A DB-API row (sqlite3.Row, psycopg2 DictRow) supports
    # ``row["col"]`` but is not a Mapping and exposes no attributes, so an
    # isinstance(Mapping) branch silently reads None off every real database row
    # — which reads as "unverifiable" and refuses every anchor.
    raw = None
    try:
        raw = row["source_doc"]
    except (TypeError, KeyError, IndexError):
        raw = getattr(row, "source_doc", None)
    if not raw:
        return None
    if isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    if any(f not in payload for f in LEAF_FIELDS):
        return None
    return {f: payload[f] for f in LEAF_FIELDS}


def recompute_leaf(row: Any) -> Optional[str]:
    """Rebuild a row's leaf from its own stored components, or ``None``.

    ``None`` means "cannot be verified", which callers must treat exactly as
    they treat a mismatch. This is what stops :class:`ChainAnchor` from
    anchoring a value nobody re-derived.
    """
    components = components_of(row)
    if components is None:
        return None
    try:
        return validation_leaf(
            components["artifact_hash"],
            components["findings_hash"],
            components["delta_chain_hash"],
            components["approver"],
        )
    except ValueError as exc:
        logger.warning("trust_validation: stored components are invalid: %s", exc)
        return None


def leaf_of(record: Mapping[str, Any]) -> str:
    """Compose a leaf from a record dict carrying the four component keys."""
    return validation_leaf(
        record.get("artifact_hash", ""),
        record.get("findings_hash", ""),
        record.get("delta_chain_hash", ""),
        record.get("approver", ""),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def record_validation(
    *,
    artifact_id: str,
    approver: str,
    artifact_text: Optional[str] = None,
    artifact_hash: Optional[str] = None,
    findings: Optional[Iterable[Any]] = None,
    delta_chain: Optional[str] = None,
    classification: str = "CUI",
    project_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Compose and persist one TRUST validation record.

    Supply either *artifact_text* (hashed here) or a precomputed
    *artifact_hash*. *delta_chain* overrides the fold; omit it and this reads
    ``trust_deltas`` for *artifact_id*.

    Returns ``{registry_id, leaf, artifact_id, artifact_hash, findings_hash,
    delta_chain_hash, approver}``.

    Raises:
        ValueError: the inputs cannot compose a leaf.
        DeltaChainUnavailable: ``trust_deltas`` could not be read.
        TrustValidationError: the registry INSERT did not land.

    That last one is the whole reason this function exists rather than a bare
    ``register_citation`` call at each site. ``register_citation`` returns ``""``
    on a swallowed database error, and a caller that does not check gets a
    validation record it believes exists and that was never written — the
    identical shape of the ``cortex`` bug (0 of 285 rows) and the
    ``asset_token`` bug (never anchored once). Here an empty id RAISES.
    """
    if artifact_hash is None:
        if artifact_text is None:
            raise ValueError("supply artifact_text or artifact_hash")
        artifact_hash = sha256_text(artifact_text)

    f_hash = findings_hash(findings)
    d_hash = delta_chain if delta_chain is not None else delta_chain_hash(
        artifact_id, db_path=db_path
    )
    leaf = validation_leaf(artifact_hash, f_hash, d_hash, approver)

    components = {
        "artifact_hash": artifact_hash,
        "findings_hash": f_hash,
        "delta_chain_hash": d_hash,
        "approver": str(approver).strip(),
    }

    from tools.provenance.registry import register_citation

    registry_id = register_citation(
        citation_type=CITATION_TYPE,
        source_table=SOURCE_TABLE,
        source_record_id=artifact_id,
        source_hash=leaf,
        # The components, so the leaf can be recomputed at anchor time. Note
        # what is NOT here: no artifact text, no finding prose. A registry row
        # is read by subsystems that are not cleared for the artifact.
        source_doc=json.dumps(components, sort_keys=True),
        classification=classification,
        project_id=project_id,
        db_path=db_path,
    )
    if not registry_id:
        raise TrustValidationError(
            f"the trust_validation citation for artifact {artifact_id!r} did not "
            "land — register_citation returned an empty id, which means the INSERT "
            "was rejected and swallowed. Check that migration "
            "20260815095725_trust_validation_citation_type has been applied."
        )

    logger.info(
        "trust_validation: recorded %s for artifact=%s approver=%s leaf=%s...",
        registry_id, artifact_id, components["approver"], leaf[:16],
    )
    return {"registry_id": registry_id, "leaf": leaf, "artifact_id": artifact_id, **components}
