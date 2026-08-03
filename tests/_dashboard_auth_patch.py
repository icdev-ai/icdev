# CUI // SP-CTI
"""Shared helpers for dashboard tests that build a real ``create_app()`` client.

Two separate obstacles sit between such a test and its first response, and they
are routinely confused with each other:

1. **The global auth hook (nav-sec-01).** ``_auth_before_request`` resolves the
   session's ``user_id`` through :func:`get_user_by_id`, which reads
   ``dashboard_users`` from the MAIN database via ``get_connection()``. Seeding
   that row into a per-test temp SQLite file and patching the module-level
   ``DB_PATH`` only works while the backend is SQLite *and* while the hook that
   actually runs came from the ``tools.`` spelling of the module. On PostgreSQL
   — the primary backend — ``get_connection()`` ignores ``db_path`` entirely, the
   seeded row is invisible, and every request 401s. Patching
   :func:`get_user_by_id` itself is backend-independent.

   ``tools.dashboard.auth`` and ``icdev.tools.dashboard.auth`` do not always
   resolve to the same module object, and the ``before_request`` hook that runs
   is the one registered by whichever spelling the app imported. Patch BOTH.
   This mirrors ``tests/test_ace_chat_endpoint.py``.

2. **The backend guard (e2p-back-04).** ``create_app()`` calls
   ``assert_primary_backend`` and raises ``SqliteServerRefused`` when
   ``ICDEV_STORAGE_BACKEND=sqlite`` meets ``ICDEV_PG_NO_FALLBACK`` — which is
   exactly the combination ``tests/conftest.py`` and the repo ``.env`` produce.
   Per ``tools/db/backend_guard.py``'s own docstring the guard targets
   *long-lived servers*, not "short-lived, isolated databases"; an in-process
   Flask test client is the latter, so it takes the documented escape hatch.
"""
from __future__ import annotations

import contextlib
import importlib
from unittest.mock import patch

from tools.db.backend_guard import ESCAPE_HATCH

#: The identity every helper below injects. Test fixtures that seed a
#: ``dashboard_users`` row use this same id, so the two stay interchangeable.
#:
#: ``tenant_id`` is None deliberately. ``tools/security/canvas_access.py``
#: treats an authenticated principal with no tenant as a platform admin and
#: skips the tenant-scoped grant checks; a concrete tenant ("default") instead
#: sends the request through ``check_access`` against grant rows that no unit
#: test seeds, so registry-gated canvas pages answer 403 instead of 200. Pass
#: ``user={...}`` to :func:`dashboard_test_app_env` when a test needs a tenant.
TEST_ADMIN = {
    "id": "test-admin",
    "email": "admin@test.local",
    "display_name": "Test Admin",
    "role": "admin",
    "status": "active",
    "tenant_id": None,
    "clearance_level": "CUI",
}


def _fake_get_user_by_id(user_id, tenant_id=None):
    """Stand-in for ``auth.get_user_by_id`` — never touches a database."""
    return dict(TEST_ADMIN)


@contextlib.contextmanager
def dashboard_test_app_env(user=None, allow_sqlite_server: bool = True):
    """Make ``create_app()`` constructible and its auth hook satisfied.

    One context manager rather than a list of them on purpose: a starred splat
    into a parenthesized ``with`` (``with (a, b, *patchers):``) is parsed as a
    single tuple expression, not as with-items, so the patchers silently never
    enter. That failure is invisible — the block still runs, just unpatched.

    ``get_user_by_id`` is patched on every *distinct* module object among
    ``tools.dashboard.auth`` and ``icdev.tools.dashboard.auth``; when the two
    import paths alias one module it is patched once, not twice.
    """
    def _fake(user_id, tenant_id=None):
        return dict(user or TEST_ADMIN)

    with contextlib.ExitStack() as stack:
        if allow_sqlite_server:
            stack.enter_context(patch.dict("os.environ", {ESCAPE_HATCH: "1"}))

        seen: set[int] = set()
        for name in ("tools.dashboard.auth", "icdev.tools.dashboard.auth"):
            try:
                mod = importlib.import_module(name)
            except ImportError:
                continue
            if id(mod) in seen:
                continue
            seen.add(id(mod))
            stack.enter_context(patch.object(mod, "get_user_by_id", _fake))

        yield
