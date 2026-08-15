# CUI // SP-CTI
"""Required-section outline contracts for LLM-drafted artifacts (trust-struct-02).

The outlining half of the structured-output pair. Where
``citation_grounding`` asks *is this sentence attributed* and ``kg_grounding``
asks *does this entity exist*, this module asks the shape question a reviewer
asks first: **does the draft have every section it is required to have, in the
required order, and nothing it invented?**

Three findings, in the shared ``{item_number, issue, detail}`` shape every other
guard here emits, so a promote/export gate consumes them symmetrically::

    missing_section       a required section is absent from the draft
    unknown_section       the draft carries a section the contract does not allow
    section_out_of_order  a required section appears before one that must precede it

**The skeletons are not declared here.** Every artifact type this platform
drafts already declares its section list somewhere, and a second copy would go
stale the first time someone edited the original — this codebase's signature
defect wearing a different hat. ``get_contract`` resolves, lazily, through the
declarations that already exist:

    ato_ssp / poam / stig_checklist / boundary_narrative
        tools.docgen.domain_profiles.ATO_DOC_TYPES[...]["sections"]
    STANDARD_GUIDE / SOP / RUNBOOK / ARCH_* and the six DIC playbook templates
        tools.document_intelligence.constants.TEMPLATE_SECTIONS
    runbook / design_doc / adr / ... (docgen doc_type names)
        aliased onto the above via constants.DOCGEN_DOCTYPE_TO_TEMPLATE

**RFI is derived, not declared, and that is deliberate.** An RFI response's
questionnaire parts come from the parsed solicitation — Part 2.1 of one RFI is
not Part 2.1 of the next — so a static full skeleton would report
``missing_section`` against every real session and be switched off within a
week. That is the ungated-applicability failure, not a contract. What IS
invariant is the floor ``rfi_workbench._seed_sections`` adds unconditionally
regardless of what the parser produced: Part 6.1-6.4 (questions to the
Government) and Appendix A/B. ``rfi_response`` registers exactly that floor,
with ``allow_unknown=True`` so the per-RFI questionnaire parts are not mistaken
for invented ones. For the exact per-session check, build the contract from the
session itself with :func:`contract_from_sections` over the ``list_sections``
payload.

**Section model.** Sections are the ``{item_number, content}`` dicts
``content_grounding.placeholder_findings`` and ``citation_grounding.citation_gate``
already take, and the rows the three ``list_sections`` surfaces already return —
``dic_sections`` (``heading``), ``rfi_workbench_sections``
(``item_number``/``title``), ``proposal_sections`` (``section_number``/``title``).
No parallel representation is introduced: a section is matched on ANY of its
identifying fields, so a contract may name a section by number ("6.1") or by
title ("System Boundary") and hit either way.

Pure regex/dict/dataclass — no LLM, no DB, no Flask. Everything but
``get_contract`` runs with no imports outside the stdlib, so stage 1 of the
TRUST gate stays air-gap-clean.

Wiring into ``structure_guard`` / ``PUBLISH_GATES`` is trust-struct-03's job and
is deliberately not done here: ``PUBLISH_GATES`` gains a value only in the phase
that can emit it, together with the migration that widens the CHECK.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Finding vocabulary ────────────────────────────────────────────────────────

ISSUE_MISSING = "missing_section"
ISSUE_UNKNOWN = "unknown_section"
ISSUE_OUT_OF_ORDER = "section_out_of_order"

#: Every issue this module can emit. A gate rendering a severity map should key
#: off this rather than a hand-written list.
OUTLINE_ISSUES: tuple[str, ...] = (ISSUE_MISSING, ISSUE_UNKNOWN, ISSUE_OUT_OF_ORDER)

#: Identifying fields on a section row, most specific first. Mirrors the label
#: precedence in ``citation_gate`` / ``placeholder_findings`` and extends it with
#: the two heading columns the DIC and proposals ``list_sections`` rows use.
SECTION_LABEL_KEYS: tuple[str, ...] = (
    "item_number", "heading", "title", "section_number", "id",
)


# ── Heading normalisation ─────────────────────────────────────────────────────
#
# A contract says "System Boundary". The draft says "## 3. System Boundary:".
# Both mean the same section, and a validator that reports a missing section
# over a markdown hash and a trailing colon gets switched off. Normalisation is
# deliberately conservative — it removes formatting and enumeration, never
# words — because the alternative (fuzzy/containment matching) silently accepts
# a section that is NOT the required one, which is the failure that matters.

_MD_HASH_RE = re.compile(r"^\s*#+\s*")
# A leading enumerator: optionally introduced by Part/Section/Appendix/Annex/
# Volume/Chapter, then a legal-style number (1, 1.2, 3.2.1), a UCF-style
# letter-number (L.3.2, M.2), a bare letter (A), or a roman numeral (IV),
# optionally closed by . ) : - and followed by whitespace or end-of-string.
# The enumerator must end at a separator or end-of-string, otherwise "3D
# Printing Overview" is read as enumerator "3" plus heading "D Printing
# Overview".
_ENUM_RE = re.compile(
    r"^\s*(?:(?:part|section|sec\.?|appendix|annex|volume|vol\.?|chapter)\s+)?"
    r"(?:[a-z]\.?)?\d+(?:\.\d+)*(?=[\s.:;)\-–—]|$)|"      # 1  1.2  L.3.2
    r"^\s*(?:(?:part|section|sec\.?|appendix|annex|volume|vol\.?|chapter)\s+)"
    r"(?:[a-z]|[ivxlcdm]+)(?=[\s.:;)\-–—]|$)",            # Appendix A  Volume IV
    re.IGNORECASE,
)
_TRAILING_PUNCT_RE = re.compile(r"[\s.:;)\-–—]+$")
_LEADING_PUNCT_RE = re.compile(r"^[\s.:;)\-–—]+")
_PUNCT_RE = re.compile(r"[^0-9a-z ]+")
_WS_RE = re.compile(r"\s+")


def _base_normalize(text: str) -> str:
    """Casefold, expand ``&``, drop punctuation, collapse whitespace."""
    s = _MD_HASH_RE.sub("", str(text or ""))
    s = s.replace("&", " and ")
    s = s.casefold()
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def normalize_heading(text: str) -> str:
    """Canonical form of a heading, enumerator retained.

    ``"## 3. System Boundary:"`` -> ``"3 system boundary"``. Use
    :func:`heading_keys` for matching — it also produces the enumerator-stripped
    form, so a contract may name a section either way.
    """
    return _base_normalize(text)


def strip_enumerator(text: str) -> str:
    """Heading with a leading enumerator removed, or unchanged if it has none.

    ``"3.2 System Boundary"`` -> ``"System Boundary"``; ``"6.1"`` -> ``"6.1"``
    (an enumerator that IS the whole heading is kept — removing it would leave
    nothing to match on, which is exactly how RFI parts are labelled).
    """
    raw = _MD_HASH_RE.sub("", str(text or ""))
    m = _ENUM_RE.match(raw)
    if not m:
        return raw.strip()
    rest = _LEADING_PUNCT_RE.sub("", raw[m.end():])
    rest = _TRAILING_PUNCT_RE.sub("", rest).strip()
    return rest if rest else raw.strip()


def heading_keys(text: str) -> set[str]:
    """Normalised forms a heading can be matched on: full, and enumerator-stripped."""
    keys = set()
    full = _base_normalize(text)
    if full:
        keys.add(full)
    bare = _base_normalize(strip_enumerator(text))
    if bare:
        keys.add(bare)
    return keys


# ── Section model (the list_sections payload) ─────────────────────────────────


def section_label(section: dict) -> str:
    """The section's display label — first present of :data:`SECTION_LABEL_KEYS`.

    Same precedence ``citation_gate`` and ``placeholder_findings`` use, so a
    finding from this module names a section the same way theirs does.
    """
    for key in SECTION_LABEL_KEYS:
        val = section.get(key)
        if val not in (None, ""):
            return str(val)
    return "?"


def section_keys(section: dict) -> set[str]:
    """Every normalised form this section can be matched against.

    A ``rfi_workbench_sections`` row carries both ``item_number`` ("6.1") and
    ``title`` ("Gap & Omission Questions"); a contract naming either must hit.
    """
    keys: set[str] = set()
    for key in SECTION_LABEL_KEYS:
        if key == "id":
            continue  # opaque uuid — never a heading
        val = section.get(key)
        if val not in (None, ""):
            keys |= heading_keys(val)
    return keys


# ── The contract ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OutlineContract:
    """The required section skeleton for one artifact type.

    Args:
        artifact_type: the type this contract governs (``"ato_ssp"``, ``"SOP"``,
            ``"rfi_response"``, or a caller-supplied name for a derived one).
        required: headings that MUST be present, in the order they must appear.
        optional: headings that MAY be present. Not required, never ``unknown``.
        ordered: enforce the relative order of the required sections present.
        allow_unknown: when False (the default for a declared skeleton), a
            heading matching neither ``required`` nor ``optional`` is reported
            ``unknown_section`` — the "no invented sections" half of the check.
            Set True when the artifact legitimately carries sections this
            contract cannot know about (see ``rfi_response``).
        source: where the skeleton came from. Carried on the contract so a
            finding can be traced to the declaration that produced it rather
            than to this module.
    """

    artifact_type: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    ordered: bool = True
    allow_unknown: bool = False
    source: str = ""
    _required_keys: tuple[frozenset[str], ...] = field(
        default=(), repr=False, compare=False
    )
    _optional_keys: tuple[frozenset[str], ...] = field(
        default=(), repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "required", tuple(self.required))
        object.__setattr__(self, "optional", tuple(self.optional))
        object.__setattr__(
            self, "_required_keys",
            tuple(frozenset(heading_keys(h)) for h in self.required),
        )
        object.__setattr__(
            self, "_optional_keys",
            tuple(frozenset(heading_keys(h)) for h in self.optional),
        )

    def to_dict(self) -> dict:
        return {
            "artifact_type": self.artifact_type,
            "required": list(self.required),
            "optional": list(self.optional),
            "ordered": self.ordered,
            "allow_unknown": self.allow_unknown,
            "source": self.source,
        }


def contract_from_sections(
    sections: list[dict],
    artifact_type: str,
    *,
    ordered: bool = True,
    allow_unknown: bool = False,
    source: str = "",
) -> OutlineContract:
    """Build a contract from a ``list_sections`` payload.

    The per-session path: an RFI workbench session or a proposal opportunity
    has already been seeded with the sections it owes, so the skeleton is that
    seeding — not a static list that would be wrong for the next solicitation.
    Sections are taken in payload order and labelled by :func:`section_label`.
    """
    return OutlineContract(
        artifact_type=artifact_type,
        required=tuple(section_label(s) for s in sections),
        ordered=ordered,
        allow_unknown=allow_unknown,
        source=source or f"derived:{artifact_type}",
    )


# ── Registry — resolves through the declarations that already exist ───────────

#: Artifact types whose skeleton lives in ``docgen.domain_profiles.ATO_DOC_TYPES``.
#: Not a copy of the sections — a copy of the KEYS, so an added ATO doc type is
#: picked up by ``list_contracts`` without editing this module.
_ATO_SOURCE = "tools.docgen.domain_profiles.ATO_DOC_TYPES"
_DIC_SOURCE = "tools.document_intelligence.constants.TEMPLATE_SECTIONS"
_DOCGEN_ALIAS_SOURCE = "tools.document_intelligence.constants.DOCGEN_DOCTYPE_TO_TEMPLATE"
_RFI_SOURCE = "tools.govcon.rfi_workbench (_PART6_SECTIONS + _APPENDIX_SECTIONS)"

#: The RFI response floor. ``_seed_sections`` appends these regardless of what
#: the parser produced, so they are required for EVERY session; the
#: questionnaire parts are per-solicitation and stay unknown-tolerant.
RFI_RESPONSE_TYPE = "rfi_response"


def _ato_sections(artifact_type: str) -> list[str] | None:
    try:
        from tools.docgen.domain_profiles import get_ato_doc_type
    except Exception:  # pragma: no cover - import environment
        return None
    cfg = get_ato_doc_type(artifact_type)
    if not cfg:
        return None
    sections = cfg.get("sections") or []
    return list(sections) or None


def _dic_template_sections(template_id: str) -> list[str] | None:
    try:
        from tools.document_intelligence.constants import TEMPLATE_SECTIONS
    except Exception:  # pragma: no cover - import environment
        return None
    sections = TEMPLATE_SECTIONS.get(template_id)
    return list(sections) if sections else None


def _docgen_alias(doc_type: str) -> str | None:
    """Map a docgen ``doc_type`` onto the Tech Writer template that shapes it."""
    try:
        from tools.document_intelligence.constants import DOCGEN_DOCTYPE_TO_TEMPLATE
    except Exception:  # pragma: no cover - import environment
        return None
    return DOCGEN_DOCTYPE_TO_TEMPLATE.get(doc_type)


def _rfi_floor() -> list[str] | None:
    try:
        from tools.govcon.rfi_workbench import _APPENDIX_SECTIONS, _PART6_SECTIONS
    except Exception:  # pragma: no cover - import environment
        return None
    rows = list(_PART6_SECTIONS) + list(_APPENDIX_SECTIONS)
    return [r[1] for r in rows] or None


def get_contract(artifact_type: str) -> OutlineContract | None:
    """Resolve the declared contract for *artifact_type*, or None if there is none.

    None means "this platform declares no skeleton for that type" — NOT "the
    draft is fine". A caller must treat an unresolved contract as unmeasured, in
    the same way ``kg_grounding`` reports ``kg_unmeasurable`` rather than
    scoring an empty graph as clean. Never fabricate a skeleton to fill the gap.

    Resolution order: ATO doc types, DIC/Tech Writer templates, docgen doc_type
    aliases onto those templates, then the RFI response floor.
    """
    if not artifact_type:
        return None
    key = str(artifact_type).strip()

    ato = _ato_sections(key)
    if ato:
        return OutlineContract(
            artifact_type=key, required=tuple(ato), source=_ATO_SOURCE,
        )

    dic = _dic_template_sections(key)
    if dic:
        return OutlineContract(
            artifact_type=key, required=tuple(dic), source=_DIC_SOURCE,
        )

    alias = _docgen_alias(key)
    if alias:
        # An ATO doc_type maps to a Tech Writer template for PROSE styling, but
        # its own section list is the authoritative one — checked above, so
        # reaching here means the alias is the only skeleton available.
        aliased = _dic_template_sections(alias)
        if aliased:
            return OutlineContract(
                artifact_type=key,
                required=tuple(aliased),
                source=f"{_DOCGEN_ALIAS_SOURCE} -> {alias} -> {_DIC_SOURCE}",
            )

    if key == RFI_RESPONSE_TYPE:
        floor = _rfi_floor()
        if floor:
            return OutlineContract(
                artifact_type=key,
                required=tuple(floor),
                # The questionnaire parts are parsed per solicitation and this
                # contract cannot know them. Flagging them would make every real
                # session fail — an applicability gap, not a finding.
                allow_unknown=True,
                source=_RFI_SOURCE,
            )
    return None


def list_contracts() -> list[str]:
    """Every artifact type :func:`get_contract` can resolve, sorted.

    Enumerated from the upstream declarations, so a section list added there
    appears here with no edit to this module.
    """
    types: set[str] = set()
    try:
        from tools.docgen.domain_profiles import ATO_DOC_TYPES

        types |= set(ATO_DOC_TYPES)
    except Exception:  # pragma: no cover - import environment
        pass
    try:
        from tools.document_intelligence.constants import (
            DOCGEN_DOCTYPE_TO_TEMPLATE,
            TEMPLATE_SECTIONS,
        )

        types |= set(TEMPLATE_SECTIONS)
        types |= {
            d for d, tpl in DOCGEN_DOCTYPE_TO_TEMPLATE.items()
            if tpl in TEMPLATE_SECTIONS
        }
    except Exception:  # pragma: no cover - import environment
        pass
    if _rfi_floor():
        types.add(RFI_RESPONSE_TYPE)
    return sorted(types)


# ── Validation ────────────────────────────────────────────────────────────────


def _match_positions(
    sections: list[dict], contract: OutlineContract
) -> tuple[list[int | None], set[int]]:
    """Map each required heading to the draft index that satisfies it.

    Returns ``(positions, matched_section_indices)``. ``positions[i]`` is the
    draft index satisfying ``contract.required[i]``, or None if absent. A draft
    section is consumed by at most one required heading, and each required
    heading takes the EARLIEST unconsumed section that matches it — so a
    document repeating a heading does not satisfy two different requirements
    with the same block of prose.
    """
    keys_per_section = [section_keys(s) for s in sections]
    positions: list[int | None] = []
    consumed: set[int] = set()
    for req_keys in contract._required_keys:
        hit: int | None = None
        for idx, sec_keys in enumerate(keys_per_section):
            if idx in consumed:
                continue
            if req_keys & sec_keys:
                hit = idx
                break
        if hit is not None:
            consumed.add(hit)
        positions.append(hit)
    return positions, consumed


def _in_order_indices(seq: list[int]) -> set[int]:
    """Indices of a longest strictly-increasing subsequence of *seq*.

    Everything outside it is what moved. Using the longest run rather than a
    left-to-right scan means one displaced section is reported once instead of
    cascading a finding onto every section after it — the difference between a
    usable report and one a reviewer stops reading.
    """
    n = len(seq)
    if n == 0:
        return set()
    best = [1] * n
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if seq[j] < seq[i] and best[j] + 1 > best[i]:
                best[i] = best[j] + 1
                prev[i] = j
    end = max(range(n), key=lambda i: best[i])
    keep = set()
    while end != -1:
        keep.add(end)
        end = prev[end]
    return keep


def outline_findings(
    sections: list[dict], contract: OutlineContract | None
) -> list[dict]:
    """Validate *sections* against *contract*. Empty list == the outline passes.

    Returns ``[{item_number, issue, detail}]`` where ``issue`` is one of
    :data:`OUTLINE_ISSUES` and ``detail`` is a list of strings, matching
    ``citation_gate`` / ``kg_gate`` so a promote gate treats them alike.

    A ``None`` contract yields NO findings — an artifact type this platform
    declares no skeleton for is unmeasured, and reporting it clean here would be
    wrong in the other direction, so the caller checks ``get_contract`` itself
    and decides. ``check_outline`` reports that distinction explicitly.

    An empty ``sections`` list against a real contract yields one
    ``missing_section`` per required heading. That is a true statement about an
    empty draft — callers must not hand this an unloaded payload.
    """
    if contract is None:
        return []

    findings: list[dict] = []
    positions, consumed = _match_positions(sections, contract)

    # 1. missing_section — a required heading nothing in the draft satisfies.
    total = len(contract.required)
    for i, pos in enumerate(positions):
        if pos is None:
            findings.append({
                "item_number": contract.required[i],
                "issue": ISSUE_MISSING,
                "detail": [f"required section {i + 1} of {total}"],
            })

    # 2. unknown_section — a draft section neither required nor optional.
    if not contract.allow_unknown:
        for idx, sec in enumerate(sections):
            if idx in consumed:
                continue
            keys = section_keys(sec)
            if any(keys & opt for opt in contract._optional_keys):
                continue
            findings.append({
                "item_number": section_label(sec),
                "issue": ISSUE_UNKNOWN,
                "detail": [
                    f"not in the {contract.artifact_type} contract "
                    f"({total} required, {len(contract.optional)} optional)"
                ],
            })

    # 3. section_out_of_order — present required sections whose draft order
    #    contradicts the contract order.
    if contract.ordered:
        present = [(pos, i) for i, pos in enumerate(positions) if pos is not None]
        present.sort()  # draft order
        expected_seq = [i for _pos, i in present]
        keep = _in_order_indices(expected_seq)
        for slot, (pos, req_idx) in enumerate(present):
            if slot in keep:
                continue
            findings.append({
                "item_number": contract.required[req_idx],
                "issue": ISSUE_OUT_OF_ORDER,
                "detail": [
                    f"contract position {req_idx + 1} of {total}",
                    f"draft position {pos + 1} of {len(sections)}",
                ],
            })
    return findings


def check_outline(
    sections: list[dict], contract: OutlineContract | None
) -> dict:
    """Report form of :func:`outline_findings`, for a gate or a CLI.

    Returns ``{measurable, findings, required, present, missing, unknown,
    out_of_order, section_count, contract}``. ``measurable`` is False when no
    contract could be resolved — an unmeasured outline and a clean one are
    different facts and are never collapsed.
    """
    if contract is None:
        return {
            "measurable": False,
            "reason": "no declared outline contract for this artifact type",
            "findings": [],
            "required": 0,
            "present": 0,
            "missing": 0,
            "unknown": 0,
            "out_of_order": 0,
            "section_count": len(sections),
            "contract": None,
        }
    findings = outline_findings(sections, contract)
    counts = {i: 0 for i in OUTLINE_ISSUES}
    for f in findings:
        counts[f["issue"]] = counts.get(f["issue"], 0) + 1
    return {
        "measurable": True,
        "findings": findings,
        "required": len(contract.required),
        "present": len(contract.required) - counts[ISSUE_MISSING],
        "missing": counts[ISSUE_MISSING],
        "unknown": counts[ISSUE_UNKNOWN],
        "out_of_order": counts[ISSUE_OUT_OF_ORDER],
        "section_count": len(sections),
        "contract": contract.to_dict(),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def _load_sections(path: str) -> list[dict]:
    """Read a ``list_sections`` payload: a JSON list, or ``{"sections": [...]}``."""
    import json
    import pathlib

    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("sections") or []
    return [s for s in data if isinstance(s, dict)]


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(
        description="Required-section outline contracts for LLM-drafted artifacts"
    )
    ap.add_argument("--list", action="store_true",
                    help="list every artifact type with a declared contract")
    ap.add_argument("--artifact-type", help="artifact type to resolve / validate against")
    ap.add_argument("--sections-file",
                    help="JSON list_sections payload to validate (list, or {sections: []})")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 when the outline has findings")
    args = ap.parse_args(argv)

    if args.list or not args.artifact_type:
        types = list_contracts()
        if args.json:
            print(json.dumps({"artifact_types": types, "total": len(types)}, indent=2))
        else:
            for t in types:
                print(t)
        return 0

    contract = get_contract(args.artifact_type)
    if not args.sections_file:
        payload = contract.to_dict() if contract else {
            "artifact_type": args.artifact_type, "declared": False,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        elif contract:
            print(f"{contract.artifact_type}  (source: {contract.source})")
            for i, h in enumerate(contract.required, 1):
                print(f"  {i}. {h}")
        else:
            print(f"no declared outline contract for {args.artifact_type!r}")
        return 0

    sections = _load_sections(args.sections_file)
    report = check_outline(sections, contract)
    if args.json:
        print(json.dumps(report, indent=2))
    elif not report["measurable"]:
        print(f"UNMEASURED — {report['reason']}")
    else:
        print(
            f"{args.artifact_type}: {report['present']}/{report['required']} required "
            f"present, {report['unknown']} unknown, {report['out_of_order']} out of order"
        )
        for f in report["findings"]:
            print(f"  [{f['issue']}] {f['item_number']} — {'; '.join(f['detail'])}")
    if args.gate and report["measurable"] and report["findings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
