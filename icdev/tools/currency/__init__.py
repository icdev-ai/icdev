# CUI // SP-CTI
"""Domain-agnostic entity-currency store (cef-fnd-04).

One question, one place: *is this thing still current, who says so, and when
did they last look?* — for any entity any provider can describe, without this
package knowing what kind of thing it is.
"""

from .entity_currency import (  # noqa: F401
    VERDICTS,
    CurrencyAssertion,
    backfill,
    normalize_key,
    resolve,
    stats,
    upsert,
)
