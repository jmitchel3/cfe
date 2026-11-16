import sys
import textwrap
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from typer.testing import CliRunner

from cfe.stack import StackConfigError, loader, registry

PKG = "cfe_test_localmods"
SECRET = "sk-super-secret-value"

LOCAL_SOURCE = textwrap.dedent(
    """
    from cfe.stack.module import EnvVar, Module


    class Tracker(Module):
        name = "tracker"
        label = "Tracker"
        provides = ("analytics",)
        env = (
            EnvVar("TRACKER_TOKEN", required=True, secret=True),
            EnvVar("TRACKER_HOST", default="local"),
        )
        calls = 0

        def settings(self, env):
            return {"TRACKER": {"HOST": env.TRACKER_HOST}}

        def checks(self, env):
            type(self).calls += 1
            if env.TRACKER_HOST == "bad":
                return [f"cannot reach {env.TRACKER_HOST} with token {env.TRACKER_TOKEN}"]
            return []


    class Needy(Module):
        name = "needy"
        python_requires = {"cfe_no_such_package_xyz": "cfe[needy]"}


    MODULES = [Tracker, Needy]
    """
)


def run(*args):
    out = StringIO()
    try:
        call_command("cfe", *args, stdout=out)
    except CommandError as exc:
        return out.getvalue(), exc
    return out.getvalue(), None


@pytest.fixture
def local_modules(tmp_path, monkeypatch):
    pkg = tmp_path / PKG
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "tracking.py").write_text(LOCAL_SOURCE)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("CFE_MODULE_PATHS", f"{PKG}.tracking")
    registry.available.cache_clear()
    yield f"{PKG}.tracking"
    registry.available.cache_clear()
    for name in [m for m in sys.modules if m.startswith(PKG)]:
        del sys.modules[name]


