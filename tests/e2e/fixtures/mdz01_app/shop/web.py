# CUI // SP-CTI
"""The shop's web views (icx-mdz-03): one class whose routes are the cart's actions, in order. Synthetic, never run."""
from flask import Blueprint

from shop import cart

bp = Blueprint("shop", __name__)


class CartViews:
    @bp.route("/cart")
    def view_cart(self):
        return "cart"

    @bp.route("/cart/add", methods=["POST"])
    def add_item_to_cart(self):
        return str(cart.total([]))

    @bp.route("/cart/checkout", methods=["POST"])
    def check_out(self):
        return "order"
