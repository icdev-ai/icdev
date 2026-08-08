#!/usr/bin/env python3
# CUI // SP-CTI
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Explicitly Identifying Unknown Information — 2026 SBOM Minimum Elements (sbx-prc-01).

The 2021 NTIA element was called *Known Unknowns* and asked only that a missing
field be flagged. The 2026 standard renames it **Explicitly Identifying Unknown
Information** and splits the flag in two: where a field is not provided, the
author must state explicitly whether the value is

* **unknown to the author** — the author looked and could not establish it, or
* **withheld by the author** — the author knows it and is deliberately not
  disclosing it.

These are now *distinct states with distinct consequences*. A recipient reading
"unknown" learns that nobody has the fact; a recipient reading "withheld" learns
that the fact exists, is held by the author, and can be asked for. ICDEV
collapsed both into the single literal ``"unspecified"`` (and ``"managed"`` for
Maven), which is neither machine-processable nor able to tell them apart.

THE CONVENTION
==============

Three parts, applied uniformly to every one of the 17 data-field elements:

1. **An in-band sentinel** in the native field, so a schema that requires the
   field still validates and a naive reader is never shown a plausible-looking
   value. :data:`UNKNOWN` (``"unknown"``) or :data:`WITHHELD` (``"withheld"``).
2. **An out-of-band property per undisclosed field**, which is the authoritative
   statement: ``icdev:unknown:<field>`` or ``icdev:withheld:<field>``, whose
   value is a machine-readable reason code. The *name* carries the state and the
   *value* carries the why, so a reader that understands only the prefix already
   has the distinction the standard asks for.
3. **Disjoint reason vocabularies.** :data:`UNKNOWN_REASONS` and
   :data:`WITHHELD_REASONS` share no member. A withheld reason can therefore
   never be recorded under the unknown prefix without failing validation — the
   conflation the 2026 wording exists to prevent is a structural error here, not
   a matter of discipline.

The two namespaces are read separately everywhere in this module. Nothing sums
them, and :func:`validate_sbom_disclosure` reports ``fields_unknown`` and
``fields_withheld`` as two numbers that are never added together.

WHY A PROPERTY AND NOT ONLY A SENTINEL
--------------------------------------

A sentinel alone cannot carry a reason, cannot be attached to a field whose type
is not a string (``hashes``, ``licenses``, ``dependencies``), and cannot be
distinguished from a component genuinely named "unknown". The properties are the
record; the sentinel is a courtesy to readers that only look at the native field.
Where the two disagree, :func:`validate_sbom_disclosure` fails the document.

RECIPIENT ENQUIRY PROCESS
=========================

The standard requires that the author *have a process* by which recipients can
ask about redacted security-related information, and notes that an SBOM
withholding essential component data may be considered incomplete. ICDEV already
marks its SBOMs ``CUI // SP-CTI`` with ``Distribution D`` — that is the
withholding posture in the standard's sense — so the enquiry route is emitted
next to those markings on **every** document, not only when a field is withheld.
It comes from ``args/sbom_disclosure_policy.yaml`` (see
:func:`load_disclosure_policy`), and withholding anything without it is a
validation error.

Completeness is stated rather than implied: ``icdev:sbom:disclosure-completeness``
is ``complete`` unless one of :data:`ESSENTIAL_COMPONENT_FIELDS` is withheld, in
which case it is ``incomplete-withheld``. This is deliberately a *different*
property from ``icdev:sbom:coverage`` (sbx-cov-01): Coverage is about which
components are listed, this is about which fields on a listed component are
disclosed.

SPDX
====

