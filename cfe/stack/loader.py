"""Resolve CFE_MODULES into a Stack whose values settings.py assigns explicitly.

    from cfe.stack import load
    stack = load()  # reads CFE_MODULES and module env vars via cfe.env

    INSTALLED_APPS = [*DJANGO_APPS, *PROJECT_APPS, *stack.installed_apps]
    MIDDLEWARE = stack.middleware(MIDDLEWARE)
    DATABASES = stack.databases(DATABASES)
    CACHES = stack.caches()
    STORAGES = stack.storages()
    globals().update(stack.extra_settings())  # library settings: CELERY_*, EMAIL_*, ...

Nothing is written into settings behind the project's back; `manage.py cfe
settings` shows every value a module provides and which module set it.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from . import registry
from .exceptions import StackConfigError
from .module import Env, EnvVar, Module

logger = logging.getLogger("cfe.stack")

MODULES_ENV = "CFE_MODULES"
STRICT_ENV = "CFE_STACK_STRICT"
_LIST_KEYS = {"INSTALLED_APPS", "MIDDLEWARE"}
# Settings with a dedicated accessor; everything else is in extra_settings().
SLOT_SETTINGS = ("DATABASES", "CACHES", "STORAGES")
_MISSING = object()
_UNSET = object()


@dataclass(repr=False)
class Stack:
    """The resolved, validated set of enabled modules and their env values."""

    modules: list[Module] = field(default_factory=list)
    envs: dict[str, Env] = field(default_factory=dict)
    slots: dict[str, str] = field(default_factory=dict)
    # Modules skipped in non-strict mode, with the reason.
    skipped: dict[str, str] = field(default_factory=dict)
    # Where env values were read from (os.environ, then .env/settings.ini).
    environ: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        # Names only: settings.CFE_STACK holds this object and Django's debug
        # page prints settings, so env values must never appear here.
        return f"<Stack {', '.join(self.names) or '(empty)'}>"

    def get(self, name: str) -> Module | None:
        return next((m for m in self.modules if m.name == name), None)

    def provider(self, slot: str) -> Module | None:
        name = self.slots.get(slot)
        return self.get(name) if name else None

    @property
    def names(self) -> list[str]:
        return [m.name for m in self.modules]

    def env(self, name: str) -> Env:
        """Resolved env values of an enabled module."""
        try:
            return self.envs[name]
        except KeyError:
            raise StackConfigError(f"module {name!r} is not enabled") from None

    # Settings

    @cached_property
    def _merged(self) -> tuple[dict[str, Any], dict[str, str]]:
        return merge_module_settings(self)

    def settings(self) -> dict[str, Any]:
        """Every setting enabled modules provide, merged (a copy)."""
        return dict(self._merged[0])

    def sources(self) -> dict[str, str]:
        """Setting name -> module that provided it (first one, for merged dicts)."""
        return dict(self._merged[1])

    def setting(self, key: str, base: Any = _UNSET) -> Any:
        """One merged setting. Dicts are layered over `base` (default: Django's
        own default for `key`), so STORAGES keeps "staticfiles" and extra
        DATABASES aliases survive. Returns `base` when no module sets `key`."""
        if base is _UNSET:
            base = _django_default(key)
        value = self._merged[0].get(key, _MISSING)
        if value is _MISSING:
            return base
        if isinstance(value, dict) and isinstance(base, dict):
            return {**base, **value}
        return value

    def databases(self, base: dict | None = None) -> dict:
        return self.setting("DATABASES", {} if base is None else base)

    def caches(self, base: dict | None = None) -> dict:
        return self.setting("CACHES", _UNSET if base is None else base)

    def storages(self, base: dict | None = None) -> dict:
        return self.setting("STORAGES", _UNSET if base is None else base)

    def extra_settings(self) -> dict[str, Any]:
        """Settings other than DATABASES/CACHES/STORAGES (library settings such
        as CELERY_*, EMAIL_*, STRIPE_*), plus CFE_STACK_* bookkeeping."""
        extra = {k: v for k, v in self._merged[0].items() if k not in SLOT_SETTINGS}
        extra["CFE_STACK"] = self
        extra["CFE_STACK_MODULES"] = self.names
        extra["CFE_STACK_SLOTS"] = dict(self.slots)
        extra["CFE_STACK_SKIPPED"] = dict(self.skipped)
        return extra

    # Apps and middleware

    @property
    def installed_apps(self) -> list[str]:
        apps = ["cfe.stack"]
        for m in self.modules:
            for app in m.installed_apps:
                if app not in apps:
                    apps.append(app)
        return apps

    def middleware(self, base: list[str] | tuple[str, ...]) -> list[str]:
        """`base` with each module's middleware inserted after its anchor.
        A missing anchor is an error, never a silent append."""
        out = list(base)
        for m in self.modules:
            for path, after in m.middleware:
                if path in out:
                    continue
                if after is None:
                    out.append(path)
                elif after in out:
                    out.insert(out.index(after) + 1, path)
                else:
                    raise StackConfigError(
                        f"module {m.name!r} needs {path} after {after}, which is not in MIDDLEWARE"
                    )
        return out


def parse_module_list(raw: str | None) -> list[str]:
    names = [n.strip() for n in re.split(r"[,\s]+", raw or "") if n.strip()]
    seen: list[str] = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return seen


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def resolve_env(module: Module, environ: Mapping[str, str]) -> tuple[Env, list[str]]:
    """Read a module's declared env vars. Returns (env, missing required names)."""
    values: dict[str, Any] = {}
    missing: list[str] = []
    for var in module.env:
        raw = _lookup(var, environ)
        if raw is _MISSING or raw == "":
            if var.required:
                missing.append(var.name)
            values[var.name] = var.default
            continue
        try:
            values[var.name] = var.cast(raw)
        except Exception as exc:  # noqa: BLE001 (casts are arbitrary callables)
            # Never echo the value; it may be a secret.
            raise StackConfigError(
                f"module {module.name!r}: env var {var.name} could not be parsed ({type(exc).__name__})"
            ) from None
    return Env(values), missing


