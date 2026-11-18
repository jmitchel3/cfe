"""Tests for `cfe uptime`, `cfe mail`, `cfe domains`, `cfe teams`. No network: `http_request` is faked."""

import json

import pytest
from typer.testing import CliRunner

from cfe import api, uptime
from cfe.main import app

runner = CliRunner()
HOST = "https://www.codingforentrepreneurs.com"
TEAM_UID = "11111111-2222-3333-4444-555555555555"
TEAMS = [
    {
        "uid": "99999999-0000-0000-0000-000000000000",
        "name": "Personal",
        "slug": "personal-x",
        "is_personal": True,
        "role": "owner",
        "member_count": 1,
    },
    {
        "uid": TEAM_UID,
        "name": "Acme",
        "slug": "acme",
        "is_personal": False,
        "role": "owner",
        "member_count": 3,
    },
]


def monitor(mid="m1", hostname="example.com", state="up", verified=True, **extra):
    return {
        "id": mid,
        "name": hostname,
        "url": f"https://{hostname}/",
        "hostname": hostname,
        "state": state,
        "status": "active" if verified else "pending_verification",
        "verified": verified,
        "paused": False,
        "hold": None,
        "latency_ms": 120,
        "uptime_24h": 99.9,
        "uptime_7d": {"percent": 99.5},
        **extra,
    }


class FakeApi:
    """Routes (method, path) to a list of queued responses; the last one repeats."""

    def __init__(self, monkeypatch):
        self.routes: dict[tuple[str, str], list] = {}
        self.calls: list[dict] = []
        monkeypatch.setattr(api, "http_request", self)

    def on(self, method, path, *responses):
        self.routes[(method, path)] = list(responses)
        return self

    def __call__(self, method, host, path, payload=None, token=None, headers=None, raw=False):
        bare = path.split("?", 1)[0].removeprefix("/api/").lstrip("/")
        self.calls.append(
            {
                "host": host,
                "full_path": path,
                "method": method,
                "path": path,
                "bare": bare,
                "payload": payload,
                "headers": headers or {},
                "token": token,
            }
        )
        queue = self.routes.get((method, bare))
        if queue is None:
            raise AssertionError(f"unexpected {method} {path}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        status, body = item if isinstance(item, tuple) else (200, item)
        return status, body, {}

    def called(self, method, bare):
        return [c for c in self.calls if c["method"] == method and c["bare"] == bare]


@pytest.fixture(autouse=True)
def logged_in(monkeypatch, tmp_path):
    monkeypatch.setenv("CFE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CFE_TOKEN", "cfe_testtoken")
    monkeypatch.delenv("CFE_TEAM", raising=False)
    monkeypatch.delenv("CFE_HOST", raising=False)
    for env in api.SERVICE_BASE_URL_ENV.values():
        monkeypatch.delenv(env, raising=False)


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__("now", clock["now"] + s))
    return clock


@pytest.fixture
def fake(monkeypatch):
    return FakeApi(monkeypatch)


def run(*args):
    return runner.invoke(app, list(args))


# ------------------------------------------------------------------ session


def test_requires_login(monkeypatch, fake):
    monkeypatch.delenv("CFE_TOKEN")
    result = run("uptime", "ls")
    assert result.exit_code == 1
    assert "cfe login" in result.output
    assert fake.calls == []


def test_401_says_login_again(fake):
    fake.on("GET", "uptime/monitors/", (401, {"detail": "Invalid token."}))
    result = run("uptime", "ls")
    assert result.exit_code == 1
    assert "Run `cfe login`" in result.output


def test_team_name_resolves_to_header_and_body(fake):
    fake.on("GET", "teams/", TEAMS)
    fake.on("GET", "uptime/monitors/", {"monitors": []})
    fake.on(
        "POST",
        "uptime/monitors/",
        (201, {"monitor": monitor(verified=False), "verification": None}),
    )
    assert run("uptime", "ls", "--team", "acme").exit_code == 0
    assert fake.called("GET", "uptime/monitors/")[0]["headers"] == {"X-Team-Id": TEAM_UID}
    assert run("uptime", "add", "example.com", "--team", "Acme").exit_code == 0
    post = fake.called("POST", "uptime/monitors/")[0]
    assert post["payload"] == {"url": "https://example.com", "team": TEAM_UID}


