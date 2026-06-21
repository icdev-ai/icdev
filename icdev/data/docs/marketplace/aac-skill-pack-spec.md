# AAC Skill Pack Bundle Format Specification

**Version:** 1.0.0
**Status:** Draft
**Scope:** ICDEV FORGE Marketplace — Framework-Specific Assured AI Coding (AAC) Rule Packs

---

## 1. Overview

An **AAC Skill Pack** is a versioned, self-contained bundle that extends ICDEV's Assured AI Coding engine with framework-specific guardrails, patterns, and Semgrep rules. Third-party publishers (vendors, internal teams, community contributors) package and submit these bundles to the ICDEV FORGE marketplace for discovery, validation, and installation.

This document defines:
- The required and optional files within a pack
- The `skill_pack.yaml` manifest schema
- Validation and packaging rules
- Example packs for common framework/language pairings

---

## 2. Bundle Layout

A valid AAC Skill Pack is a **ZIP archive** (`.aacsp`) with the following root contents:

```
{pack-name}-{version}.aacsp
├── skill_pack.yaml                 # REQUIRED — manifest
├── aac_rules/                      # REQUIRED — Semgrep YAML rules
│   ├── rule-001.yaml
│   ├── rule-002.yaml
│   └── ...
├── pattern_catalog_extension.json   # REQUIRED — framework pattern overrides
├── guardrails_extension.json        # REQUIRED — additional guardrails
├── README.md                        # REQUIRED — human-readable description
└── (optional assets: icons, changelogs, provenance attestations)
```

### File Requirements

| File | Cardinality | Description |
|------|-------------|-------------|
| `skill_pack.yaml` | 1 | Manifest: metadata, targeting, dependencies, signing |
| `aac_rules/` | 1 directory, ≥1 file | Semgrep rules in YAML format (see Section 3) |
| `pattern_catalog_extension.json` | 1 | Framework-specific pattern catalog deltas (see Section 4) |
| `guardrails_extension.json` | 1 | Additional guardrails for the target domain (see Section 5) |
| `README.md` | 1 | Human-readable pack description, usage, examples |

---

## 3. Manifest (`skill_pack.yaml`)

```yaml
# Required fields
name: python-fastapi-aac          # kebab-case, unique within marketplace
version: 1.2.0                    # semver
publisher:
  name: Acme Security
  contact: security@acme.example
  url: https://acme.example

# Targeting — determines when the pack is active
target_languages:
  - python
target_frameworks:
  - fastapi
  - starlette

# Optional: minimum ICDEV version that understands this pack format
minimum_icdev_version: "3.4.0"

# Optional: pack-level dependencies on other AAC skill packs
dependencies:
  - name: python-base-aac
    version: ">=2.0.0"

# Security / signing
signature:
  algorithm: ed25519
  public_key: "-----BEGIN PUBLIC KEY-----\n..."
  signed_digest: "sha256=..."

# Classification (CUI per ICDEV compliance rules)
classification:
  banner: CUI
  distribution: LIMITED
```

### Validation Rules
- `name` must match `^[a-z0-9-]+$`, ≤64 characters.
- `version` must be strict semver (`MAJOR.MINOR.PATCH`).
- At least one entry in `target_languages` and `target_frameworks`.
- `signature` is optional during development; **required** for marketplace publish.

---

## 4. Semgrep Rules (`aac_rules/`)

Each `.yaml` file under `aac_rules/` must be a valid Semgrep rule file containing one or more rules. Rules should be scoped to the declared `target_languages` and `target_frameworks`.

### File Naming Convention
- `aac_rules/{severity}-{category}-{id}.yaml`
- Example: `aac_rules/high-injection-sql-001.yaml`

### Rule Metadata Requirements
Every rule must include:
- `id` — unique within the pack, prefixed with pack name (e.g., `python-fastapi-aac.injection-sql-001`)
- `message` — human-readable finding description
- `severity` — `ERROR`, `WARNING`, or `INFO`
- `metadata` block with:
  - `framework`: matching `target_frameworks`
  - `aac_category`: one of `injection`, `crypto`, `auth`, `secrets`, ` deserialization`, `race`, `logging`, `config`
  - `references`: list of URLs
  - `cwe`: list of CWE identifiers

### Example Rule (`aac_rules/high-injection-sql-001.yaml`)

```yaml
rules:
  - id: python-fastapi-aac.injection-sql-001
    pattern: |
      cursor.execute($X % (...))
    languages:
      - python
    message: |
      Detected string-formatting-based SQL query construction. Use parameterized queries.
    severity: ERROR
    metadata:
      framework: fastapi
      aac_category: injection
      cwe:
        - CWE-89
      references:
        - https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html
```

---

## 5. Pattern Catalog Extension (`pattern_catalog_extension.json`)

This file extends or overrides the ICDEV base pattern catalog with framework-specific patterns. It is a JSON array of pattern objects.

```json
[
  {
    "pattern_id": "fastapi-route-handler",
    "category": "framework_convention",
    "language": "python",
    "framework": "fastapi",
    "description": "Identifies FastAPI route handler functions for guardrail scoping",
    "matchers": [
      {
        "type": "decorator",
        "pattern": "@app.(get|post|put|delete|patch|head|options|websocket)"
      }
    ],
    "tags": ["routing", "entrypoint"]
  },
  {
    "pattern_id": "pydantic-model-field",
    "category": "data_validation",
    "language": "python",
    "framework": "fastapi",
    "description": "Matches Pydantic model field definitions for input validation rules",
    "matchers": [
      {
        "type": "class_attribute",
        "base_class": "pydantic.BaseModel"
      }
    ],
    "tags": ["validation", "schema"]
  }
]
```