def _lookup(var: EnvVar, environ: Mapping[str, str]) -> Any:
    for key in (var.name, *var.aliases):
        if key in environ:
            return environ[key]
    return _MISSING


def build(
    names: list[str],
    environ: Mapping[str, str] | None = None,
    strict: bool = True,
) -> Stack:
    """Resolve module names into a validated Stack without touching settings."""
    environ = os.environ if environ is None else environ
    stack = Stack()
    errors: list[str] = []

    # Local module paths (CFE_MODULE_PATHS) come from the same environ.
    paths = registry.env_module_paths(environ)
    classes = [registry.get(n, module_paths=paths) for n in names]
    modules = [cls() for cls in classes]
    enabled = {m.name for m in modules}

    for m in modules:
        for dep in m.requires:
            if dep not in enabled:
                errors.append(f"module {m.name!r} requires module {dep!r}; add it to {MODULES_ENV}")
        for slot in m.provides:
            if slot in stack.slots:
                errors.append(
                    f"modules {stack.slots[slot]!r} and {m.name!r} both provide the {slot!r} slot; enable only one"
                )
            else:
                stack.slots[slot] = m.name
    for m in modules:
        for slot in m.requires_slots:
            if slot not in stack.slots:
                errors.append(f"module {m.name!r} needs a module providing the {slot!r} slot")
    if errors:
        raise StackConfigError("; ".join(errors))

    missing_by_module: dict[str, list[str]] = {}
    for m in modules:
        env, missing = resolve_env(m, environ)
        if missing:
            missing_by_module[m.name] = missing
        stack.envs[m.name] = env

    if missing_by_module:
        detail = "; ".join(
            f"{name}: {', '.join(vars_)}" for name, vars_ in missing_by_module.items()
        )
        if strict:
            raise StackConfigError(f"missing required env vars ({detail})")
        logger.warning("cfe.stack skipping modules with missing env vars (%s)", detail)
        for name, vars_ in missing_by_module.items():
            stack.skipped[name] = f"missing {', '.join(vars_)}"
        modules = [m for m in modules if m.name not in missing_by_module]
        stack.slots = {s: n for s, n in stack.slots.items() if n not in missing_by_module}

    stack.modules = modules
    stack.environ = environ
    return stack


