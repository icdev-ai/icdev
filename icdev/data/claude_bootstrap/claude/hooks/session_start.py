# [TEMPLATE: CUI // SP-CTI]
"""SessionStart hook — inject a token-capped memory index as session context (xrv-mem-01).

Claude Code injects a SessionStart hook's STDOUT into the session as context.
This hook prints the block ``tools/hooks/session_context.build_block``
composes — the newest ``memory_entries`` as one index line each plus the
project builder's setup warnings — capped at ~1,500 tokens and withheld
outright if composing it took over one second. It then records one
``hook_events`` row (``hook_type='session_start'``) carrying the MEASUREMENT
(counts, tokens, elapsed, reason), never the block.

``|| true`` IS ACCEPTABLE ON THIS ENTRY in ``.claude/settings.json``, and this
docstring is where that is stated. CLAUDE.md's rule against shell neutralisers
is about SECURITY hooks: a PreToolUse hook signals "block" with exit 2, and
``|| true`` discards the decision. This hook is a NON-BLOCKING context hook —
it has no decision to discard, it exits 0 on every path by construction, and
the wrapper only converts an interpreter that cannot start (no python on PATH,
a scaffolded project with no ``tools/``) into silence, which is the correct
outcome for a context hook. exa-bench-05's survey obligation applies to a
check that can REFUSE; this one refuses nothing.

Kill switch: ``ICDEV_SESSION_START_HOOK=0`` prints nothing and records nothing.
Debug: ``ICDEV_SESSION_START_DEBUG=1`` writes the measurement (reason, elapsed,
tokens, counts) to stderr, never to stdout.
Bounds: ``ICDEV_SESSION_START_MAX_TOKENS`` (1500), ``ICDEV_SESSION_START_BUDGET_SECONDS`` (1.0).

In a project scaffolded by ``icdev init`` the payload ships this file beside
the other hooks but no ``tools/`` tree, so the import below fails, and the
hook prints nothing and exits 0 — stated rather than discovered.

Always exits 0. Prints nothing on ANY exception.
"""

import json
import os
import sys
import time
from pathlib import Path

# The wall clock starts on the hook's first line, not inside the builder, so
# the budget covers the imports too (measured 2026-09-11: ~0.1s storage import,
# ~0.27s for the read on a 27k-row board).
STARTED = time.perf_counter()

HOOKS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = HOOKS_DIR.parent.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _read_input() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    if os.environ.get("ICDEV_SESSION_START_HOOK", "1") == "0":
        sys.exit(0)

    input_data = _read_input()
    result = None
    try:
        from tools.hooks.session_context import build_block, payload_for_event

        result = build_block(
            directory=input_data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or None,
            started=STARTED,
        )
        if result.get("context"):
            # The block IS the hook's output: Claude Code injects stdout as context.
            sys.stdout.write(result["context"])
            sys.stdout.flush()
    except Exception:  # noqa: BLE001 — a context hook never fails a session
        result = None

    if os.environ.get("ICDEV_SESSION_START_DEBUG") == "1":
        # Opt-in: the measurement on stderr (Claude Code shows hook stderr only
        # in verbose mode), so a withheld block can be diagnosed by its reason
        # and elapsed time instead of guessed at.
        try:
            measurement = payload_for_event(result) if result else {"reason": "error:builder_unavailable"}
            sys.stderr.write(json.dumps(measurement) + "\n")
        except Exception:  # noqa: BLE001
            pass

    # The event write is SEPARATE from the print: a refused row (an existing
    # SQLite data/icdev.db whose CHECK predates migration 20260911220746 raises
    # IntegrityError, which send_event does not catch) must not cost the block
    # that was already composed. Measured, not assumed, that this is the
    # common case on a dev checkout today.
    try:
        from send_event import get_session_id, store_event

        payload = payload_for_event(result) if result else {"emitted": False, "reason": "error:builder_unavailable"}
        payload["source"] = input_data.get("source")
        store_event(
            session_id=input_data.get("session_id") or get_session_id(),
            hook_type="session_start",
            payload=payload,
        )
    except Exception:  # noqa: BLE001
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