def test_team_personal_sends_null_and_no_header(fake):
    fake.on("GET", "domains/", {"domains": []})
    fake.on("POST", "domains/", (201, {"id": "d1", "hostname": "example.com", "verified": True}))
    assert run("domains", "add", "example.com", "--team", "personal").exit_code == 0
    assert fake.called("POST", "domains/")[0]["payload"] == {
        "hostname": "example.com",
        "team": None,
    }
    assert fake.called("POST", "domains/")[0]["headers"] == {}


def test_unknown_team(fake):
    fake.on("GET", "teams/", TEAMS)
    result = run("uptime", "ls", "--team", "nope")
    assert result.exit_code == 1
    assert "No team matches 'nope'" in result.output and "Acme" in result.output


def test_no_team_omits_team_key(fake):
    fake.on("POST", "uptime/monitors/", (201, {"monitor": monitor(), "verified": True}))
    assert run("uptime", "add", "https://example.com").exit_code == 0
    assert "team" not in fake.called("POST", "uptime/monitors/")[0]["payload"]


# ------------------------------------------------------------------ base URLs


def test_uptime_base_url_routes_uptime_only(fake, monkeypatch):
    monkeypatch.setenv("UPTIME_BASE_URL", "http://127.0.0.1:8800/api/uptime/")
    fake.on("GET", "teams/", TEAMS)
    fake.on("GET", "uptime/monitors/", {"monitors": []})
    assert run("uptime", "ls", "--team", "acme").exit_code == 0
    teams_call, list_call = fake.calls
    assert teams_call["host"] == api.normalize_host(None)
    assert list_call["host"] == "http://127.0.0.1:8800"
    assert list_call["full_path"] == "/api/uptime/monitors/"


def test_base_url_custom_prefix(fake, monkeypatch):
    monkeypatch.setenv("MAILCHECK_BASE_URL", "https://staging.example.com/v2/mail")
    monkeypatch.setenv("DOMAINS_BASE_URL", "staging.example.com/api/domains")
    fake.on("GET", "v2/mail/checks/", {"checks": []})
    fake.on("GET", "domains/", {"domains": []})
    assert run("mail", "ls").exit_code == 0
    assert run("domains", "ls").exit_code == 0
    mail_call, domains_call = fake.calls
    assert (mail_call["host"], mail_call["full_path"]) == (
        "https://staging.example.com",
        "/v2/mail/checks/?limit=25",
    )
    assert domains_call["host"] == "https://staging.example.com"


def test_base_url_uses_that_hosts_token_only(fake, monkeypatch):
    from cfe import auth

    monkeypatch.delenv("CFE_TOKEN")
    auth.store_token(api.normalize_host(None), "cfe_prod", {"email": "a@b.c"})
    monkeypatch.setenv("UPTIME_BASE_URL", "http://127.0.0.1:8800/api/uptime")
    fake.on("GET", "uptime/monitors/", {"monitors": []})
    result = run("uptime", "ls")
    assert result.exit_code == 1
    assert "Not logged in to http://127.0.0.1:8800 (from UPTIME_BASE_URL)" in result.output
    assert "cfe login --host http://127.0.0.1:8800" in " ".join(result.output.split())
    assert fake.calls == []  # the prod token never went to the other host

    auth.store_token("http://127.0.0.1:8800", "cfe_local", {"email": "a@b.c"})
    assert run("uptime", "ls").exit_code == 0
    assert fake.calls[0]["token"] == "cfe_local"


def test_bad_base_url(fake, monkeypatch):
    monkeypatch.setenv("UPTIME_BASE_URL", "ftp://example.com/uptime")
    result = run("uptime", "ls")
    assert result.exit_code == 2
    assert "UPTIME_BASE_URL must be an http(s) URL" in result.output


# ------------------------------------------------------------------ uptime