def merge_module_settings(stack: Stack) -> tuple[dict[str, Any], dict[str, str]]:
    """Combine every module's settings(). Dicts deep-merge; other clashes error."""
    merged: dict[str, Any] = {}
    owners: dict[str, str] = {}
    for m in stack.modules:
        contributed = m.settings(stack.envs[m.name]) or {}
        for key, value in contributed.items():
            if key in _LIST_KEYS:
                raise StackConfigError(
                    f"module {m.name!r} sets {key} in settings(); use the installed_apps/middleware attributes"
                )
            if key not in merged:
                merged[key] = value
                owners[key] = m.name
            elif isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = _deep_merge(merged[key], value, path=key, a=owners[key], b=m.name)
            elif merged[key] != value:
                raise StackConfigError(
                    f"modules {owners[key]!r} and {m.name!r} both set {key} to different values"
                )
    return merged, owners


def _deep_merge(left: dict, right: dict, path: str, a: str, b: str) -> dict:
    out = dict(left)
    for k, v in right.items():
        if k not in out:
            out[k] = v
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v, f"{path}[{k!r}]", a, b)
        elif out[k] != v:
            raise StackConfigError(
                f"modules {a!r} and {b!r} both set {path}[{k!r}] to different values"
            )
    return out


def _django_default(key: str) -> Any:
    """Django's own default for a setting the project left unset, so a module
    setting STORAGES["default"] keeps Django's default "staticfiles" entry."""
    try:
        from django.conf import global_settings
    except ImportError:  # pragma: no cover
        return None
    value = getattr(global_settings, key, None)
    return dict(value) if isinstance(value, dict) else None


def env_source(search_path: str | os.PathLike[str] | None = None) -> Mapping[str, str]:
    """os.environ first, then the nearest .env or settings.ini (via cfe.env)."""
    from cfe.env import AutoConfig

    return AutoConfig(search_path).loaded().as_mapping()


def load(
    modules: list[str] | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    strict: bool | None = None,
    search_path: str | os.PathLike[str] | None = None,
) -> Stack:
    """Build the stack for settings.py.

    - `environ` defaults to `env_source(search_path)`; `search_path` defaults
      to the caller's directory, the same rule python-decouple uses.
    - `modules` defaults to CFE_MODULES from that environ.
    - `strict` defaults to CFE_STACK_STRICT (true). When false, modules with
      missing required env vars are skipped with a warning, which keeps
      build-time commands like collectstatic working without secrets.
    """
    if environ is None:
        if search_path is None:
            search_path = os.path.dirname(os.path.abspath(sys._getframe(1).f_code.co_filename))
        environ = env_source(search_path)
    if modules is None:
        modules = environ.get(MODULES_ENV, "")
    names = parse_module_list(modules) if isinstance(modules, str) else list(modules)
    if strict is None:
        strict = to_bool(environ.get(STRICT_ENV, "true"))
    return build(names, environ=environ, strict=strict)


def runtime() -> Stack:
    """The stack settings.py loaded (settings.CFE_STACK, set by extra_settings()).

    Falls back to rebuilding from settings.CFE_STACK_MODULES, reading env from
    the settings module's directory, without raising for missing env vars.
    """
    from django.conf import settings

    stack = getattr(settings, "CFE_STACK", None)
    if isinstance(stack, Stack):
        return stack
    names = list(getattr(settings, "CFE_STACK_MODULES", []))
    module = sys.modules.get(os.environ.get("DJANGO_SETTINGS_MODULE", ""))
    path = os.path.dirname(module.__file__) if getattr(module, "__file__", None) else None
    stack = build(names, environ=env_source(path), strict=False)
    stack.skipped.update(getattr(settings, "CFE_STACK_SKIPPED", {}))
    return stack
