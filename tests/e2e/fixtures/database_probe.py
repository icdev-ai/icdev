# CUI // SP-CTI
"""Which database does a SPEC'S OWN SUBPROCESS actually reach? (qa-fail-679a43311f34d5c9)

``globalSetup`` asserted E2E database isolation by asking ``/api/health``, which
measures ``current_database()`` off the dashboard's live connection. That is a
true statement about the SERVER -- and the server is not the only writer. A spec
that spawns its own ICDEV Python process (the DIC workspace seed fixture, the
second dashboard ``dwo_restart_durability`` starts, the gateway
``dwo_trigger_linkage`` starts) inherited the operator's ambient
``ICDEV_DATABASE_URL``, which every connection site in ``tools/db/storage.py``
reads BEFORE the discrete ``ICDEV_PG_DATABASE``. So the run printed

    E2E database confirmed: server is on 'icdev_e2e'

while its fixtures were being committed to the canonical ``icdev``. An isolation
check that covers one writer and reports ``confirmed`` is the shape of the very
defect it was built to close.

This script is that second measurement. It is spawned by ``globalSetup`` with the
SAME environment a spec's subprocess gets -- ``icdevSubprocessEnv()``, not a
hand-built one -- so what it measures is the path a fixture really takes. A
hand-built environment would measure something no spec uses.

IT NEVER ECHOES THE ENVIRONMENT. ``active_database()`` opens a connection and
asks PostgreSQL ``current_database()`` (SQLite: ``PRAGMA database_list``).
Re-reading ``ICDEV_PG_DATABASE`` back out would prove only that a variable can be
passed to a child, which is exactly the reasoning that shipped the broken recipe.

IT IS READ-ONLY. One connection, one read, no INSERT/UPDATE/DELETE and no
``init_db`` import -- importing a canvas ``init_db`` APPLIES that canvas's ADD
COLUMN migrations, and a probe that modified the database it is auditing would be
its own finding.

Exit status is always 0 when a verdict could be produced, including
``measured: false``: "I could not tell" is a real answer the caller must be able
to read, and collapsing it into a non-zero exit would make an unmeasurable probe
indistinguishable from a crashed one. Exit 2 means no verdict at all.

Re-derive by hand, which is the whole point of it being a file::

    python tests/e2e/fixtures/database_probe.py
    ICDEV_PG_DATABASE=icdev_e2e python tests/e2e/fixtures/database_probe.py
    ICDEV_PG_DATABASE=icdev_e2e ICDEV_DATABASE_URL= python tests/e2e/fixtures/database_probe.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# The repo root, from THIS FILE's location. Never cwd: a spec spawns this with
# `cwd: ROOT`, but an operator re-deriving it by hand may be anywhere, and
# `Path(__file__)` gives the same answer either way.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    try:
        from tools.db.storage import active_database
    except Exception as exc:  # pragma: no cover - exercised on a broken install
        # An import failure is NOT an unmeasurable database -- it is an
        # unmeasurable PROBE, and the two send a reader to different fixes. Said
        # on stdout in the same shape, so the caller parses one thing.
        print(json.dumps({
            "measured": False,
            "backend": None,
            "database": None,
            "error": f"could not import tools.db.storage: {exc}",
        }))
        return 0

    try:
        result = active_database()
    except Exception as exc:
        print(json.dumps({
            "measured": False,
            "backend": None,
            "database": None,
            "error": f"could not open a connection: {exc}",
        }))
        return 0

    print(json.dumps({
        "measured": bool(result.get("measured")),
        "backend": result.get("backend"),
        "database": result.get("database"),
        "error": None if result.get("measured") else "the connection did not report a database",
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover
        print(f"database_probe: no verdict could be produced: {exc}", file=sys.stderr)
        sys.exit(2)
# CUI // SP-CTI
