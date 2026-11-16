# cfe.stack commands

All commands print env var names only, never values.

## `python manage.py cfe list`

Table of every available module (name, label, slots, enabled, source) and the slot map for this process. Source is `builtin`, `entry point`, or `local`. Modules skipped by non-strict mode are listed under the slot map.

## `python manage.py cfe doctor [--offline]`

For each enabled module:

- required env vars are set (secret ones are marked `(secret)`),
- every `python_requires` package is importable (otherwise it suggests the pip extra, e.g. `pip install "cfe[redis]"`),
- `module.checks(env)` passes (reachability; skipped with `--offline`). Secret values are masked as `***` if a check message contains one.

Modules skipped by non-strict mode (`CFE_STACK_STRICT=false`) count as failures. Exit code is 1 when anything fails, so it works as a deploy or CI gate.

## `python manage.py cfe env [--all] [--names]`

Every env var of the enabled modules: module, name, required, secret, has default, set. `--all` covers every available module. `--names` prints bare names, one per line, which is handy for creating Doppler configs:

```bash
python manage.py cfe env --all --names
```

## `python manage.py cfe new <name> [--path FILE] [--force]`

Scaffolds a local module (default `./modules/<name>.py`, dashes become underscores) from the sqlite reference shape, creates the package `__init__.py` if missing, and prints the `CFE_MODULE_PATHS` value to add.

## Local modules: `CFE_MODULE_PATHS`

Projects can keep modules in their own code. List dotted module paths, comma separated; each must expose `MODULES = [...]`:

```bash
CFE_MODULE_PATHS=myproject.modules.analytics,myproject.modules.audit
CFE_MODULES=postgres,redis,analytics
```

Discovery order is built-ins, then `cfe.stack.modules` entry points, then local paths. A name defined twice is an error; nothing can shadow a built-in. `configure(globals(), environ=...)` reads `CFE_MODULE_PATHS` from that same `environ`. In code: `registry.available(registry.env_module_paths(environ))`.

## `cfe stack modules` (CLI)

Lists available modules without a Django project or settings. Needs Django installed (`pip install "cfe[stack]"`); otherwise it prints that hint and exits 1. Honors `CFE_MODULE_PATHS` (the modules must be importable from the current Python path) and marks names found in `CFE_MODULES`.
