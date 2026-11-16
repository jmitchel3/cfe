"""Turn CFE_MODULES into Django settings.

Usage, at the end of a project's settings.py:

    import cfe.stack
    cfe.stack.configure(globals())

Anything assigned after that call overrides what modules set.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

from . import registry
from .exceptions import StackConfigError
from .module import Env, EnvVar, Module

logger = logging.getLogger("cfe.stack")

MODULES_ENV = "CFE_MODULES"
STRICT_ENV = "CFE_STACK_STRICT"
_LIST_KEYS = {"INSTALLED_APPS", "MIDDLEWARE"}
_MISSING = object()


@dataclass
class Stack:
    """The resolved stack for this process."""

    modules: list[Module] = field(default_factory=list)
    envs: dict[str, Env] = field(default_factory=dict)
    slots: dict[str, str] = field(default_factory=dict)
    # Modules skipped in non-strict mode, with the reason.
    skipped: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> Module | None:
        return next((m for m in self.modules if m.name == name), None)

    def provider(self, slot: str) -> Module | None:
        name = self.slots.get(slot)
        return self.get(name) if name else None


_current: Stack | None = None


def current() -> Stack:
    """The stack built by the last configure() call (empty if none)."""
    return _current or Stack()


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

    classes = [registry.get(n) for n in names]
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
    return stack


def merge_module_settings(stack: Stack) -> dict[str, Any]:
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
    return merged


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


def configure(
    namespace: MutableMapping[str, Any],
    modules: list[str] | str | None = None,
    environ: Mapping[str, str] | None = None,
    strict: bool | None = None,
) -> Stack:
    """Apply enabled modules to a settings namespace (usually `globals()`).

    - `modules` defaults to the CFE_MODULES env var.
    - `strict` defaults to CFE_STACK_STRICT (true). When false, modules with
      missing required env vars are skipped with a warning, which keeps
      build-time commands like collectstatic working without secrets.
    - For each top-level settings key a module sets: if the project already
      has a dict there, the module's keys replace matching keys in it (so
      DATABASES["default"] is replaced, other aliases kept); otherwise the
      module's value replaces the project's.
    """
    global _current
    environ = os.environ if environ is None else environ
    if modules is None:
        modules = environ.get(MODULES_ENV, "")
    names = parse_module_list(modules) if isinstance(modules, str) else list(modules)
    if strict is None:
        strict = to_bool(environ.get(STRICT_ENV, "true"))

    stack = build(names, environ=environ, strict=strict)

    for key, value in merge_module_settings(stack).items():
        existing = namespace.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            namespace[key] = {**existing, **value}
        else:
            namespace[key] = value

    apps = list(namespace.get("INSTALLED_APPS", []))
    for app in ("cfe.stack", *[a for m in stack.modules for a in m.installed_apps]):
        if app not in apps:
            apps.append(app)
    namespace["INSTALLED_APPS"] = apps

    middleware = list(namespace.get("MIDDLEWARE", []))
    for m in stack.modules:
        for path, after in m.middleware:
            if path in middleware:
                continue
            if after and after in middleware:
                middleware.insert(middleware.index(after) + 1, path)
            else:
                middleware.append(path)
    namespace["MIDDLEWARE"] = middleware

    namespace["CFE_STACK_MODULES"] = [m.name for m in stack.modules]
    namespace["CFE_STACK_SLOTS"] = dict(stack.slots)
    _current = stack
    return stack