def test_uptime_ls_table(fake):
    fake.on(
        "GET",
        "uptime/monitors/",
        {"monitors": [monitor(), monitor("m2", "down.example.com", "down")], "limit": 5},
    )
    result = run("uptime", "ls")
    assert result.exit_code == 0
    assert "example.com" in result.output and "down" in result.output
    assert "99.90%" in result.output and "99.50%" in result.output
    assert "2 of 5" in result.output


def test_uptime_add_prints_verification(fake):
    verification = {
        "token": "tok",
        "dns": {
            "type": "TXT",
            "name": "_cfe-verify.example.com",
            "value": "cfe-site-verification=tok",
        },
        "file": {"url": "https://example.com/.well-known/cfe-verify.txt", "contents": "tok"},
    }
    fake.on(
        "POST",
        "uptime/monitors/",
        (
            201,
            {"monitor": monitor(verified=False), "verification": verification, "verified": False},
        ),
    )
    result = run("uptime", "add", "example.com", "--name", "Site")
    assert result.exit_code == 0
    assert "_cfe-verify.example.com" in result.output
    assert "cfe-site-verification=tok" in result.output
    assert fake.called("POST", "uptime/monitors/")[0]["payload"]["name"] == "Site"


def test_uptime_verify_wait_polls(fake):
    fake.on(
        "POST",
        "uptime/monitors/m1/verify/",
        {"verified": False},
        {"verified": False},
        {"verified": True, "method": "dns"},
    )
    result = run("uptime", "verify", "m1", "--wait", "--interval", "5")
    assert result.exit_code == 0
    assert "Verified (dns)" in result.output
    assert len(fake.called("POST", "uptime/monitors/m1/verify/")) == 3


def test_uptime_verify_not_yet_exits_1(fake):
    fake.on(
        "POST",
        "uptime/monitors/m1/verify/",
        {"verified": False, "verification": {"dns": {"name": "_cfe-verify.x", "value": "v"}}},
    )
    result = run("uptime", "verify", "m1")
    assert result.exit_code == 1
    assert "Not verified yet" in result.output


def test_hostname_ref_resolves_to_id(fake):
    fake.on("GET", "uptime/monitors/", {"monitors": [monitor("abc", "www.example.com")]})
    fake.on("POST", "uptime/monitors/abc/pause/", {"monitor": monitor("abc", "www.example.com")})
    result = run("uptime", "pause", "https://www.example.com/")
    assert result.exit_code == 0, result.output
    assert "Paused www.example.com" in result.output


def test_hostname_ref_missing(fake):
    fake.on("GET", "uptime/monitors/", {"monitors": []})
    result = run("uptime", "show", "nope.com")
    assert result.exit_code == 1
    assert "No monitor for nope.com" in result.output


def test_uptime_status_gate(fake):
    fake.on("GET", "uptime/monitors/", {"monitors": [monitor(), monitor("m2", "b.com", "down")]})
    assert run("uptime", "status").exit_code == 1
    assert run("uptime", "status", "example.com").exit_code == 0
    result = run("uptime", "status", "--json")
    states = [m["display_state"] for m in json.loads(result.stdout)]
    assert states == ["up", "down"]


def test_uptime_wait_until_up(fake):
    fake.on(
        "GET",
        "uptime/monitors/m1/",
        {"monitor": monitor(state="down")},
        {"monitor": monitor(state="up")},
    )
    result = run("uptime", "wait", "m1", "--until", "up")
    assert result.exit_code == 0
    assert "is up" in result.output


def test_uptime_wait_timeout_124(fake):
    fake.on("GET", "uptime/monitors/m1/", {"monitor": monitor(state="down")})
    result = run("uptime", "wait", "m1", "--until", "up", "--interval", "10", "--timeout", "30")
    assert result.exit_code == 124
    assert "Timed out" in result.output


def test_uptime_dns_setup(fake):
    fake.on(
        "GET",
        "uptime/monitors/m1/dns-setup/",
        {
            "hostname": "www.example.com",
            "zone": "example.com",
            "verified": False,
            "record": {
                "type": "TXT",
                "host": "_cfe-verify.www",
                "fqdn": "_cfe-verify.www.example.com",
                "value": "cfe-site-verification=x",
            },
            "provider": {"name": "Cloudflare", "steps": ["Open the dashboard", "Add TXT"]},
            "domain_connect": {
                "available": True,
                "provider_name": "Cloudflare",
                "apply_url": "https://dash.cloudflare.com/apply",
            },
        },
    )
    result = run("uptime", "dns", "m1")
    assert result.exit_code == 0
    assert "_cfe-verify.www" in result.output
    assert "1. Open the dashboard" in result.output
    assert "https://dash.cloudflare.com/apply" in result.output


