#!/usr/bin/env python3
# CUI // SP-CTI
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""SBOM minimum-elements conformance gate — 2026 SBOM Minimum Elements (sbx-gov-01).

WHAT WAS WRONG
==============

``args/security_gates.yaml`` already carried five SBOM conditions before this
module existed:

======================================  =============================
Condition                               Gates
======================================  =============================
``sbom_not_generated``                  deployment_gates, swft
``sbom_attestation_missing``            devsecops
``sbom_stale_over_30_days``             sbd, swft
``sbom_generation_failed``              marketplace, production
``sbom_generation_skipped``             marketplace, production
======================================  =============================

Every one of them is a **presence, freshness or exit-code check**. Not one opens
the document. The consequence is exact and testable: the file

.. code-block:: json

   {"bomFormat": "CycloneDX", "specVersion": "1.4"}

was generated, is not stale, did not fail and was not skipped, and can be signed
and attested — so it clears all five. It also states nothing whatsoever about
the software it claims to describe. This module gates on what the SBOM *says*.

TWO CONDITIONS
==============

``sbom_minimum_elements_not_met`` (blocking)
    The document is short enough of the 17 data-field elements that it is not
    usable as evidence, or it lists no components at all.

``sbom_conformance_below_threshold`` (warning)
    The document clears the blocking floor but is still short of the full set.
    The 2026 standard is a floor rather than a target, so partial conformance is
    said out loud rather than passed silently.

WHERE THE NUMBERS LIVE
======================

In ``args/security_gates.yaml`` under ``sbom_conformance.thresholds``, and
nowhere else. This module holds **no default for any threshold**:
:func:`load_gate_config` raises :class:`SbomGateConfigError` when the block is
missing or incomplete rather than falling back to a constant. A gate whose
strictness is a Python literal is a gate nobody can retune without a code review,
and the one thing worse than an unconfigurable gate is an unconfigurable gate
that silently substitutes a value the operator never chose.

WHO DOES THE SCORING
====================

Not this module, whenever it can avoid it. ``sbx-sig-02`` owns
``tools/compliance/sbom_minimum_elements_validator.py``, the full validator that
scores CycloneDX *and* SPDX across all 17 data fields *and* the 6 applicable
practices, with per-element rationale and the unknown-vs-withheld distinction.
:func:`score_sbom` imports it and delegates the moment it is importable — no
edit here is needed on the day it merges.

Until then the fallback in :func:`_score_structural` keeps the gate from being
inert. It is deliberately narrow: presence of the 17 data fields in a CycloneDX
document, delegating Component Producer wholesale to
``component_producer.validate_sbom_producers`` — the entry point that module
advertises for exactly this purpose. It scores no practices and reads no SPDX.
It is a stand-in for a measurement instrument, not a second one, and
:data:`SCORER_VALIDATOR` vs :data:`SCORER_STRUCTURAL` is reported on every result
so no caller can mistake which one produced a number.

USAGE
=====

.. code-block:: bash

   python tools/compliance/sbom_conformance_gate.py --sbom out/sbom.cdx.json --json
   python tools/compliance/sbom_conformance_gate.py --sbom out/sbom.cdx.json --gate swft

Exit code 1 when the gate blocks, 0 otherwise.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GATES_PATH = PROJECT_ROOT / "args" / "security_gates.yaml"

CONFIG_SECTION = "sbom_conformance"

CONDITION_NOT_MET = "sbom_minimum_elements_not_met"
CONDITION_BELOW_THRESHOLD = "sbom_conformance_below_threshold"

#: Which module produced the score on a given result.
SCORER_VALIDATOR = "sbom_minimum_elements_validator"
SCORER_STRUCTURAL = "structural-interim"

VALIDATOR_MODULE = "tools.compliance.sbom_minimum_elements_validator"

MET = "met"
GAP = "gap"

