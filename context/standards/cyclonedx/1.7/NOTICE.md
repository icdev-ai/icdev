# CycloneDX 1.7 JSON schema (vendored, unmodified)

- Files: `bom-1.7.schema.json` and the three schemas it refers to by relative id, `spdx.schema.json`,
  `jsf-0.82.schema.json`, `cryptography-defs.schema.json`.
- Source: https://github.com/CycloneDX/specification/tree/1.7.2/schema (release tag `1.7.2`, commit
  `349314a9d7671d7d2ca5b711a725f49a73979da6`, published 2026-09-17; the latest release when vendored, 2026-11-18).
  The specification version these schemas define is CycloneDX **1.7** (`specVersion: "1.7"`).
- sha256:
  - `bom-1.7.schema.json` `73308edec3ab2d38bfffd993e96a042b594314143b6971a6e9ed98bbb6bd76ce`
  - `spdx.schema.json` `4b345e2329f209f34e960ae2a8e7cb46a166907e6a45e94978565925dc47b359`
  - `jsf-0.82.schema.json` `8bae002c25e723db7ee1f26afde680ae1a2b1a8f6b4b4b0fd65dc3becb090aae`
  - `cryptography-defs.schema.json` `027b059a729a06d591bac79a584ef04f83fc32d91a826fdba6ad3c98a10e5b44`
- Specification: https://cyclonedx.org/docs/1.7/json/ (the ML-BOM fields used: component types
  `machine-learning-model` and `data`, `modelCard.modelParameters`, `modelCard.considerations`, `data[]`
  `componentData`, `hashes`).
- Licence: Apache License 2.0 (`LICENSE` here, copied from the same tag). Copyright the OWASP Foundation / CycloneDX
  contributors. The files are unmodified.

`tools/compliance/aibom_bundle.py` (ICDEV[IT]) validates every CycloneDX ML-BOM it generates against these files
(JSON Schema draft-07), resolving the relative references from this directory, never from the network. A test pins each
file's sha256.
