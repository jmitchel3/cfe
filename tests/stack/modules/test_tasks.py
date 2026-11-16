import logging
import sys
import types
from unittest import mock

import pytest

from cfe.stack import Module, StackConfigError, loader, registry, tasks

QSTASH_ENV = {
    "QSTASH_TOKEN": "tok",
    "QSTASH_CURRENT_SIGNING_KEY": "cur",
    "QSTASH_NEXT_SIGNING_KEY": "next",
    "DJANGO_QSTASH_DOMAIN": "https://example.com/",
}


class FakeBroker(Module):
    name = "fakebroker"
    provides = ("broker",)

    def settings(self, env):
        return {"CFE_BROKER_URL": "redis://broker:6379/0"}


@pytest.fixture
def with_fake_broker(monkeypatch):
    registry.available.cache_clear()
    mods = {**registry.available(), "fakebroker": FakeBroker}
    monkeypatch.setattr(registry, "available", lambda: mods)


def test_sync_settings():
    ns = {}
    loader.configure(ns, modules="sync", environ={})
    assert ns["CFE_TASKS_BACKEND"] == "sync"
    assert ns["CFE_STACK_SLOTS"] == {"tasks": "sync"}


def test_celery_settings(with_fake_broker):
    ns = {}
    loader.configure(ns, modules="fakebroker,celery", environ={"CELERY_TASK_ALWAYS_EAGER": "true"})
    assert ns["CFE_TASKS_BACKEND"] == "celery"
    assert ns["CELERY_RESULT_BACKEND"] == "django-db"
    assert ns["CELERY_BEAT_SCHEDULER"] == "django_celery_beat.schedulers:DatabaseScheduler"
    assert ns["CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP"] is True
    assert ns["CELERY_TASK_ALWAYS_EAGER"] is True
    assert "CELERY_BROKER_URL" not in ns
    assert ns["INSTALLED_APPS"][-2:] == ["django_celery_results", "django_celery_beat"]


def test_celery_eager_defaults_off(with_fake_broker):
    ns = {}
    loader.configure(ns, modules="fakebroker,celery", environ={})
    assert ns["CELERY_TASK_ALWAYS_EAGER"] is False


def test_celery_without_broker_errors():
    registry.available.cache_clear()
    with pytest.raises(StackConfigError, match="needs a module providing the 'broker' slot"):
        loader.build(["celery"], environ={})


def test_qstash_settings():
    ns = {}
    loader.configure(ns, modules="qstash", environ=QSTASH_ENV)
    assert ns["CFE_TASKS_BACKEND"] == "qstash"
    assert ns["QSTASH_TOKEN"] == "tok"
    assert ns["QSTASH_CURRENT_SIGNING_KEY"] == "cur"
    assert ns["QSTASH_NEXT_SIGNING_KEY"] == "next"
    assert ns["DJANGO_QSTASH_DOMAIN"] == "https://example.com"
    assert ns["DJANGO_QSTASH_WEBHOOK_PATH"] == "/qstash/webhook/"
    assert ns["INSTALLED_APPS"][-3:] == [
        "django_qstash",
        "django_qstash.results",
        "django_qstash.schedules",
    ]


def test_qstash_missing_env_lists_names_only():
    with pytest.raises(StackConfigError) as exc:
        loader.build(["qstash"], environ={"QSTASH_TOKEN": "supersecret"})
    msg = str(exc.value)
    assert "QSTASH_CURRENT_SIGNING_KEY" in msg and "DJANGO_QSTASH_DOMAIN" in msg
    assert "supersecret" not in msg


def test_qstash_secrets_marked():
    cls = registry.get("qstash")
    secret = {v.name for v in cls.env if v.secret}
    assert secret == {"QSTASH_TOKEN", "QSTASH_CURRENT_SIGNING_KEY", "QSTASH_NEXT_SIGNING_KEY"}


def test_qstash_urlpatterns_follow_webhook_path(settings):
    from cfe.stack.modules import qstash

    settings.DJANGO_QSTASH_WEBHOOK_PATH = "/hooks/qstash/"
    fake_urls = types.ModuleType("fake_qstash_urls")
    fake_urls.urlpatterns = []
    with mock.patch.dict(sys.modules, {"django_qstash.urls": fake_urls}):
        (pattern,) = qstash.urlpatterns
    assert str(pattern.pattern) == "hooks/qstash/"
    with pytest.raises(AttributeError):
        qstash.app_name  # noqa: B018


def test_sync_shim_runs_inline(settings):
    settings.CFE_TASKS_BACKEND = "sync"
    calls = []

    @tasks.shared_task
    def add(a, b):
        calls.append((a, b))
        return a + b

    assert add(1, 2) == 3
    result = add.delay(2, 3)
    assert result.get() == 5 and result.successful() and result.status == "SUCCESS"
    assert add.apply_async(args=(4,), kwargs={"b": 5}).get() == 9
    assert calls == [(1, 2), (2, 3), (4, 5)]
    assert add.__name__ == "add"


