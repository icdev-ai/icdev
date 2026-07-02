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
