# CUI // SP-CTI
"""The tools -> icdev.tools shim must resolve submodules by attribute access.

`tools/__init__.py` redirects `tools.db` by returning the `icdev.tools.db` module
object. Python then looks a submodule up as an *attribute* of that object, and
with a bare `icdev/tools/db/__init__.py` that attribute did not exist. The result
was an asymmetry that is easy to trip over and hard to read:

    import tools.db.storage as storage   # ImportError: cannot import name 'storage'
    from tools.db import storage         # fine

`from ... import` falls back to importing a submodule; plain `import a.b.c as x`
does not. Test modules using the first form failed on origin/main.

These tests pin both forms and, importantly, pin that the two namespaces resolve
to the SAME module object — a lazy import that produced a second copy of
storage.py would give every caller its own connection pool and make monkeypatching
depend on which spelling the code under test happened to use.
"""
from __future__ import annotations

import importlib

import pytest


def test_plain_import_as_resolves():
    """The form that used to raise ImportError."""
    import tools.db.storage as storage

    assert storage.__name__ == "icdev.tools.db.storage"
    assert hasattr(storage, "get_connection")


def test_from_import_still_resolves():
    from tools.db import storage

    assert hasattr(storage, "get_connection")


def test_both_namespaces_are_the_same_module_object():
    """A second module object would silently split connection state."""
    import icdev.tools.db.storage as canonical
    import tools.db.storage as shimmed

    assert shimmed is canonical


def test_submodule_is_reachable_as_a_package_attribute():
    """This is the getattr the import machinery performs."""
    pkg = importlib.import_module("icdev.tools.db")

    assert getattr(pkg, "storage") is importlib.import_module("icdev.tools.db.storage")


def test_unknown_submodule_raises_attribute_error():
    """The lazy resolver must not mask a genuine typo as something stranger."""
    pkg = importlib.import_module("icdev.tools.db")

    with pytest.raises(AttributeError):
        pkg.no_such_submodule


def test_dunder_lookups_are_not_treated_as_submodules():
    """copy/pickle probe for dunders; importing them would be nonsense."""
    pkg = importlib.import_module("icdev.tools.db")

    with pytest.raises(AttributeError):
        pkg.__wrapped__
