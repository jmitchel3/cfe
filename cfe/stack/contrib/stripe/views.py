"""Stripe webhook endpoint.

Verifies the Stripe-Signature header, then fires `stripe.<event.type>` and
the normalized billing hooks. Handler errors are logged by cfe.stack.hooks
and never turn into a 500, so Stripe does not retry for app bugs.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from cfe.stack.hooks import do_action

from . import client

logger = logging.getLogger("cfe.stack.stripe")

ACTIVE_STATUSES = {"active", "trialing"}


def _get(obj: Any, key: str) -> Any:
    """Read a field from a StripeObject (not a dict in stripe-python 12+)."""
    if obj is None:
        return None
    try:
        return obj[key]
    except (KeyError, TypeError):
        return None


def _id(value: Any) -> Any:
    # Expanded objects carry an id; unexpanded references are plain strings.
    return _get(value, "id") if value is not None and not isinstance(value, str) else value


def normalized_hooks(event: Any) -> list[tuple[str, dict[str, Any]]]:
    """Map a verified event to (hook name, kwargs) pairs, excluding stripe.<type>."""
    etype = event.type
    obj = _get(event.data, "object")
    names: list[str] = []
    if etype == "checkout.session.completed":
        names = ["checkout_completed"]
    elif etype == "customer.subscription.created":
        names = ["subscription_activated"]
    elif etype == "customer.subscription.updated":
        names = ["subscription_updated"]
        previous = _get(_get(event.data, "previous_attributes"), "status")
        if (
            _get(obj, "status") in ACTIVE_STATUSES
            and previous is not None
            and previous not in ACTIVE_STATUSES
        ):
            names.append("subscription_activated")
    elif etype == "customer.subscription.deleted":
        names = ["subscription_cancelled"]
    if not names:
        return []

    is_subscription = _get(obj, "object") == "subscription"
    kwargs: dict[str, Any] = {
        "event": event,
        "stripe_customer_id": _id(_get(obj, "customer")),
    }
    sub_id = _get(obj, "id") if is_subscription else _id(_get(obj, "subscription"))
    if sub_id:
        kwargs["stripe_subscription_id"] = sub_id
    status = _get(obj, "status")
    if status:
        kwargs["status"] = status
    return [(name, kwargs) for name in names]


@csrf_exempt
@require_POST
def webhook(request):
    secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", None)
    if not secret:
        logger.error("stripe webhook rejected: STRIPE_WEBHOOK_SECRET is not set")
        return HttpResponseBadRequest("webhook not configured")

    import stripe

    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = client().construct_event(request.body, sig_header, secret)
    except stripe.SignatureVerificationError:
        logger.warning("stripe webhook rejected: bad signature")
        return HttpResponseBadRequest("invalid signature")
    except ValueError:
        logger.warning("stripe webhook rejected: invalid payload")
        return HttpResponseBadRequest("invalid payload")

    logger.info("stripe webhook %s (%s)", event.id, event.type)
    do_action(f"stripe.{event.type}", event=event)
    for name, kwargs in normalized_hooks(event):
        do_action(name, **kwargs)
    return HttpResponse(status=200)
