# CUI // SP-CTI
"""The cart: depends on the catalogue."""
from shop import catalog


def total(items: list[str]) -> float:
    return sum(catalog.price(i) for i in items)
