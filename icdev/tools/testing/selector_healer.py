# Backward-compat shim — canonical module is icdev/tools/testing/selector_healer.py
from icdev.tools.testing.selector_healer import (  # noqa: F401
    BrokenSelector,
    RepairProposal,
    detect_broken_selectors,
    propose_repair,
    apply_repair_to_spec,
    main,
)

if __name__ == "__main__":
    import sys
    sys.exit(main())
