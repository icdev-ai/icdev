# CUI // SP-CTI
"""The catalogue: depends on nothing else in the application."""

PRICES = {"apple": 1.0, "pear": 1.5}


def price(item: str) -> float:
    return PRICES.get(item, 0.0)
