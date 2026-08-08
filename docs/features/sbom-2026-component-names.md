# CUI // SP-CTI

# Component Name alternates and Component Version unknown-marking (sbx-fld-06)

**Elements:** Component Name (minor update), Component Version (major update) —
*2026 Minimum Elements for a Software Bill of Materials*, CISA et al., v2.1, 2026-07-29.

**Modules:** `tools/compliance/component_names.py` (new),
`tools/compliance/sbom_generator.py`, `tools/compliance/dependency_resolver.py`.
Each mirrored at `icdev/tools/compliance/`.

**Tests:** `tests/test_sbom_component_names.py` (37).

---

## What the standard asks for

> **Component Name** — Name assigned by the component producer. *Data formats
> implementing this element must allow multiple entries to capture alternate names.*
>
> **Component Version** — Producer's version identifier. *If the producer does not
> provide a version, the SBOM author must indicate the version is unknown.*

---

## What ICDEV did before

**Component Name was worse than "one name per component".** The element is defined
as the name assigned by the *producer*, and ICDEV emitted a name it had derived:

| Ecosystem | The producer's name | What ICDEV emitted |
|---|---|---|
| python | `Flask` (from `METADATA`'s `Name:`) | `flask` |
| python | `zope.interface` | `zope.interface` — and never the PEP 503 `zope-interface` a vuln feed is keyed on |
| npm | `@babel/core` | `core`, with `@babel` split off into `group` |
| maven | `org.apache.commons:commons-lang3` | `commons-lang3`, ambiguous across groups |
| golang | `github.com/foo/bar/v2` | that only, never the `/v2`-less path the same project is also cited by |

**Component Version** wrote the literal `"unspecified"` for an unresolved version
and `"managed"` for a Maven dependency whose version lives in a parent POM.
Neither is a machine-interpretable unknown marker, and both conflate the two
states that Explicitly Identifying Unknown Information exists to separate.
sbx-prc-01 replaced the literals in the parsers; this card closed what was left.

---

## What it does now

### Component Name

`component_names.alternate_names(component)` returns every additional name a
component is legitimately known by. The **primary name does not move** — the
purl, the `bom-ref` and `_component_identity` all key on it — and the rest
become alternates:

| Source code | Ecosystem | Alternate |
|---|---|---|
| `producer-declared-spelling` | any | `declared_name`, the string the producer published under |
| `pep-503-normalized` | python | the PEP 503 form where the primary is not already it |
| `registry-scoped-name` | npm | `@scope/name`, reassembled from `group` |
| `ecosystem-coordinate` | maven, gradle | `groupId:artifactId` |
| `go-module-path-without-major-version` | golang | the module path minus `/vN` |

Every alternate is a **mechanical transform of a field already in the component
record**. Nothing is accepted from an input document and nothing is inferred, so
the module is pure and offline — no filesystem read, no network, no subprocess.

Carrying `declared_name` took five call sites, because normalization happens in
five places: `_parse_requirements_txt`, `_parse_pyproject_toml`,
`dependency_resolver._resolve_python_environment` (which read `Name: Flask` and
then discarded it), `_resolve_python_lock` and `_resolve_pipfile_lock`. It also
had to survive `_adopt_declared`, the reshape every declared manifest passes
through on the way to a real SBOM.

### CycloneDX mapping

No supported spec version has an alias array on `component`, so the alternates
travel in properties — the seam Component Producer (sbx-fld-02) and the
disclosure convention (sbx-prc-01) already use:

```json
{"name": "icdev:component:alternate-name",                    "value": "@babel/core"},
{"name": "icdev:component:alternate-name-source:@babel/core", "value": "registry-scoped-name"}
```

The bare property **repeats, once per alternate** — that repetition is the
"multiple entries" the element requires. A single joined string would satisfy a
schema and not the standard. Properties are emitted sorted, so regenerating an
unchanged SBOM stays byte-stable, and `alternate_names_from_cyclonedx` reads them
back into the same records, so a consumer that re-emits a component does not
quietly drop the extra names.

### Component Version

Every declared parser marks an absent version with the `unknown` sentinel plus
`icdev:unknown:version` naming why, and the purl no longer carries a version
segment it does not have:

| Parser | Input with no version | Reason emitted |
|---|---|---|
| `_parse_requirements_txt` | `requests` | `declared-without-a-version` |
| `_parse_pyproject_toml` | `dependencies = ["urllib3"]` | `declared-without-a-version` |
| `_parse_package_json` | `"left-pad": "*"` | `declared-without-a-version` |
| `_parse_cargo_toml` | both the `= ""` and `{ path = ... }` forms | `declared-without-a-version` |
| `_parse_pom_xml` | `<dependency>` with no `<version>` | `version-managed-by-parent` |
| `_parse_csproj` | `<PackageReference Include="X" />` | `declared-without-a-version` |

`version-managed-by-parent` is deliberately its own reason: a version held in a
parent POM's `dependencyManagement` is not unknown to the world, it is
unresolvable by a *declared-only* parser — the fact the Coverage element
(sbx-cov-01) can actually act on.

---

## Three defects found while proving the above

1. **`_adopt_declared` was flattening the reason.** It rebuilt every declared
   component from a fixed field list and dropped `version_unknown_reason`, so the
   Maven parent-POM case silently degraded to `declared-without-a-version` in
   every real SBOM. The per-parser tests never caught it because they call the
   parsers directly and never cross that seam. Pinned now by
   `test_the_declared_adoption_keeps_the_reason_the_parser_established`.

2. **`_parse_csproj` dropped versionless references entirely.** Every pattern in
   it required a `Version`, so a `<PackageReference Include="X" />` under Central
   Package Management — where the version lives in `Directory.Packages.props` —
   vanished from the SBOM rather than being listed with an unknown version. That
   is a Coverage failure on top of a Component Version one.

3. **`_parse_go_mod` invented a component named `(`.** Its single-line `require`
   pattern separated groups with `\s`, which crosses a newline, so the block
   header `require (` swallowed the first module of the block and emitted a
   component named `(` whose *version* was a module path. The parenthesized block
   is how essentially every real `go.mod` is written, so this corrupted both of
   this card's elements in every Go SBOM ICDEV had ever produced.

Also fixed: `component_names.py` and `component_producer.py` import
`tools.compliance.dependency_resolver` absolutely, which does not resolve when
the module is run by path — every `python tools/compliance/component_producer.py`
form in `docs/reference/commands.md` raised `ModuleNotFoundError`. Both now
bootstrap the repo root onto `sys.path` when `__package__` is empty.

---

## Interactions

**A withheld name emits no alternates.** A component whose `name` sbx-prc-01's
policy withholds gets none, and `validate_document` fails a document that pairs
them — an alternate would hand back the value the redaction removed.

**Deduplication no longer loses a spelling.** Two instances differing only in the
producer's spelling collapse to one component, so `_build_cyclonedx_sbom` collects
the spellings of collapsed twins *before* deduplicating and hands them to the
derivation. `Flask` in `requirements.txt` and `FLASK` in `pyproject.toml` yield
one component with both alternates, rather than whichever won the race.

---

## CLI

```bash
python tools/compliance/component_names.py --validate "/path/to/sbom.cdx.json" --json
python tools/compliance/component_names.py --names    "/path/to/sbom.cdx.json" --json
python tools/compliance/component_names.py --vocabulary --json
```

`--validate` exits 1 on a conformance failure. It rejects an alternate with no
source, with a source outside the closed vocabulary, repeating the primary name,
repeated outright, or present on a component whose name is undisclosed.

## Library

```python
from tools.compliance.component_names import (
    alternate_names,               # component record -> [{"name", "source"}]
    component_names,               # every name, primary first
    apply_to_cyclonedx,            # append alternates to a CycloneDX component
    alternate_names_from_cyclonedx,  # read them back — the round trip
    all_names_from_cyclonedx,      # primary + alternates, from a document
    ecosystem_of,                  # stated `ecosystem`, else derived from the purl
    validate_document,
)
```

---

## Still open

* **SPDX** has no alias field either, so sbx-fmt-01 decides how the alternates
  cross into that format. `all_names_from_cyclonedx` is exported for it.
* **Persistence** alongside `sbom_components` waits on sbx-fld-04's
  `_persist_components`, the single writer — exactly as the producer and the
  disclosure convention do. Adding a second writer would collide.
