"""The Celery app for projects using the `celery` module.

    DJANGO_SETTINGS_MODULE=myproject.settings celery -A cfe.stack.celery_app worker --beat

Reads CELERY_* Django settings. The broker defaults to CFE_BROKER_URL (set by
the module filling the `broker` slot) unless CELERY_BROKER_URL is set.
"""

import os

from celery import Celery
from django.conf import settings

if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    raise RuntimeError("set DJANGO_SETTINGS_MODULE before loading cfe.stack.celery_app")

app = Celery("cfe")
app.config_from_object("django.conf:settings", namespace="CELERY")


def _apply_broker_url(celery_app, django_settings):
    if not getattr(django_settings, "CELERY_BROKER_URL", None):
        url = getattr(django_settings, "CFE_BROKER_URL", None)
        if url:
            celery_app.conf.broker_url = url


_apply_broker_url(app, settings)
app.autodiscover_tasks()