SPDX has one marker, ``NOASSERTION``, and no way to say "withheld" in a field.
So both states map to ``NOASSERTION`` in the native SPDX field and the
distinction moves to an annotation — which is what :func:`spdx_mapping` renders,
for sbx-fmt-01's writer to emit. The state is never lost in translation; it
changes carrier. CycloneDX keeps both states in properties, which every
supported spec version can hold.
"""

import argparse
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
POLICY_PATH = BASE_DIR / "args" / "sbom_disclosure_policy.yaml"

# =====================================================================================
# States
# =====================================================================================

#: The value is not known to the SBOM author.
UNKNOWN = "unknown"

#: The value is known to the SBOM author and deliberately not disclosed.
WITHHELD = "withheld"

#: Every state this convention can express. There is no third one, and there is
#: no "absent" — a field that is not provided is in one of these two states.
STATES = (UNKNOWN, WITHHELD)

#: In-band sentinel per state. Identical to the state name on purpose: a human
#: reading the raw JSON gets the answer without a lookup table.
SENTINELS = {UNKNOWN: UNKNOWN, WITHHELD: WITHHELD}

#: Literals ICDEV emitted before this convention, plus the empty string. These
#: conflate the two states and must never appear in a generated document again;
#: :func:`validate_sbom_disclosure` fails on them.
LEGACY_SENTINELS = frozenset({"unspecified", "managed", "n/a", "none", "null", "-", ""})

# =====================================================================================
# Field vocabulary — the 17 data-field elements of the 2026 standard
# =====================================================================================

# SBOM Metadata elements
FIELD_SBOM_AUTHOR = "sbom_author"
FIELD_AUTHOR_SIGNATURE = "author_signature"
FIELD_DATA_FORMAT_NAME = "data_format_name"
FIELD_DATA_FORMAT_VERSION = "data_format_version"
FIELD_GENERATION_CONTEXT = "generation_context"
FIELD_TIMESTAMP = "timestamp"
FIELD_TOOL_NAME = "tool_name"
FIELD_TOOL_VERSION = "tool_version"
FIELD_SBOM_VERSION = "sbom_version"

# Component Data elements
FIELD_PRODUCER = "producer"
FIELD_DEPENDENCY_RELATIONSHIP = "dependency_relationship"
FIELD_HASH_VALUE = "hash_value"
FIELD_HASH_ALGORITHM = "hash_algorithm"
FIELD_IDENTIFIERS = "identifiers"
FIELD_LICENSE = "license"
FIELD_NAME = "name"
FIELD_VERSION = "version"

METADATA_FIELDS = (
    FIELD_SBOM_AUTHOR,
    FIELD_AUTHOR_SIGNATURE,
    FIELD_DATA_FORMAT_NAME,
    FIELD_DATA_FORMAT_VERSION,
    FIELD_GENERATION_CONTEXT,
    FIELD_TIMESTAMP,
    FIELD_TOOL_NAME,
    FIELD_TOOL_VERSION,
    FIELD_SBOM_VERSION,
)

COMPONENT_FIELDS = (
    FIELD_PRODUCER,
    FIELD_DEPENDENCY_RELATIONSHIP,
    FIELD_HASH_VALUE,
    FIELD_HASH_ALGORITHM,
    FIELD_IDENTIFIERS,
    FIELD_LICENSE,
    FIELD_NAME,
    FIELD_VERSION,
)

#: All 17 elements. A property naming anything outside this set is a validation
#: error: an unrecognised field name is unreadable to a recipient's tooling, and
#: silently accepting one would let a typo pass for a disclosure.
FIELDS = METADATA_FIELDS + COMPONENT_FIELDS

#: Component data the standard treats as essential. Withholding any of these is
#: what makes an SBOM "may be considered incomplete", so it flips the document's
#: disclosure-completeness marker rather than passing unnoticed. Hash value and
#: algorithm are not here: the standard's own example of legitimately absent data
#: is an author who cannot reach the artifact, and a withheld hash does not stop
#: a recipient identifying the component.
ESSENTIAL_COMPONENT_FIELDS = frozenset(
    {FIELD_PRODUCER, FIELD_NAME, FIELD_VERSION, FIELD_IDENTIFIERS, FIELD_LICENSE}
)

# =====================================================================================
# Reason vocabularies — DISJOINT BY CONSTRUCTION
# =====================================================================================

# --- Unknown to the author -----------------------------------------------------------
#: The producer ships no such value at all.
REASON_NOT_PROVIDED_BY_PRODUCER = "not-provided-by-producer"
#: A manifest named the dependency but pinned no version (a range is not a version).
REASON_DECLARED_WITHOUT_VERSION = "declared-without-a-version"
#: Maven ``dependencyManagement`` / a Gradle BOM holds the version, not this POM.
REASON_VERSION_MANAGED_BY_PARENT = "version-managed-by-parent"
#: The author cannot reach the executable artifact, so no hash is computable.
REASON_ARTIFACT_NOT_ACCESSIBLE = "artifact-not-accessible"
#: The package carries no metadata from which the value could be read.
REASON_METADATA_ABSENT = "package-metadata-absent"
#: No producing organization could be established (see ``component_producer``).
REASON_PRODUCER_NOT_IDENTIFIABLE = "producer-not-identifiable"
#: The value exists only behind a network call an air-gapped build cannot make.
REASON_NOT_RESOLVABLE_OFFLINE = "not-resolvable-offline"
#: The generating tool did not report the value.
REASON_TOOL_DID_NOT_REPORT = "tool-did-not-report"

UNKNOWN_REASONS = frozenset(
    {
        REASON_NOT_PROVIDED_BY_PRODUCER,
        REASON_DECLARED_WITHOUT_VERSION,
        REASON_VERSION_MANAGED_BY_PARENT,
        REASON_ARTIFACT_NOT_ACCESSIBLE,
        REASON_METADATA_ABSENT,
        REASON_PRODUCER_NOT_IDENTIFIABLE,
        REASON_NOT_RESOLVABLE_OFFLINE,
        REASON_TOOL_DID_NOT_REPORT,
    }
)

# --- Withheld by the author ----------------------------------------------------------
#: Disclosure is restricted by the artifact's classification (CUI, SECRET).
REASON_CLASSIFICATION_RESTRICTED = "classification-restricted"
#: Disclosure is restricted by export control (ITAR, EAR).
REASON_EXPORT_CONTROLLED = "export-controlled"
#: A contract or NDA with a third party forbids disclosure.
REASON_CONTRACTUAL_RESTRICTION = "contractual-restriction"
#: Disclosure would materially aid an adversary against a fielded system.
REASON_OPERATIONAL_SECURITY = "operational-security"
#: The value is a trade secret of the author's organization.
REASON_PROPRIETARY = "proprietary"
#: An unpatched vulnerability makes the value security-sensitive until remediated.
REASON_ACTIVE_VULNERABILITY = "active-vulnerability-mitigation"

WITHHELD_REASONS = frozenset(
    {
        REASON_CLASSIFICATION_RESTRICTED,
        REASON_EXPORT_CONTROLLED,
        REASON_CONTRACTUAL_RESTRICTION,
        REASON_OPERATIONAL_SECURITY,
        REASON_PROPRIETARY,
        REASON_ACTIVE_VULNERABILITY,
    }
)

#: Reasons per state, for validation and for callers that need to offer a choice.
REASONS = {UNKNOWN: UNKNOWN_REASONS, WITHHELD: WITHHELD_REASONS}

# =====================================================================================
# CycloneDX property names
# =====================================================================================

#: ``icdev:unknown:<field>`` — value is the reason code. Presence means the field
#: is unknown to the author.
PROPERTY_UNKNOWN_PREFIX = "icdev:unknown:"

#: ``icdev:withheld:<field>`` — value is the reason code. Presence means the
#: field is known to the author and deliberately not disclosed.
PROPERTY_WITHHELD_PREFIX = "icdev:withheld:"

#: Free-text elaboration on an *unknown*. Never emitted for a withheld field:
#: explaining a redaction in the document defeats the redaction.
PROPERTY_UNKNOWN_DETAIL_PREFIX = "icdev:unknown-detail:"

PROPERTY_PREFIXES = {UNKNOWN: PROPERTY_UNKNOWN_PREFIX, WITHHELD: PROPERTY_WITHHELD_PREFIX}

# Document-level, emitted in ``metadata.properties`` beside the CUI markings.
PROPERTY_ENQUIRY_PROCESS = "icdev:sbom:enquiry-process"
PROPERTY_ENQUIRY_CONTACT = "icdev:sbom:enquiry-contact"
PROPERTY_ENQUIRY_URI = "icdev:sbom:enquiry-uri"
PROPERTY_ENQUIRY_RESPONSE_DAYS = "icdev:sbom:enquiry-response-target-days"
PROPERTY_DISCLOSURE_COMPLETENESS = "icdev:sbom:disclosure-completeness"
PROPERTY_FIELDS_UNKNOWN = "icdev:sbom:fields-unknown"
PROPERTY_FIELDS_WITHHELD = "icdev:sbom:fields-withheld"

COMPLETENESS_COMPLETE = "complete"
COMPLETENESS_INCOMPLETE_WITHHELD = "incomplete-withheld"

# Environment overrides — an operator sets the enquiry route without editing YAML.
ENV_ENQUIRY_PROCESS = "ICDEV_SBOM_ENQUIRY_PROCESS"
ENV_ENQUIRY_CONTACT = "ICDEV_SBOM_ENQUIRY_CONTACT"
ENV_ENQUIRY_URI = "ICDEV_SBOM_ENQUIRY_URI"

#: Used when the policy file is missing entirely. Stating a route the operator
#: has not configured would be worse than useless, so this names the route the
#: repository does guarantee: the distribution statement already on the document.
DEFAULT_ENQUIRY_PROCESS = (
    "Recipients may request information withheld from this SBOM, including "
    "security-related redactions, from the distributing organization named in "
    "the document's distribution statement. Requests are answered within the "
    "stated response target."
)
DEFAULT_RESPONSE_TARGET_DAYS = 30


# =====================================================================================
# Reason helpers
# =====================================================================================


def state_of_reason(reason):
    """The state a reason code belongs to, or ``None`` if it belongs to neither.

    The vocabularies are disjoint, so a reason code identifies its state on its
    own — which is what stops a withheld reason being filed as an unknown.
    """
    if reason in UNKNOWN_REASONS:
        return UNKNOWN
    if reason in WITHHELD_REASONS:
        return WITHHELD
    return None


def sentinel(state):
    """The in-band literal for a state. Raises on anything that is not a state."""
    if state not in SENTINELS:
        raise ValueError(f"Unknown disclosure state: {state!r}. Expected one of {STATES}.")
    return SENTINELS[state]


def is_legacy_sentinel(value):
    """True for the pre-2026 literals that conflated unknown with withheld."""
    return str(value or "").strip().lower() in LEGACY_SENTINELS


# =====================================================================================
# Disclosure record
# =====================================================================================


class Disclosure:
    """The undisclosed fields of one object — a component, or the document.

    Two dicts keyed by field name, one per state, never merged. A field can be in
    at most one of them: :meth:`unknown` and :meth:`withheld` each evict the
    other, because a value cannot simultaneously be beyond the author's knowledge
    and held back by them.
    """

    __slots__ = ("_unknown", "_withheld", "_details")

    def __init__(self, unknown=None, withheld=None, details=None):
        self._unknown = dict(unknown or {})
        self._withheld = dict(withheld or {})
        self._details = dict(details or {})

    # --- recording -------------------------------------------------------------------

    def unknown(self, field, reason, detail=None):
        """Record ``field`` as unknown to the author. Returns ``self``."""
        self._check_field(field)
        if reason not in UNKNOWN_REASONS:
            raise ValueError(
                f"{reason!r} is not an unknown-reason. "
                f"Withheld reasons ({sorted(WITHHELD_REASONS)}) must be recorded with .withheld()."
            )
        self._withheld.pop(field, None)
        self._unknown[field] = reason
        if detail:
            self._details[field] = str(detail)
        return self

    def withheld(self, field, reason):
        """Record ``field`` as withheld by the author. Returns ``self``.

        No detail argument: an explanation of what was redacted, carried in the
        document the redaction protects, would undo it. The reason code is the
        category, and the enquiry process is where the specifics are answered.
        """
        self._check_field(field)
        if reason not in WITHHELD_REASONS:
            raise ValueError(
                f"{reason!r} is not a withheld-reason. "
                f"Unknown reasons ({sorted(UNKNOWN_REASONS)}) must be recorded with .unknown()."
            )
        self._unknown.pop(field, None)
        self._details.pop(field, None)
        self._withheld[field] = reason
        return self

    def mark(self, field, state, reason, detail=None):
        """Record ``field`` in ``state``. Dispatches to :meth:`unknown`/:meth:`withheld`."""
        if state == UNKNOWN:
            return self.unknown(field, reason, detail)
        if state == WITHHELD:
            return self.withheld(field, reason)
        raise ValueError(f"Unknown disclosure state: {state!r}. Expected one of {STATES}.")

    @staticmethod
    def _check_field(field):
        if field not in FIELDS:
            raise ValueError(
                f"{field!r} is not a 2026 minimum-elements field. Expected one of {sorted(FIELDS)}."
            )

    # --- reading ---------------------------------------------------------------------

    @property
    def unknown_fields(self):
        """``{field: reason}`` for fields unknown to the author."""
        return dict(self._unknown)

    @property
    def withheld_fields(self):
        """``{field: reason}`` for fields withheld by the author."""
        return dict(self._withheld)

    @property
    def details(self):
        return dict(self._details)

    def state_of(self, field):
        """``'unknown'``, ``'withheld'`` or ``None`` when the field is disclosed."""
        if field in self._unknown:
            return UNKNOWN
        if field in self._withheld:
            return WITHHELD
        return None

    def reason_for(self, field):
        return self._unknown.get(field) or self._withheld.get(field)

    def is_empty(self):
        return not self._unknown and not self._withheld

    def value_for(self, field, actual=None):
        """The value to emit for ``field`` — the sentinel when undisclosed."""
        state = self.state_of(field)
        return sentinel(state) if state else actual

    def withholds_essential(self):
        """True when an essential component field is withheld.

        This, and not the presence of any withholding at all, is what the
        standard's "may be considered incomplete" sentence turns on.
        """
        return bool(set(self._withheld) & ESSENTIAL_COMPONENT_FIELDS)

    def merge(self, other):
        """Fold ``other`` into this record. ``other`` wins on conflict."""
        if not other:
            return self
        for field, reason in other.unknown_fields.items():
            self.unknown(field, reason, other.details.get(field))
        for field, reason in other.withheld_fields.items():
            self.withheld(field, reason)
        return self

    def __bool__(self):
        return not self.is_empty()

    def __repr__(self):
        return f"Disclosure(unknown={self._unknown!r}, withheld={self._withheld!r})"

    def __eq__(self, other):
        if not isinstance(other, Disclosure):
            return NotImplemented
        return self._unknown == other._unknown and self._withheld == other._withheld

    # --- rendering -------------------------------------------------------------------

    def properties(self):
        """CycloneDX ``properties`` entries for every undisclosed field.

        Sorted by field name so two runs over the same input produce byte-equal
        documents — an SBOM that churns on regeneration cannot be diffed, and the
        Frequency element expects one per release.
        """
        properties = []
        for field in sorted(self._unknown):
            properties.append({"name": f"{PROPERTY_UNKNOWN_PREFIX}{field}", "value": self._unknown[field]})
            if field in self._details:
                properties.append(
                    {"name": f"{PROPERTY_UNKNOWN_DETAIL_PREFIX}{field}", "value": self._details[field]}
                )
        for field in sorted(self._withheld):
            properties.append({"name": f"{PROPERTY_WITHHELD_PREFIX}{field}", "value": self._withheld[field]})
        return properties

    def db_values(self):
        """``(unknown_fields_json, withheld_fields_json)`` for ``sbom_components``.

        Two columns, not one with a state key, because that is the shape
        sbx-fnd-02 created and because separate columns make "count the withheld
        ones" a query rather than a JSON walk. Both default to ``'{}'`` in the
        schema, so an empty record round-trips to the column default.
        """
        return (
            json.dumps(self._unknown, sort_keys=True),
            json.dumps(self._withheld, sort_keys=True),
        )

    @classmethod
    def from_db_values(cls, unknown_json, withheld_json):
        """Inverse of :meth:`db_values`. Unreadable JSON degrades to empty."""

        def load(raw):
            if isinstance(raw, dict):
                return raw
            try:
                value = json.loads(raw or "{}")
            except (TypeError, ValueError):
                return {}
            return value if isinstance(value, dict) else {}

        return cls(unknown=load(unknown_json), withheld=load(withheld_json))

    @classmethod
    def from_cyclonedx(cls, obj):
        """Read back what :meth:`properties` wrote, from a component or metadata block.

        Properties whose reason code is not in the matching vocabulary are kept
        as-is rather than dropped: :func:`validate_sbom_disclosure` has to be able
        to see and report them, and a reader that silently discarded them would
        turn a malformed disclosure into a missing one.
        """
        record = cls()
        for prop in (obj or {}).get("properties") or []:
            if not isinstance(prop, dict):
                continue
            name = str(prop.get("name") or "")
            value = prop.get("value")
            if name.startswith(PROPERTY_UNKNOWN_DETAIL_PREFIX):
                record._details[name[len(PROPERTY_UNKNOWN_DETAIL_PREFIX) :]] = value
            elif name.startswith(PROPERTY_UNKNOWN_PREFIX):
                record._unknown[name[len(PROPERTY_UNKNOWN_PREFIX) :]] = value
            elif name.startswith(PROPERTY_WITHHELD_PREFIX):
                record._withheld[name[len(PROPERTY_WITHHELD_PREFIX) :]] = value
        return record


def apply_to_cyclonedx(cdx_obj, disclosure):
    """Append a disclosure's properties to a CycloneDX component or metadata block."""
    if disclosure is None or disclosure.is_empty():
        return cdx_obj
    cdx_obj.setdefault("properties", []).extend(disclosure.properties())
    return cdx_obj


