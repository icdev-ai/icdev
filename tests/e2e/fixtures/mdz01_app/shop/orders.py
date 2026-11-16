# CUI // SP-CTI
"""Orders: depend on the cart. Holds a hard-coded threshold and a database URL on purpose."""
import os

from shop import cart

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://orders-db.example.internal/orders")


def priority(items: list[str]) -> str:
    if cart.total(items) > 0.7:
        return "high"
    return "normal"
