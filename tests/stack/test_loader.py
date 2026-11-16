import pytest

from cfe.stack import EnvVar, Module, StackConfigError, hooks, loader, registry


class Alpha(Module):
    name = "alpha"
    provides = ("cache",)
    installed_apps = ("alpha_app",)
    middleware = (("alpha.Middleware", "django.middleware.common.CommonMiddleware"),)
    env = (EnvVar("ALPHA_URL", required=True, secret=True),)

    def settings(self, env):
        return {"CACHES": {"default": {"LOCATION": env.ALPHA_URL}}, "ALPHA": True}


class Beta(Module):
    name = "beta"
    provides = ("cache",)


class Gamma(Module):
    name = "gamma"
    requires_slots = ("cache",)
    env = (EnvVar("GAMMA_N", cast=int, default=3), EnvVar("GAMMA_FLAG", aliases=("OLD_FLAG",)))

    def settings(self, env):
        return {"CACHES": {"other": {"N": env.GAMMA_N}}, "GAMMA_FLAG": env.GAMMA_FLAG}


@pytest.fixture
def fake_registry(monkeypatch):
    mods = {m.name: m for m in (Alpha, Beta, Gamma)}
    monkeypatch.setattr(registry, "available", lambda: mods)
    yield mods


def test_parse_module_list():
    assert loader.parse_module_list(" a, b  c,,a ") == ["a", "b", "c"]
    assert loader.parse_module_list(None) == []


def test_configure_merges_settings_apps_and_middleware(fake_registry):
    ns = {
        "INSTALLED_APPS": ["django.contrib.auth"],
        "MIDDLEWARE": ["django.middleware.common.CommonMiddleware", "x.Last"],
        "CACHES": {"default": {"LOCATION": "old"}, "keep": {}},
    }
    stack = loader.configure(
        ns, modules="alpha,gamma", environ={"ALPHA_URL": "redis://x", "OLD_FLAG": "on"}
    )
    assert [m.name for m in stack.modules] == ["alpha", "gamma"]
    assert ns["CACHES"] == {"default": {"LOCATION": "redis://x"}, "other": {"N": 3}, "keep": {}}
    assert ns["GAMMA_FLAG"] == "on"
    assert ns["INSTALLED_APPS"] == ["django.contrib.auth", "cfe.stack", "alpha_app"]
    assert ns["MIDDLEWARE"] == [
        "django.middleware.common.CommonMiddleware",
        "alpha.Middleware",
        "x.Last",
    ]
    assert ns["CFE_STACK_MODULES"] == ["alpha", "gamma"]
    assert ns["CFE_STACK_SLOTS"] == {"cache": "alpha"}


def test_slot_conflict(fake_registry):
    with pytest.raises(StackConfigError, match="both provide the 'cache' slot"):
        loader.build(["alpha", "beta"], environ={"ALPHA_URL": "x"})


def test_required_slot_missing(fake_registry):
    with pytest.raises(StackConfigError, match="needs a module providing the 'cache' slot"):
        loader.build(["gamma"], environ={})


def test_missing_env_strict_lists_names_not_values(fake_registry):
    with pytest.raises(StackConfigError, match=r"alpha: ALPHA_URL"):
        loader.build(["alpha"], environ={})


def test_missing_env_non_strict_skips_module(fake_registry):
    stack = loader.build(["alpha", "gamma"], environ={}, strict=False)
    assert [m.name for m in stack.modules] == ["gamma"]
    assert stack.skipped == {"alpha": "missing ALPHA_URL"}


def test_cast_error_hides_value(fake_registry):
    with pytest.raises(StackConfigError) as exc:
        loader.build(["alpha", "gamma"], environ={"ALPHA_URL": "x", "GAMMA_N": "supersecret"})
    assert "supersecret" not in str(exc.value)


def test_unknown_module(fake_registry):
    with pytest.raises(StackConfigError, match="unknown cfe.stack module 'nope'"):
        loader.build(["nope"])


def test_conflicting_scalar_settings(fake_registry, monkeypatch):
    class Delta(Module):
        name = "delta"

        def settings(self, env):
            return {"ALPHA": False}

    monkeypatch.setattr(registry, "available", lambda: {**fake_registry, "delta": Delta})
    with pytest.raises(StackConfigError, match="both set ALPHA"):
        loader.configure({}, modules="alpha,delta", environ={"ALPHA_URL": "x"})


def test_builtin_registry_has_sqlite():
    registry.available.cache_clear()
    assert "sqlite" in registry.available()


def test_hooks_actions_and_filters():
    hooks.clear()
    seen = []
    hooks.add_action("evt", lambda **kw: seen.append(kw["n"]))
    hooks.add_action("evt", lambda **kw: 1 / 0)  # errors are logged, not raised
    hooks.do_action("evt", n=1)
    assert seen == [1]
    hooks.add_filter("f", lambda v, **kw: v + ["late"], priority=20)
    hooks.add_filter("f", lambda v, **kw: v + ["early"], priority=5)
    assert hooks.apply_filters("f", []) == ["early", "late"]
    hooks.clear()


def test_django_settings_loaded(settings):
    assert settings.CFE_STACK_MODULES == ["sqlite"]
    assert "cfe.stack" in settings.INSTALLED_APPS
    assert settings.DATABASES["default"]["NAME"] == ":memory:"


def test_unset_dict_setting_merges_with_django_default(monkeypatch):
    class Store(Module):
        name = "store"
        provides = ("storage",)

        def settings(self, env):
            return {"STORAGES": {"default": {"BACKEND": "x.Storage"}}}

    monkeypatch.setattr(registry, "available", lambda: {"store": Store})
    ns = {}
    loader.configure(ns, modules="store", environ={})
    assert ns["STORAGES"]["default"] == {"BACKEND": "x.Storage"}
    assert "staticfiles" in ns["STORAGES"]