# =====================================================================================
# Bridges from element-specific resolvers
# =====================================================================================

#: ``component_producer`` unknown-reason codes describe *why no producer was
#: found*; this convention needs the coarser fact that the producer is not
#: identifiable. The detail keeps the specific code, so nothing is lost.
def disclosure_from_producer(producer_result, into=None):
    """Fold a ``component_producer`` result into a :class:`Disclosure`.

    sbx-fld-02 produces the *unknown* half of this convention for one element and
    deliberately left the general form here. This is the seam: the producer's own
    ``icdev:component-producer*`` properties stay exactly as they are, and the
    component additionally states ``icdev:unknown:producer`` so a validator can
    read every undisclosed field of every element in one pass.
    """
    record = into if into is not None else Disclosure()
    if not producer_result:
        return record
    if str(producer_result.get("producer") or UNKNOWN) == UNKNOWN:
        record.unknown(
            FIELD_PRODUCER,
            REASON_PRODUCER_NOT_IDENTIFIABLE,
            detail=producer_result.get("reason"),
        )
    return record


# =====================================================================================
# SPDX mapping (for sbx-fmt-01)
# =====================================================================================

#: SPDX's only "no value" marker. It does not distinguish the two states, so the
#: distinction moves into an annotation rather than being dropped.
SPDX_NOASSERTION = "NOASSERTION"

