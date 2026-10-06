# ruff: noqa: F821 -- charge & BudgetExceeded are defined by the learner submission this is appended to
# Auto-grader for m-aie-04 Step 2 — discriminating tests plus a real fix.
#
# The learner's script and this grader run in ONE shared namespace, so the learner's
# test_* functions are visible in globals() and look up `charge` (and BudgetExceeded)
# there at call time. Rebinding the `charge` global swaps the implementation under
# test: the learner's own fix, a hidden reference, then one mutant per planted defect.

assert "BudgetExceeded" in globals() and isinstance(BudgetExceeded, type), (
    "Keep the BudgetExceeded exception class — the spec raises it."
)
assert callable(globals().get("charge")), "Keep the charge() function."

_learner_charge = charge


def _reference_charge(ledger, user, tokens, limit=1000):
    """The spec, with nothing extra."""
    if tokens < 1:
        raise ValueError("tokens must be at least 1")
    new_total = ledger.get(user, 0) + tokens
    if new_total > limit:
        raise BudgetExceeded(f"{user} would exceed {limit}")
    ledger[user] = new_total
    return new_total


def _mutant_boundary(ledger, user, tokens, limit=1000):
    """Planted defect 1: refuses a charge that lands exactly on the limit."""
    if tokens < 1:
        raise ValueError("tokens must be at least 1")
    new_total = ledger.get(user, 0) + tokens
    if new_total >= limit:
        raise BudgetExceeded(f"{user} would exceed {limit}")
    ledger[user] = new_total
    return new_total


def _mutant_swallowed(ledger, user, tokens, limit=1000):
    """Planted defect 2: every error is swallowed and the call reports a total."""
    try:
        if tokens < 1:
            raise ValueError("tokens must be at least 1")
        new_total = ledger.get(user, 0) + tokens
        if new_total > limit:
            raise BudgetExceeded(f"{user} would exceed {limit}")
        ledger[user] = new_total
    except Exception:
        pass
    return ledger.get(user, 0)


def _mutant_partial_write(ledger, user, tokens, limit=1000):
    """Planted defect 3: the ledger is written BEFORE the limit check."""
    if tokens < 1:
        raise ValueError("tokens must be at least 1")
    ledger[user] = ledger.get(user, 0) + tokens
    if ledger[user] > limit:
        raise BudgetExceeded(f"{user} would exceed {limit}")
    return ledger[user]


_MUTANTS = [
    ("boundary (a charge landing exactly on the limit is refused)", _mutant_boundary),
    ("swallowed exception (errors are caught and the call reports success)", _mutant_swallowed),
    ("partial write (a refused charge still changes the ledger)", _mutant_partial_write),
]


# ---------------------------------------------------------------------------
# 1. Hidden spec checks against the learner's own charge
# ---------------------------------------------------------------------------

def _raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return True
    except Exception:
        return False
    return False


def _spec_failures(impl):
    out = []
    ledger = {}
    if impl(ledger, "ana", 100) != 100 or ledger != {"ana": 100}:
        out.append("a first charge of 100 must return 100 and record it")
    if impl(ledger, "ana", 200) != 300 or ledger != {"ana": 300}:
        out.append("charges must accumulate: 100 then 200 is 300")
    at_edge = {"ana": 900}
    try:
        if impl(at_edge, "ana", 100) != 1000 or at_edge != {"ana": 1000}:
            out.append("spending exactly up to the limit must be allowed (rule 2)")
    except Exception as exc:
        out.append(f"spending exactly up to the limit raised {type(exc).__name__} (rule 2)")
    over = {"ana": 900}
    if not _raises(BudgetExceeded, impl, over, "ana", 101):
        out.append("a charge one token over the limit must raise BudgetExceeded (rule 2)")
    if over != {"ana": 900}:
        out.append("a refused charge must leave the ledger unchanged (rule 3)")
    if not _raises(BudgetExceeded, impl, {}, "bo", 6, limit=5):
        out.append("the limit argument must be honoured, not only the default")
    zero = {"ana": 5}
    if not _raises(ValueError, impl, zero, "ana", 0):
        out.append("tokens below 1 must raise ValueError (rule 1)")
    if zero != {"ana": 5}:
        out.append("a rejected charge of 0 tokens must leave the ledger unchanged")
    if not _raises(ValueError, impl, {}, "ana", -5):
        out.append("negative tokens must raise ValueError (rule 1)")
    return out


_spec = _spec_failures(_learner_charge)
assert not _spec, (
    "Your charge() does not meet the spec yet:\n  " + "\n  ".join(_spec)
)

# ---------------------------------------------------------------------------
# 2-4. The learner's tests: green on the fix and the reference, red on each mutant
# ---------------------------------------------------------------------------

_tests = sorted(
    (name, fn) for name, fn in globals().items()
    if name.startswith("test_") and callable(fn)
)
assert _tests, "No tests found. Define at least one function whose name starts with test_."


def _run_all(impl):
    """Call every learner test with `impl` bound as charge. Return the failures."""
    globals()["charge"] = impl
    failures = []
    for name, fn in _tests:
        try:
            fn()
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    return failures


_own = _run_all(_learner_charge)
assert not _own, (
    "Your tests must PASS against your fixed charge, but these failed:\n  " + "\n  ".join(_own)
)
_ref = _run_all(_reference_charge)
assert not _ref, (
    "Your tests must PASS against a correct charge, but these failed:\n  "
    + "\n  ".join(_ref)
    + "\nA test that fails on correct code is broken, not discriminating."
)

_survivors = [label for label, mutant in _MUTANTS if not _run_all(mutant)]
globals()["charge"] = _learner_charge
assert not _survivors, (
    "These mutants SURVIVED — every one of your tests passed with the defect put back, "
    "so your suite cannot catch it:\n  " + "\n  ".join(_survivors)
    + "\nWhich spec rule does it break, and which test of yours ever tries that case?"
)

print(
    f"PASS: charge meets the spec; {len(_tests)} test(s) green on the fix and the "
    f"reference, and all {len(_MUTANTS)} mutants killed."
)
