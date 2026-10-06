# Mission m-aie-02 — Verification Harnesses
# Step 2: write a test that goes RED on the buggy code and GREEN on the fixed code.
#
# Spec: bulk_price(quantity, unit_price) returns the order total rounded to 2 dp.
# Orders of 10 or more units get 10% off. A quantity below 1 raises ValueError.


def bulk_price(quantity: int, unit_price: float) -> float:
    """The agent's implementation. It reported this as done."""
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    total = quantity * unit_price
    if quantity > 10:
        total *= 0.9
    return round(total, 2)


# The agent's own test. It passes — but does it prove the spec?
def test_bulk_price():
    assert bulk_price(20, 1.0) == 18.0


# TODO: replace the test above with tests written from the SPEC.
# The grader runs every test_* function against this buggy bulk_price (at least one
# must FAIL) and against a correct one (all must PASS).


for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        try:
            _fn()
            print(f"PASS {_name}")
        except Exception as exc:
            print(f"FAIL {_name}: {type(exc).__name__}: {exc}")
