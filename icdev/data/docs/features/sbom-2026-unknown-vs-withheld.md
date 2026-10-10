# CUI // SP-CTI

# Explicitly Identifying Unknown Information — unknown vs withheld

**Card:** `sbx` · **Task:** `sbx-prc-01`
**Element:** *Explicitly Identifying Unknown Information* (2026 SBOM Minimum
Elements, Practices and Processes). Major update — the 2021 NTIA element was
*Known Unknowns*.
**Gap analysis:** [sbom-2026-minimum-elements-gap-analysis.md](../compliance/sbom-2026-minimum-elements-gap-analysis.md)
sections 1.3 and 3.3.

---

## What changed in the standard

The 2021 element asked only that a missing field be flagged. The 2026 element
renames it and **splits the flag in two**. Where a field is not provided, the
author must state explicitly whether the value is

- **unknown to the author** — the author looked and could not establish it, or
- **withheld by the author** — the author knows it and is deliberately not
  disclosing it.

Two further obligations come with the split:

1. The author must **have a process** by which recipients can ask about redacted
   security-related information.
2. An SBOM **withholding essential component data may be considered incomplete**.

The distinction is not cosmetic. A recipient reading *unknown* learns that nobody
holds the fact and that chasing it is pointless. A recipient reading *withheld*
learns that the fact exists, is held by a named party, and can be asked for. The
two lead to different actions, which is exactly why conflating them is a defect.

## What ICDEV did before

One literal, `"unspecified"` — plus `"managed"` for a Maven dependency whose
version lives in a parent POM. Both were emitted straight into
`component.version`. Neither is machine-processable, and neither says which of
the two states it means; a recipient could not tell an unpinned `requirements.txt`
line from a deliberate redaction. Nothing anywhere in the tree recorded a
recipient enquiry route.

---

## The convention

One convention, applied uniformly to all 17 data-field elements. Three parts.

### 1. An in-band sentinel

The native field carries the literal `unknown` or `withheld`. This exists so a
schema that requires the field still validates, and so a naive reader is never
shown a plausible-looking value in place of one that was never established.

### 2. An out-of-band property — the authoritative statement

```json
{"name": "icdev:unknown:version",    "value": "declared-without-a-version"}
{"name": "icdev:withheld:hash_value", "value": "export-controlled"}
```

The **property name carries the state** and the **value carries the reason**. A
reader that understands nothing but the two prefixes already has the distinction
the standard asks for. `icdev:unknown-detail:<field>` may elaborate on an
unknown; it is never emitted for a withheld field, because explaining a redaction
inside the document the redaction protects undoes it.

Where the sentinel and the property disagree, the validator fails the document.
The property wins as the record; the sentinel is a courtesy.

Fields whose CycloneDX carrier is not a plain string — `hashes`, `licenses`,
`dependencies` — have no sentinel and live only in the properties. That is why
the properties, not the sentinel, are authoritative.

### 3. Disjoint reason vocabularies

| Unknown to the author | Withheld by the author |
|---|---|
| `not-provided-by-producer` | `classification-restricted` |
| `declared-without-a-version` | `export-controlled` |
| `version-managed-by-parent` | `contractual-restriction` |
| `artifact-not-accessible` | `operational-security` |
| `package-metadata-absent` | `proprietary` |
| `producer-not-identifiable` | `active-vulnerability-mitigation` |
| `not-resolvable-offline` | |
| `tool-did-not-report` | |

The two sets **share no member**. A reason code therefore identifies its own
state, and a withheld reason recorded under the unknown prefix is a structural
error the validator catches — not a matter of discipline. `Disclosure.unknown()`
raises on a withheld reason and `Disclosure.withheld()` raises on an unknown one,
so the mistake cannot be made at authoring time either.

A field is in at most one state: recording either evicts the other, because a
value cannot simultaneously be beyond the author's knowledge and held back by
them.

### Field vocabulary

All 17 elements, named in `snake_case`: the nine metadata elements
(`sbom_author`, `author_signature`, `data_format_name`, `data_format_version`,
`generation_context`, `timestamp`, `tool_name`, `tool_version`, `sbom_version`)
and the eight component elements (`producer`, `dependency_relationship`,
`hash_value`, `hash_algorithm`, `identifiers`, `license`, `name`, `version`). A
property naming anything else fails validation — an unrecognised field name is
unreadable to a recipient's tooling, and accepting one would let a typo pass for
a disclosure.

---

## Recipient enquiry process

> **This is the process the standard requires the SBOM author to have.** It is
> emitted into every ICDEV-generated SBOM as `icdev:sbom:enquiry-process`,
> alongside `icdev:classification` and `icdev:distribution`.

