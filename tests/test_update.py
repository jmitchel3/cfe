"""Tests for update checks and `cfe update`. No network: urlopen is faked."""

import io
import json
import subprocess
import time
import urllib.error

import pytest
from typer.testing import CliRunner

from cfe import __version__, update
from cfe.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CFE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CFE_CONFIG_DIR", str(tmp_path / "config"))
    for name in ("CI", "CFE_NO_UPDATE_CHECK", "CFE_HOST", "CFE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(update.sys.stderr, "isatty", lambda: True, raising=False)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(routes, calls=None):
    """routes: url substring -> dict body, or an int HTTP error status."""

    def urlopen(req, timeout=None):
        url = req.full_url
        if calls is not None:
            calls.append(url)
        for key, value in routes.items():
            if key in url:
                if isinstance(value, int):
                    raise urllib.error.HTTPError(url, value, "err", {}, io.BytesIO(b""))
                return Resp(json.dumps(value).encode())
        raise urllib.error.URLError("offline")

    return urlopen


def bump(version, by=1):
    parts = [int(p) for p in version.split(".")[:3]]
    parts[-1] += by
    return ".".join(map(str, parts))


NEWER = bump(__version__)


# ----------------------------------------------------------------- versions


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("0.0.24", "0.0.23"),
        ("0.1.0", "0.0.99"),
        ("0.1.0", "0.1.0rc1"),
        ("1.10.0", "1.9.0"),
        ("0.1.0.post1", "0.1.0rc2"),
    ],
)
def test_is_newer(a, b):
    assert update.is_newer(a, b)
    assert not update.is_newer(b, a)


def test_equal_versions_are_not_newer():
    assert not update.is_newer("0.1", "0.1.0")
    assert not update.is_newer(None, "0.1.0")
    assert not update.is_newer("garbage", "0.1.0")


# -------------------------------------------------------------------- fetch


def test_fetch_prefers_site_endpoint(monkeypatch):
    calls = []
    routes = {"/api/cli/version/": {"latest": NEWER, "min_supported": "0.0.1", "message": "hi"}}
    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen(routes, calls))
    info = update.fetch_latest()
    assert info == {"latest": NEWER, "min_supported": "0.0.1", "message": "hi"}
    assert calls == ["https://www.codingforentrepreneurs.com/api/cli/version/"]


def test_fetch_falls_back_to_pypi(monkeypatch):
    calls = []
    routes = {"/api/cli/version/": 404, "pypi.org": {"info": {"version": NEWER}}}
    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen(routes, calls))
    assert update.fetch_latest()["latest"] == NEWER
    assert calls[-1] == update.PYPI_URL


# --------------------------------------------------------- background check


def test_background_check_refreshes_stale_cache(monkeypatch):
    routes = {"pypi.org": {"info": {"version": NEWER}}}
    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen(routes))
    thread = update.start_background_check(["list"])
    assert thread is not None
    thread.join(2)
    assert update.read_cache()["latest"] == NEWER


def test_background_check_skips_fresh_cache(monkeypatch):
    update.write_cache({"latest": __version__, "checked_at": time.time()})
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda *a, **k: pytest.fail("fetched"))
    assert update.start_background_check(["list"]) is None


@pytest.mark.parametrize(
    "var", [("CI", "true"), ("CFE_NO_UPDATE_CHECK", "1"), ("CFE_NO_UPDATE_CHECK", "yes")]
)
def test_background_check_disabled_by_env(monkeypatch, var):
    monkeypatch.setenv(*var)
    assert update.start_background_check(["list"]) is None


def test_background_check_disabled_without_tty(monkeypatch):
    monkeypatch.setattr(update.sys.stderr, "isatty", lambda: False, raising=False)
    assert update.start_background_check(["list"]) is None


def test_background_check_skipped_for_update_commands():
    assert update.start_background_check(["update"]) is None
    assert update.start_background_check(["upgrade"]) is None
    assert update.start_background_check(["--version"]) is None


def test_failed_check_records_attempt(monkeypatch):
    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen({}))
    update.start_background_check(["list"]).join(2)
    cache = update.read_cache()
    assert cache["checked_at"] > 0
    assert "latest" not in cache


# ------------------------------------------------------------------- notice


def test_notice_for_newer_version():
    text = update.notice_text({"latest": NEWER})
    assert NEWER in text and "cfe update" in text


def test_no_notice_when_current():
    assert update.notice_text({"latest": __version__}) is None
    assert update.notice_text({}) is None


def test_notice_below_min_supported():
    text = update.notice_text({"latest": NEWER, "min_supported": NEWER, "message": "API changed"})
    assert "no longer supported" in text and "API changed" in text


def test_finish_prints_notice_to_stderr(capsys, monkeypatch):
    monkeypatch.setattr(update, "checks_enabled", lambda: True)
    update.write_cache({"latest": NEWER, "checked_at": time.time()})
    update.finish(None, ["list"])
    err = capsys.readouterr().err
    assert f"cfe {NEWER} is available" in err


