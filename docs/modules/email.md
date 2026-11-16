# Email modules (slot `email`)

## `smtp`

Django's SMTP backend for any provider, and Mailpit locally.

| Env var | Default | Notes |
|---|---|---|
| `EMAIL_HOST` | required unless `EMAIL_URL` is set | |
| `EMAIL_PORT` | `587` | |
| `EMAIL_HOST_USER` | empty | |
| `EMAIL_HOST_PASSWORD` | empty | Secret. |
| `EMAIL_USE_TLS` | `true` | STARTTLS. |
| `EMAIL_USE_SSL` | `false` | Implicit TLS. Setting both TLS and SSL to true is an error. |
| `DEFAULT_FROM_EMAIL` | Django default | Only set when given. |
| `SERVER_EMAIL` | Django default | Only set when given. |
| `EMAIL_TIMEOUT` | `10` | Seconds. |
| `EMAIL_URL` | none | Secret. Overrides host, port, user, password, TLS and SSL. |

`EMAIL_URL` schemes:

| Scheme | TLS | SSL | Default port |
|---|---|---|---|
| `smtp://` | off | off | 25 |
| `smtp+tls://`, `submission://` | on | off | 587 |
| `smtps://`, `smtp+ssl://` | off | on | 465 |

Percent-encode special characters in the user and password, e.g. `smtp+tls://apikey:p%40ss@smtp.example.com:587`.

Mailpit (Docker, local):

```sh
EMAIL_HOST=localhost EMAIL_PORT=1025 EMAIL_USE_TLS=false
# or
EMAIL_URL=smtp://localhost:1025
```

`cfe doctor` connects with the configured timeout and sends EHLO; failures report the host, port, and exception type only.

## `console`

Prints outgoing mail to stdout. No env vars.
