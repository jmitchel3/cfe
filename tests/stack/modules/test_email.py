import smtplib
from unittest import mock

import pytest

from cfe.stack import StackConfigError, loader
from cfe.stack.modules.smtp import SMTP, parse_email_url

SECRET = "p@ss/w0rd-secret"
BACKEND = "django.core.mail.backends.smtp.EmailBackend"


def configure(modules, environ):
    ns = {}
    loader.configure(ns, modules=modules, environ=environ)
    return ns


def smtp_env(**environ):
    env, _ = loader.resolve_env(SMTP(), environ)
    return env


def test_smtp_defaults():
    ns = configure("smtp", {"EMAIL_HOST": "smtp.example.com"})
    assert ns["EMAIL_BACKEND"] == BACKEND
    assert ns["EMAIL_HOST"] == "smtp.example.com"
    assert ns["EMAIL_PORT"] == 587
    assert ns["EMAIL_USE_TLS"] is True
    assert ns["EMAIL_USE_SSL"] is False
    assert ns["EMAIL_HOST_USER"] == ""
    assert ns["EMAIL_HOST_PASSWORD"] == ""
    assert ns["EMAIL_TIMEOUT"] == 10
    assert "DEFAULT_FROM_EMAIL" not in ns
    assert "SERVER_EMAIL" not in ns


def test_smtp_mailpit_and_addresses():
    ns = configure(
        "smtp",
        {
            "EMAIL_HOST": "localhost",
            "EMAIL_PORT": "1025",
            "EMAIL_USE_TLS": "false",
            "EMAIL_HOST_USER": "u",
            "EMAIL_HOST_PASSWORD": SECRET,
            "DEFAULT_FROM_EMAIL": "app@example.com",
            "SERVER_EMAIL": "errors@example.com",
            "EMAIL_TIMEOUT": "3",
        },
    )
    assert ns["EMAIL_PORT"] == 1025
    assert ns["EMAIL_USE_TLS"] is False
    assert ns["EMAIL_HOST_PASSWORD"] == SECRET
    assert ns["DEFAULT_FROM_EMAIL"] == "app@example.com"
    assert ns["SERVER_EMAIL"] == "errors@example.com"
    assert ns["EMAIL_TIMEOUT"] == 3


def test_smtp_tls_ssl_conflict():
    with pytest.raises(StackConfigError, match="EMAIL_USE_TLS and EMAIL_USE_SSL"):
        configure("smtp", {"EMAIL_HOST": "h", "EMAIL_USE_SSL": "true"})


def test_smtp_needs_host_or_url():
    with pytest.raises(StackConfigError, match="EMAIL_HOST or EMAIL_URL"):
        configure("smtp", {})


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("smtp+tls://user:pass@mail.example.com:2525", ("mail.example.com", 2525, True, False)),
        ("smtp+tls://mail.example.com", ("mail.example.com", 587, True, False)),
        ("smtps://mail.example.com", ("mail.example.com", 465, False, True)),
        ("smtp+ssl://mail.example.com:4650", ("mail.example.com", 4650, False, True)),
        ("smtp://localhost:1025", ("localhost", 1025, False, False)),
        ("smtp://localhost", ("localhost", 25, False, False)),
    ],
)
def test_parse_email_url(url, expected):
    v = parse_email_url(url)
    assert (v["EMAIL_HOST"], v["EMAIL_PORT"], v["EMAIL_USE_TLS"], v["EMAIL_USE_SSL"]) == expected


def test_parse_email_url_quoted_credentials():
    v = parse_email_url("smtp+tls://apikey:p%40ss%2Fw0rd-secret@smtp.sendgrid.net:587")
    assert v["EMAIL_HOST_USER"] == "apikey"
    assert v["EMAIL_HOST_PASSWORD"] == SECRET


@pytest.mark.parametrize("url", ["http://u:secretpw@h", "smtp://u:secretpw@h:notaport", "smtp://"])
def test_parse_email_url_errors_hide_secret(url):
    with pytest.raises(StackConfigError) as exc:
        parse_email_url(url)
    assert "secretpw" not in str(exc.value)


def test_email_url_fills_settings():
    ns = configure(
        "smtp",
        {
            "EMAIL_URL": "smtps://u:p%40ss%2Fw0rd-secret@smtp.example.com",
            "EMAIL_HOST": "ignored",
            "DEFAULT_FROM_EMAIL": "a@example.com",
        },
    )
    assert ns["EMAIL_HOST"] == "smtp.example.com"
    assert ns["EMAIL_PORT"] == 465
    assert ns["EMAIL_USE_SSL"] is True
    assert ns["EMAIL_USE_TLS"] is False
    assert ns["EMAIL_HOST_PASSWORD"] == SECRET
    assert ns["DEFAULT_FROM_EMAIL"] == "a@example.com"


def test_secrets_marked():
    secret = {v.name for v in SMTP.env if v.secret}
    assert secret == {"EMAIL_URL", "EMAIL_HOST_PASSWORD"}


def test_smtp_checks_ok():
    conn = mock.MagicMock()
    conn.__enter__.return_value = conn
    conn.ehlo.return_value = (250, b"ok")
    with mock.patch.object(smtplib, "SMTP", return_value=conn) as factory:
        assert SMTP().checks(smtp_env(EMAIL_HOST="localhost", EMAIL_PORT="1025")) == []
    factory.assert_called_once_with("localhost", 1025, timeout=10)
    conn.ehlo.assert_called_once()


def test_smtp_checks_ssl_uses_smtp_ssl():
    conn = mock.MagicMock()
    conn.__enter__.return_value = conn
    conn.ehlo.return_value = (250, b"ok")
    with mock.patch.object(smtplib, "SMTP_SSL", return_value=conn) as factory:
        env = smtp_env(
            EMAIL_URL="smtps://u:p%40ss%2Fw0rd-secret@mail.example.com", EMAIL_TIMEOUT="2"
        )
        assert SMTP().checks(env) == []
    factory.assert_called_once_with("mail.example.com", 465, timeout=2)


def test_smtp_checks_failures_hide_secret():
    env = smtp_env(EMAIL_HOST="mail.example.com", EMAIL_HOST_PASSWORD=SECRET)
    with mock.patch.object(smtplib, "SMTP", side_effect=OSError(SECRET)):
        problems = SMTP().checks(env)
    assert problems == ["SMTP server mail.example.com:587 not reachable (OSError)"]

    conn = mock.MagicMock()
    conn.__enter__.return_value = conn
    conn.ehlo.return_value = (554, SECRET.encode())
    with mock.patch.object(smtplib, "SMTP", return_value=conn):
        problems = SMTP().checks(env)
    assert problems == ["SMTP server mail.example.com:587 rejected EHLO (code 554)"]
    assert SECRET not in problems[0]


def test_smtp_checks_config_error():
    assert SMTP().checks(smtp_env()) == ["module 'smtp' needs EMAIL_HOST or EMAIL_URL"]


def test_console():
    ns = configure("console", {})
    assert ns["EMAIL_BACKEND"] == "django.core.mail.backends.console.EmailBackend"


def test_only_one_email_provider():
    with pytest.raises(StackConfigError, match="email"):
        configure("smtp,console", {"EMAIL_HOST": "h"})
