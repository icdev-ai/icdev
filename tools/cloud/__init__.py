# [TEMPLATE: CUI // SP-CTI]
"""Cloud Service Provider (CSP) abstraction layer.

Provides vendor-agnostic interfaces for cloud services:
  - Secrets management (AWS Secrets Manager, Azure Key Vault, GCP Secret Manager, OCI Vault)
  - Object storage (S3, Azure Blob, GCS, OCI Object Storage)
  - Key management/encryption (AWS KMS, Azure Key Vault, GCP Cloud KMS, OCI Key Management)

Pattern: tools/llm/provider.py (ABC + implementations, D66)
ADRs: D223 (CSP abstraction), D224 (local fallback), D225 (per-service override)
"""
