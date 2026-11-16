from unittest import mock

import pytest

from cfe.stack import StackConfigError, loader, registry
from cfe.stack.modules.redis import Redis, RedisUpstash
from tests.stack.helpers import apply_stack

UPSTASH = "rediss://default:tok3n@eu1-sharp-cat-1.upstash.io:6379"


@pytest.fixture(autouse=True)
def _registry():
    registry.available.cache_clear()
    yield
    registry.available.cache_clear()


def configure(modules, environ):
    ns = {}
    ns = apply_stack(modules, environ, ns)
    return ns


def test_redis_plain_url():
    ns = configure("redis", {"REDIS_URL": "redis://localhost:6379/0"})
    cache = ns["CACHES"]["default"]
    assert cache["BACKEND"] == "django_redis.cache.RedisCache"
    assert cache["LOCATION"] == "redis://localhost:6379/0"
    assert "KEY_PREFIX" not in cache
    assert ns["CFE_BROKER_URL"] == "redis://localhost:6379/0"
    assert ns["CFE_STACK_SLOTS"] == {"cache": "redis", "broker": "redis"}


def test_redis_tls_adds_cert_reqs():
    ns = configure("redis", {"REDIS_URL": "rediss://:pw@redis.example.com:6380/1"})
    url = "rediss://:pw@redis.example.com:6380/1?ssl_cert_reqs=required"
    assert ns["CACHES"]["default"]["LOCATION"] == url
    assert ns["CFE_BROKER_URL"] == url


def test_redis_tls_cert_reqs_env_and_existing_query():
    ns = configure(
        "redis",
        {"REDIS_URL": "rediss://h:6379/0?db=2", "REDIS_SSL_CERT_REQS": "CERT_NONE"},
    )
    assert ns["CFE_BROKER_URL"] == "rediss://h:6379/0?db=2&ssl_cert_reqs=none"
    ns = configure("redis", {"REDIS_URL": "rediss://h:6379?ssl_cert_reqs=optional"})
    assert ns["CFE_BROKER_URL"] == "rediss://h:6379?ssl_cert_reqs=optional"


def test_redis_bad_cert_reqs_hides_url():
    with pytest.raises(StackConfigError) as exc:
        configure("redis", {"REDIS_URL": "rediss://:pw@h:6379", "REDIS_SSL_CERT_REQS": "maybe"})
    assert "pw@" not in str(exc.value)


def test_tls_url_accepted_by_celery_and_kombu():
    import ssl

    from kombu import Connection

    ns = configure("redis", {"REDIS_URL": "rediss://:pw@h:6379/0"})
    assert Connection(ns["CFE_BROKER_URL"]).ssl == {"ssl_cert_reqs": ssl.CERT_REQUIRED}


def test_tls_url_accepted_by_django_redis_pool():
    import redis

    ns = configure("redis", {"REDIS_URL": "rediss://:pw@h:6379/0"})
    pool = redis.ConnectionPool.from_url(ns["CACHES"]["default"]["LOCATION"])
    assert pool.connection_class is redis.SSLConnection
    assert pool.connection_kwargs["ssl_cert_reqs"] == "required"


def test_key_prefix():
    ns = configure("redis", {"REDIS_URL": "redis://r:6379", "REDIS_KEY_PREFIX": "app"})
    assert ns["CACHES"]["default"]["KEY_PREFIX"] == "app"


def test_alias_env_var():
    ns = configure("redis", {"REDIS_CACHE_URL": "redis://old:6379/3"})
    assert ns["CFE_BROKER_URL"] == "redis://old:6379/3"


def test_missing_url():
    with pytest.raises(StackConfigError, match="REDIS_URL"):
        configure("redis", {})


def test_bad_scheme():
    with pytest.raises(StackConfigError, match="must start with"):
        configure("redis", {"REDIS_URL": "http://h"})


def test_redis_rejects_plain_upstash():
    with pytest.raises(StackConfigError, match="Upstash requires TLS") as exc:
        configure("redis", {"REDIS_URL": "redis://default:tok3n@x.upstash.io:6379"})
    assert "tok3n" not in str(exc.value)


def test_upstash_settings():
    ns = configure("redis-upstash", {"REDIS_URL": UPSTASH, "REDIS_KEY_PREFIX": "p"})
    url = UPSTASH + "?ssl_cert_reqs=required"
    cache = ns["CACHES"]["default"]
    assert cache["LOCATION"] == url
    assert cache["KEY_PREFIX"] == "p"
    assert cache["OPTIONS"]["CONNECTION_POOL_KWARGS"] == {
        "health_check_interval": 30,
        "socket_keepalive": True,
    }
    assert ns["CFE_BROKER_URL"] == url
    assert ns["CFE_STACK_SLOTS"] == {"cache": "redis-upstash", "broker": "redis-upstash"}


def test_upstash_enforces_tls():
    with pytest.raises(StackConfigError, match="requires a rediss://") as exc:
        configure("redis-upstash", {"REDIS_URL": UPSTASH.replace("rediss", "redis")})
    assert "tok3n" not in str(exc.value)


def test_upstash_alias():
    ns = configure("redis-upstash", {"REDIS_CACHE_URL": UPSTASH})
    assert ns["CFE_BROKER_URL"].startswith(UPSTASH)


def test_locmem():
    ns = configure("locmem", {})
    assert ns["CACHES"]["default"]["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache"
    assert "CFE_BROKER_URL" not in ns
    assert ns["CFE_STACK_SLOTS"] == {"cache": "locmem"}


@pytest.mark.parametrize("redis_name", ["redis", "redis-upstash"])
def test_slot_conflict_with_locmem(redis_name):
    with pytest.raises(StackConfigError, match="both provide the 'cache' slot"):
        loader.build([redis_name, "locmem"], environ={"REDIS_URL": UPSTASH})


def test_checks_ping_ok():
    env = loader.resolve_env(Redis(), {"REDIS_URL": "rediss://:pw@h:6379"})[0]
    with mock.patch("redis.Redis.from_url") as from_url:
        assert Redis().checks(env) == []
    from_url.assert_called_once_with(
        "rediss://:pw@h:6379?ssl_cert_reqs=required", socket_connect_timeout=3, socket_timeout=3
    )
    from_url.return_value.ping.assert_called_once_with()


def test_checks_ping_failure_hides_url():
    import redis

    env = loader.resolve_env(RedisUpstash(), {"REDIS_URL": UPSTASH})[0]
    with mock.patch("redis.Redis.from_url") as from_url:
        from_url.return_value.ping.side_effect = redis.ConnectionError(f"cannot reach {UPSTASH}")
        problems = RedisUpstash().checks(env)
    assert problems == ["Redis ping failed (ConnectionError)"]
    assert "tok3n" not in problems[0]


def test_checks_reports_config_error():
    env = loader.resolve_env(RedisUpstash(), {"REDIS_URL": "redis://default:tok3n@x.upstash.io"})[0]
    problems = RedisUpstash().checks(env)
    assert len(problems) == 1
    assert "tok3n" not in problems[0]
