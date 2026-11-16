# Cache and broker modules

| Module | Slots | Env |
|---|---|---|
| `redis` | cache, broker | `REDIS_URL` (required, secret; alias `REDIS_CACHE_URL`), `REDIS_KEY_PREFIX`, `REDIS_SSL_CERT_REQS` |
| `redis-upstash` | cache, broker | `REDIS_URL` (required, secret; alias `REDIS_CACHE_URL`), `REDIS_KEY_PREFIX` |
| `locmem` | cache | none |

Install the SDKs with `pip install "cfe[redis]"`.

## What they set

- `CACHES["default"]`: `django_redis.cache.RedisCache` at `REDIS_URL`, 5s socket timeouts, `KEY_PREFIX` when `REDIS_KEY_PREFIX` is set.
- `CFE_BROKER_URL`: the same URL, for Celery.
- `locmem` sets `CACHES["default"]` to `LocMemCache` (per process; tests and dev only). It cannot be enabled together with `redis` or `redis-upstash`.

## `redis`

Works with Docker Redis, Valkey, and managed Redis (Railway, Render, Redis Cloud): `redis://` for plain TCP, `rediss://` for TLS.

For `rediss://` URLs, `ssl_cert_reqs` is appended to the URL unless it already has one. It defaults to `required` (verify the server certificate); set `REDIS_SSL_CERT_REQS=none` only for providers with self-signed certificates. Celery needs this parameter: its Redis result backend rejects a `rediss://` URL without it, and kombu (the broker) otherwise falls back to unverified TLS with a warning.

A `redis://` URL pointing at an `upstash.io` host is rejected, since Upstash only accepts TLS.

## `redis-upstash`

Use the TCP URL from the Upstash console (`rediss://default:<token>@<host>:6379`). Any non-`rediss://` URL is rejected. The URL gets `ssl_cert_reqs=required`, the form in Upstash's Celery guide.

The cache pool also sets `health_check_interval=30` and `socket_keepalive=True`, so pooled connections that sat idle (Upstash closes idle connections) are pinged before reuse. kombu already health-checks broker connections every 25s.

Upstash notes:
- Celery workers poll Redis continuously, which drives up command counts; Upstash recommends a Fixed plan over Pay-As-You-Go for Celery.
- Plans cap concurrent connections (`ERR max concurrent connections exceeded`); keep worker concurrency and web processes within the limit.

## Doctor

`checks()` pings the server with a 3s timeout. Messages name the error type only, never the URL or token.