def test_finish_tolerates_corrupt_cache(capsys, monkeypatch):
    monkeypatch.setattr(update, "checks_enabled", lambda: True)
    update.cache_dir().mkdir(parents=True)
    update.cache_file().write_text("{not json")
    update.finish(None, ["list"])
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------- install method


def test_detect_editable(monkeypatch):
    monkeypatch.setattr(update, "_editable_install", lambda: True)
    assert update.detect_install() == ("source", None)


def test_detect_uv_tool(monkeypatch, tmp_path):
    tools = tmp_path / "uv" / "tools"
    monkeypatch.setattr(update, "_editable_install", lambda: False)
    monkeypatch.setattr(update.sys, "prefix", str(tools / "cfe"))
    monkeypatch.setattr(update, "_uv_dir", lambda kind: str(tools) if kind == "tool" else None)
    monkeypatch.setattr(update.shutil, "which", lambda name: f"/bin/{name}")
    assert update.detect_install() == ("uv tool", ["/bin/uv", "tool", "upgrade", "cfe"])


def test_detect_uvx(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "_editable_install", lambda: False)
    monkeypatch.setattr(update.sys, "prefix", str(tmp_path / "cache" / "archive-v0" / "abc"))
    monkeypatch.setattr(update, "_uv_dir", lambda kind: None)
    assert update.detect_install() == ("uvx", None)


def test_detect_pipx(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "_editable_install", lambda: False)
    monkeypatch.setattr(update.sys, "prefix", str(tmp_path / "pipx" / "venvs" / "cfe"))
    monkeypatch.setattr(update, "_uv_dir", lambda kind: None)
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    assert update.detect_install() == ("pipx", ["pipx", "upgrade", "cfe"])


def test_detect_plain_venv_with_uv(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "_editable_install", lambda: False)
    monkeypatch.setattr(update.sys, "prefix", str(tmp_path / "project" / ".venv"))
    monkeypatch.setattr(update, "_uv_dir", lambda kind: None)
    monkeypatch.setattr(update.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    method, cmd = update.detect_install()
    assert method == "pip"
    assert cmd[:3] == ["/bin/uv", "pip", "install"] and cmd[-2:] == ["--upgrade", "cfe"]


def test_detect_plain_venv_without_uv(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "_editable_install", lambda: False)
    monkeypatch.setattr(update.sys, "prefix", str(tmp_path / "project" / ".venv"))
    monkeypatch.setattr(update, "_uv_dir", lambda kind: None)
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    assert update.detect_install() == (
        "pip",
        [update.sys.executable, "-m", "pip", "install", "--upgrade", "cfe"],
    )


# ------------------------------------------------------------------ command


@pytest.fixture
def newer_available(monkeypatch):
    routes = {"pypi.org": {"info": {"version": NEWER}}}
    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen(routes))


@pytest.mark.parametrize("name", ["update", "upgrade"])
def test_update_runs_upgrade_command(monkeypatch, newer_available, name):
    ran = []
    monkeypatch.setattr(update, "detect_install", lambda: ("uv tool", ["uv", "tool", "upgrade"]))
    monkeypatch.setattr(
        update.subprocess,
        "run",
        lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    result = runner.invoke(app, [name])
    assert result.exit_code == 0, result.output
    assert ran == [["uv", "tool", "upgrade"]]


def test_update_check_does_not_install(monkeypatch, newer_available):
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: pytest.fail("ran"))
    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert NEWER in result.output


def test_update_dry_run_prints_command(monkeypatch, newer_available):
    monkeypatch.setattr(update, "detect_install", lambda: ("pipx", ["pipx", "upgrade", "cfe"]))
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: pytest.fail("ran"))
    result = runner.invoke(app, ["update", "--dry-run"])
    assert result.exit_code == 0
    assert "pipx upgrade cfe" in result.output


def test_update_already_latest(monkeypatch):
    routes = {"pypi.org": {"info": {"version": __version__}}}
    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen(routes))
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: pytest.fail("ran"))
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "latest version" in result.output


def test_update_uvx_explains(monkeypatch, newer_available):
    monkeypatch.setattr(update, "detect_install", lambda: ("uvx", None))
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "uvx cfe@latest" in result.output


def test_update_failure_exit_code(monkeypatch, newer_available):
    monkeypatch.setattr(update, "detect_install", lambda: ("pip", ["pip"]))
    monkeypatch.setattr(
        update.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3)
    )
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 3


def test_update_offline(monkeypatch):
    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen({}))
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert "Could not check for updates" in result.output


# --------------------------------------------------------------- top level


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"cfe {__version__}"


def test_upgrade_hidden_from_help():
    result = runner.invoke(app, ["--help"])
    assert "update" in result.output
    assert "upgrade" not in result.output
    for name in ("login", "logout", "whoami"):
        assert name in result.output


def test_whoami_requires_login():
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 1
    assert "cfe login" in result.output
