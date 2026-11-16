"""Minimal Django settings for cfe.stack tests, written the way a project would."""

import cfe.stack

stack = cfe.stack.load("sqlite", environ={"SQLITE_PATH": ":memory:"})

SECRET_KEY = "test-only"
USE_TZ = True
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", *stack.installed_apps]
MIDDLEWARE = stack.middleware([])
ROOT_URLCONF = "tests.urls"
DATABASES = stack.databases()
globals().update(stack.extra_settings())
