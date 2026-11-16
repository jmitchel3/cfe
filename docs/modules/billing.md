# Billing: `stripe`

Fills the `billing` slot. Install with `pip install "cfe[stripe]"` and add `stripe` to `CFE_MODULES`.

## Env vars

| Name | Required | Secret | Notes |
|---|---|---|---|
| `STRIPE_SECRET_KEY` | yes | yes | Prefer a restricted key (`rk_...`) over `sk_...`. |
| `STRIPE_PUBLISHABLE_KEY` | no | no | `pk_...`, for browser code. |
| `STRIPE_WEBHOOK_SECRET` | yes | yes | Endpoint signing secret, `whsec_...`. |
| `STRIPE_API_VERSION` | no | no | Pins the API version; unset uses the SDK's pinned version. |

Each is copied to a Django setting of the same name.

## Client

```python
from cfe.stack.contrib.stripe import client

session = client().v1.checkout.sessions.create(params={...})
```

`client()` returns a `stripe.StripeClient` built from settings. Do not use the deprecated global `stripe.api_key`.

## Webhook

The module mounts `stripe/webhook/` (include `cfe.stack.urls.module_urlpatterns()` in your root urls). Point a Stripe webhook endpoint at `https://<host>/stripe/webhook/`.

- POST only (GET returns 405); CSRF exempt.
- The `Stripe-Signature` header is verified with `STRIPE_WEBHOOK_SECRET`. A bad signature or payload returns 400.
- After dispatch it returns 200. Handler exceptions are logged by `cfe.stack.hooks` and never become a 500.
- Only the event id and type are logged.

## Hooks

Every verified event fires `stripe.<event.type>` with `event=`:

```python
from cfe.stack import hooks

@hooks.on("stripe.invoice.payment_failed")
def on_failed(event):
    ...
```

Normalized billing hooks, for app code that should not depend on Stripe:

| Hook | Fired on |
|---|---|
| `checkout_completed` | `checkout.session.completed` |
| `subscription_activated` | `customer.subscription.created`, or `customer.subscription.updated` whose status moved from a non-active status into `active` or `trialing` |
| `subscription_updated` | `customer.subscription.updated` |
| `subscription_cancelled` | `customer.subscription.deleted` |

Keyword args: `event`, `stripe_customer_id` (may be `None`, e.g. guest checkout), plus `stripe_subscription_id` and `status` when present on the event object. Accept `**kwargs` in handlers so new fields do not break them.

```python
@hooks.on("subscription_activated")
def grant_access(event, stripe_customer_id, **kwargs):
    ...
```

Stripe can deliver events more than once and out of order; make handlers idempotent (for example, key on `event.id`). For async payment methods, also handle `stripe.checkout.session.async_payment_succeeded` and check `payment_status` before fulfilling.

## Doctor

`cfe doctor` calls `client.v1.accounts.retrieve_current()` and checks key prefixes. Output names the key mode (`test` or `live`) and the error type only, never a key.
