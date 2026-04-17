# ICDEV™ Frontend

TypeScript API types auto-generated from the ICDEV™ OpenAPI schema.

## Generated Files

| File | Description |
|------|-------------|
| `lib/api-types.ts` | TypeScript interfaces for all API endpoints — **do not edit manually** |

## Codegen

```bash
# Regenerate types from the live server (requires server on :5050)
npm run codegen
```

This generates `lib/api-types.ts` from `http://localhost:5050/api/v1/openapi.json`.

## Build Integration

Run `npm run codegen` as part of every build to keep `lib/api-types.ts` in sync with the backend schema. In CI/CD, add it as a pre-build step before any TypeScript compilation:

```yaml
# .gitlab-ci.yml excerpt
before_script:
  - cd frontend && npm ci && npm run codegen
```

> `lib/api-types.ts` is committed to version control so the repo is self-contained even without a running server.