def test_uptime_scan_fail_on(fake):
    scan = {
        "hostname": "example.com",
        "score": 60,
        "sections": [
            {
                "id": "email",
                "title": "Email",
                "score": 50,
                "checks": [
                    {
                        "id": "dmarc",
                        "title": "DMARC",
                        "status": "fail",
                        "detail": "missing",
                        "fix": "Add DMARC",
                    },
                    {"id": "spf", "title": "SPF", "status": "pass", "detail": None, "fix": None},
                ],
            },
        ],
    }
    fake.on("POST", "uptime/monitors/m1/sitescan/", scan)
    result = run("uptime", "scan", "m1")
    assert result.exit_code == 0
    assert "DMARC" in result.output and "fix: Add DMARC" in result.output
    assert "SPF" not in result.output
    assert run("uptime", "scan", "m1", "--fail-on", "fail").exit_code == 1


def test_uptime_checks_csv(fake, tmp_path):
    fake.on("GET", "uptime/monitors/m1/checks.csv", b"checked_at,ok\n1,1\n")
    out = tmp_path / "c.csv"
    assert run("uptime", "checks", "m1", "--csv", str(out)).exit_code == 0
    assert out.read_bytes() == b"checked_at,ok\n1,1\n"


def test_diff_events():
    before = {
        "a": monitor("a"),
        "b": monitor("b", verified=False, state="unknown"),
        "c": monitor("c"),
    }
    after = {"a": monitor("a", state="down"), "b": monitor("b", state="up"), "d": monitor("d")}
    names = sorted((e["event"], e["monitor"]["id"]) for e in uptime.diff_events(before, after))
    assert names == [("added", "d"), ("down", "a"), ("removed", "c"), ("verified", "b")]
    recovered = uptime.diff_events({"a": monitor("a", state="down")}, {"a": monitor("a")})
    assert [e["event"] for e in recovered] == ["up"]
    assert recovered[0]["previous"] == {"state": "down"}


def test_watch_exec_and_exit_on(fake, monkeypatch):
    fake.on(
        "GET",
        "uptime/monitors/",
        {"monitors": [monitor()]},
        {"monitors": [monitor()]},
        {"monitors": [monitor(state="down")]},
    )
    hooks = []

    class Done:
        returncode = 0

    def fake_run(cmd, shell, input, text, env, check):
        hooks.append((cmd, json.loads(input), env["CFE_EVENT"], env["CFE_MONITOR_HOSTNAME"]))
        return Done()

    monkeypatch.setattr(uptime.subprocess, "run", fake_run)
    result = run("uptime", "watch", "--exec", "notify", "--exit-on", "down", "--interval", "5")
    assert result.exit_code == 0, result.output
    line = json.loads(next(l for l in result.stdout.splitlines() if l.startswith("{")))
    assert line["event"] == "down" and line["monitor"]["hostname"] == "example.com"
    assert hooks == [("notify", line, "down", "example.com")]


def test_watch_initial_reports_already_down(fake):
    fake.on("GET", "uptime/monitors/", {"monitors": [monitor(state="down")]})
    result = run("uptime", "watch", "--initial", "--exit-on", "down")
    assert result.exit_code == 0
    assert '"event": "down"' in result.stdout


def test_watch_survives_network_errors(fake, monkeypatch):
    calls = {"n": 0}
    real = fake.__call__

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise api.ApiError("Could not reach host")
        return real(*a, **kw)

    fake.on(
        "GET", "uptime/monitors/", {"monitors": [monitor()]}, {"monitors": [monitor(state="down")]}
    )
    monkeypatch.setattr(api, "http_request", flaky)
    result = run("uptime", "watch", "--exit-on", "down", "--interval", "1")
    assert result.exit_code == 0
    assert "Could not reach host" in result.output


