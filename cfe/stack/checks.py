"""Django system checks: catch a settings.py that loads the stack but forgets
to assign one of its values, so a module can never be silently ignored."""

from django.conf import settings
from django.core import checks

from .exceptions import StackConfigError


def _contains(actual, expected) -> bool:
    """True when every key/value in `expected` is present in `actual`.
    Django adds defaults to DATABASES entries, so equality is too strict."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        return all(k in actual and _contains(actual[k], v) for k, v in expected.items())
    return actual == expected


@checks.register()
def stack_settings_applied(app_configs=None, **kwargs):
    from . import loader

    if not hasattr(settings, "CFE_STACK_MODULES"):
        return [
            checks.Warning(
                "cfe.stack is installed but CFE_STACK_MODULES is not set.",
                hint="Add `globals().update(stack.extra_settings())` to settings.py.",
                id="cfe_stack.W001",
            )
        ]
    try:
        stack = loader.runtime()
    except StackConfigError as exc:
        return [checks.Error(str(exc), id="cfe_stack.E001")]

    problems = []
    sources = stack.sources()
    merged = stack.settings()
    for key in loader.SLOT_SETTINGS:
        if key not in merged:
            continue
        actual = getattr(settings, key, None)
        for alias, value in merged[key].items():
            if not _contains((actual or {}).get(alias), value):
                problems.append(
                    checks.Warning(
                        f"module {sources[key]!r} provides {key}[{alias!r}] but settings.{key} does not use it.",
                        hint=f"Assign `{key} = stack.{key.lower()}(...)` in settings.py.",
                        id="cfe_stack.W002",
                    )
                )
    missing_apps = [a for a in stack.installed_apps if a not in settings.INSTALLED_APPS]
    if missing_apps:
        problems.append(
            checks.Warning(
                f"INSTALLED_APPS is missing {', '.join(missing_apps)}.",
                hint="Add `*stack.installed_apps` to INSTALLED_APPS.",
                id="cfe_stack.W003",
            )
        )
    missing_mw = [
        path for m in stack.modules for path, _ in m.middleware if path not in settings.MIDDLEWARE
    ]
    if missing_mw:
        problems.append(
            checks.Warning(
                f"MIDDLEWARE is missing {', '.join(missing_mw)}.",
                hint="Use `MIDDLEWARE = stack.middleware(MIDDLEWARE)`.",
                id="cfe_stack.W004",
            )
        )
    return problems