# The 17 data-field elements of the 2026 standard: 9 SBOM Metadata (§1.1) and 8
# Component Data (§1.2). Practices and Processes (§1.3) are not scored here —
# they are sbx-sig-02's to score.
METADATA_ELEMENTS = (
    "sbom_author",
    "sbom_author_signature",
    "sbom_data_format_name",
    "sbom_data_format_version",
    "sbom_generation_context",
    "sbom_timestamp",
    "sbom_tool_name",
    "sbom_tool_version",
    "sbom_version",
)
COMPONENT_ELEMENTS = (
    "component_producer",
    "component_dependency_relationship",
    "component_hash_value",
    "component_hash_algorithm",
    "component_identifiers",
    "component_license",
    "component_name",
    "component_version",
)
DATA_FIELD_ELEMENTS = METADATA_ELEMENTS + COMPONENT_ELEMENTS

#: Where `component_hasher` (sbx-fld-03) states the two hash elements when the answer is
#: an explicit unknown or withheld marker rather than a digest. Named rather than
#: imported, like every other `icdev:` property this scorer reads.
PROPERTY_HASH_VALUE = "icdev:component-hash"
PROPERTY_HASH_ALGORITHM = "icdev:component-hash-algorithm"


class SbomGateConfigError(RuntimeError):
    """``args/security_gates.yaml`` does not configure this gate."""


class SbomScoreError(RuntimeError):
    """The conformance validator returned something this gate cannot read.

    Raised rather than absorbed, because the quiet failure here is the expensive
    one: a result whose element names this gate does not recognise maps to zero
    elements met, which looks exactly like a genuinely empty SBOM and would block
    every deployment for a reason no message states.
    """


# =====================================================================================
# Configuration — args/security_gates.yaml is the only source
# =====================================================================================


