"""Minimal Django settings for cfe.stack tests."""

import cfe.stack

SECRET_KEY = "test-only"
USE_TZ = True
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth"]
MIDDLEWARE = []
ROOT_URLCONF = "tests.urls"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

cfe.stack.configure(globals(), modules="sqlite", environ={"SQLITE_PATH": ":memory:"})