def test_sync_shim_with_options_and_failure(settings):
    settings.CFE_TASKS_BACKEND = "sync"

    @tasks.shared_task(bind=True, name="custom.boom")
    def boom(self):
        assert self.name == "custom.boom"
        raise ValueError("nope")

    result = boom.delay()
    assert result.failed() and result.status == "FAILURE"
    with pytest.raises(ValueError):
        result.get()
    assert isinstance(result.get(propagate=False), ValueError)


def test_default_backend_is_sync(settings):
    del settings.CFE_TASKS_BACKEND
    assert tasks.backend() == "sync"


def test_unknown_backend_errors(settings):
    settings.CFE_TASKS_BACKEND = "rq"
    with pytest.raises(ValueError, match="CFE_TASKS_BACKEND"):
        tasks.shared_task(lambda: None)


def _fake_decorator():
    def fake(func=None, **opts):
        if func is None:
            return lambda f: ("task", f, opts)
        return ("task", func, {})

    return mock.Mock(side_effect=fake)


def test_shim_delegates_to_celery(settings, monkeypatch):
    import celery

    settings.CFE_TASKS_BACKEND = "celery"
    fake = _fake_decorator()
    monkeypatch.setattr(celery, "shared_task", fake)

    def work():
        return 1

    assert tasks.shared_task(work) == ("task", work, {})
    assert tasks.shared_task(bind=True)(work) == ("task", work, {"bind": True})
    fake.assert_any_call(work)
    fake.assert_any_call(bind=True)


def test_shim_delegates_to_qstash(settings, monkeypatch):
    settings.CFE_TASKS_BACKEND = "qstash"
    fake = _fake_decorator()
    monkeypatch.setitem(sys.modules, "django_qstash", types.SimpleNamespace(shared_task=fake))

    def work():
        return 1

    assert tasks.shared_task(work) == ("task", work, {})
    assert tasks.shared_task(name="x")(work) == ("task", work, {"name": "x"})


def test_celery_tasks_stay_callable(settings):
    settings.CFE_TASKS_BACKEND = "celery"

    @tasks.shared_task
    def double(n):
        return n * 2

    assert double(4) == 8
    assert hasattr(double, "delay") and hasattr(double, "apply_async")


def test_schedule_sync_warns(settings, caplog):
    settings.CFE_TASKS_BACKEND = "sync"
    with caplog.at_level(logging.WARNING, logger="cfe.stack"):
        assert tasks.schedule("nightly", "app.tasks.x", "0 3 * * *") is None
    assert "nightly" in caplog.text


def test_schedule_celery(settings, monkeypatch):
    settings.CFE_TASKS_BACKEND = "celery"
    models = types.SimpleNamespace(CrontabSchedule=mock.Mock(), PeriodicTask=mock.Mock())
    models.CrontabSchedule.objects.get_or_create.return_value = ("cron", True)
    models.PeriodicTask.objects.update_or_create.return_value = ("row", True)
    monkeypatch.setitem(sys.modules, "django_celery_beat.models", models)

    assert tasks.schedule("nightly", "app.tasks.x", "0 3 * * 1", args=[1], kwargs={"a": 2}) == "row"
    models.CrontabSchedule.objects.get_or_create.assert_called_once_with(
        minute="0", hour="3", day_of_month="*", month_of_year="*", day_of_week="1", timezone="UTC"
    )
    _, kw = models.PeriodicTask.objects.update_or_create.call_args
    assert kw["name"] == "nightly"
    assert kw["defaults"]["task"] == "app.tasks.x"
    assert kw["defaults"]["crontab"] == "cron"
    assert kw["defaults"]["args"] == "[1]" and kw["defaults"]["kwargs"] == '{"a": 2}'


def test_schedule_qstash(settings, monkeypatch):
    settings.CFE_TASKS_BACKEND = "qstash"
    models = types.SimpleNamespace(TaskSchedule=mock.Mock())
    models.TaskSchedule.objects.update_or_create.return_value = ("row", False)
    monkeypatch.setitem(sys.modules, "django_qstash.schedules.models", models)

    def x():
        pass

    task = types.SimpleNamespace(func=x)
    assert tasks.schedule("nightly", task, "*/5 * * * *") == "row"
    models.TaskSchedule.objects.update_or_create.assert_called_once_with(
        name="nightly",
        defaults={
            "task": f"{__name__}.x",
            "cron": "*/5 * * * *",
            "args": [],
            "kwargs": {},
        },
    )


def test_celery_app_broker_from_cfe_setting():
    from cfe.stack import celery_app

    conf = types.SimpleNamespace(broker_url=None)
    fake_app = types.SimpleNamespace(conf=conf)
    celery_app._apply_broker_url(fake_app, types.SimpleNamespace(CFE_BROKER_URL="redis://b"))
    assert conf.broker_url == "redis://b"
    conf.broker_url = None
    celery_app._apply_broker_url(
        fake_app,
        types.SimpleNamespace(CELERY_BROKER_URL="redis://explicit", CFE_BROKER_URL="redis://b"),
    )
    assert conf.broker_url is None
    assert celery_app.app.main == "cfe"