SPDX_ANNOTATION_TYPE = "OTHER"


def spdx_value(state):
    """The SPDX field value for a state. Both states yield ``NOASSERTION``."""
    if state not in SENTINELS:
        raise ValueError(f"Unknown disclosure state: {state!r}. Expected one of {STATES}.")
    return SPDX_NOASSERTION


def spdx_annotation_comment(field, state, reason):
    """The annotation text that carries the state SPDX's own field cannot."""
    if state == WITHHELD:
        return (
            f"{field}: NOASSERTION because the value is WITHHELD by the SBOM author "
            f"({reason}). The value is known to the author; see the SBOM's recipient "
            f"enquiry process to request it."
        )
    return f"{field}: NOASSERTION because the value is UNKNOWN to the SBOM author ({reason})."


def spdx_mapping(disclosure):
    """Render a disclosure for an SPDX writer.

    Returns one entry per undisclosed field::

        {"field": ..., "state": ..., "reason": ..., "value": "NOASSERTION",
         "annotationType": "OTHER", "comment": "..."}

    sbx-fmt-01 emits ``value`` into the SPDX field and ``comment`` as an
    annotation on the package. Unknown and withheld stay distinguishable in the
    SPDX document because the comment says which, in a fixed form a parser can
    match on the words ``UNKNOWN``/``WITHHELD``.
    """
    entries = []
    for field, reason in sorted(disclosure.unknown_fields.items()):
        entries.append(
            {
                "field": field,
                "state": UNKNOWN,
                "reason": reason,
                "value": SPDX_NOASSERTION,
                "annotationType": SPDX_ANNOTATION_TYPE,
                "comment": spdx_annotation_comment(field, UNKNOWN, reason),
            }
        )
    for field, reason in sorted(disclosure.withheld_fields.items()):
        entries.append(
            {
                "field": field,
                "state": WITHHELD,
                "reason": reason,
                "value": SPDX_NOASSERTION,
                "annotationType": SPDX_ANNOTATION_TYPE,
                "comment": spdx_annotation_comment(field, WITHHELD, reason),
            }
        )
    return entries


