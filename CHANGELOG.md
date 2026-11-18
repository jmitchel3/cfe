# Changelog

## Unreleased

## 0.0.23 (2026-11-18)

### Fixed

- `cfe login` and every other API call follow redirects with the method and body kept. Before, a redirect from `www.` to the bare domain turned the login POST into a GET and the server answered 405. The token is only forwarded to the same site, and https-to-http redirects are refused.
- The default host is now `https://codingforentrepreneurs.com`.

### Added

- `cfe update` (alias `cfe upgrade`): upgrades cfe using the matching command for how it was installed (uv tool, pipx, pip or uv pip; uvx and editable installs get instructions instead). `--check` and `--dry-run` supported.
- Daily background update check with a one-line notice on stderr. Reads `/api/cli/version/` on the CFE host (`latest`, `min_supported`, `message`), falling back to PyPI. Never blocks; skipped in CI, without a terminal, or with `CFE_NO_UPDATE_CHECK=1`. Cached in `~/.cache/cfe/update-check.json`.
- `cfe --version`, plus top-level `cfe login`, `cfe logout`, and `cfe whoami` shortcuts for the `cfe auth` commands.
- API responses with HTTP 426 tell you to run `cfe update`.
- `cfe auth login`, `cfe auth status`, `cfe auth logout`, and `cfe auth token`: sign in with the browser device flow, check your account and access, and print the token for scripts. Tokens are stored in `~/.config/cfe/hosts.json` (`0600`); `CFE_TOKEN`, `CFE_HOST`, and `CFE_CONFIG_DIR` override defaults. Standard library HTTP only, no new dependencies.
- `cfe.stack`: a WordPress-style module system for Django. Enable modules with `CFE_MODULES` load it with `cfe.stack.load()` in `settings.py`, and assign its values explicitly. `manage.py cfe settings` shows where each value comes from.
- `cfe.env`: a bundled fork of python-decouple 3.8 (MIT) for env and `.env` loading, no extra dependency. See [docs/stack.md](docs/stack.md).
- Module contract (`Module`, `EnvVar`), single-provider slots (database, cache, broker, tasks, storage, email, billing), dependency and env var validation, and a strict mode toggle (`CFE_STACK_STRICT`).
- Actions and filters hooks (`cfe.stack.hooks`) and `module_urlpatterns()` for mounting module URLs.
- Third-party module discovery through the `cfe.stack.modules` entry point group.
- First-party modules for databases, cache, broker, tasks, storage, email, and billing, with `cfe list` and `cfe doctor` management commands.
- Install extras: `stack`, `postgres`, `redis`, `qstash`, `celery`, `s3`, `stripe`, `all`. Plain `pip install cfe` still installs the CLI only, without Django.
- PyPI release workflow using trusted publishing. See [docs/releasing.md](docs/releasing.md).
