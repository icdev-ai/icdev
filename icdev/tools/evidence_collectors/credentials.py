# CUI // SP-CTI
"""Credentials BY REFERENCE only: the customer's vault or the OS keyring (icx-ent-02).

A collector's configuration names where a credential lives, never the credential:

    keyring:<service>/<username>   the OS keyring (the ``keyring`` library: Secret Service, macOS Keychain,
                                   Windows Credential Locker)
    vault:<path>#<field>           HashiCorp Vault KV, through ICDEV's existing resolver
                                   (tools/databridge/resolvers/vault_resolver.py; VAULT_ADDR and VAULT_TOKEN from the
                                   environment of the process, as DataBridge reads them)

Anything else -- a literal, ``env:``, ``file:`` -- is refused when the configuration is loaded
(:func:`check_ref`), so a secret never lands in ``args/`` or git. A resolved value is wrapped in :class:`Secret`,
whose ``repr`` and ``str`` are redacted: it is never logged, recorded, returned by a route or written into a
bundle. Errors name the scheme only, never the reference or a value.
"""
from __future__ import annotations

import re

from tools.evidence_collectors.constants import SECRET_SCHEMES

_KEYRING = re.compile(r"keyring:([!-.0-~]{1,200})/([!-~]{1,200})")      # fullmatch; printable ASCII only
_VAULT = re.compile(r"vault:([!-\"$-~]{1,500})#([!-\"$-~]{1,200})")


class CredentialError(Exception):
    """A credential reference that is malformed or cannot be resolved. The message never carries a secret."""


class Secret:
    """A resolved credential. Redacted everywhere but :meth:`reveal`, which only a transport header calls."""

    __slots__ = ("_v", "ref")

    def __init__(self, value: str, ref: str):
        self._v = value
        self.ref = ref

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return f"Secret({self.ref.split(':', 1)[0]}, <redacted>)"

    __str__ = __repr__


def check_ref(ref) -> str:
    """The reference itself when it is one of the accepted forms; otherwise :class:`CredentialError`.

    The error never quotes the value: a literal pasted into the configuration may be the secret itself.
    """
    s = str(ref or "").strip()
    if _KEYRING.fullmatch(s) or _VAULT.fullmatch(s):
        return s
    raise CredentialError("a credential is configured only by reference: " + " or ".join(
        f"{p}..." for p in SECRET_SCHEMES) + " (the value given is not one; it is not repeated here)")


def resolve(ref: str) -> Secret:
    """Read the credential a reference names. :class:`CredentialError` when it cannot be read."""
    s = check_ref(ref)
    scheme = s.split(":", 1)[0]          # errors name the scheme, never the reference (it names the customer's entry)
    m = _KEYRING.fullmatch(s)
    if m:
        try:
            import keyring
        except ModuleNotFoundError as exc:
            raise CredentialError(f"{scheme}: the OS keyring library (keyring) is not installed") from exc
        try:
            value = keyring.get_password(m.group(1), m.group(2))
        except Exception as exc:
            raise CredentialError(f"{scheme}: the OS keyring could not be read ({type(exc).__name__})") from exc
        if not value:
            raise CredentialError(f"{scheme}: no such entry in the OS keyring")
        return Secret(value, s)
    from tools.databridge.resolvers.vault_resolver import SecretResolverError
    from tools.databridge.resolvers.vault_resolver import resolve as vault_resolve

    try:
        return Secret(vault_resolve(s), s)
    except SecretResolverError as exc:
        raise CredentialError(f"{scheme}: the vault could not be read ({type(exc).__name__})") from exc
