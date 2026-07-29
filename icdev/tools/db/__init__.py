# [TEMPLATE: CUI // SP-CTI]
"""icdev.tools.db — database package.

Submodules resolve lazily by attribute access (PEP 562). This exists because of
how the ``tools`` -> ``icdev.tools`` compatibility shim in ``tools/__init__.py``
works: ``tools.db`` is resolved by returning the ``icdev.tools.db`` module
object, after which Python looks the submodule up as an *attribute* of that
object. With a bare ``__init__.py`` that attribute did not exist, so::

    import tools.db.storage as storage     # ImportError
    from tools.db import storage           # worked

The asymmetry is real and confusing — ``from ... import`` falls back to importing
a submodule, plain ``import a.b.c as x`` does not. Several test modules use the
first form and failed on ``origin/main`` with "cannot import name 'storage' from
'icdev.tools.db'".

Two deliberate choices:

* ``__getattr__`` rather than eager ``from . import storage``, so importing this
  package stays cheap and cannot introduce an import cycle — ``storage`` pulls in
  the whole backend stack and modules inside this package import each other.
* the canonical ``icdev.tools.db`` prefix is written out rather than derived from
  ``__name__``. This file is mirrored verbatim to ``tools/db/__init__.py``, where
  ``__name__`` would be ``tools.db``; resolving against that would risk importing
  a *second* module object for the same file, giving callers separate connection
  state depending on which spelling they used. Pinned by
  ``tests/test_tools_shim_submodule_import.py``.
"""

import importlib

_CANONICAL = "icdev.tools.db"


def __getattr__(name: str):
    """Resolve icdev.tools.db.<name> on first attribute access."""
    if name.startswith("_"):
        raise AttributeError(name)
    try:
        return importlib.import_module(f"{_CANONICAL}.{name}")
    except ModuleNotFoundError as exc:
        raise AttributeError(
            f"module {_CANONICAL!r} has no attribute {name!r}"
        ) from exc