# =====================================================================================
# Disclosure policy — the enquiry route and any declared withholdings
# =====================================================================================


def _default_policy():
    return {
        "enquiry": {
            "process": DEFAULT_ENQUIRY_PROCESS,
            "contact": "",
            "uri": "",
            "response_target_days": DEFAULT_RESPONSE_TARGET_DAYS,
        },
        "withhold": {"document": [], "components": []},
    }


def load_disclosure_policy(path=None, env=None):
    """Load ``args/sbom_disclosure_policy.yaml``.

    A missing or unreadable file degrades to :func:`_default_policy` — which
    withholds nothing and still states an enquiry route — rather than to silence.
    An SBOM that withholds a field and names no way to ask about it is precisely
    what the standard forbids, so the route can never come back empty.
    """
    environ = os.environ if env is None else env
    policy = _default_policy()

    policy_path = Path(path) if path else POLICY_PATH
    try:
        import yaml

        with open(policy_path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except Exception:
        raw = {}

    if isinstance(raw, dict):
        enquiry = raw.get("enquiry")
        if isinstance(enquiry, dict):
            for key in ("process", "contact", "uri"):
                value = str(enquiry.get(key) or "").strip()
                if value:
                    policy["enquiry"][key] = value
            try:
                days = int(enquiry.get("response_target_days"))
                if days > 0:
                    policy["enquiry"]["response_target_days"] = days
            except (TypeError, ValueError):
                pass

        withhold = raw.get("withhold")
        if isinstance(withhold, dict):
            policy["withhold"]["document"] = _clean_rules(withhold.get("document"))
            policy["withhold"]["components"] = _clean_rules(withhold.get("components"))

    for key, env_name in (
        ("process", ENV_ENQUIRY_PROCESS),
        ("contact", ENV_ENQUIRY_CONTACT),
        ("uri", ENV_ENQUIRY_URI),
    ):
        value = str(environ.get(env_name) or "").strip()
        if value:
            policy["enquiry"][key] = value

    return policy


def _clean_rules(rules):
    """Keep only rules naming a real field and a real withheld reason.

    A rule with a typo'd reason is dropped rather than applied with a default,
    because guessing which redaction category the operator meant is exactly the
    kind of invention this element exists to prevent. :func:`policy_defects`
    reports what was dropped so it is visible rather than silent.
    """
    cleaned = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        field = str(rule.get("field") or "").strip()
        reason = str(rule.get("reason") or "").strip()
        if field not in FIELDS or reason not in WITHHELD_REASONS:
            continue
        entry = {"field": field, "reason": reason}
        match = rule.get("match")
        if isinstance(match, dict):
            entry["match"] = {
                key: str(value).strip()
                for key, value in match.items()
                if key in ("name", "group", "purl", "ecosystem") and str(value or "").strip()
            }
        cleaned.append(entry)
    return cleaned


def policy_defects(path=None):
    """Rules the policy file declares that :func:`load_disclosure_policy` drops.

    Returns a list of human-readable strings. Surfaced by the CLI so an operator
    who mistyped a reason code learns that their redaction is *not* being applied
    instead of believing it is.
    """
    defects = []
    try:
        import yaml

        with open(Path(path) if path else POLICY_PATH, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except Exception:
        return defects

    withhold = (raw or {}).get("withhold")
    if not isinstance(withhold, dict):
        return defects

    for scope in ("document", "components"):
        for index, rule in enumerate(withhold.get(scope) or []):
            if not isinstance(rule, dict):
                defects.append(f"withhold.{scope}[{index}]: not a mapping")
                continue
            field = str(rule.get("field") or "").strip()
            reason = str(rule.get("reason") or "").strip()
            if field not in FIELDS:
                defects.append(f"withhold.{scope}[{index}]: {field!r} is not a minimum-elements field")
            if reason not in WITHHELD_REASONS:
                defects.append(
                    f"withhold.{scope}[{index}]: {reason!r} is not a withheld-reason "
                    f"(it is {'an unknown-reason' if reason in UNKNOWN_REASONS else 'unrecognised'})"
                )
    return defects


def _rule_matches(rule, component):
    """Whether a component-scoped withholding rule applies to ``component``.

    No match block means "every component". Every named key must match, and an
    unmatched key is a non-match rather than a wildcard — a redaction that
    accidentally applies to the whole tree is worse than one that applies to
    nothing, because the second is visible on inspection.
    """
    match = rule.get("match")
    if not match:
        return True
    for key, expected in match.items():
        actual = str((component or {}).get(key) or "")
        if key == "purl":
            if not actual.startswith(expected):
                return False
        elif actual.strip().lower() != expected.strip().lower():
            return False
    return True


def apply_component_policy(component, policy, into=None):
    """Apply a policy's component withholding rules to one component."""
    record = into if into is not None else Disclosure()
    for rule in (policy or {}).get("withhold", {}).get("components", []):
        if _rule_matches(rule, component):
            record.withheld(rule["field"], rule["reason"])
    return record


def apply_document_policy(policy, into=None):
    """Apply a policy's document-level withholding rules."""
    record = into if into is not None else Disclosure()
    for rule in (policy or {}).get("withhold", {}).get("document", []):
        record.withheld(rule["field"], rule["reason"])
    return record


def enquiry_properties(policy):
    """The recipient enquiry route, as CycloneDX metadata properties.

    Always emitted, including when nothing is withheld: ICDEV's ``CUI // SP-CTI``
    and ``Distribution D`` markings are themselves a restriction on what the
    recipient may do with the document, and the standard's process requirement is
    what makes that restriction answerable rather than final.
    """
    enquiry = (policy or _default_policy()).get("enquiry", {})
    properties = [
        {"name": PROPERTY_ENQUIRY_PROCESS, "value": enquiry.get("process") or DEFAULT_ENQUIRY_PROCESS},
        {
            "name": PROPERTY_ENQUIRY_RESPONSE_DAYS,
            "value": str(enquiry.get("response_target_days") or DEFAULT_RESPONSE_TARGET_DAYS),
        },
    ]
    if enquiry.get("contact"):
        properties.append({"name": PROPERTY_ENQUIRY_CONTACT, "value": enquiry["contact"]})
    if enquiry.get("uri"):
        properties.append({"name": PROPERTY_ENQUIRY_URI, "value": enquiry["uri"]})
    return properties


def completeness_properties(disclosures):
    """Document-level totals plus the disclosure-completeness marker.

    The two counts are separate properties on purpose. A single "undisclosed
    fields" number would re-create the conflation the 2026 split exists to
    remove: 40 unknown fields is a data-quality problem, 40 withheld fields is a
    policy decision the recipient can appeal.
    """
    unknown_total = 0
    withheld_total = 0
    incomplete = False
    for disclosure in disclosures:
        if not disclosure:
            continue
        unknown_total += len(disclosure.unknown_fields)
        withheld_total += len(disclosure.withheld_fields)
        if disclosure.withholds_essential():
            incomplete = True

    return [
        {"name": PROPERTY_FIELDS_UNKNOWN, "value": str(unknown_total)},
        {"name": PROPERTY_FIELDS_WITHHELD, "value": str(withheld_total)},
        {
            "name": PROPERTY_DISCLOSURE_COMPLETENESS,
            "value": COMPLETENESS_INCOMPLETE_WITHHELD if incomplete else COMPLETENESS_COMPLETE,
        },
    ]


# =====================================================================================
# Validation
# =====================================================================================


def validate_sbom_disclosure(sbom):
    """Check a CycloneDX document against this convention.

    Returns ``(errors, summary)``. The summary keeps ``fields_unknown`` and
    ``fields_withheld`` as two separate counts and never adds them: "the
    validator distinguishes them and never counts withheld as unknown" is the
    acceptance criterion, and a single total would break it silently.

    sbx-sig-02's conformance validator can call this as-is, as it does
    ``component_producer.validate_sbom_producers``.
    """
    errors = []
    fields_unknown = 0
    fields_withheld = 0
    components_with_unknown = 0
    components_with_withheld = 0
    essential_withheld = []

    metadata = sbom.get("metadata") or {}
    targets = [("metadata", metadata)]
    target_component = metadata.get("component")
    if isinstance(target_component, dict):
        targets.append((_label(target_component, "metadata.component"), target_component))
    for index, component in enumerate(sbom.get("components") or []):
        targets.append((_label(component, f"components[{index}]"), component))

    for label, obj in targets:
        disclosure = Disclosure.from_cyclonedx(obj)
        unknown_fields = disclosure.unknown_fields
        withheld_fields = disclosure.withheld_fields

        both = set(unknown_fields) & set(withheld_fields)
        for field in sorted(both):
            errors.append(f"{label}: {field!r} is marked both unknown and withheld")

        for field, reason in sorted(unknown_fields.items()):
            errors.extend(_field_errors(label, field, reason, UNKNOWN))
        for field, reason in sorted(withheld_fields.items()):
            errors.extend(_field_errors(label, field, reason, WITHHELD))
            if field in ESSENTIAL_COMPONENT_FIELDS and label != "metadata":
                essential_withheld.append(f"{label}:{field}")

        for field in disclosure.details:
            if field in withheld_fields:
                errors.append(
                    f"{label}: {field!r} is withheld but carries an "
                    f"{PROPERTY_UNKNOWN_DETAIL_PREFIX}* property, which explains the redaction"
                )

        fields_unknown += len(unknown_fields)
        fields_withheld += len(withheld_fields)
        if unknown_fields and label != "metadata":
            components_with_unknown += 1
        if withheld_fields and label != "metadata":
            components_with_withheld += 1

        errors.extend(_sentinel_errors(label, obj, disclosure))

    errors.extend(_document_errors(metadata, fields_unknown, fields_withheld, essential_withheld))

    return errors, {
        "fields_unknown": fields_unknown,
        "fields_withheld": fields_withheld,
        "components_with_unknown": components_with_unknown,
        "components_with_withheld": components_with_withheld,
        "essential_fields_withheld": sorted(essential_withheld),
        "disclosure_completeness": (
            COMPLETENESS_INCOMPLETE_WITHHELD if essential_withheld else COMPLETENESS_COMPLETE
        ),
    }


def _label(component, fallback):
    return component.get("bom-ref") or component.get("name") or fallback


def _field_errors(label, field, reason, state):
    """Vocabulary checks for one disclosed-as-missing field."""
    errors = []
    prefix = PROPERTY_PREFIXES[state]
    if field not in FIELDS:
        errors.append(f"{label}: {prefix}{field} names no 2026 minimum-elements field")
    if not reason:
        errors.append(f"{label}: {prefix}{field} states no reason")
        return errors
    if reason not in REASONS[state]:
        other = state_of_reason(reason)
        if other:
            errors.append(
                f"{label}: {prefix}{field} carries {reason!r}, which is a "
                f"{other}-reason — the two states must not be conflated"
            )
        else:
            errors.append(f"{label}: {prefix}{field} carries unrecognised reason {reason!r}")
    return errors


#: Which CycloneDX key on a component carries each string-typed element, for the
#: sentinel/property agreement check. Fields whose CycloneDX carrier is not a
#: plain string (``hashes``, ``licenses``, ``dependencies``) are absent by design:
#: they have no in-band sentinel and live only in the properties.
_CYCLONEDX_FIELD_KEYS = {FIELD_NAME: "name", FIELD_VERSION: "version"}

#: Sentinel literal -> state. The two are spelled identically today; going
#: through a map keeps the check correct if that ever stops being true.
_STATE_OF_SENTINEL = {value: state for state, value in SENTINELS.items()}


def _sentinel_errors(label, obj, disclosure):
    """The in-band sentinel and the out-of-band property must agree."""
    errors = []
    for field, key in _CYCLONEDX_FIELD_KEYS.items():
        if key not in obj:
            continue
        value = str(obj.get(key) or "")
        declared = disclosure.state_of(field)
        literal = value.strip().lower()

        sentinel_state = _STATE_OF_SENTINEL.get(literal)
        if sentinel_state:
            if declared is None:
                errors.append(
                    f"{label}: {key} is the {literal!r} sentinel but no "
                    f"{PROPERTY_PREFIXES[sentinel_state]}{field} property states why"
                )
            elif declared != sentinel_state:
                errors.append(
                    f"{label}: {key} is the {literal!r} sentinel but the field is declared {declared!r}"
                )
        elif is_legacy_sentinel(value):
            errors.append(
                f"{label}: {key} is the pre-2026 literal {value!r}, which conflates "
                f"unknown with withheld — use the {field} disclosure convention"
            )
        elif declared is not None:
            errors.append(
                f"{label}: {field} is declared {declared!r} but {key} carries the value {value!r}"
            )
    return errors


def _document_errors(metadata, fields_unknown, fields_withheld, essential_withheld):
    """Document-level obligations: the enquiry route and the completeness marker."""
    errors = []
    values = {
        prop.get("name"): prop.get("value")
        for prop in (metadata.get("properties") or [])
        if isinstance(prop, dict)
    }

    if fields_withheld and not values.get(PROPERTY_ENQUIRY_PROCESS):
        errors.append(
            f"document withholds {fields_withheld} field(s) but states no "
            f"{PROPERTY_ENQUIRY_PROCESS} — the standard requires a process by which "
            f"recipients can ask about redacted information"
        )

    expected = COMPLETENESS_INCOMPLETE_WITHHELD if essential_withheld else COMPLETENESS_COMPLETE
    stated = values.get(PROPERTY_DISCLOSURE_COMPLETENESS)
    if stated is not None and stated != expected:
        errors.append(
            f"document states {PROPERTY_DISCLOSURE_COMPLETENESS}={stated!r} but "
            f"essential withheld fields make it {expected!r}"
        )

    # The two totals are checked against two separate counts. Checking a combined
    # total would let an SBOM move a field from unknown to withheld without the
    # document-level statement changing — the exact substitution this element bans.
    for name, count in (
        (PROPERTY_FIELDS_UNKNOWN, fields_unknown),
        (PROPERTY_FIELDS_WITHHELD, fields_withheld),
    ):
        stated_count = values.get(name)
        if stated_count is None:
            continue
        try:
            if int(stated_count) != count:
                errors.append(f"document states {name}={stated_count!r} but {count} were found")
        except (TypeError, ValueError):
            errors.append(f"document states {name}={stated_count!r}, which is not a number")

    return errors


# =====================================================================================
# CLI
# =====================================================================================


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Explicitly Identifying Unknown Information (2026 SBOM Minimum Elements) — "
            "the unknown-vs-withheld convention"
        )
    )
    parser.add_argument("--validate", help="Path to a CycloneDX JSON SBOM to check for disclosure conformance")
    parser.add_argument("--policy", action="store_true", help="Print the loaded disclosure policy and exit")
    parser.add_argument("--vocabulary", action="store_true", help="Print the field and reason vocabularies")
    parser.add_argument("--policy-path", dest="policy_path", help="Override args/sbom_disclosure_policy.yaml")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.vocabulary:
        payload = {
            "states": list(STATES),
            "sentinels": SENTINELS,
            "metadata_fields": list(METADATA_FIELDS),
            "component_fields": list(COMPONENT_FIELDS),
            "essential_component_fields": sorted(ESSENTIAL_COMPONENT_FIELDS),
            "unknown_reasons": sorted(UNKNOWN_REASONS),
            "withheld_reasons": sorted(WITHHELD_REASONS),
        }
        print(json.dumps(payload, indent=2) if args.json_output else payload)
        return 0

    if args.policy:
        policy = load_disclosure_policy(args.policy_path)
        defects = policy_defects(args.policy_path)
        payload = {"path": str(Path(args.policy_path) if args.policy_path else POLICY_PATH), **policy}
        if defects:
            payload["defects"] = defects
        if args.json_output:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Policy: {payload['path']}")
            print(f"  enquiry process: {policy['enquiry']['process']}")
            print(f"  contact:         {policy['enquiry']['contact'] or '-'}")
            print(f"  uri:             {policy['enquiry']['uri'] or '-'}")
            print(f"  response target: {policy['enquiry']['response_target_days']} days")
            print(f"  withheld rules:  {len(policy['withhold']['document'])} document, "
                  f"{len(policy['withhold']['components'])} component")
            for defect in defects:
                print(f"  DROPPED {defect}")
        return 1 if defects else 0

    if not args.validate:
        parser.error("one of --validate, --policy or --vocabulary is required")

    try:
        with open(args.validate, "r", encoding="utf-8") as handle:
            sbom = json.load(handle)
    except Exception as exc:
        print(f"ERROR: could not read SBOM: {args.validate}: {exc}", file=sys.stderr)
        return 1

    errors, summary = validate_sbom_disclosure(sbom)
    payload = {"valid": not errors, "errors": errors, **summary}
    if args.json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Fields unknown to the author:  {summary['fields_unknown']}")
        print(f"Fields withheld by the author: {summary['fields_withheld']}")
        print(f"Components with unknowns:      {summary['components_with_unknown']}")
        print(f"Components with withholdings:  {summary['components_with_withheld']}")
        print(f"Disclosure completeness:       {summary['disclosure_completeness']}")
        for error in errors:
            print(f"  ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
