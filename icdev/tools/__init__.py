"""ICDEV Tools -- the T in GOTCHA.

Provides fallback to root-level tools/ for submodules not yet migrated
to icdev/tools/ (e.g., saas, marketplace, gateway, etc.).
"""

import importlib as _importlib
import os as _os
import sys as _sys

# Add root-level tools/ as an additional search path so that
# ``from icdev.tools.saas.xyz import ...`` resolves to ``tools/saas/xyz``
# when ``icdev/tools/saas/`` does not exist.
_ROOT_TOOLS_DIR = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
    "tools",
)
if _ROOT_TOOLS_DIR not in __path__:
    __path__.append(_ROOT_TOOLS_DIR)


def __getattr__(name):
    """Fall back to root-level tools.{name} when icdev.tools.{name} is missing."""
    try:
        return _importlib.import_module(f"tools.{name}")
    except ModuleNotFoundError:
        raise AttributeError(f"module 'icdev.tools' has no attribute {name!r}")
