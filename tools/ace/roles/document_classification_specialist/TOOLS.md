# Document Classification Specialist — Capability Scope

- ICDEV Security Scanner: Scans document metadata and content for sensitive keywords matching CAPCO registers.
- WriteGuard Integration API: Interfaces with WriteGuard to validate proposed markings against real-time policy updates.

- Unfiltered Web Browsing: Prevents exposure of classified context to unsecured external networks or non-compliant sources.
- General Purpose Chat Models: Avoids potential data leakage into public LLM contexts; relies only on domain-specific knowledge bases.

- Any tool capable of exfiltrating document content to external repositories.
- Tools lacking FIPS-validated cryptographic integrity checks for audit trails.

- /tools/security/classification_engine
- /tools/network/capco_register_lookup