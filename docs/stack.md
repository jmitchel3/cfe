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

Load the stack at the top of `settings.py`, then assign its values explicitly:

```python
import cfe.stack
from cfe.env import Csv, config

stack = cfe.stack.load()  # reads CFE_MODULES and module env vars

DEBUG = config("DJANGO_DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="", cast=Csv())

INSTALLED_APPS = [*DJANGO_APPS, *PROJECT_APPS, *stack.installed_apps]
MIDDLEWARE = stack.middleware([...])               # module middleware inserted at its anchor
DATABASES = stack.databases({"default": SQLITE})   # module value layered over yours
CACHES = stack.caches()
STORAGES = stack.storages()                        # keeps Django's "staticfiles" entry

# Library settings modules provide (CELERY_*, EMAIL_*, STRIPE_*, ...), plus
# CFE_STACK, CFE_STACK_MODULES, CFE_STACK_SLOTS.
globals().update(stack.extra_settings())
```

Nothing is written into your settings behind your back. `python manage.py cfe settings` lists every value a module provides, which module set it, and where to assign it. Django system checks (`cfe_stack.W001` to `W004`) warn when a module's database, cache, storage, apps, or middleware are not actually assigned, so a module is never silently ignored.

### Env vars: `cfe.env`

`cfe.env` is a fork of [python-decouple](https://github.com/HBNetwork/python-decouple) 3.8 (MIT), bundled so the stack has one env loader with no extra dependency. `config("KEY", default=..., cast=...)`, `Csv`, `Choices`, `AutoConfig`, and the `.env`, `settings.ini`, and secrets-directory repositories work as in decouple. Lookup order: `os.environ` first (so `doppler run --` and container env win), then the nearest `settings.ini` or `.env` walking up from the directory of `settings.py`. `.env` lines may start with `export `.

`cfe.stack.load()` reads module env vars through the same source. Pass `environ={...}` (tests) or `search_path=` to override; `modules="postgres,redis"` overrides `CFE_MODULES`.

Merge rules: `stack.databases(base)` returns `base` with the module's aliases replacing matching ones (`default` is replaced, other aliases kept). With no `base`, dict settings layer over Django's own default. Two modules setting the same non-dict setting to different values is an error, and so is a middleware anchor that is not in your list.

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

`settings.CFE_STACK` is the loaded stack (its repr shows module names only, never env values). `settings.CFE_STACK.provider("cache")` gives the module filling a slot, and `stack.env("stripe").STRIPE_SECRET_KEY` reads the resolved env of an enabled module.

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
