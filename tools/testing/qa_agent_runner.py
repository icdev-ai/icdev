import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_ICDEV_ROOT = _REPO_ROOT if (_REPO_ROOT / "icdev").is_dir() else _REPO_ROOT.parent
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

# Backward-compat shim — canonical module is icdev/tools/testing/qa_agent_runner.py
from icdev.tools.testing.qa_agent_runner import (  # noqa: F401
    TestFailure,
    QARunResult,
    discover_coverage_gaps,
    generate_spec_stub,
    run_e2e_suite,
    parse_playwright_json,
    file_failure_tasks,
    record_run,
    record_failure,
    get_run_status,
    main,
)

if __name__ == "__main__":
    import sys
    sys.exit(main())
