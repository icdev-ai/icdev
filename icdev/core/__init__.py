# CUI // SP-CTI
"""ICDEV core contract — the seam between the domain-neutral kernel and a parent.

A parent (ICDEV[IT], ICDEV[FT], ...) declares itself in an ``icdev_domain.yaml``
at its repository root. This package reads that declaration and answers the
three questions every kernel module used to answer for itself, 2,054 times
over, with ``Path(__file__).resolve().parent...``:

* :mod:`icdev.core.paths`   — where is the repository root and its data?
* :mod:`icdev.core.domain`  — which domain is this, and what did it declare?
* :mod:`icdev.core.context` — is this process allowed to run HERE, against
  THIS database? (``assert_identity``)

Nothing in this package imports anything outside the standard library and
PyYAML, so it can be imported before ``tools.db.storage`` and from either the
``tools.*`` shim or the ``icdev.tools.*`` canonical namespace.

Programme: docs/programmes/icdev-domain-split.md (xit-decl-01).
"""
from __future__ import annotations

__all__ = ["paths", "domain", "context"]
