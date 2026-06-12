# CUI // SP-CTI
"""DataBridge secret resolvers package.

Public API:
  - resolve_vault(ref)  → plaintext value from HashiCorp Vault KV
  - resolve_aws(ref)    → plaintext value from AWS Secrets Manager
  - resolve_file(ref)   → plaintext value from filesystem (air-gap)

Each resolver raises SecretResolverError on failure; never returns empty.
Import the backing modules directly for full control (caching, class API, etc.).
"""

from __future__ import annotations

from tools.databridge.resolvers.vault_resolver import (
    SecretResolverError as VaultResolverError,
    resolve as resolve_vault,
)

__all__ = [
    "VaultResolverError",
    "resolve_vault",
]

# AWS and file resolvers are optional — skip gracefully if deps absent
try:
    from tools.databridge.resolvers.aws_resolver import (
        SecretResolverError as AWSResolverError,  # type: ignore[assignment]
        resolve as resolve_aws,
    )
    __all__ += ["AWSResolverError", "resolve_aws"]
except ImportError:
    pass

try:
    from tools.databridge.resolvers.file_resolver import (
        SecretResolverError as FileResolverError,  # type: ignore[assignment]
        resolve as resolve_file,
    )
    __all__ += ["FileResolverError", "resolve_file"]
except ImportError:
    pass
