"""Upstash QStash tasks via django-qstash.

QStash calls back into the app over HTTPS, so the webhook is mounted by
`cfe.stack.urls.module_urlpatterns()` at DJANGO_QSTASH_WEBHOOK_PATH.
"""

from typing import ClassVar

from cfe.stack.module import EnvVar, Module


class QStash(Module):
    name = "qstash"
    label = "QStash"
    description = "Serverless background tasks and schedules on Upstash QStash."
    provides = ("tasks",)
    env = (
        EnvVar("QSTASH_TOKEN", required=True, secret=True, help="QStash API token."),
        EnvVar(
            "QSTASH_CURRENT_SIGNING_KEY",
            required=True,
            secret=True,
            help="Verifies webhook signatures.",
        ),
        EnvVar(
            "QSTASH_NEXT_SIGNING_KEY",
            required=True,
            secret=True,
            help="Verifies webhook signatures after key rotation.",
        ),
        EnvVar(
            "DJANGO_QSTASH_DOMAIN",
            required=True,
            help="Public origin QStash calls back to, e.g. https://example.com.",
        ),
        EnvVar(
            "DJANGO_QSTASH_WEBHOOK_PATH",
            default="/qstash/webhook/",
            help="Path the webhook is served at.",
        ),
        EnvVar(
            "QSTASH_URL",
            help="QStash API base URL; read by django-qstash from the environment (local dev server).",
        ),
    )
    python_requires: ClassVar[dict[str, str]] = {"django_qstash": "cfe[qstash]"}
    installed_apps = ("django_qstash", "django_qstash.results", "django_qstash.schedules")
    # `urlpatterns` below is built from DJANGO_QSTASH_WEBHOOK_PATH at URL load.
    urls = "cfe.stack.modules.qstash"
    url_prefix = ""

    def settings(self, env):
        stripped = env.DJANGO_QSTASH_WEBHOOK_PATH.strip("/")
        path = f"/{stripped}/" if stripped else "/"
        return {
            "CFE_TASKS_BACKEND": "qstash",
            "QSTASH_TOKEN": env.QSTASH_TOKEN,
            "QSTASH_CURRENT_SIGNING_KEY": env.QSTASH_CURRENT_SIGNING_KEY,
            "QSTASH_NEXT_SIGNING_KEY": env.QSTASH_NEXT_SIGNING_KEY,
            "DJANGO_QSTASH_DOMAIN": env.DJANGO_QSTASH_DOMAIN.rstrip("/"),
            "DJANGO_QSTASH_WEBHOOK_PATH": path,
        }


MODULES = [QStash]


def __getattr__(name):
    # Lazy `urlpatterns` so importing this module never imports Django URLs
    # or django-qstash (which creates its client at import time).
    if name != "urlpatterns":
        raise AttributeError(name)
    from django.conf import settings
    from django.urls import include, path

    prefix = settings.DJANGO_QSTASH_WEBHOOK_PATH.lstrip("/")
    return [path(prefix, include("django_qstash.urls"))]
