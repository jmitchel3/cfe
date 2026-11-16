"""Redis cache and Celery broker: Docker Redis, Valkey, managed Redis, Upstash.

Both modules fill the `cache` slot (django-redis) and the `broker` slot
(`CFE_BROKER_URL`). For `rediss://` URLs the broker URL always carries
`ssl_cert_reqs`, because Celery's Redis result backend refuses a rediss URL
without it and kombu otherwise falls back to unverified TLS.
"""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cfe.stack.exceptions import StackConfigError
from cfe.stack.module import EnvVar, Module

CERT_REQS = ("required", "optional", "none")
# Short timeouts so a dead Redis fails fast instead of hanging requests.
SOCKET_TIMEOUT = 5
CHECK_TIMEOUT = 3

_URL = EnvVar(
    "REDIS_URL",
    required=True,
    secret=True,
    aliases=("REDIS_CACHE_URL",),
    help="redis:// or rediss:// URL, e.g. redis://localhost:6379/0.",
)
_PREFIX = EnvVar("REDIS_KEY_PREFIX", default="", help="Optional cache key prefix.")


def _parts(url: str):
    parts = urlsplit(url)
    if parts.scheme not in ("redis", "rediss", "unix"):
        raise StackConfigError("REDIS_URL must start with redis://, rediss://, or unix://")
    return parts


def _is_upstash(host: str | None) -> bool:
    host = (host or "").lower()
    return host == "upstash.io" or host.endswith(".upstash.io")


def with_cert_reqs(url: str, cert_reqs: str) -> str:
    """Return a rediss URL with `ssl_cert_reqs` set (an existing value wins)."""
    parts = urlsplit(url)
    if parts.scheme != "rediss":
        return url
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(k == "ssl_cert_reqs" for k, _ in query):
        query.append(("ssl_cert_reqs", cert_reqs))
    return urlunsplit(parts._replace(query=urlencode(query)))


def _cert_reqs(value: str) -> str:
    value = value.strip().lower().removeprefix("cert_")
    if value not in CERT_REQS:
        raise ValueError("expected required, optional, or none")
    return value


def _cache(url: str, prefix: str, pool_kwargs: dict | None = None) -> dict:
    options = {
        "CLIENT_CLASS": "django_redis.client.DefaultClient",
        "SOCKET_CONNECT_TIMEOUT": SOCKET_TIMEOUT,
        "SOCKET_TIMEOUT": SOCKET_TIMEOUT,
    }
    if pool_kwargs:
        options["CONNECTION_POOL_KWARGS"] = pool_kwargs
    cache = {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": url,
        "OPTIONS": options,
    }
    if prefix:
        cache["KEY_PREFIX"] = prefix
    return cache


def _ping(url: str) -> list[str]:
    try:
        import redis
    except ImportError:
        return ["redis is not installed (pip install 'cfe[redis]')"]
    try:
        client = redis.Redis.from_url(
            url, socket_connect_timeout=CHECK_TIMEOUT, socket_timeout=CHECK_TIMEOUT
        )
        client.ping()
    except Exception as exc:  # noqa: BLE001 (never echo the message: it can contain the URL)
        return [f"Redis ping failed ({type(exc).__name__})"]
    return []


class Redis(Module):
    name = "redis"
    label = "Redis"
    description = "Redis or Valkey (Docker or managed) as the Django cache and Celery broker."
    provides = ("cache", "broker")
    python_requires: ClassVar[dict[str, str]] = {
        "django_redis": "cfe[redis]",
        "redis": "cfe[redis]",
    }
    env = (
        _URL,
        _PREFIX,
        EnvVar(
            "REDIS_SSL_CERT_REQS",
            default="required",
            cast=_cert_reqs,
            help="TLS certificate check for rediss:// URLs: required, optional, or none.",
        ),
    )

    def url(self, env) -> str:
        parts = _parts(env.REDIS_URL)
        if parts.scheme == "redis" and _is_upstash(parts.hostname):
            raise StackConfigError("Upstash requires TLS: use a rediss:// REDIS_URL")
        return with_cert_reqs(env.REDIS_URL, env.REDIS_SSL_CERT_REQS)

    def settings(self, env):
        url = self.url(env)
        return {
            "CACHES": {"default": _cache(url, env.REDIS_KEY_PREFIX)},
            "CFE_BROKER_URL": url,
        }

    def checks(self, env):
        try:
            url = self.url(env)
        except StackConfigError as exc:
            return [str(exc)]
        return _ping(url)


class RedisUpstash(Redis):
    name = "redis-upstash"
    label = "Upstash Redis"
    description = "Upstash Redis over TLS as the Django cache and Celery broker."
    env = (_URL, _PREFIX)

    def url(self, env) -> str:
        parts = _parts(env.REDIS_URL)
        if parts.scheme != "rediss":
            raise StackConfigError(
                "redis-upstash requires a rediss:// REDIS_URL (Upstash only accepts TLS)"
            )
        # Upstash's Celery guide: rediss://...?ssl_cert_reqs=required
        return with_cert_reqs(env.REDIS_URL, "required")

    def settings(self, env):
        url = self.url(env)
        # Upstash closes idle TCP connections; ping pooled connections that
        # sat idle before reuse (redis-py production guidance) and keep TCP
        # alive. kombu's broker transport already health-checks every 25s.
        pool = {"health_check_interval": 30, "socket_keepalive": True}
        return {
            "CACHES": {"default": _cache(url, env.REDIS_KEY_PREFIX, pool)},
            "CFE_BROKER_URL": url,
        }


MODULES = [Redis, RedisUpstash]