ICDEV marks every SBOM `CUI // SP-CTI` with `Distribution D — Authorized DoD
Personnel Only`. Those markings *are* the withholding posture in the standard's
sense: they tell a recipient what they may not have. The enquiry route is what
makes that answerable rather than final, so it is emitted on **every** document,
not only on documents that withhold a field.

### The process

1. **Identify what you are asking about.** A request names the SBOM
   `serialNumber`, the component `bom-ref`, and the field named in the
   `icdev:withheld:<field>` property. The reason code on that property is the
   category of the restriction and tells the requester which authority the answer
   will turn on.
2. **Submit it.** To the organization named in the document's distribution
   statement, or to the address in `icdev:sbom:enquiry-contact` /
   `icdev:sbom:enquiry-uri` when the operator has configured one.
3. **Receipt and authorization check.** The author confirms receipt and verifies
   the requester's authorization against the document's distribution statement.
4. **Release or state the authority.** The author either releases the value under
   that authorization, or states the specific authority for continued
   withholding. "No" with a named authority is a conforming answer; silence is
   not.
5. **Response target.** Within `icdev:sbom:enquiry-response-target-days` — 30 by
   default.

**Exception:** a request concerning `active-vulnerability-mitigation` is handled
under the author's coordinated vulnerability disclosure process instead, not this
one. Answering "which version of the vulnerable component" through a general
enquiry channel would disclose an unpatched vulnerability to whoever asked.

### Configuring it

`args/sbom_disclosure_policy.yaml`, or the environment for a deployment that
should not edit the file:

```bash
ICDEV_SBOM_ENQUIRY_PROCESS=...   # replace the process text
ICDEV_SBOM_ENQUIRY_CONTACT=sbom@example.mil
ICDEV_SBOM_ENQUIRY_URI=https://example.mil/sbom/requests
```

A missing or unreadable policy file degrades to a default that withholds nothing
and **still states an enquiry route**. The route can never come back empty,
because an SBOM that withholds a field and names no way to ask about it is
precisely what the standard forbids — and the validator enforces that: any
withholding without `icdev:sbom:enquiry-process` is an error.

---

## Withholding: declared, never inferred

Unknown is *discovered* at generation time. Withheld is *declared* by the
operator, in `args/sbom_disclosure_policy.yaml`:

```yaml
withhold:
  components:
    - match: {purl: "pkg:maven/mil.example.restricted/"}
      field: version
      reason: classification-restricted
    - match: {ecosystem: golang, name: internal-crypto}
      field: hash_value
      reason: export-controlled
```

Two rules govern how those are read, both chosen so that a mistake fails visibly:

- **A rule with an unrecognised field or reason is dropped, not defaulted.**
  Guessing which redaction category the operator meant is the kind of invention
  this element exists to prevent. `--policy` lists every dropped rule and exits
  non-zero, so a mistyped redaction is loud rather than silently not applied. In
  particular an *unknown*-reason such as `not-provided-by-producer` is rejected
  here: it belongs to the other state.
- **An unmatched `match` key is a non-match, not a wildcard.** A redaction that
  accidentally applied to the whole dependency tree would be far worse than one
  that applied to nothing, because only the second is visible on inspection. A
  rule with no `match` block at all does apply to every component — deliberate,
  and rarely what you want.

Withholding wins over unknown for the same field: "we are not telling you" is the
stronger and more actionable statement than "we could not establish it".

ICDEV's own builds withhold nothing. Everything they cannot state is genuinely
unknown to them, and is emitted as such.

### Completeness

`icdev:sbom:disclosure-completeness` is `complete`, or
`incomplete-withheld` when one of the **essential component fields** — `producer`,
`name`, `version`, `identifiers`, `license` — is withheld. That is the standard's
"may be considered incomplete" sentence made machine-readable.

`hash_value` and `hash_algorithm` are deliberately *not* essential: the
standard's own example of legitimately absent data is an author who cannot reach
the artifact, and a withheld hash does not stop a recipient identifying the
component.

**Unknowns never make a document incomplete.** An SBOM full of honest unknowns is
a data-quality signal; the incompleteness sentence is about withholding.

This is a *different* property from `icdev:sbom:coverage` (sbx-cov-01). Coverage
is about which components are listed. This is about which fields on a listed
component are disclosed.

---

## SPDX

SPDX has one marker, `NOASSERTION`, and no way to say "withheld" in a field. So
both states map to `NOASSERTION` in the native SPDX field and the distinction
moves to an annotation:

