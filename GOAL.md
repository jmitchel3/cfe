# Goal: CFE Stack POC

Ship the first working version of `cfe.stack`: a module system for Django, inspired by WordPress plugins, that lets one codebase (starting with `cfe.run`) run on open-source services locally and swap in managed services per deploy by changing env vars only.

## Done means

1. `pip install "cfe[all]"` installs the CLI, Django, and every first-party module's SDK. `pip install cfe` installs the CLI only, with no Django.
2. A Django project enables modules with one env var, e.g. `CFE_MODULES=postgres,redis,celery,s3,smtp,stripe`, and its `settings.py` calls `stack = cfe.stack.load()` and assigns each value explicitly (`DATABASES = stack.databases(...)`), with env loaded through `cfe.env`. No vendor-specific code remains in the project's settings.
3. `cfe.run` boots and passes its tests in two configurations, with no code changes between them:
   - **Local, open source:** `docker compose up` with Postgres, Redis, RustFS (S3), Mailpit, and a Celery worker.
   - **Managed:** Neon (or Xata) Postgres, Upstash Redis, QStash tasks, Cloudflare R2, with secrets injected by `doppler run --`.
4. `python manage.py cfe list` shows enabled modules and the slot each fills; `python manage.py cfe doctor` validates required env vars (names only, never values) and reachability.
5. Third-party and paid modules install as separate packages (`cfe-stack-<name>`) and are discovered through the `cfe.stack.modules` entry point group.
6. `cfe` 0.1.0 is released to PyPI from GitHub Actions via trusted publishing (`release.yaml`, `release` environment).

## Out of scope for the POC

- Paid-module access control (GitHub team membership granted from Stripe webhooks). Designed, not built.
- A module marketplace or index.
- Non-Django frameworks.

## First-party modules

| Slot | Modules |
|---|---|
| database | `postgres` (any `DATABASE_URL`), `neon`, `xata`, `sqlite` |
| cache | `redis` (Docker or managed), `redis-upstash`, `locmem` |
| broker | `redis`, `redis-upstash` |
| tasks | `celery`, `qstash`, `sync` |
| storage | `s3` (S3, R2, RustFS, MinIO, Tigris), `local` |
| email | `smtp`, `console` |
| billing | `stripe` |
