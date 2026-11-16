"""Postgres databases: any DATABASE_URL, plus Neon and Xata presets.

All three fill the `database` slot and set DATABASES["default"] from
DATABASE_URL via dj_database_url. Neon and Xata may also set
DATABASES["direct"] from DATABASE_DIRECT_URL (a non-pooled connection, for
migrations and other session-level work).
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlsplit

from cfe.stack.loader import to_bool
from cfe.stack.module import Env, EnvVar, Module

CONNECT_TIMEOUT = 5


def _env(direct: bool = False, pooled_default: bool | None = False) -> tuple[EnvVar, ...]:
    pooled_help = (
        "Set true behind a transaction-mode pooler (PgBouncer)."
        if pooled_default is False
        else "Override pooled detection (default: true when the host contains '-pooler.')."
    )
    vars_ = [
        EnvVar("DATABASE_URL", required=True, secret=True, help="postgres:// connection URL."),
        EnvVar(
            "DATABASE_CONN_MAX_AGE",
            default=300,
            cast=int,
            help="Seconds to keep connections open (ignored when pooled).",
        ),
        EnvVar(
            "DATABASE_CONN_HEALTH_CHECKS",
            default=True,
            cast=to_bool,
            help="Check persistent connections before reuse.",
        ),
        EnvVar("DATABASE_POOLED", default=pooled_default, cast=to_bool, help=pooled_help),
        EnvVar("DATABASE_SSLMODE", help="libpq sslmode; overrides the URL and module default."),
    ]
    if direct:
        vars_.append(
            EnvVar(
                "DATABASE_DIRECT_URL",
                secret=True,
                help='Non-pooled URL, exposed as DATABASES["direct"] when set.',
            )
        )
    return tuple(vars_)


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


class Postgres(Module):
    name = "postgres"
    label = "PostgreSQL"
    description = "Any PostgreSQL database from DATABASE_URL."
    provides = ("database",)
    env = _env()
    python_requires: ClassVar[dict[str, str]] = {"psycopg": "cfe[postgres]"}

    # Module defaults applied only when the URL does not set the option.
    default_options: ClassVar[dict[str, str]] = {}

    def is_pooled(self, env: Env, url: str) -> bool:
        return bool(env.DATABASE_POOLED)

    def db_config(self, env: Env, url: str, pooled: bool) -> dict[str, Any]:
        import dj_database_url

        config = dict(
            dj_database_url.parse(
                url,
                conn_max_age=0 if pooled else env.DATABASE_CONN_MAX_AGE,
                conn_health_checks=env.DATABASE_CONN_HEALTH_CHECKS,
                disable_server_side_cursors=pooled,
            )
        )
        options = dict(config.get("OPTIONS") or {})
        for key, value in self.default_options.items():
            options.setdefault(key, value)
        if env.DATABASE_SSLMODE:
            options["sslmode"] = env.DATABASE_SSLMODE
        if options:
            config["OPTIONS"] = options
        return config

    def settings(self, env):
        url = env.DATABASE_URL
        databases = {"default": self.db_config(env, url, self.is_pooled(env, url))}
        direct = env.get("DATABASE_DIRECT_URL")
        if direct:
            databases["direct"] = self.db_config(env, direct, pooled=False)
        return {"DATABASES": databases}

    def checks(self, env):
        problems = []
        targets = [("DATABASE_URL", env.DATABASE_URL)]
        if env.get("DATABASE_DIRECT_URL"):
            targets.append(("DATABASE_DIRECT_URL", env.DATABASE_DIRECT_URL))
        for var, url in targets:
            problem = self._check_one(env, var, url)
            if problem:
                problems.append(problem)
        return problems

    def _check_one(self, env: Env, var: str, url: str) -> str | None:
        try:
            import psycopg
        except ImportError:
            return f"psycopg is not installed; install {self.python_requires['psycopg']}"
        options = self.db_config(env, url, pooled=False).get("OPTIONS", {})
        kwargs = {k: v for k, v in options.items() if k in {"sslmode", "channel_binding"}}
        host = _host(url) or "unknown host"
        try:
            with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT, **kwargs) as conn:
                conn.execute("SELECT 1")
        except Exception as exc:  # noqa: BLE001 (report any failure, never raise)
            return f"{var}: cannot connect to {host} ({type(exc).__name__}: {_scrub(exc, url)})"
        return None


def _scrub(exc: Exception, url: str) -> str:
    """First line of the error, with the URL and password removed."""
    text = (str(exc).strip().splitlines() or [""])[0]
    secrets = [url]
    try:
        password = urlsplit(url).password
    except ValueError:
        password = None
    if password:
        secrets.append(password)
    for secret in secrets:
        text = text.replace(secret, "***")
    return text[:200]


class Neon(Postgres):
    name = "neon"
    label = "Neon"
    description = "Neon serverless Postgres; pooled when the host is a -pooler endpoint."
    env = _env(direct=True, pooled_default=None)
    # Neon's console URLs include both; keep them when a URL omits them.
    default_options: ClassVar[dict[str, str]] = {"sslmode": "require", "channel_binding": "require"}

    def is_pooled(self, env, url):
        if env.DATABASE_POOLED is not None:
            return bool(env.DATABASE_POOLED)
        return "-pooler." in _host(url)


class Xata(Neon):
    name = "xata"
    label = "Xata"
    description = "Xata Postgres; pooled when the host is a -pooler branch endpoint."
    # Xata documents no sslmode or channel_binding defaults; use the URL as given.
    default_options: ClassVar[dict[str, str]] = {}


MODULES = [Postgres, Neon, Xata]