```
version: NOASSERTION because the value is UNKNOWN to the SBOM author (not-provided-by-producer).
license: NOASSERTION because the value is WITHHELD by the SBOM author (proprietary).
         The value is known to the author; see the SBOM's recipient enquiry process to request it.
```

The wording is fixed so a parser can match on `UNKNOWN`/`WITHHELD`. The state is
never lost in translation; it changes carrier. `spdx_mapping()` renders this for
sbx-fmt-01's writer to emit — that task consumes it, it does not have to
re-derive it.

---

## What the generator emits now

- The pre-2026 literals are gone. `tests/test_sbom_unknown_information.py`
  asserts against the *parsed constants* of both `tools/compliance/sbom_generator.py`
  and its `icdev/` mirror, so neither can reintroduce them.
- An unpinned `requirements.txt` line yields `version: "unknown"` plus
  `icdev:unknown:version = declared-without-a-version`, and a purl with **no**
  version segment — a purl claiming `@unspecified` was a false identifier.
- A Maven dependency whose version lives in a parent POM yields
  `version-managed-by-parent`, which is a distinct fact from "nobody declared
  one" and is now recorded as such.
- The Component Producer element (sbx-fld-02) joins the uniform convention. Its
  own `icdev:component-producer*` properties are untouched — the finer-grained
  reason (`forge-host-is-not-a-producer` and so on) survives as the unknown's
  detail — and the component *additionally* states
  `icdev:unknown:producer = producer-not-identifiable`. A validator can then read
  every undisclosed field of every element from one pair of prefixes without
  knowing that the producer element has properties of its own.
- Document totals are two separate properties, `icdev:sbom:fields-unknown` and
  `icdev:sbom:fields-withheld`. A single "undisclosed fields" number would
  re-create the conflation the split exists to remove: 40 unknown fields is a
  data-quality problem, 40 withheld fields is a policy decision the recipient can
  appeal.

## Persistence

`Disclosure.db_values()` returns the pair for `sbom_components.unknown_fields_json`
and `withheld_fields_json` — the two columns sbx-fnd-02 created, both
`NOT NULL DEFAULT '{}'`, which an empty record round-trips to exactly. Two
columns rather than one keyed by state, so "count the withheld ones" is a query
rather than a JSON walk.

The component writer itself is **sbx-fld-04's** `_persist_components`; this task
exports the serializer for it to call in one line rather than adding a second
writer that would collide with it — the same seam sbx-fld-02 left for
`producer_db_value()`.

---

## Verification

```bash
# Conformance of any CycloneDX document, ICDEV-produced or not
python tools/compliance/unknown_information.py --validate path/to/sbom.cdx.json --json

# The policy in force, and any rule that was dropped rather than applied
python tools/compliance/unknown_information.py --policy --json

# The 17 fields and the two disjoint reason vocabularies
python tools/compliance/unknown_information.py --vocabulary --json

# Tests
pytest tests/test_sbom_unknown_information.py -v
```

`validate_sbom_disclosure()` is the acceptance criterion in executable form and
returns `(errors, summary)`. The summary keeps `fields_unknown` and
`fields_withheld` as **two numbers that are never added together** — a single
total would let a document move a field from unknown to withheld without the
document-level statement changing, which is the exact substitution this element
bans. sbx-sig-02's conformance validator can call it as-is, as it can
`component_producer.validate_sbom_producers`.

What it rejects:

| Defect | Why it matters |
|---|---|
| A withheld reason under `icdev:unknown:` (or the reverse) | The conflation the element exists to remove |
| A field marked both unknown and withheld | Two contradictory claims about one value |
| A bare `unknown`/`withheld` sentinel with no property | A state with no reason is not an explicit identification |
| A sentinel disagreeing with its property | The document says two different things |
| A declared-unknown field carrying a real value | The disclosure is stale or wrong |
| `unspecified` / `managed` anywhere | The pre-2026 literal, which says neither state |
| An unrecognised field name | Unreadable to a recipient's tooling |
| Withholding with no `icdev:sbom:enquiry-process` | The standard's process requirement |
| `disclosure-completeness: complete` while withholding essentials | Contradicts the standard's incompleteness sentence |
| `icdev:unknown-detail:` on a withheld field | Explains the redaction, undoing it |

---

## Downstream

`sbx-fld-02` through `sbx-fld-06` each land an element that can be unknown or
withheld. They call `Disclosure.unknown()` / `.withheld()` and
`apply_to_cyclonedx()` rather than inventing a per-element marker, and their
per-component records reach the document totals through
`completeness_properties()`. `sbx-fmt-01` calls `spdx_mapping()`. `sbx-sig-02`
calls `validate_sbom_disclosure()`.

# CUI // SP-CTI
