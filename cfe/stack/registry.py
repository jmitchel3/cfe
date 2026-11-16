"""Find available modules, in this order:

1. built-ins under cfe.stack.modules,
2. any package exposing a `cfe.stack.modules` entry point,
3. local (in-project) modules listed by dotted path in CFE_MODULE_PATHS,
   e.g. `CFE_MODULE_PATHS=myproject.modules.analytics`.

Built-in and local files declare `MODULES = [SomeModule, ...]` and must not import
their vendor SDKs at import time (import them inside settings()/checks()).
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from importlib.metadata import entry_points

from .exceptions import StackConfigError
from .module import Module

logger = logging.getLogger("cfe.stack")

ENTRY_POINT_GROUP = "cfe.stack.modules"
MODULE_PATHS_ENV = "CFE_MODULE_PATHS"

BUILTIN = "builtin"
ENTRY_POINT = "entry point"
LOCAL = "local"


def parse_module_paths(raw: str | None) -> tuple[str, ...]:
    """Split a CFE_MODULE_PATHS value into unique dotted paths."""
    paths: list[str] = []
    for p in re.split(r"[,\s]+", raw or ""):
        if p.strip() and p.strip() not in paths:
            paths.append(p.strip())
    return tuple(paths)


def env_module_paths(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The local module paths configured in `environ` (default os.environ)."""
    environ = os.environ if environ is None else environ
    return parse_module_paths(environ.get(MODULE_PATHS_ENV))


def _builtin_modules() -> dict[str, type[Module]]:
    from . import modules as pkg

    found: dict[str, type[Module]] = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
        for cls in getattr(mod, "MODULES", ()):
            _add(found, cls, source=mod.__name__)
    return found


def _entry_point_modules() -> dict[str, type[Module]]:
    found: dict[str, type[Module]] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            obj = ep.load()
        except Exception:
            logger.exception("could not load cfe.stack module entry point %r", ep.name)
            continue
        classes = obj if isinstance(obj, (list, tuple)) else [obj]
        for cls in classes:
            _add(found, cls, source=f"entry point {ep.value}")
    return found


def _local_modules(paths: tuple[str, ...]) -> dict[str, type[Module]]:
    found: dict[str, type[Module]] = {}
    for path in paths:
        try:
            mod = importlib.import_module(path)
        except ImportError as exc:
            raise StackConfigError(
                f"{MODULE_PATHS_ENV}: could not import {path!r} ({exc})"
            ) from None
        classes = getattr(mod, "MODULES", None)
        if classes is None:
            raise StackConfigError(f"{MODULE_PATHS_ENV}: {path!r} has no MODULES = [...] list")
        for cls in classes:
            _add(found, cls, source=path)
    return found


def _add(found: dict[str, type[Module]], cls: type[Module], source: str) -> None:
    if not (isinstance(cls, type) and issubclass(cls, Module)) or not cls.name:
        raise StackConfigError(f"{source}: {cls!r} is not a named cfe.stack Module")
    if cls.name in found and found[cls.name] is not cls:
        raise StackConfigError(
            f"module name {cls.name!r} is defined twice ({found[cls.name].__module__} and {source})"
        )
    found[cls.name] = cls


@lru_cache(maxsize=32)
def _discover(paths: tuple[str, ...]) -> tuple[tuple[str, type[Module], str], ...]:
    found = {name: (cls, BUILTIN) for name, cls in _builtin_modules().items()}
    for kind, extra in ((ENTRY_POINT, _entry_point_modules()), (LOCAL, _local_modules(paths))):
        for name, cls in extra.items():
            if name in found and found[name][0] is not cls:
                raise StackConfigError(
                    f"{kind} module {name!r} ({cls.__module__}) clashes with "
                    f"{found[name][1]} module {name!r} ({found[name][0].__module__})"
                )
            found.setdefault(name, (cls, kind))
    return tuple((name, cls, kind) for name, (cls, kind) in sorted(found.items()))


def available(module_paths: Iterable[str] = ()) -> dict[str, type[Module]]:
    """All discoverable modules by name.

    `module_paths` are local module paths (see `env_module_paths()`); callers
    pass them explicitly so the list comes from the same environ as the rest
    of the configuration. Entry points and local modules cannot shadow
    built-ins or each other.
    """
    return {name: cls for name, cls, _ in _discover(tuple(module_paths))}


available.cache_clear = _discover.cache_clear  # type: ignore[attr-defined]


def sources(module_paths: Iterable[str] = ()) -> dict[str, str]:
    """Where each available module came from: builtin, entry point, or local."""
    return {name: kind for name, _, kind in _discover(tuple(module_paths))}


def get(name: str, module_paths: Iterable[str] | None = None) -> type[Module]:
    """Look up a module class by name.

    With `module_paths=None` (e.g. rebuilding the stack in AppConfig.ready),
    local modules from os.environ's CFE_MODULE_PATHS are also searched.
    """
    paths = tuple(module_paths) if module_paths is not None else ()
    # Call available() without arguments when there are no local paths, so
    # tests that monkeypatch `registry.available` with a zero-arg callable work.
    modules = available(paths) if paths else available()
    if name not in modules and module_paths is None and env_module_paths():
        modules = available(env_module_paths())
    try:
        return modules[name]
    except KeyError:
        raise StackConfigError(
            f"unknown cfe.stack module {name!r}. Available: {', '.join(modules) or 'none'}"
        ) from None
