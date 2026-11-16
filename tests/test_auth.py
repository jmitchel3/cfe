"""Tests for `cfe auth`. No network: urllib.request.urlopen is faked."""

import io
import json
import os
import stat
import urllib.error
from email.message import Message

import pytest
from typer.testing import CliRunner

from cfe import auth
from cfe.main import app

runner = CliRunner()
HOST = "https://www.codingforentrepreneurs.com"
TOKEN = "cfe_abcdefghijklmnopqrstuvwxyz0123456789"
USER = {"id": 42, "email": "you@example.com", "name": "You"}
DEVICE = {
    "device_code": "dev123",
    "user_code": "BCDF-GHJK",
    "verification_uri": f"{HOST}/cli/activate",
    "verification_uri_complete": f"{HOST}/cli/activate?code=BCDF-GHJK",
    "expires_in": 600,
    "interval": 5,
}


class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._raw = json.dumps(body).encode() if body is not None else b""
        self.headers = headers or {}

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def reply(status, body=None, headers=None):
    """Return a callable producing either a response or an HTTPError."""

    def make(req):
        if status >= 400:
            msg = Message()
            for k, v in (headers or {}).items():
                msg[k] = v
            raw = json.dumps(body).encode() if body is not None else b""
            return urllib.error.HTTPError(req.full_url, status, "err", msg, io.BytesIO(raw))
        return FakeResp(status, body, headers)

    return make


class Server:
    def __init__(self, routes):
        self.routes = routes  # "METHOD path" -> list of reply() items (last repeats)
        self.calls = []

    def __call__(self, req, timeout=None):
        key = f"{req.get_method()} {req.full_url.split('/api/cli/')[1]}"
        body = json.loads(req.data) if req.data else None
        self.calls.append((key, body, dict(req.header_items()), req.full_url, timeout))
        queue = self.routes[key]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        result = item(req)
        if isinstance(result, Exception):
            raise result
        return result

    def keys(self):
        return [c[0] for c in self.calls]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CFE_CONFIG_DIR", str(tmp_path / "cfg"))
    for var in ("CFE_TOKEN", "CFE_HOST", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(auth.time, "sleep", lambda s: sleeps.append(s))
    sleeps.clear()
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: opened.append(url) or True)
    opened.clear()
    return tmp_path / "cfg"


sleeps: list = []
opened: list = []


def serve(monkeypatch, routes):
    server = Server(routes)
    monkeypatch.setattr(auth.urllib.request, "urlopen", server)
    return server


def pending():
    return reply(400, {"error": "authorization_pending", "error_description": "wait"})


def success():
    return reply(200, {"access_token": TOKEN, "token_type": "Bearer", "scope": "", "user": USER})


def saved(cfg, host=HOST):
    return json.loads((cfg / "hosts.json").read_text())[host]