### Schema
- `pattern_id` — unique within the pack, kebab-case.
- `category` — one of: `framework_convention`, `data_validation`, `middleware`, `dependency_injection`, `orm`, `template`, `build`, `test`.
- `language`, `framework` — must intersect with `skill_pack.yaml` targets.
- `matchers` — array of matcher objects; `type` must be one of: `decorator`, `function_signature`, `class_attribute`, `import`, `ast_node`.

---

## 6. Guardrails Extension (`guardrails_extension.json`)

This file defines additional guardrails that augment the base AAC guardrail set for the target domain.

```json
{
  "guardrails": [
    {
      "id": "gr-fastapi-no-global-app-state-secrets",
      "name": "No Secrets in Global App State",
      "description": "Prohibits storing sensitive values in FastAPI/Starlette app.state",
      "severity": "critical",
      "scope": ["python"],
      "frameworks": ["fastapi", "starlette"],
      "trigger": {
        "type": "assignment",
        "target": "app.state.*",
        "prohibited_values": ["API_KEY", "SECRET", "PASSWORD", "TOKEN"]
      },
      "remediation": "Use ICDEV secret_manager or environment variables with ICDEV config loader."
    },
    {
      "id": "gr-fastapi-dependency-injection-audit",
      "name": "Audit Dependency Injection Targets",
      "description": "All FastAPI dependency functions must be decorated with @icdev.audited",
      "severity": "high",
      "scope": ["python"],
      "frameworks": ["fastapi"],
      "trigger": {
        "type": "function_decorator",
        "decorator": "Depends",
        "require_decorator": "icdev.audited"
      },
      "remediation": "Add @icdev.audited to dependency functions or register in allowlist."
    }
  ]
}
```

### Schema
- `id` — unique, prefixed with pack shorthand.
- `severity` — `critical`, `high`, `medium`, `low`, `info`.
- `scope` — array of languages (must intersect with manifest).
- `frameworks` — array of frameworks (must intersect with manifest).
- `trigger` — matcher object; `type` is one of: `assignment`, `function_call`, `function_decorator`, `import`, `class_inheritance`.
- `remediation` — human-readable fix guidance.

---

## 7. README.md

Must contain:
1. **Title** — human-readable pack name.
2. **Description** — 2-4 sentences on what the pack covers.
3. **Supported Versions** — language and framework version ranges.
4. **Rules Summary** — table or list of included Semgrep rules.
5. **Guardrails Summary** — list of added guardrails.
6. **Installation** — how to install via ICDEV CLI or marketplace UI.
7. **Changelog** — version history (or link to `CHANGELOG.md`).
8. **License** — SPDX identifier and full text or link.

---

## 8. Example Packs

The following example packs illustrate the format for common stacks. They are **documentation-only**; actual packs may be registered in the marketplace separately.

| Pack Name | Languages | Frameworks | Focus |
|-----------|-----------|------------|-------|
| `python-fastapi-aac` | python | fastapi, starlette | Async injection, dependency audit, Pydantic validation |
| `java-spring-aac` | java | spring-boot, spring-web | Bean injection, JPA queries, actuator exposure |
| `dotnet-aspnet-aac` | csharp | aspnetcore, efcore | Razor injection, middleware pipeline, config secrets |
| `go-chi-aac` | go | chi, gorilla | Middleware ordering, context values, handler signatures |
| `rust-axum-aac` | rust | axum, tokio | Extractor safety, tower middleware, spawn lifecycle |
| `ts-nestjs-aac` | typescript | nestjs, express | Decorator metadata, provider scope, DI container leaks |

Each pack follows the bundle layout, manifest schema, and file requirements defined above.

---

## 9. Packaging & Distribution

### Building a Pack
```bash
# Validate manifest and rules
python tools/marketplace/aac_pack_validator.py --input ./my-pack/ --strict

# Sign and package (produces .aacsp file)
python tools/marketplace/aac_pack_builder.py --input ./my-pack/ --sign --output ./dist/
```

### Publishing
1. Build and sign the `.aacsp` file.
2. Submit to the ICDEV FORGE marketplace via:
   - CLI: `icdev marketplace publish --file ./dist/my-pack-1.0.0.aacsp`
   - API: `POST /api/marketplace/assets` with multipart upload
3. The marketplace runs the Asset Scanner (7-gate pipeline: SAST, secrets, deps, CUI, SBOM, provenance, signature).
4. Upon passing, the asset enters the catalog with `status: vetted`.

### Installation
```bash
# Install into current project
icdev marketplace install --asset-id python-fastapi-aac --version 1.2.0

# Or via API
POST /api/marketplace/install
{ "asset_id": "python-fastapi-aac", "version": "1.2.0", "tenant_id": "..." }
```

---

## 10. Versioning & Compatibility

- **Pack format version** is declared in `skill_pack.yaml` via `minimum_icdev_version`.
- **ICDEV guarantees** backward compatibility for format versions within a major ICDEV release.
- Breaking changes to the pack format will be announced in ICDEV release notes and ADRs.

---

## 11. Compliance & Classification

All published AAC Skill Packs must:
- Include a valid CUI classification banner in `skill_pack.yaml`.
- Pass the marketplace 7-gate security scan.
- Include an SBOM for any bundled binary assets (none expected for pure YAML/JSON packs).
- Be signed with an Ed25519 key registered in the tenant or central trust registry.

---

## 12. References

- [Semgrep Rule Syntax](https://semgrep.dev/docs/writing-rules/rule-syntax)
- [ICDEV FORGE Marketplace Guide](docs/marketplace/index.md)
- [ICDEV AAC Engine Architecture](docs/reference/architecture.md#aac-engine)
- [Compliance & Security Rules](docs/reference/compliance-security.md)

---

**Classification:** CUI // SP-CTI
**Last Updated:** 2026-05-22
