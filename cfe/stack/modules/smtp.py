"""SMTP email for any provider (Postmark, SES, Resend, Mailgun, ...) and Mailpit locally."""

from urllib.parse import unquote, urlsplit

from cfe.stack.exceptions import StackConfigError
from cfe.stack.loader import to_bool
from cfe.stack.module import EnvVar, Module

BACKEND = "django.core.mail.backends.smtp.EmailBackend"

# scheme -> (use_tls, use_ssl, default port)
_SCHEMES = {
    "smtp": (False, False, 25),
    "smtp+tls": (True, False, 587),
    "submission": (True, False, 587),
    "smtps": (False, True, 465),
    "smtp+ssl": (False, True, 465),
}


def parse_email_url(url):
    """Parse e.g. smtp+tls://user:pass@host:587 into EMAIL_* values.

    Errors never include the URL, which may carry a password.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _SCHEMES:
        raise StackConfigError(
            f"module 'smtp': EMAIL_URL scheme must be one of {', '.join(sorted(_SCHEMES))}"
        )
    try:
        port = parts.port
    except ValueError:
        raise StackConfigError("module 'smtp': EMAIL_URL has an invalid port") from None
    if not parts.hostname:
        raise StackConfigError("module 'smtp': EMAIL_URL has no host")
    use_tls, use_ssl, default_port = _SCHEMES[scheme]
    return {
        "EMAIL_HOST": parts.hostname,
        "EMAIL_PORT": port or default_port,
        "EMAIL_HOST_USER": unquote(parts.username or ""),
        "EMAIL_HOST_PASSWORD": unquote(parts.password or ""),
        "EMAIL_USE_TLS": use_tls,
        "EMAIL_USE_SSL": use_ssl,
    }


class SMTP(Module):
    name = "smtp"
    label = "SMTP email"
    description = "Sends email through any SMTP server (Mailpit locally)."
    provides = ("email",)
    env = (
        EnvVar(
            "EMAIL_URL",
            secret=True,
            help="Optional, e.g. smtp+tls://user:pass@host:587; overrides host, port, user, password, TLS/SSL.",
        ),
        # Required unless EMAIL_URL is set; enforced in settings().
        EnvVar("EMAIL_HOST", help="SMTP server host (required unless EMAIL_URL is set)."),
        EnvVar("EMAIL_PORT", default=587, cast=int, help="SMTP port."),
        EnvVar("EMAIL_HOST_USER", default="", help="SMTP username."),
        EnvVar("EMAIL_HOST_PASSWORD", default="", secret=True, help="SMTP password."),
        EnvVar("EMAIL_USE_TLS", default=True, cast=to_bool, help="STARTTLS (default true)."),
        EnvVar("EMAIL_USE_SSL", default=False, cast=to_bool, help="Implicit TLS (default false)."),
        EnvVar("DEFAULT_FROM_EMAIL", help="Default sender address."),
        EnvVar("SERVER_EMAIL", help="Sender address for error emails to ADMINS."),
        EnvVar("EMAIL_TIMEOUT", default=10, cast=int, help="Socket timeout in seconds."),
    )

    def resolve(self, env):
        values = {
            "EMAIL_HOST": env.EMAIL_HOST,
            "EMAIL_PORT": env.EMAIL_PORT,
            "EMAIL_HOST_USER": env.EMAIL_HOST_USER or "",
            "EMAIL_HOST_PASSWORD": env.EMAIL_HOST_PASSWORD or "",
            "EMAIL_USE_TLS": env.EMAIL_USE_TLS,
            "EMAIL_USE_SSL": env.EMAIL_USE_SSL,
        }
        if env.EMAIL_URL:
            values.update(parse_email_url(env.EMAIL_URL))
        if not values["EMAIL_HOST"]:
            raise StackConfigError("module 'smtp' needs EMAIL_HOST or EMAIL_URL")
        if values["EMAIL_USE_TLS"] and values["EMAIL_USE_SSL"]:
            raise StackConfigError(
                "module 'smtp': EMAIL_USE_TLS and EMAIL_USE_SSL are both true; enable only one"
            )
        return values

    def settings(self, env):
        out = {"EMAIL_BACKEND": BACKEND, **self.resolve(env), "EMAIL_TIMEOUT": env.EMAIL_TIMEOUT}
        for key in ("DEFAULT_FROM_EMAIL", "SERVER_EMAIL"):
            if env.get(key):
                out[key] = env.get(key)
        return out

    def checks(self, env):
        import smtplib

        try:
            v = self.resolve(env)
        except StackConfigError as exc:
            return [str(exc)]
        cls = smtplib.SMTP_SSL if v["EMAIL_USE_SSL"] else smtplib.SMTP
        where = f"{v['EMAIL_HOST']}:{v['EMAIL_PORT']}"
        try:
            with cls(v["EMAIL_HOST"], v["EMAIL_PORT"], timeout=env.EMAIL_TIMEOUT) as conn:
                code, _ = conn.ehlo()
                if code != 250:
                    return [f"SMTP server {where} rejected EHLO (code {code})"]
        except Exception as exc:  # noqa: BLE001 (report any failure, never its text)
            return [f"SMTP server {where} not reachable ({type(exc).__name__})"]
        return []


MODULES = [SMTP]
