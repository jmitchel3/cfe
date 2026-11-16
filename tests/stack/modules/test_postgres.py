import sys
import types

import pytest

from cfe.stack import loader
from cfe.stack.modules.postgres import Neon, Postgres, Xata

PASSWORD = "s3cr3t-pw"
PLAIN = f"postgres://app:{PASSWORD}@db.internal:5432/app"
NEON_POOLER = f"postgresql://app:{PASSWORD}@ep-cool-123-pooler.us-east-2.aws.neon.tech/neondb"
NEON_DIRECT = f"postgresql://app:{PASSWORD}@ep-cool-123.us-east-2.aws.neon.tech/neondb"
XATA_POOLER = f"postgresql://ws:{PASSWORD}@br-1-pooler.us-east-1.xata.sh:5432/app:main"
XATA_RW = f"postgresql://ws:{PASSWORD}@br-1-rw.us-east-1.xata.sh:5432/app:main"


def settings_for(name, **environ):
    stack = loader.build([name], environ=environ)
    return loader.merge_module_settings(stack)["DATABASES"]


def test_plain_postgres():
    dbs = settings_for("postgres", DATABASE_URL=PLAIN)
    assert set(dbs) == {"default"}
    db = dbs["default"]
    assert db["ENGINE"] == "django.db.backends.postgresql"
    assert (db["HOST"], db["PORT"], db["NAME"], db["USER"]) == ("db.internal", 5432, "app", "app")
    assert db["CONN_MAX_AGE"] == 300
    assert db["CONN_HEALTH_CHECKS"] is True
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is False
    assert "sslmode" not in db.get("OPTIONS", {})


def test_plain_postgres_env_overrides():
    db = settings_for(
        "postgres",
        DATABASE_URL=PLAIN,
        DATABASE_CONN_MAX_AGE="60",
        DATABASE_CONN_HEALTH_CHECKS="false",
        DATABASE_SSLMODE="verify-full",
    )["default"]
    assert db["CONN_MAX_AGE"] == 60
    assert db["CONN_HEALTH_CHECKS"] is False
    assert db["OPTIONS"]["sslmode"] == "verify-full"


def test_pooled_postgres():
    db = settings_for("postgres", DATABASE_URL=PLAIN, DATABASE_POOLED="true")["default"]
    assert db["CONN_MAX_AGE"] == 0
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is True


def test_postgres_does_not_sniff_pooler_host():
    db = settings_for("postgres", DATABASE_URL=NEON_POOLER)["default"]
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is False


def test_neon_pooler_auto_detect_and_defaults():
    dbs = settings_for("neon", DATABASE_URL=NEON_POOLER)
    db = dbs["default"]
    assert db["CONN_MAX_AGE"] == 0
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is True
    assert db["OPTIONS"] == {"sslmode": "require", "channel_binding": "require"}
    assert "direct" not in dbs


def test_neon_direct_host_not_pooled():
    db = settings_for("neon", DATABASE_URL=NEON_DIRECT)["default"]
    assert db["CONN_MAX_AGE"] == 300
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is False


def test_neon_explicit_pooled_override_and_url_options_win():
    db = settings_for(
        "neon",
        DATABASE_URL=NEON_POOLER + "?sslmode=verify-full&channel_binding=prefer",
        DATABASE_POOLED="false",
    )["default"]
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is False
    assert db["OPTIONS"] == {"sslmode": "verify-full", "channel_binding": "prefer"}


def test_neon_direct_url_alias():
    dbs = settings_for("neon", DATABASE_URL=NEON_POOLER, DATABASE_DIRECT_URL=NEON_DIRECT)
    assert dbs["default"]["DISABLE_SERVER_SIDE_CURSORS"] is True
    direct = dbs["direct"]
    assert direct["HOST"] == "ep-cool-123.us-east-2.aws.neon.tech"
    assert direct["CONN_MAX_AGE"] == 300
    assert direct["DISABLE_SERVER_SIDE_CURSORS"] is False


def test_xata_pooler_and_rw():
    pooled = settings_for("xata", DATABASE_URL=XATA_POOLER)["default"]
    assert pooled["DISABLE_SERVER_SIDE_CURSORS"] is True
    assert pooled["CONN_MAX_AGE"] == 0
    assert pooled["NAME"] == "app:main"
    assert "OPTIONS" not in pooled or "sslmode" not in pooled["OPTIONS"]
    rw = settings_for("xata", DATABASE_URL=XATA_RW)["default"]
    assert rw["DISABLE_SERVER_SIDE_CURSORS"] is False


def test_missing_url_error_names_only():
    with pytest.raises(Exception, match="DATABASE_URL") as exc:
        loader.build(["neon"], environ={})
    assert PASSWORD not in str(exc.value)


def test_bad_cast_error_hides_value():
    with pytest.raises(Exception) as exc:
        loader.build(
            ["postgres"], environ={"DATABASE_URL": PLAIN, "DATABASE_CONN_MAX_AGE": PASSWORD}
        )
    assert PASSWORD not in str(exc.value)


def test_url_vars_are_secret():
    for cls in (Postgres, Neon, Xata):
        secret = {v.name for v in cls.env if v.secret}
        assert "DATABASE_URL" in secret
    assert "DATABASE_DIRECT_URL" in {v.name for v in Neon.env if v.secret}


class FakeConn:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.calls.append(sql)


@pytest.fixture
def fake_psycopg(monkeypatch):
    mod = types.SimpleNamespace(calls=[], error=None)

    def connect(url, **kwargs):
        mod.calls.append((url, kwargs))
        if mod.error:
            raise mod.error
        return FakeConn(mod.calls)

    mod.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", mod)
    return mod


def env_for(name, **environ):
    return loader.build([name], environ=environ).envs[name]


def test_checks_ok(fake_psycopg):
    assert Neon().checks(env_for("neon", DATABASE_URL=NEON_POOLER)) == []
    url, kwargs = fake_psycopg.calls[0]
    assert url == NEON_POOLER
    assert kwargs == {"connect_timeout": 5, "sslmode": "require", "channel_binding": "require"}
    assert fake_psycopg.calls[1] == "SELECT 1"


def test_checks_failure_hides_secrets(fake_psycopg):
    fake_psycopg.error = RuntimeError(f"connection to {PLAIN} failed: password {PASSWORD} rejected")
    problems = Postgres().checks(env_for("postgres", DATABASE_URL=PLAIN))
    assert len(problems) == 1
    msg = problems[0]
    assert "db.internal" in msg and "RuntimeError" in msg
    assert PASSWORD not in msg and PLAIN not in msg


def test_checks_direct_url_too(fake_psycopg):
    fake_psycopg.error = OSError("timeout")
    problems = Neon().checks(
        env_for("neon", DATABASE_URL=NEON_POOLER, DATABASE_DIRECT_URL=NEON_DIRECT)
    )
    assert [p.split(":")[0] for p in problems] == ["DATABASE_URL", "DATABASE_DIRECT_URL"]
    assert all(PASSWORD not in p for p in problems)


def test_checks_without_psycopg(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    problems = Postgres().checks(env_for("postgres", DATABASE_URL=PLAIN))
    assert problems == ["psycopg is not installed; install cfe[postgres]"]
