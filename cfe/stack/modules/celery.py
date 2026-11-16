"""Celery tasks with database-backed results and beat schedules.

Needs a module filling the `broker` slot (it sets CFE_BROKER_URL). Run workers
with `celery -A cfe.stack.celery_app worker --beat`.
"""

from typing import ClassVar

from cfe.stack.loader import to_bool
from cfe.stack.module import EnvVar, Module


class Celery(Module):
    name = "celery"
    label = "Celery"
    description = "Celery workers and beat, with results and schedules stored in the database."
    provides = ("tasks",)
    requires_slots = ("broker",)
    env = (
        EnvVar(
            "CELERY_TASK_ALWAYS_EAGER",
            default=False,
            cast=to_bool,
            help="Run tasks inline instead of sending them to the broker.",
        ),
    )
    python_requires: ClassVar[dict[str, str]] = {
        "celery": "cfe[celery]",
        "django_celery_beat": "cfe[celery]",
        "django_celery_results": "cfe[celery]",
    }
    installed_apps = ("django_celery_results", "django_celery_beat")

    def settings(self, env):
        return {
            "CFE_TASKS_BACKEND": "celery",
            "CELERY_RESULT_BACKEND": "django-db",
            "CELERY_BEAT_SCHEDULER": "django_celery_beat.schedulers:DatabaseScheduler",
            "CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP": True,
            "CELERY_TASK_ALWAYS_EAGER": env.CELERY_TASK_ALWAYS_EAGER,
        }

    def ready(self):
        # Bind shared tasks to the configured app so .delay() from web
        # processes uses CFE_BROKER_URL instead of Celery's default broker.
        import cfe.stack.celery_app  # noqa: F401


MODULES = [Celery]