@pytest.fixture
def enable(monkeypatch, settings):
    """Configure the stack in-process for the given modules, using os.environ."""

    def _enable(modules, strict=True, **env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        # Live env source (os.environ, then .env), like a real settings.py.
        stack = loader.load(modules, strict=strict)
        for key, value in stack.extra_settings().items():
            if key.startswith("CFE_STACK"):
                setattr(settings, key, value)
        return stack.extra_settings()

    return _enable


def test_local_discovery_via_env(local_modules):
    assert registry.available().keys() >= {"sqlite"}
    assert "tracker" not in registry.available()
    paths = registry.env_module_paths()
    assert paths == (local_modules,)
    assert {"sqlite", "tracker", "needy"} <= registry.available(paths).keys()
    assert registry.sources(paths)["tracker"] == "local"
    assert registry.sources(paths)["sqlite"] == "builtin"


def test_configure_reads_module_paths_from_its_environ(local_modules, monkeypatch):
    monkeypatch.delenv("CFE_MODULE_PATHS")
    environ = {"CFE_MODULE_PATHS": local_modules, "TRACKER_TOKEN": "t"}
    stack = loader.load("tracker", environ=environ)
    assert stack.names == ["tracker"]
    assert stack.extra_settings()["TRACKER"] == {"HOST": "local"}
    with pytest.raises(StackConfigError, match="unknown cfe.stack module 'tracker'"):
        loader.build(["tracker"], environ={"TRACKER_TOKEN": "t"})


def test_local_module_cannot_shadow_builtin(tmp_path, monkeypatch):
    (tmp_path / "cfe_test_shadow.py").write_text(
        "from cfe.stack.module import Module\n"
        "class S(Module):\n    name = 'sqlite'\n"
        "MODULES = [S]\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(StackConfigError, match="local module 'sqlite'.*clashes with builtin"):
        registry.available(("cfe_test_shadow",))
    sys.modules.pop("cfe_test_shadow", None)


def test_bad_local_paths():
    with pytest.raises(StackConfigError, match="could not import 'cfe_nope_missing'"):
        registry.available(("cfe_nope_missing",))
    with pytest.raises(StackConfigError, match="has no MODULES"):
        registry.available(("json",))


def test_parse_module_paths():
    assert registry.parse_module_paths(" a.b, c.d ,,a.b ") == ("a.b", "c.d")
    assert registry.parse_module_paths(None) == ()


def test_list_shows_modules_sources_and_slots(local_modules, enable):
    enable("sqlite,tracker", TRACKER_TOKEN=SECRET)
    out, err = run("list")
    assert err is None
    lines = {line.split()[0]: line.split() for line in out.splitlines() if line.strip()}
    assert lines["sqlite"][-2:] == ["yes", "builtin"]
    assert lines["tracker"][-2:] == ["yes", "local"]
    assert lines["needy"][-2:] == ["no", "local"]
    assert "database -> sqlite" in out
    assert "analytics -> tracker" in out
    assert SECRET not in out


def test_doctor_passes_for_default_settings():
    out, err = run("doctor")
    assert err is None, out
    assert "sqlite" in out
    assert "all good" in out


def test_doctor_missing_env_fails_with_name(local_modules, enable, monkeypatch):
    enable("tracker", TRACKER_TOKEN=SECRET)
    monkeypatch.delenv("TRACKER_TOKEN")
    out, err = run("doctor")
    assert err is not None and err.returncode == 1
    assert "TRACKER_TOKEN (secret) is required but not set" in out


def test_doctor_never_prints_secret_values(local_modules, enable):
    enable("tracker", TRACKER_TOKEN=SECRET, TRACKER_HOST="bad")
    out, err = run("doctor")
    assert err is not None
    assert "TRACKER_TOKEN (secret) is set" in out
    assert "cannot reach bad with token ***" in out
    assert SECRET not in out


def test_doctor_offline_skips_checks(local_modules, enable):
    ns = enable("tracker", TRACKER_TOKEN=SECRET, TRACKER_HOST="bad")
    cls = registry.get("tracker")
    cls.calls = 0
    out, err = run("doctor", "--offline")
    assert err is None, out
    assert cls.calls == 0
    assert "skip  checks (--offline)" in out
    assert ns["CFE_STACK_MODULES"] == ["tracker"]


def test_doctor_missing_python_package_suggests_extra(local_modules, enable):
    enable("needy")
    out, err = run("doctor", "--offline")
    assert err is not None
    assert 'pip install "cfe[needy]"' in out


def test_doctor_reports_skipped_modules(local_modules, enable, monkeypatch):
    monkeypatch.delenv("TRACKER_TOKEN", raising=False)
    enable("sqlite,tracker", strict=False)
    out, err = run("doctor", "--offline")
    assert err is not None
    assert "skipped by non-strict mode: missing TRACKER_TOKEN" in out


def test_env_lists_names_only(local_modules, enable):
    enable("tracker", TRACKER_TOKEN=SECRET)
    out, err = run("env")
    assert err is None
    rows = [line.split() for line in out.splitlines()[1:]]
    assert ["tracker", "TRACKER_TOKEN", "yes", "yes", "no", "yes"] in rows
    assert ["tracker", "TRACKER_HOST", "no", "no", "yes", "no"] in rows
    assert not any(r[0] == "sqlite" for r in rows)
    assert SECRET not in out

    out, _ = run("env", "--all")
    assert "SQLITE_PATH" in out and "TRACKER_TOKEN" in out

    out, _ = run("env", "--all", "--names")
    assert {"SQLITE_PATH", "TRACKER_TOKEN", "TRACKER_HOST"} <= set(out.split())


def test_new_scaffolds_importable_module(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out, err = run("new", "my-analytics")
    assert err is None, out
    target = tmp_path / "modules" / "my_analytics.py"
    assert target.exists()
    assert (tmp_path / "modules" / "__init__.py").exists()
    assert "CFE_MODULE_PATHS=modules.my_analytics" in out

    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        found = registry.available(("modules.my_analytics",))
        cls = found["my-analytics"]
        assert [v.name for v in cls.env] == ["MY_ANALYTICS_URL", "MY_ANALYTICS_TIMEOUT"]
    finally:
        for name in [m for m in sys.modules if m == "modules" or m.startswith("modules.")]:
            del sys.modules[name]
        registry.available.cache_clear()

    _, err = run("new", "my-analytics")
    assert err is not None and "already exists" in str(err)
    _, err = run("new", "Bad Name")
    assert err is not None and "invalid module name" in str(err)


def test_new_custom_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out, err = run("new", "billing2", "--path", "app/stack/billing2.py")
    assert err is None
    assert (tmp_path / "app" / "stack" / "billing2.py").exists()
    assert "CFE_MODULE_PATHS=app.stack.billing2" in out


def test_cli_stack_modules(local_modules):
    from cfe.main import app

    result = CliRunner().invoke(app, ["stack", "modules"])
    assert result.exit_code == 0, result.output
    assert "sqlite" in result.output
    assert "tracker" in result.output
    assert "local" in result.output


def test_cli_stack_modules_without_django(monkeypatch):
    from cfe.main import app

    monkeypatch.setitem(sys.modules, "django", None)
    result = CliRunner().invoke(app, ["stack", "modules"])
    assert result.exit_code == 1
    assert 'pip install "cfe[stack]"' in result.output
