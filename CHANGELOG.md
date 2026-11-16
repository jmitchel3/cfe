# Changelog

## Unreleased

### Added

- `cfe.stack`: a WordPress-style module system for Django. Enable modules with `CFE_MODULES` and call `cfe.stack.configure(globals())` at the end of `settings.py`. See [docs/stack.md](docs/stack.md).
- Module contract (`Module`, `EnvVar`), single-provider slots (database, cache, broker, tasks, storage, email, billing), dependency and env var validation, and a strict mode toggle (`CFE_STACK_STRICT`).
- Actions and filters hooks (`cfe.stack.hooks`) and `module_urlpatterns()` for mounting module URLs.
- Third-party module discovery through the `cfe.stack.modules` entry point group.
- First-party modules for databases, cache, broker, tasks, storage, email, and billing, with `cfe list` and `cfe doctor` management commands.
- Install extras: `stack`, `postgres`, `redis`, `qstash`, `celery`, `s3`, `stripe`, `all`. Plain `pip install cfe` still installs the CLI only, without Django.
- PyPI release workflow using trusted publishing. See [docs/releasing.md](docs/releasing.md).
