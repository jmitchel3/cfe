"""Django app for the stripe module: `client()` and the webhook view."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stripe import StripeClient


def make_client(api_key: str, api_version: str | None = None) -> StripeClient:
    from stripe import StripeClient

    return StripeClient(api_key, stripe_version=api_version or None)


def client() -> StripeClient:
    """A StripeClient configured from STRIPE_SECRET_KEY and STRIPE_API_VERSION."""
    from django.conf import settings

    return make_client(settings.STRIPE_SECRET_KEY, getattr(settings, "STRIPE_API_VERSION", None))