def test_login_full_flow(monkeypatch, env):
    server = serve(
        monkeypatch,
        {
            "POST device/": [reply(200, DEVICE)],
            "POST token/": [
                pending(),
                reply(400, {"error": "slow_down", "interval": 10}),
                pending(),
                success(),
            ],
        },
    )
    result = runner.invoke(app, ["auth", "login", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "First copy your one-time code: BCDF-GHJK" in result.output
    assert DEVICE["verification_uri_complete"] in result.output
    assert "Logged in as you@example.com" in result.output
    assert sleeps == [5, 5, 10, 10]
    assert opened == []
    first = server.calls[0]
    assert first[1]["client_name"].startswith("cfe CLI ")
    assert first[1]["hostname"]
    assert first[2]["User-agent"].startswith("cfe-cli/")
    assert first[4] == 15
    token_call = server.calls[1]
    assert token_call[1] == {"device_code": "dev123", "grant_type": auth.GRANT_TYPE}
    entry = saved(env)
    assert entry["token"] == TOKEN
    assert entry["user"] == USER
    assert entry["created_at"]


def test_token_file_permissions(monkeypatch, env):
    serve(
        monkeypatch,
        {"POST device/": [reply(200, DEVICE)], "POST token/": [success()]},
    )
    runner.invoke(app, ["auth", "login", "--no-browser"])
    assert stat.S_IMODE(os.stat(env / "hosts.json").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o700


def test_login_opens_browser_when_interactive(monkeypatch):
    serve(monkeypatch, {"POST device/": [reply(200, DEVICE)], "POST token/": [success()]})
    monkeypatch.setattr(auth, "_is_interactive", lambda: True)
    result = runner.invoke(app, ["auth", "login"], input="\n")
    assert result.exit_code == 0, result.output
    assert f"Press Enter to open {DEVICE['verification_uri']} in your browser" in result.output
    assert opened == [DEVICE["verification_uri_complete"]]


@pytest.mark.parametrize(
    "error,text",
    [
        ("expired_token", "expired"),
        ("access_denied", "denied"),
        ("invalid_grant", "no longer valid"),
    ],
)
def test_login_terminal_errors(monkeypatch, env, error, text):
    serve(
        monkeypatch,
        {
            "POST device/": [reply(200, DEVICE)],
            "POST token/": [pending(), reply(400, {"error": error})],
        },
    )
    result = runner.invoke(app, ["auth", "login", "--no-browser"])
    assert result.exit_code == 1
    assert text in result.output
    assert not (env / "hosts.json").exists()


def test_login_rate_limited_sleeps_retry_after(monkeypatch):
    serve(
        monkeypatch,
        {
            "POST device/": [reply(200, DEVICE)],
            "POST token/": [
                reply(429, {"error": "rate_limited"}, {"Retry-After": "42"}),
                success(),
            ],
        },
    )
    result = runner.invoke(app, ["auth", "login", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert sleeps == [5, 42, 5]


def test_login_times_out_locally(monkeypatch):
    short = dict(DEVICE, expires_in=12)
    serve(monkeypatch, {"POST device/": [reply(200, short)], "POST token/": [pending()]})
    result = runner.invoke(app, ["auth", "login", "--no-browser"])
    assert result.exit_code == 1
    assert "expired" in result.output


def test_login_connection_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(auth.urllib.request, "urlopen", boom)
    result = runner.invoke(app, ["auth", "login", "--no-browser"])
    assert result.exit_code == 1
    assert "Could not reach" in result.output
    assert "Traceback" not in result.output


def test_login_already_logged_in(monkeypatch, env):
    auth.store_token(HOST, "cfe_old", USER)
    server = serve(monkeypatch, {})
    result = runner.invoke(app, ["auth", "login", "--no-browser"], input="n\n")
    assert result.exit_code == 0
    assert "Already logged in" in result.output
    assert server.calls == []
    assert saved(env)["token"] == "cfe_old"


def test_login_force_skips_prompt(monkeypatch, env):
    auth.store_token(HOST, "cfe_old", USER)
    serve(monkeypatch, {"POST device/": [reply(200, DEVICE)], "POST token/": [success()]})
    result = runner.invoke(app, ["auth", "login", "--no-browser", "--force"])
    assert result.exit_code == 0, result.output
    assert saved(env)["token"] == TOKEN


def test_host_option_and_env(monkeypatch, env):
    server = serve(
        monkeypatch,
        {"POST device/": [reply(200, DEVICE)], "POST token/": [success()]},
    )
    runner.invoke(app, ["auth", "login", "--no-browser", "--host", "http://127.0.0.1:8800/"])
    assert server.calls[0][3] == "http://127.0.0.1:8800/api/cli/device/"
    assert "http://127.0.0.1:8800" in json.loads((env / "hosts.json").read_text())

    monkeypatch.setenv("CFE_HOST", "https://staging.example.com")
    server2 = serve(monkeypatch, {"POST device/": [reply(200, DEVICE)], "POST token/": [success()]})
    runner.invoke(app, ["auth", "login", "--no-browser"])
    assert server2.calls[0][3] == "https://staging.example.com/api/cli/device/"


ME = {
    "id": 42,
    "email": "you@example.com",
    "name": "You",
    "access": {
        "active_subscription": True,
        "lifetime": False,
        "courses": [{"id": 7, "slug": "try-django", "title": "Try Django"}],
        "roadmaps": [],
    },
}


def test_status_success(monkeypatch):
    auth.store_token(HOST, TOKEN, USER)
    server = serve(monkeypatch, {"GET me/": [reply(200, ME)]})
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0, result.output
    assert HOST in result.output
    assert "you@example.com" in result.output
    assert TOKEN[:12] + "…" in result.output
    assert TOKEN not in result.output
    assert "Try Django" in result.output
    assert "active" in result.output
    assert server.calls[0][2]["Authorization"] == f"Bearer {TOKEN}"


def test_status_401(monkeypatch):
    auth.store_token(HOST, TOKEN, USER)
    serve(monkeypatch, {"GET me/": [reply(401, {"detail": "no"})]})
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 1
    assert "revoked" in result.output


def test_status_not_logged_in(monkeypatch):
    server = serve(monkeypatch, {})
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 1
    assert "Not logged in" in result.output
    assert server.calls == []


def test_cfe_token_env_overrides(monkeypatch):
    auth.store_token(HOST, "cfe_stored", USER)
    monkeypatch.setenv("CFE_TOKEN", "cfe_fromenv_123456")
    server = serve(monkeypatch, {"GET me/": [reply(200, ME)]})
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert server.calls[0][2]["Authorization"] == "Bearer cfe_fromenv_123456"
    assert runner.invoke(app, ["auth", "token"]).output.strip() == "cfe_fromenv_123456"


def test_status_with_only_cfe_token(monkeypatch):
    monkeypatch.setenv("CFE_TOKEN", "cfe_fromenv_123456")
    serve(monkeypatch, {"GET me/": [reply(200, ME)]})
    assert runner.invoke(app, ["auth", "status"]).exit_code == 0


def test_logout_revokes_and_removes(monkeypatch, env):
    auth.store_token(HOST, TOKEN, USER)
    auth.store_token("https://other.example.com", "cfe_other", USER)
    server = serve(monkeypatch, {"POST logout/": [reply(204)]})
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0, result.output
    assert server.keys() == ["POST logout/"]
    assert server.calls[0][2]["Authorization"] == f"Bearer {TOKEN}"
    hosts = json.loads((env / "hosts.json").read_text())
    assert HOST not in hosts
    assert "https://other.example.com" in hosts


def test_logout_network_error_still_removes(monkeypatch, env):
    auth.store_token(HOST, TOKEN, USER)

    def boom(req, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(auth.urllib.request, "urlopen", boom)
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0
    assert "Warning" in result.output
    assert HOST not in json.loads((env / "hosts.json").read_text())


def test_logout_not_logged_in(monkeypatch):
    serve(monkeypatch, {})
    assert runner.invoke(app, ["auth", "logout"]).exit_code == 1


def test_token_command(monkeypatch):
    auth.store_token(HOST, TOKEN, USER)
    result = runner.invoke(app, ["auth", "token"])
    assert result.exit_code == 0
    assert result.output == TOKEN + "\n"


def test_token_command_not_logged_in():
    assert runner.invoke(app, ["auth", "token"]).exit_code == 1


def test_config_dir_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("CFE_CONFIG_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert auth.hosts_file() == tmp_path / "xdg" / "cfe" / "hosts.json"
