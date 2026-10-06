# Auto-grader for m-aie-02 Step 2 — a test that goes RED on the buggy code.
#
# The learner's script and this grader run in ONE shared namespace, so the learner's
# test_* functions are visible in globals() and look up `bulk_price` there at call
# time. Rebinding that global swaps the implementation under test — the same move
# tools/ci/red_first_gate.py makes by checking out the merge base under a test file.


def _buggy_bulk_price(quantity, unit_price):
    """The agent's version: the discount boundary is off by one (> 10, not >= 10)."""
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    total = quantity * unit_price
    if quantity > 10:
        total *= 0.9
    return round(total, 2)


def _fixed_bulk_price(quantity, unit_price):
    """The spec: 10 or more units get 10% off; quantity below 1 raises ValueError."""
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    total = quantity * unit_price
    if quantity >= 10:
        total *= 0.9
    return round(total, 2)


_tests = sorted(
    (name, fn) for name, fn in globals().items()
    if name.startswith("test_") and callable(fn)
)
assert _tests, "No tests found. Define at least one function whose name starts with test_."


def _run_all(impl):
    """Call every learner test with `impl` bound as bulk_price. Return the failures."""
    globals()["bulk_price"] = impl
    failures = []
    for name, fn in _tests:
        try:
            fn()
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    return failures


_red = _run_all(_buggy_bulk_price)
_green = _run_all(_fixed_bulk_price)

assert not _green, (
    "Your tests must PASS against a correct bulk_price, but these failed:\n  "
    + "\n  ".join(_green)
    + "\nA test that fails on correct code is broken, not red-first."
)
assert _red, (
    "Every test PASSED against the buggy bulk_price, so none of them can catch the bug. "
    "A test that passes on the buggy code is not a test. Re-read the spec: which case "
    "does it name that your tests never try?"
)

print(f"PASS: {len(_red)} of {len(_tests)} test(s) went RED on the buggy code; all GREEN on the fix.")
