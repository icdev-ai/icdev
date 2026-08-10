# CUI // SP-CTI

# Component Name alternates and Component Version unknown-marking (sbx-fld-06)

**Elements:** Component Name (minor update), Component Version (major update) —
*2026 Minimum Elements for a Software Bill of Materials*.

**Modules:** `tools/compliance/component_names.py` (new),
`tools/compliance/sbom_generator.py`, `tools/compliance/dependency_resolver.py`,
`tools/security/ai_bom_generator.py`. Each mirrored at `icdev/`.

**Tests:** `tests/test_sbom_component_names.py` (38).

---

## What the standard asks for

> **Component Name** — Name assigned to a unit of software. *Data formats
> implementing this element must allow multiple entries to capture alternate
> names.*
>
> **Component Version** — Identifier used by the supplier to specify a change
> from a previously identified version. *If the producer does not provide a
> version, the SBOM author must indicate the version is unknown.*

---

## What ICDEV did before

**Component Name was worse than "one name per component".** ICDEV emitted a name
it had *derived*:

| Ecosystem | The producer's name | What ICDEV emitted |
|---|---|---|
| python | `Flask`, read from `METADATA`'s `Name:` and then discarded | `flask` |
| python | `Flask_Login` | `flask-login`, and neither other spelling |
| npm | `@babel/core` | `core`, with `@babel` split off into `group` |
| maven | `org.apache.commons:commons-lang3` | `commons-lang3`, ambiguous across groups |
| golang | `github.com/spf13/cobra` | that only, never the `cobra` everyone calls it |

**Component Version** wrote the literal `"unspecified"` for an unresolved version
and `"managed"` for a Maven dependency whose version lives in a parent POM.
Neither is a machine-interpretable unknown marker, and both conflate the two
states that Explicitly Identifying Unknown Information exists to separate.
sbx-prc-01 replaced the literals in the parsers; this card closed what was left.

---

## What it does now

### Component Name

`component_names.derive_names(component)` returns
`{"primary": str, "alternates": [{"name", "kind"}]}`. **The primary does not
move** — the purl, the `bom-ref` and `_component_identity` all key on it, and
`component_id` makes it the `sbom_components` primary key as well — so changing
which spelling wins would renumber every component in every historical document
to say nothing new. The element asks for the alternates to be *available*, not
for a different one to be *preferred*.

| Kind | Ecosystem | The alternate |
|---|---|---|
| `declared` | any | the spelling as the manifest wrote it, before normalization |
| `normalized` | python | the PEP 503 form of the declared spelling |
| `qualified` | npm, maven, gradle | `@scope/name`, `groupId:artifactId` |
| `short` | golang | the last segment of a path-shaped name |
| `purl` | any | the decoded name segment of the purl, when it differs |

Every alternate is a **mechanical transform of a field already in the component
record**. Nothing is accepted from an input document, no registry is consulted
and no network call is made, so the module is pure and behaves identically in an
air-gapped enclave. Two derivations that produce the same string yield one
alternate: the earlier kind in `NAME_KINDS` keeps it, so two runs over one input
diff clean.

Carrying the producer's spelling took five call sites, because normalization
happens in five places: `_parse_requirements_txt`, `_parse_pyproject_toml`, and
the three `dependency_resolver` Python paths — `_resolve_python_lock`,
`_resolve_pipfile_lock` and `_resolve_python_environment`. It also had to survive
`_adopt_declared`, the reshape every declared manifest passes through.

### Carrier

CycloneDX has no alternate-name array in any spec version ICDEV emits, so the
alternates are properties:

```json
{"name": "icdev:component-name-alternate:declared",  "value": "Flask"}
{"name": "icdev:component-name-alternate:qualified", "value": "@babel/core"}
```

`properties` is an array and does not require unique names, so the repetition
**is** the "multiple entries" the element obliges the format to allow. The pair
round-trips through `parse_names_from_cyclonedx`, and `spdx_writer` carries the
whole `properties` array through as an annotation, so one emission gives both
serializations the element.

### Two interactions

- **A withheld name has no alternates.** Publishing `@internal/core` beside a
  `name` of `withheld` would undo the redaction. The generator passes the
  `Disclosure`, and the same holds for a name that is *unknown* — there is no
  established spelling to have alternates of.
- **Deduplication no longer loses a spelling.** `_component_identity` keys on the
  normalized name, so `Flask` in `requirements.txt` and `FLASK` in
  `pyproject.toml` are one component and the loser's spelling would vanish. The
  spellings are collected against the surviving identity and re-attached. Losing
  a name is precisely what this element exists to prevent.

### Component Version

Three defects, all pre-existing, all invisible to a per-parser test:

1. **`_adopt_declared` dropped `version_unknown_reason`.** It rebuilt every
   declared component from a fixed field list, so each declared unknown flattened
   to "nobody pinned one" — silently losing the Maven `version-managed-by-parent`
   case, which is the one an operator can act on. The per-parser tests passed
   throughout, because they call the parser directly and never cross that seam.
2. **`_parse_csproj` dropped a versionless `PackageReference` outright.** Central
   Package Management puts the version in `Directory.Packages.props`, which this
   declared-only parser does not read, and every pattern required a `Version`. The
   component vanished from the SBOM rather than appearing with its version stated
   as unknown — a Coverage failure as much as a Component Version one.
3. **`tools/security/ai_bom_generator.py` still wrote `"unspecified"`** into the
   AI-BOM. It now writes the shared `unknown` sentinel. Its risk rule still
   recognises the old literal, because a BOM already in the database says it —
   what changed is only what the generator *writes*.

---

## CLI

```bash
python tools/compliance/component_names.py --validate compliance/sbom.cdx.json --json
python tools/compliance/component_names.py --name core --group "@babel" \
    --purl "pkg:npm/%40babel%2Fcore@7.23.9" --json
```

`--validate` reports `components`, `components_with_alternates` and
`alternate_names`, and exits non-zero on a conformance failure: an alternate that
repeats the primary, one listed twice, or one carrying a kind outside
`NAME_KINDS`.

---

## Verification

`tests/test_sbom_component_names.py` — 38 tests:

- **One per parser that ever emitted a placeholder** — `requirements.txt`,
  `pyproject.toml`, `package.json`, `Cargo.toml` (both declaration forms),
  `pom.xml` (including the parent-managed case), `.csproj` (including the
  versionless reference), `go.mod` — plus a whole-tree sweep asserting no legacy
  sentinel survives by any path. "No placeholder anywhere" is a claim about six
  independent regex parsers, and a whole-file grep cannot tell a fixed parser
  from a deleted one.
- **The two seams**: `_adopt_declared` keeps the reason and the declared
  spelling, and a parent-managed Maven version still says so in the finished
  document.
- **Per-ecosystem alternates**, the CycloneDX and JSON round trips, the
  deterministic derivation order, and the negative validation paths.
- **End to end**: a real database, real manifests on disk, a real file written —
  asserting the producer's spelling reached the artifact, the unpinned dependency
  says unknown and why, and neither `"unspecified"` nor `"managed"` appears
  anywhere in the serialized bytes.
- **Mirror parity** for all four changed modules.

---

## Sandbox coverage

Gap 56 in [docs/security/sandbox-coverage.md](../security/sandbox-coverage.md):
**bypass-documented**. Every input is treated as an opaque string and every
output is a rewriting of one — no `exec`, no `subprocess`, no SQL, no network, no
content-derived filesystem path. A hostile package name becomes a string in a
property value; it is never interpreted.
