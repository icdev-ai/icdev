# Mission m-aie-04 — Capstone
# Step 2: write discriminating tests, then fix the three planted defects.
#
# Spec: charge(ledger, user, tokens, limit=1000) adds tokens to user's running total
# in the ledger dict and returns the new total.
#   1. tokens below 1 raises ValueError.
#   2. A charge that would take the total ABOVE limit raises BudgetExceeded.
#      Spending exactly up to the limit is allowed.
#   3. A refused charge leaves the ledger unchanged.


class BudgetExceeded(Exception):
    """Raised when a charge would take a user over their token limit."""


def charge(ledger, user, tokens, limit=1000):
    """The AI's implementation. It ran once without an error, so it shipped."""
    try:
        if tokens < 1:
            raise ValueError("tokens must be at least 1")
        ledger[user] = ledger.get(user, 0) + tokens
        if ledger[user] >= limit:
            raise BudgetExceeded(f"{user} is over budget")
    except Exception:
        pass
    return ledger.get(user, 0)


# The agent's own test. It passes — but does it prove the spec?
def test_charge():
    ledger = {}
    assert charge(ledger, "ana", 100) == 100


# TODO: replace the test above with tests written from the SPEC, watch them fail,
# then fix charge(). The grader runs every test_* function against your charge, a
# hidden reference, and three mutants (one planted defect each).


for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        try:
            _fn()
            print(f"PASS {_name}")
        except Exception as exc:
            print(f"FAIL {_name}: {type(exc).__name__}: {exc}")
