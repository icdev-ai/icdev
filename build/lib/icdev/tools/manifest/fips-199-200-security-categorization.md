# FIPS 199/200 Security Categorization

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## FIPS 199/200 Security Categorization
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| FIPS 199 Categorizer | tools/compliance/fips199_categorizer.py | FIPS 199 security categorization with SP 800-60 information types, high watermark, CNSSI 1253 | --project-id, --add-type, --categorize, --list-catalog, --gate, --json | Categorization + baseline |
| FIPS 200 Validator | tools/compliance/fips200_validator.py | FIPS 200 minimum security requirements validation (17 areas) | --project-id, --gate, --json | Gap report + validation |

