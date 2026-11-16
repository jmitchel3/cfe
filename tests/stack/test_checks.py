from cfe.stack import checks as stack_checks


def ids(messages):
    return [m.id for m in messages]


def test_project_settings_pass_checks():
    assert stack_checks.stack_settings_applied() == []


def test_unassigned_database_warns(settings):
    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "other.db"}}
    assert "cfe_stack.W002" in ids(stack_checks.stack_settings_applied())


def test_missing_bookkeeping_warns(settings):
    del settings.CFE_STACK_MODULES
    assert ids(stack_checks.stack_settings_applied()) == ["cfe_stack.W001"]
