# Background tasks: `celery`, `qstash`, `sync`

All three fill the `tasks` slot and set `CFE_TASKS_BACKEND`. App code never imports a backend directly:

```python
from cfe.stack.tasks import schedule, shared_task

@shared_task
def send_welcome(user_id): ...

@shared_task(bind=True, max_retries=3)
def sync_account(self, account_id): ...

send_welcome.delay(42)
send_welcome(42)  # still a plain call
schedule("nightly-cleanup", "myapp.tasks.cleanup", "0 3 * * *")
```

`shared_task` picks the backend when the function is decorated, from `settings.CFE_TASKS_BACKEND` (default `"sync"`), and passes options through to `celery.shared_task` or `django_qstash.shared_task`. Use options both backends understand (`bind`, `name`) if you switch between them.

`schedule(name, task, cron, args=None, kwargs=None)` creates or updates a periodic task from a five-field cron expression. `task` is a decorated task or its dotted path.

| Backend | `schedule()` writes |
|---|---|
| celery | django-celery-beat `PeriodicTask` with a UTC `CrontabSchedule` |
| qstash | django-qstash `TaskSchedule` (synced to QStash on save) |
| sync | nothing; logs a warning |

Call it from a data migration or a management command, not at import time.

## `celery`

Requires a module filling the `broker` slot (`redis` or `redis-upstash`), which sets `CFE_BROKER_URL`. Install `cfe[celery]`.

| Env | Default | Notes |
|---|---|---|
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Run tasks inline. |

Sets `CELERY_RESULT_BACKEND="django-db"`, `CELERY_BEAT_SCHEDULER` to the django-celery-beat database scheduler, and `CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP=True`. Adds `django_celery_results` and `django_celery_beat` to `INSTALLED_APPS` (run `migrate`).

Run the worker with the bundled app; `DJANGO_SETTINGS_MODULE` must already be set:

```sh
DJANGO_SETTINGS_MODULE=myproject.settings celery -A cfe.stack.celery_app worker --beat
```

`cfe.stack.celery_app` reads `CELERY_*` Django settings and uses `CFE_BROKER_URL` as the broker unless `CELERY_BROKER_URL` is set. The module imports it on Django startup, so `.delay()` from web processes uses the same broker.

## `qstash`

Upstash QStash via django-qstash (0.10). Install `cfe[qstash]`.

| Env | Required | Notes |
|---|---|---|
| `QSTASH_TOKEN` | yes, secret | |
| `QSTASH_CURRENT_SIGNING_KEY` | yes, secret | Webhook signature check. |
| `QSTASH_NEXT_SIGNING_KEY` | yes, secret | Webhook signature check. |
| `DJANGO_QSTASH_DOMAIN` | yes | Public origin QStash calls, e.g. `https://example.com`. |
| `DJANGO_QSTASH_WEBHOOK_PATH` | no, `/qstash/webhook/` | |
| `QSTASH_URL` | no | API base URL for a local QStash dev server; django-qstash reads it from the environment. |

Adds `django_qstash`, `django_qstash.results`, `django_qstash.schedules` (run `migrate`). The webhook is mounted at `DJANGO_QSTASH_WEBHOOK_PATH` by `module_urlpatterns()`, so the project's `urls.py` needs `*module_urlpatterns()` and nothing qstash-specific.

## `sync`

No env. `.delay()` and `.apply_async()` run the function immediately and return a result with `.get()`, `.status`, `.successful()`. Exceptions are logged and re-raised by `.get()`. For development and tests.
