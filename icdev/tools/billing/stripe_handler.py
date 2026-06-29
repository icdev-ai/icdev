# CUI // SP-CTI
"""Stripe webhook handler — canonical icdev namespace re-export."""
from tools.billing.stripe_handler import verify_stripe_signature, handle_webhook  # noqa: F401

__all__ = ["verify_stripe_signature", "handle_webhook"]