def load_gate_config(gates_path=None):
    """Read ``sbom_conformance`` out of ``args/security_gates.yaml``.

    Returns a dict with ``gates``, ``blocking``, ``warning`` and ``thresholds``.
    Raises :class:`SbomGateConfigError` rather than defaulting: every threshold
    this gate applies is an operator's decision, so an absent one is a
    configuration bug to surface, not a number to invent.
    """
    import yaml

    path = Path(gates_path) if gates_path else GATES_PATH
    if not path.exists():
        raise SbomGateConfigError(f"security gate configuration not found: {path}")

    with open(path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    section = config.get(CONFIG_SECTION)
    if not isinstance(section, dict):
        raise SbomGateConfigError(f"{path}: no '{CONFIG_SECTION}' block — this gate has no thresholds to apply")

    thresholds = section.get("thresholds")
    if not isinstance(thresholds, dict):
        raise SbomGateConfigError(f"{path}: '{CONFIG_SECTION}' has no 'thresholds' block")

    for key in ("block_below_pct", "warn_below_pct", "require_components"):
        if key not in thresholds:
            raise SbomGateConfigError(
                f"{path}: '{CONFIG_SECTION}.thresholds.{key}' is missing — this module carries no default for it"
            )

    gates = section.get("gates") or []
    if not gates:
        raise SbomGateConfigError(f"{path}: '{CONFIG_SECTION}.gates' is empty — the conditions are wired into nothing")

    return {
        "gates": list(gates),
        "blocking": list(section.get("blocking") or []),
        "warning": list(section.get("warning") or []),
        "thresholds": {
            "block_below_pct": float(thresholds["block_below_pct"]),
            "warn_below_pct": float(thresholds["warn_below_pct"]),
            "require_components": bool(thresholds["require_components"]),
        },
    }


# =====================================================================================
# Scoring — delegated to sbx-sig-02 when it is available
# =====================================================================================


def _load_validator():
    """Return sbx-sig-02's validator module, or ``None`` if it has not landed."""
    try:
        return importlib.import_module(VALIDATOR_MODULE)
    except ImportError:
        return None


def _normalize_validator_result(raw):
    """Map the validator's result onto the shape this gate consumes.

    The validator reports per-element ``met``/``partial``/``gap``. A gate is a
    binary decision, and ``partial`` is not ``met`` — a half-stated element is
    the case the 2026 standard withdrew tolerance for — so only ``met`` counts.

    Two modules agreeing on a number is easy; agreeing on a *vocabulary* is not.
    If the validator names its elements differently from :data:`DATA_FIELD_ELEMENTS`,
    every lookup misses, the score is zero and the gate blocks everything while
    reporting a perfectly plausible reason. So a result that overlaps this gate's
    element names nowhere raises :class:`SbomScoreError` instead of scoring zero.
    """
    if not isinstance(raw, dict):
        raise SbomScoreError(f"{VALIDATOR_MODULE}.validate_sbom returned {type(raw).__name__}, expected a dict")

    elements = _validator_elements_by_name(raw.get("elements"))
    scored = None

    if isinstance(elements, dict) and elements:
        recognized = [name for name in DATA_FIELD_ELEMENTS if name in elements]
        if not recognized:
            raise SbomScoreError(
                f"{VALIDATOR_MODULE} scored {len(elements)} element(s), none of which this gate recognises "
                f"(expected names such as {', '.join(DATA_FIELD_ELEMENTS[:3])}) — the two modules disagree on "
                "the element vocabulary; reconcile them rather than treating this as zero conformance"
            )
        scored = {}
        for name in DATA_FIELD_ELEMENTS:
            entry = elements.get(name)
            status = entry.get("status") if isinstance(entry, dict) else entry
            scored[name] = MET if status == MET else GAP

    # The validator's own aggregate wins when it supplies one: it scores the 6
    # practices too, and it owns the definition of what counts.
    if raw.get("elements_met") is not None and raw.get("elements_total"):
        met = int(raw["elements_met"])
        total = int(raw["elements_total"])
    elif scored is not None:
        met = sum(1 for status in scored.values() if status == MET)
        total = len(DATA_FIELD_ELEMENTS)
    else:
        raise SbomScoreError(
            f"{VALIDATOR_MODULE}.validate_sbom returned neither an 'elements' map nor an "
            "'elements_met'/'elements_total' pair — nothing to gate on"
        )

    return {
        "elements": scored if scored is not None else {},
        "elements_met": met,
        "elements_total": total,
        "score_pct": round(100.0 * met / total, 2) if total else 0.0,
        "component_count": _validator_component_count(raw),
        "scored_by": SCORER_VALIDATOR,
    }


def _validator_elements_by_name(elements):
    """The validator's per-element results keyed by element id.

    The validator returns a LIST of ``{"id": ..., "status": ...}``; this gate
    only ever handled a dict, so every per-element branch below — including the
    SbomScoreError that is supposed to fire when the two modules disagree about
    the element vocabulary — was unreachable. The gate silently fell through to
    the aggregate (12 of 23, practices included) and blocked on a percentage,
    with no statement of WHICH element was short. The safety net that exists
    precisely for a two-module disagreement was dead in the shape the other
    module actually returns.

    Accepts either shape and returns a dict, or None when there is nothing
    per-element to read.
    """
    if isinstance(elements, dict) and elements:
        return elements
    if isinstance(elements, list) and elements:
        by_name = {}
        for entry in elements:
            if not isinstance(entry, dict):
                continue
            name = entry.get("id") or entry.get("name")
            if name:
                by_name[str(name)] = entry
        return by_name or None
    return None


def _validator_component_count(raw):
    """The validator's component count, read from where it actually puts it.

    It reports the count under ``document``, not at the top level, and the read
    here was ``int(raw.get("component_count") or 0)`` — so an ABSENT count
    scored as ZERO, and ``require_components`` then blocked with "the SBOM lists
    no components" against a document holding two of them. The gate reported a
    perfectly plausible reason for a conclusion it had invented, which is worse
    than failing loudly: the number it printed was never measured.

    Absent is not zero. When neither location states a count, return None so the
    caller can count the document it is already holding.
    """
    document = raw.get("document")
    for source in (document, raw):
        if not isinstance(source, dict):
            continue
        value = source.get("component_count")
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _text(value):
    return isinstance(value, str) and value.strip() != ""


def _all_components(sbom):
    """Every component the document describes, target component included.

    The target is a component too, so the Component Data elements apply to it —
    the same reading ``component_producer.validate_sbom_producers`` takes.
    """
    components = [comp for comp in (sbom.get("components") or []) if isinstance(comp, dict)]
    target = (sbom.get("metadata") or {}).get("component")
    if isinstance(target, dict):
        components.append(target)
    return components


def _component_property(component, name):
    for prop in component.get("properties") or []:
        if isinstance(prop, dict) and prop.get("name") == name:
            return prop.get("value")
    return None


def _states_hash(component, hashes_key, property_name):
    """Does this component state one of the answers the hash elements permit?

    Two shapes count. A native CycloneDX ``hashes[]`` entry is what a third-party SBOM
    carries and what an ICDEV component carries when its artifact was reachable. The
    ``icdev:component-hash*`` property is what carries the other permitted answer — the
    explicit unknown or withheld marker — because CycloneDX has no way to spell either
    inside ``hashes``. `component_hasher` owns that property; only its name is repeated
    here, the same way this scorer already names ``icdev:sbom-author``.
    """
    if any(_text(entry.get(hashes_key)) for entry in (component.get("hashes") or []) if isinstance(entry, dict)):
        return True
    return _text(_component_property(component, property_name))


def _metadata_property(sbom, name):
    for prop in ((sbom.get("metadata") or {}).get("properties") or []):
        if isinstance(prop, dict) and prop.get("name") == name:
            return prop.get("value")
    return None


def _has_detached_signature(sbom_path):
    """Is there a signature beside this SBOM?

    ``sbom_signer`` (sbx-sig-01) writes the SBOM Author Signature to a **detached**
    ``<sbom>.sig.json``, so the document itself carries nothing to find. Scoring
    the document alone would report every correctly signed SBOM as unsigned —
    a wrong answer, not a missing one. Only a path can answer this, so an
    in-memory document falls back to the in-document check.
    """
    if not sbom_path:
        return False
    try:
        from tools.compliance.sbom_signer import signature_path_for
    except ImportError:
        return Path(str(sbom_path) + ".sig.json").exists()
    return signature_path_for(sbom_path).exists()


def _score_structural(sbom, sbom_path=None):
    """Interim CycloneDX presence check. Superseded by sbx-sig-02's validator.

    An element is met only when *every* component states it. Partial coverage is
    a gap: an SBOM where half the components carry a license is not an SBOM that
    meets Component License.
    """
    metadata = sbom.get("metadata") or {}
    tools = metadata.get("tools") or []
    if isinstance(tools, dict):  # CycloneDX 1.5+ moved tools to an object form
        tools = (tools.get("components") or []) + (tools.get("services") or [])
    first_tool = next((tool for tool in tools if isinstance(tool, dict)), {})

    components = _all_components(sbom)
    has_components = bool(components)

    def every(predicate):
        return has_components and all(predicate(comp) for comp in components)

    producer_errors, _producer_summary = _score_producers(sbom)
    dependency_report = _score_dependency_graph(sbom)

    scored = {
        # --- SBOM Metadata (§1.1) ---
        "sbom_author": bool(metadata.get("authors")) or _text(_metadata_property(sbom, "icdev:sbom-author")),
        "sbom_author_signature": (
            bool(sbom.get("signature"))
            or _text(_metadata_property(sbom, "icdev:sbom-author-signature"))
            or _has_detached_signature(sbom_path)
        ),
        "sbom_data_format_name": _text(sbom.get("bomFormat")),
        "sbom_data_format_version": _text(sbom.get("specVersion")),
        "sbom_generation_context": _text(_metadata_property(sbom, "icdev:sbom-generation-context")),
        "sbom_timestamp": _text(metadata.get("timestamp")),
        "sbom_tool_name": _text(first_tool.get("name")),
        "sbom_tool_version": _text(first_tool.get("version")),
        "sbom_version": sbom.get("version") is not None,
        # --- Component Data (§1.2) ---
        "component_producer": has_components and not producer_errors,
        "component_dependency_relationship": dependency_report["status"] == MET,
        # NOT "every component has a digest". The standard's own text for Component Hash
        # Value says an artifact the author cannot access is marked unknown rather than
        # the field omitted, so a document where every artifact was out of reach still
        # meets the element. Scoring the `hashes` array alone would report that
        # conformant document as a gap forever, because there is nothing a generator
        # reading lockfiles could ever have put in the array. A native `hashes[]` entry
        # is still what a third-party SBOM will carry, so both are accepted.
        "component_hash_value": every(lambda c: _states_hash(c, "content", PROPERTY_HASH_VALUE)),
        "component_hash_algorithm": every(
            lambda c: _states_hash(c, "alg", PROPERTY_HASH_ALGORITHM)
        ),
        "component_identifiers": every(lambda c: _text(c.get("purl")) or _text(c.get("cpe"))),
        "component_license": every(lambda c: bool(c.get("licenses"))),
        "component_name": every(lambda c: _text(c.get("name"))),
        "component_version": every(lambda c: _text(c.get("version"))),
    }

    elements = {name: (MET if scored[name] else GAP) for name in DATA_FIELD_ELEMENTS}
    met = sum(1 for status in elements.values() if status == MET)
    return {
        "elements": elements,
        "elements_met": met,
        "elements_total": len(DATA_FIELD_ELEMENTS),
        "score_pct": round(100.0 * met / len(DATA_FIELD_ELEMENTS), 2),
        "component_count": len(components),
        "scored_by": SCORER_STRUCTURAL,
    }


def _score_producers(sbom):
    """Delegate Component Producer to the module that owns the element.

    An unimportable dependency is raised, not absorbed into a gap. Absorbing it
    is how running this file as a script rather than importing it silently cost
    one element: ``sys.path[0]`` is the script's own directory, ``tools`` then
    resolves to whatever package shim is installed, the import fails, and the
    SBOM scores one lower for a reason that never reaches the operator. A wrong
    score that looks like a real one is worse than a traceback.
    """
    try:
        from tools.compliance.component_producer import validate_sbom_producers
    except ImportError as exc:
        raise SbomScoreError(
            f"cannot import tools.compliance.component_producer, which owns the Component Producer "
            f"element ({exc}) — the repository root is not on sys.path, so this score would be "
            "understated by one element rather than simply unavailable"
        ) from exc
    return validate_sbom_producers(sbom)


def _score_dependency_graph(sbom):
    """Delegate Component Dependency Relationship to the module that owns it.

    Presence of a ``dependencies`` array was the interim check, and presence is
    not the element: an array whose ``dependsOn`` names a ref no component
    carries builds no graph at all, and it is precisely the shape a partial
    implementation produces. `dependency_graph` (sbx-cov-02) emits the array and
    scores it, so the element cannot be met by a document its own writer would
    reject. Unimportable is raised rather than scored zero, for the reason
    :func:`_score_producers` gives.
    """
    try:
        from tools.compliance.dependency_graph import validate_dependency_graph
    except ImportError as exc:
        raise SbomScoreError(
            f"cannot import tools.compliance.dependency_graph, which owns the Component "
            f"Dependency Relationship element ({exc}) — the repository root is not on sys.path, "
            "so this score would be understated by one element rather than simply unavailable"
        ) from exc
    return validate_dependency_graph(sbom)


def score_sbom(sbom, sbom_path=None):
    """Score a parsed SBOM against the 17 data-field elements.

    Prefers sbx-sig-02's validator and falls back to the interim structural
    check. The returned ``scored_by`` says which ran. ``sbom_path`` is what makes
    the detached SBOM Author Signature findable; omit it and that element is
    scored from the document alone.
    """
    validator = _load_validator()
    if validator is not None and hasattr(validator, "validate_sbom"):
        return _normalize_validator_result(validator.validate_sbom(sbom))
    return _score_structural(sbom, sbom_path=sbom_path)


# =====================================================================================
# Gate evaluation
# =====================================================================================


def evaluate_sbom_gate(sbom, gate=None, gates_path=None, config=None, sbom_path=None):
    """Evaluate the conformance conditions for one gate.

    ``sbom`` is a parsed CycloneDX document. ``gate`` names one of the gates in
    ``sbom_conformance.gates``; omit it to evaluate the conditions on their own.
    Returns a result dict whose ``passed`` is false when anything blocking fired.
    """
    config = config or load_gate_config(gates_path)
    thresholds = config["thresholds"]

    if gate is not None and gate not in config["gates"]:
        raise SbomGateConfigError(
            f"gate '{gate}' is not in {CONFIG_SECTION}.gates ({', '.join(config['gates'])})"
        )

    score = score_sbom(sbom, sbom_path=sbom_path)

    # A scorer that does not STATE a component count has not counted zero of
    # them. Count the document we are already holding rather than blocking on a
    # number nobody measured — `require_components` is meant to catch an SBOM
    # that genuinely lists nothing, not one whose scorer reports the total
    # somewhere this gate did not look.
    if score.get("component_count") is None:
        score["component_count"] = len(_all_components(sbom))

    blocking = []
    warnings = []

    if thresholds["require_components"] and score["component_count"] == 0:
        blocking.append(
            {
                "condition": CONDITION_NOT_MET,
                "reason": "the SBOM lists no components, so it meets no Component Data element",
            }
        )
    elif score["score_pct"] < thresholds["block_below_pct"]:
        blocking.append(
            {
                "condition": CONDITION_NOT_MET,
                "reason": (
                    f"{score['elements_met']} of {score['elements_total']} minimum-elements data fields met "
                    f"({score['score_pct']}%), below the {thresholds['block_below_pct']}% blocking threshold"
                ),
            }
        )

    if not blocking and score["score_pct"] < thresholds["warn_below_pct"]:
        warnings.append(
            {
                "condition": CONDITION_BELOW_THRESHOLD,
                "reason": (
                    f"{score['elements_met']} of {score['elements_total']} minimum-elements data fields met "
                    f"({score['score_pct']}%), below the {thresholds['warn_below_pct']}% target"
                ),
            }
        )

    return {
        "gate": gate,
        "passed": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "gaps": sorted(name for name, status in score["elements"].items() if status == GAP),
        **score,
        "thresholds": dict(thresholds),
    }


def evaluate_sbom_file(sbom_path, gate=None, gates_path=None):
    """Read a CycloneDX JSON document from disk and evaluate the gate on it."""
    with open(sbom_path, encoding="utf-8") as handle:
        sbom = json.load(handle)
    result = evaluate_sbom_gate(sbom, gate=gate, gates_path=gates_path, sbom_path=sbom_path)
    result["sbom_path"] = str(sbom_path)
    return result


# =====================================================================================
# CLI
# =====================================================================================


def _bootstrap_import_path():
    """Put the repository root on ``sys.path`` when this file is run as a script.

    ``python tools/compliance/sbom_conformance_gate.py`` sets ``sys.path[0]`` to
    ``tools/compliance/`` and does not add the working directory, so the sibling
    imports this gate delegates to are not importable without this. Importing the
    module normally never reaches here.
    """
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def main():
    _bootstrap_import_path()
    parser = argparse.ArgumentParser(
        description="Gate an SBOM on conformance to the 2026 minimum elements, not just its presence"
    )
    parser.add_argument("--sbom", required=True, help="Path to a CycloneDX JSON SBOM")
    parser.add_argument("--gate", help="deployment_gates, swft or devsecops (default: evaluate the conditions alone)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    try:
        result = evaluate_sbom_file(args.sbom, gate=args.gate)
    except (SbomGateConfigError, SbomScoreError) as exc:
        print(f"CUI // SP-CTI\nSBOM conformance gate could not run: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print("CUI // SP-CTI")
        print(f"SBOM: {result['sbom_path']}")
        print(f"Gate: {result['gate'] or '(conditions only)'}")
        print(
            f"Conformance: {result['elements_met']}/{result['elements_total']} data fields "
            f"({result['score_pct']}%) — scored by {result['scored_by']}"
        )
        for entry in result["blocking"]:
            print(f"  BLOCK  {entry['condition']}: {entry['reason']}")
        for entry in result["warnings"]:
            print(f"  WARN   {entry['condition']}: {entry['reason']}")
        if result["gaps"]:
            print(f"  Gaps: {', '.join(result['gaps'])}")
        print("PASSED" if result["passed"] else "FAILED")

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