def test_watch_unknown_event(fake):
    result = run("uptime", "watch", "--on", "sideways")
    assert result.exit_code == 2


# ------------------------------------------------------------------ mail


REPORT = {
    "token": "abcdefghijklmnop",
    "status": "complete",
    "message": {
        "from": "news@example.com",
        "from_domain": "example.com",
        "subject": "Launch",
        "size_bytes": 2048,
        "authenticated": True,
    },
    "summary": {"pass": 60, "info": 5, "warn": 1, "fail": 1},
    "results": [
        {
            "id": "dkim-pass",
            "group": "auth",
            "title": "DKIM passes",
            "severity": "pass",
            "detail": "ok",
        },
        {
            "id": "list-unsubscribe",
            "group": "headers",
            "title": "List-Unsubscribe header",
            "severity": "fail",
            "detail": "Missing",
            "learn_more": "https://example.com/docs",
        },
        {
            "id": "subject-length",
            "group": "content",
            "title": "Subject length",
            "severity": "warn",
            "detail": "Long",
        },
    ],
}
CREATED = {
    "token": "abcdefghijklmnop",
    "address": "check+abcdefghijklmnop@cfe.run",
    "status": "waiting",
    "expires_at": 1_900_000_000_000,
}


def test_mail_check_loop(fake):
    fake.on("POST", "mail/checks/", (201, CREATED))
    fake.on(
        "GET",
        "mail/checks/abcdefghijklmnop/",
        {"status": "waiting"},
        {"status": "processing", "groups_pending": ["links"]},
        REPORT,
    )
    result = run("mail", "check")
    assert result.exit_code == 1  # one fail, default --fail-on fail
    assert "check+abcdefghijklmnop@cfe.run" in result.output
    assert "List-Unsubscribe header" in result.output
    assert "Subject length" in result.output
    assert "DKIM passes" not in result.output
    assert fake.called("POST", "mail/checks/")[0]["payload"] == {}


def test_mail_check_fail_on_never_and_all(fake):
    fake.on("POST", "mail/checks/", (201, CREATED))
    fake.on("GET", "mail/checks/abcdefghijklmnop/", REPORT)
    result = run("mail", "check", "--fail-on", "never", "--all")
    assert result.exit_code == 0
    assert "DKIM passes" in result.output


def test_mail_check_bad_fail_on_before_create(fake):
    result = run("mail", "check", "--fail-on", "sometimes")
    assert result.exit_code == 2
    assert fake.calls == []


def test_mail_check_send_command(fake, monkeypatch):
    fake.on("POST", "mail/checks/", (201, CREATED))
    fake.on(
        "GET",
        "mail/checks/abcdefghijklmnop/",
        {**REPORT, "summary": {"pass": 1, "warn": 0, "fail": 0}, "results": []},
    )
    seen = {}

    class Done:
        returncode = 0

    def fake_run(cmd, shell, env, check):
        seen.update(cmd=cmd, address=env["CFE_MAIL_ADDRESS"])
        return Done()

    from cfe import mailcheck

    monkeypatch.setattr(mailcheck.subprocess, "run", fake_run)
    result = run("mail", "check", "--send", "./send.sh")
    assert result.exit_code == 0, result.output
    assert seen == {"cmd": "./send.sh", "address": "check+abcdefghijklmnop@cfe.run"}


def test_mail_check_no_wait_prints_address(fake):
    fake.on("POST", "mail/checks/", (201, CREATED))
    result = run("mail", "check", "--no-wait")
    assert result.exit_code == 0
    assert result.stdout.splitlines()[0] == "check+abcdefghijklmnop@cfe.run"


def test_mail_check_expired(fake):
    fake.on("POST", "mail/checks/", (201, CREATED))
    fake.on("GET", "mail/checks/abcdefghijklmnop/", {"status": "expired"})
    result = run("mail", "check")
    assert result.exit_code == 1
    assert "expired" in result.output


def test_mail_report_accepts_address(fake):
    fake.on("GET", "mail/checks/abcdefghijklmnop/", REPORT)
    result = run("mail", "report", "check+ABCDEFGHIJKLMNOP@cfe.run", "--json", "--fail-on", "never")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["token"] == "abcdefghijklmnop"


