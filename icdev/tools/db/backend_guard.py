#!/usr/bin/env python3
# CUI // SP-CTI
"""Refuse to start a long-lived server on the SQLite fallback (e2p-back-04).

PostgreSQL is the primary backend; ``translate_sql`` is "a thin SQLite
init-fallback ONLY, never load-bearing" (CLAUDE.md). ``.env`` carries
``ICDEV_PG_NO_FALLBACK=true`` to enforce that — but it cannot, on its own:

* The flag is only consulted inside ``get_connection()``'s ``postgresql``
  branch, when PG was chosen and the *connection failed*. It stops a silent
  degrade. An explicit ``ICDEV_STORAGE_BACKEND=sqlite`` is a **pin, not a
  fallback**, so the SQLite connection is returned before the flag is read.
* An explicitly-set environment variable always beats ``.env``:
  ``load_dotenv()`` defaults to ``override=False``. A process spawned with
  ``env={...os.environ, "ICDEV_STORAGE_BACKEND": "sqlite"}`` cannot be talked
  out of it by any config file.

So the pin has to be refused in code, at the point a *long-lived server* starts.

Observed 2026-07-28: ``tests/e2e/dwo_restart_durability.spec.ts`` pinned sqlite
for the dashboard it spawns, and ``playwright.config.ts`` does the same for the
shared webServer. The spec had never passed, and the failure looked like a
product defect (500s from the Studio API) rather than a database that nothing
maintains. `data/icdev.db` had drifted: RLS columns missing on three studio
tables and eight-plus migration checksum mismatches. Hours went into the wrong
diagnosis. A guard that says so at boot turns that into one line of output.

Deliberately NOT blanket. ``tests/conftest.py`` forces sqlite for the whole
pytest suite and is right to — those are short-lived, isolated databases. This
guard only fires where something is asked to *serve* on the fallback, and only
when the install has declared it does not want a fallback.
"""
from __future__ import annotations

import os

#: Set to bypass the guard. The E2E suite carries it until the whole suite runs
#: on PostgreSQL (e2p-back-03) — greppable on purpose, so the debt is visible.
ESCAPE_HATCH = "ICDEV_ALLOW_SQLITE_SERVER"


class SqliteServerRefused(RuntimeError):
    """A long-lived server was asked to serve on the SQLite fallback."""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def check_primary_backend(component: str, env: dict | None = None) -> str | None:
    """Return a refusal message, or None when it is fine to start.

    Pure and side-effect free so it can be tested without a database, and so a
    caller may choose to warn instead of raise.
    """
    env = os.environ if env is None else env

    backend = (env.get("ICDEV_STORAGE_BACKEND") or "postgresql").strip().lower()
    if backend != "sqlite":
        return None
    if not _truthy(env.get("ICDEV_PG_NO_FALLBACK")):
        # This install has not declared PG-only; a SQLite deployment is its
        # own choice (air-gap, demo, laptop) and none of our business.
        return None
    if _truthy(env.get(ESCAPE_HATCH)):
        return None

    return (
        f"{component} refuses to start: ICDEV_STORAGE_BACKEND=sqlite but this "
        f"install sets ICDEV_PG_NO_FALLBACK=true.\n"
        f"PostgreSQL is the primary backend; SQLite is an init-only fallback and "
        f"data/icdev.db is not maintained — it drifts (missing columns, migration "
        f"checksum mismatches), and serving from it produces 500s that look like "
        f"product defects.\n"
        f"Note an explicit backend pin bypasses ICDEV_PG_NO_FALLBACK, and an env "
        f"var beats .env, so no config file can catch this — hence this check.\n"
        f"Fix: unset ICDEV_STORAGE_BACKEND (or set it to postgresql). To start on "
        f"SQLite deliberately, set {ESCAPE_HATCH}=1."
    )


def assert_primary_backend(component: str, env: dict | None = None) -> None:
    """Raise :class:`SqliteServerRefused` unless it is fine to start."""
    message = check_primary_backend(component, env)
    if message:
        raise SqliteServerRefused(message)


if __name__ == "__main__":
    import sys

    msg = check_primary_backend(sys.argv[1] if len(sys.argv) > 1 else "server")
    print(msg or "OK — not serving from the SQLite fallback")
    sys.exit(1 if msg else 0)
