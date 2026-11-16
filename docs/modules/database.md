# Database modules

`postgres`, `neon`, and `xata` live in `cfe/stack/modules/postgres.py`. Each fills the `database` slot and sets `DATABASES["default"]` from `DATABASE_URL` using `dj_database_url.parse`. Install with `pip install "cfe[postgres]"` (psycopg 3).

```bash
CFE_MODULES=postgres      # or neon, or xata
DATABASE_URL=postgres://user:pass@host:5432/db
```

## Env vars

| Name | Default | Notes |
|---|---|---|
| `DATABASE_URL` | required | Secret. |
| `DATABASE_CONN_MAX_AGE` | `300` | Seconds; forced to `0` when pooled. |
| `DATABASE_CONN_HEALTH_CHECKS` | `true` | |
| `DATABASE_POOLED` | `false` (`postgres`), auto (`neon`, `xata`) | Set when behind a transaction-mode pooler such as PgBouncer. |
| `DATABASE_SSLMODE` | unset | Overrides the URL's `sslmode` and any module default. |
| `DATABASE_DIRECT_URL` | unset | `neon` and `xata` only. Secret. When set, also exposes `DATABASES["direct"]` (never pooled). |

Pooled means `CONN_MAX_AGE=0` and `DISABLE_SERVER_SIDE_CURSORS=True`, because a transaction-mode pooler does not keep session state (server-side cursors, `SET`, session advisory locks) between transactions.

Options in the URL query string (`?sslmode=...&channel_binding=...`) are passed through as `OPTIONS` and win over module defaults.

## postgres

Any Postgres. Nothing is inferred: `sslmode` is left to the URL unless `DATABASE_SSLMODE` is set.

## neon

- Pooled is auto-detected when the host contains `-pooler.` (Neon's PgBouncer endpoint, transaction mode). `DATABASE_POOLED` overrides the detection.
- Defaults `sslmode=require` and `channel_binding=require` when the URL omits them, matching the connection strings Neon's console generates.
- Neon recommends a direct (non-pooled) connection for schema migrations. Set `DATABASE_DIRECT_URL` to the non-pooler host and run `python manage.py migrate --database direct`, or point `DATABASE_URL` at the direct host for the migrate step.
- Keep `DATABASE_CONN_MAX_AGE` at or below the compute's scale-to-zero timeout (5 minutes by default), with health checks on.

## xata

Xata is standard Postgres. Get the URL with `xata branch url` (add `--type pooler` for the pooled endpoint).

- Pooled is auto-detected when the host contains `-pooler.` (`<branch-id>-pooler.<region>.xata.sh`, PgBouncer in transaction mode). The direct endpoint is `<branch-id>-rw.<region>.xata.sh`.
- `DATABASE_DIRECT_URL` works as for Neon.
- No `sslmode` or `channel_binding` default is applied; the URL from `xata branch url` is used as given.

## Doctor

`checks()` connects with psycopg (5 second timeout) and runs `SELECT 1` against `DATABASE_URL` and, if set, `DATABASE_DIRECT_URL`. Failures report the variable name, host, and error class; the URL and password are removed from the message.