def test_mail_paste(fake, tmp_path):
    eml = tmp_path / "m.eml"
    eml.write_text("From: a@example.com\nSubject: hi\n\nbody")
    fake.on(
        "POST", "mail/checks/paste/", (201, {"token": "abcdefghijklmnop", "status": "processing"})
    )
    fake.on("GET", "mail/checks/abcdefghijklmnop/", REPORT)
    result = run("mail", "paste", str(eml), "--fail-on", "warn")
    assert result.exit_code == 1
    assert fake.called("POST", "mail/checks/paste/")[0]["payload"]["raw"].startswith(
        "From: a@example.com"
    )


def test_mail_address_wait(fake):
    old = {"token": "oldoldoldoldoldo", "status": "complete"}
    new = {"token": "abcdefghijklmnop", "status": "processing"}
    fake.on(
        "GET",
        "mail/addresses/abc/checks/",
        {"address": {"address": "check+news-abc@cfe.run"}, "checks": [old]},
        {"checks": [old]},
        {"checks": [new, old]},
    )
    fake.on("GET", "mail/checks/abcdefghijklmnop/", REPORT)
    result = run("mail", "address", "wait", "abc", "--fail-on", "never")
    assert result.exit_code == 0, result.output
    assert "check+news-abc@cfe.run" in result.output
    assert "Launch" in result.output


def test_mail_address_add(fake):
    fake.on(
        "POST",
        "mail/addresses/",
        (201, {"id": "abc", "name": "News", "address": "check+news-abc@cfe.run"}),
    )
    result = run("mail", "address", "add", "News")
    assert result.exit_code == 0
    assert "check+news-abc@cfe.run" in result.output
    assert fake.called("POST", "mail/addresses/")[0]["payload"] == {"name": "News"}


# ------------------------------------------------------------------ domains


DOMAIN = {
    "id": "d-uid",
    "hostname": "example.com",
    "verified": False,
    "verification": {
        "record": {
            "type": "TXT",
            "host": "_cfe-verify",
            "fqdn": "_cfe-verify.example.com",
            "value": "cfe-site-verification=t",
        }
    },
    "summary": {},
    "monitors": [],
}


def test_domains_add_prints_record(fake):
    fake.on("POST", "domains/", (201, DOMAIN))
    result = run("domains", "add", "https://Example.com/path")
    assert result.exit_code == 0
    assert fake.called("POST", "domains/")[0]["payload"] == {"hostname": "example.com"}
    assert "_cfe-verify.example.com" in result.output
    assert "cfe-site-verification=t" in result.output


def test_domains_verify_by_hostname(fake):
    fake.on("GET", "domains/", {"domains": [DOMAIN]})
    fake.on(
        "POST",
        "domains/d-uid/verify/",
        {"verified": False, "detail": "TXT not found", "domain": DOMAIN},
        {"verified": True, "method": "dns", "domain": {**DOMAIN, "verified": True}},
    )
    first = run("domains", "verify", "example.com")
    assert first.exit_code == 1
    assert "TXT not found" in first.output
    second = run("domains", "verify", "example.com", "--wait")
    assert second.exit_code == 0
    assert "Verified example.com" in second.output


def test_domains_scan_unverified_conflict(fake):
    fake.on(
        "POST",
        "domains/d-uid/sitescan/",
        (409, {"detail": "Verify this domain to run a site scan.", "code": "domain_unverified"}),
    )
    result = run("domains", "scan", "d-uid")
    assert result.exit_code == 1
    assert "Verify this domain" in result.output


def test_domains_rm_lists_paused_monitors(fake):
    fake.on(
        "DELETE",
        "domains/d-uid/",
        {
            "removed": "example.com",
            "paused_monitors": [{"id": "m1", "hostname": "www.example.com"}],
        },
    )
    result = run("domains", "rm", "d-uid", "--yes")
    assert result.exit_code == 0
    assert "paused monitor www.example.com" in result.output


def test_teams(fake):
    fake.on("GET", "teams/", TEAMS)
    result = run("teams")
    assert result.exit_code == 0
    assert "Acme" in result.output and TEAM_UID in result.output
