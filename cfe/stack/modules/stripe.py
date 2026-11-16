"""Stripe billing: API client settings plus a signed webhook endpoint that
dispatches cfe.stack hooks. See docs/modules/billing.md.
"""

from typing import ClassVar

from cfe.stack.module import EnvVar, Module


def key_mode(key: str | None) -> str:
    """Return "test", "live", or "unknown" from a Stripe key's prefix only."""
    key = key or ""
    for prefix in ("sk_", "rk_", "pk_"):
        if key.startswith(prefix + "test_"):
            return "test"
        if key.startswith(prefix + "live_"):
            return "live"
    return "unknown"


class Stripe(Module):
    name = "stripe"
    label = "Stripe"
    description = "Stripe billing: StripeClient settings and a verified webhook endpoint."
    provides = ("billing",)
    env = (
        EnvVar(
            "STRIPE_SECRET_KEY",
            required=True,
            secret=True,
            help="Secret or restricted API key (sk_... or rk_...).",
        ),
        EnvVar("STRIPE_PUBLISHABLE_KEY", help="Publishable key (pk_...) for the browser."),
        EnvVar(
            "STRIPE_WEBHOOK_SECRET",
            required=True,
            secret=True,
            help="Webhook endpoint signing secret (whsec_...).",
        ),
        EnvVar("STRIPE_API_VERSION", help="Pin an API version; defaults to the SDK's version."),
    )
    python_requires: ClassVar[dict[str, str]] = {"stripe": "cfe[stripe]"}
    installed_apps = ("cfe.stack.contrib.stripe",)
    urls = "cfe.stack.contrib.stripe.urls"
    url_prefix = "stripe/"

    def settings(self, env):
        return {
            "STRIPE_SECRET_KEY": env.STRIPE_SECRET_KEY,
            "STRIPE_PUBLISHABLE_KEY": env.STRIPE_PUBLISHABLE_KEY,
            "STRIPE_WEBHOOK_SECRET": env.STRIPE_WEBHOOK_SECRET,
            "STRIPE_API_VERSION": env.STRIPE_API_VERSION,
        }

    def checks(self, env):
        problems = []
        mode = key_mode(env.STRIPE_SECRET_KEY)
        if mode == "unknown":
            problems.append("STRIPE_SECRET_KEY has an unrecognized prefix (expected sk_ or rk_)")
        pk = env.get("STRIPE_PUBLISHABLE_KEY")
        if pk and key_mode(pk) != mode:
            problems.append(
                f"STRIPE_PUBLISHABLE_KEY mode ({key_mode(pk)}) does not match "
                f"STRIPE_SECRET_KEY mode ({mode})"
            )
        if not (env.STRIPE_WEBHOOK_SECRET or "").startswith("whsec_"):
            problems.append("STRIPE_WEBHOOK_SECRET has an unrecognized prefix (expected whsec_)")
        try:
            from cfe.stack.contrib.stripe import make_client

            make_client(
                env.STRIPE_SECRET_KEY, env.STRIPE_API_VERSION
            ).v1.accounts.retrieve_current()
        except Exception as exc:  # noqa: BLE001
            # Type and HTTP status only: Stripe error messages can echo part of the key.
            status = getattr(exc, "http_status", None)
            detail = f"{type(exc).__name__}" + (f", HTTP {status}" if status else "")
            problems.append(f"Stripe account lookup failed with {mode} mode key ({detail})")
        return problems


MODULES = [Stripe]
