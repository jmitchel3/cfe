# CFE Stack

`cfe.stack` is a module system for Django, in the spirit of WordPress plugins. One codebase runs on open-source services locally and on managed services in production; you switch between them by changing env vars only.

## Install

```bash
pip install "cfe[stack]"      # Django plus the module loader
pip install "cfe[postgres]"   # a vendor extra: the stack plus that service's SDK
pip install "cfe[all]"        # every first-party module's SDK
```

Vendor extras: `postgres`, `redis`, `qstash`, `celery`, `s3`, `stripe`. Plain `pip install cfe` installs the CLI only, without Django.

## Enable modules

List modules in `CFE_MODULES` (comma or space separated):

```bash
CFE_MODULES=postgres,redis,celery,s3,smtp,stripe
```

At the end of `settings.py`:

```python
import cfe.stack

cfe.stack.configure(globals())
```

`configure()` validates the selection, reads each module's env vars, and writes their settings into the namespace. It also appends `cfe.stack` and each module's apps to `INSTALLED_APPS`, inserts their middleware, and sets `CFE_STACK_MODULES` and `CFE_STACK_SLOTS`. Anything you assign after the call overrides what modules set.

Merge rules: if your settings already hold a dict under a key a module sets, the module's keys replace matching keys (so `DATABASES["default"]` is replaced and other aliases are kept). Otherwise the module's value wins. Two modules setting the same non-dict setting to different values is an error.

In `urls.py`, mount the URLs of enabled modules:

```python
from cfe.stack.urls import module_urlpatterns

urlpatterns = [
    # ... your routes
    *module_urlpatterns(),
]
```

Each module mounts at its `url_prefix` (default `<name>/`).

## Slots

A slot is a capability that exactly one enabled module may fill: `database`, `cache`, `broker`, `tasks`, `storage`, `email`, `billing`. Enabling two modules for the same slot is a configuration error. A module can also require another module (`requires`) or require that some module fills a slot (`requires_slots`, e.g. celery needs a `broker`).

`cfe.stack.current()` returns the resolved stack; `current().provider("cache")` gives the module filling a slot.

## Hooks

Actions are events; filters transform a value. Handlers run in priority order (lower first, default 10).

```python
from cfe.stack import hooks

@hooks.on("user_signed_up")
def welcome(user, **kwargs):
    ...

@hooks.filter("dashboard_nav")
def add_item(items, request=None, **kwargs):
    return [*items, {"label": "Reports", "url": "/reports/"}]

hooks.do_action("user_signed_up", user=user)
items = hooks.apply_filters("dashboard_nav", [], request=request)
```

Action handler errors are logged, not raised (pass `raise_errors=True` to change that). Modules declare hooks as `hooks = {"hook_name": "dotted.path.to.callable"}`; they are registered when Django's app registry is ready. A callable is registered as a filter if it has `is_filter = True` or its name starts with `filter_`.

## Strict mode

By default a missing required env var stops startup with `StackConfigError` (names only, never values). Build steps that run without secrets can turn this off:

```bash
CFE_STACK_STRICT=0 python manage.py collectstatic --noinput
```

With strict mode off, modules missing required env vars are skipped with a warning.

## Writing a module

Subclass `cfe.stack.Module`, set its class attributes, and override `settings()` (and optionally `checks()` and `ready()`). `cfe/stack/modules/sqlite.py` is the reference shape:

```python
from cfe.stack import EnvVar, Module

class SQLite(Module):
    name = "sqlite"
    provides = ("database",)
    env = (EnvVar("SQLITE_PATH", default="db.sqlite3"),)

    def settings(self, env):
        return {"DATABASES": {"default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": env.SQLITE_PATH,
        }}}

MODULES = [SQLite]
```

Rules:

- Import vendor SDKs inside methods, never at module import time, and declare them in `python_requires` (e.g. `{"psycopg": "cfe[postgres]"}`).
- Mark credentials `EnvVar(..., secret=True)` and never include values in errors or `checks()` output.
- Use the `installed_apps` and `middleware` attributes; `settings()` may not return `INSTALLED_APPS` or `MIDDLEWARE`.

## Third-party modules

Packages named `cfe-stack-<name>` expose modules through the `cfe.stack.modules` entry point group. In the package's `pyproject.toml`:

```toml
[project.entry-points."cfe.stack.modules"]
mymodule = "cfe_stack_mymodule:MODULES"
```

The target can be a single `Module` subclass or a list of them. Once the package is installed, add the module's `name` to `CFE_MODULES`. Entry-point modules cannot reuse a built-in module's name.

## Module docs

- [Database](modules/database.md)
- [Cache](modules/cache.md)
- [Tasks](modules/tasks.md)
- [Storage](modules/storage.md)
- [Email](modules/email.md)
- [Billing](modules/billing.md)
- [Management commands](commands.md) (`cfe list`, `cfe doctor`)
