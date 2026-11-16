"""Backend-neutral background tasks.

App code imports from here instead of Celery or django-qstash:

    from cfe.stack.tasks import shared_task

    @shared_task
    def send_welcome(user_id): ...

    send_welcome.delay(42)

The backend comes from `settings.CFE_TASKS_BACKEND` ("celery", "qstash" or
"sync"; default "sync"), set by whichever module fills the `tasks` slot. It is
read when a function is decorated, so Django settings must be configured by
then (they are, for task modules imported by Django or Celery autodiscovery).
"""

from __future__ import annotations

import functools
import logging
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("cfe.stack")

BACKENDS = ("celery", "qstash", "sync")


def backend() -> str:
    from django.conf import settings

    name = getattr(settings, "CFE_TASKS_BACKEND", "sync") or "sync"
    if name not in BACKENDS:
        raise ValueError(f"CFE_TASKS_BACKEND must be one of {', '.join(BACKENDS)}, got {name!r}")
    return name


class SyncResult:
    """Minimal stand-in for Celery's AsyncResult when tasks run inline."""

    def __init__(self, result: Any = None, exception: BaseException | None = None):
        self.id = str(uuid.uuid4())
        self.result = exception if exception is not None else result
        self.status = "FAILURE" if exception is not None else "SUCCESS"
        self._exception = exception

    def ready(self) -> bool:
        return True

    def successful(self) -> bool:
        return self._exception is None

    def failed(self) -> bool:
        return self._exception is not None

    def get(self, timeout: float | None = None, propagate: bool = True) -> Any:
        if self._exception is not None and propagate:
            raise self._exception
        return self.result


class SyncTask:
    """A function whose `.delay()`/`.apply_async()` run it immediately."""

    def __init__(
        self, func: Callable[..., Any], name: str | None = None, bind: bool = False, **options: Any
    ):
        functools.update_wrapper(self, func)
        self.func = func
        self.name = name or f"{func.__module__}.{func.__qualname__}"
        self.bind = bind
        self.options = options

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.bind:
            return self.func(self, *args, **kwargs)
        return self.func(*args, **kwargs)

    def delay(self, *args: Any, **kwargs: Any) -> SyncResult:
        return self.apply_async(args, kwargs)

    def apply_async(self, args: Any = None, kwargs: Any = None, **options: Any) -> SyncResult:
        try:
            return SyncResult(self(*(args or ()), **(kwargs or {})))
        except Exception as exc:
            logger.exception("sync task %s failed", self.name)
            return SyncResult(exception=exc)

    def __repr__(self) -> str:
        return f"<SyncTask {self.name}>"


def _register(func: Callable[..., Any], options: dict[str, Any]) -> Any:
    kind = backend()
    if kind == "celery":
        from celery import shared_task as backend_shared_task
    elif kind == "qstash":
        from django_qstash import shared_task as backend_shared_task
    else:
        return SyncTask(func, **options)
    return backend_shared_task(**options)(func) if options else backend_shared_task(func)


def shared_task(*args: Any, **options: Any) -> Any:
    """Decorate a task. Works as `@shared_task` and `@shared_task(**opts)`.

    Options are passed through to the backend's own `shared_task`.
    """
    if args:
        if len(args) != 1 or not callable(args[0]):
            raise TypeError("shared_task takes a function or keyword options only")
        return _register(args[0], options)
    return lambda func: _register(func, options)


def _task_name(task: Any) -> str:
    if isinstance(task, str):
        return task
    func = getattr(task, "func", None)  # django-qstash QStashTask, SyncTask
    if func is not None and backend() == "qstash":
        return f"{func.__module__}.{func.__name__}"
    return getattr(task, "name", None) or f"{task.__module__}.{task.__name__}"


def schedule(
    name: str,
    task: Any,
    cron: str,
    args: list | tuple | None = None,
    kwargs: dict | None = None,
) -> Any:
    """Create or update a periodic task named `name` on a five-field `cron`.

    Celery: a django-celery-beat PeriodicTask with a CrontabSchedule (UTC).
    QStash: a django-qstash TaskSchedule (synced to QStash on save).
    Sync: logs a warning and does nothing (no scheduler runs).
    `task` is a decorated task or its dotted name. Returns the saved row or None.
    """
    args = list(args or [])
    kwargs = dict(kwargs or {})
    name_of_task = _task_name(task)
    kind = backend()

    if kind == "celery":
        import json

        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        minute, hour, day_of_month, month_of_year, day_of_week = cron.split()
        crontab, _ = CrontabSchedule.objects.get_or_create(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
            timezone="UTC",
        )
        obj, _ = PeriodicTask.objects.update_or_create(
            name=name,
            defaults={
                "task": name_of_task,
                "crontab": crontab,
                "interval": None,
                "args": json.dumps(args),
                "kwargs": json.dumps(kwargs),
                "enabled": True,
            },
        )
        return obj

    if kind == "qstash":
        from django_qstash.schedules.models import TaskSchedule

        obj, _ = TaskSchedule.objects.update_or_create(
            name=name,
            defaults={"task": name_of_task, "cron": cron, "args": args, "kwargs": kwargs},
        )
        return obj

    logger.warning("cfe.stack sync tasks backend has no scheduler; schedule %r ignored", name)
    return None
