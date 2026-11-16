"""Find available modules: built-ins under cfe.stack.modules, plus any
package exposing a `cfe.stack.modules` entry point.

Built-in files declare `MODULES = [SomeModule, ...]` and must not import
their vendor SDKs at import time (import them inside settings()/checks()).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from functools import lru_cache
from importlib.metadata import entry_points

from .exceptions import StackConfigError
from .module import Module

logger = logging.getLogger("cfe.stack")

ENTRY_POINT_GROUP = "cfe.stack.modules"


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


def _add(found: dict[str, type[Module]], cls: type[Module], source: str) -> None:
    if not (isinstance(cls, type) and issubclass(cls, Module)) or not cls.name:
        raise StackConfigError(f"{source}: {cls!r} is not a named cfe.stack Module")
    if cls.name in found and found[cls.name] is not cls:
        raise StackConfigError(
            f"module name {cls.name!r} is defined twice ({found[cls.name].__module__} and {source})"
        )
    found[cls.name] = cls


@lru_cache(maxsize=1)
def available() -> dict[str, type[Module]]:
    """All discoverable modules by name. Entry points cannot shadow built-ins."""
    found = _builtin_modules()
    for name, cls in _entry_point_modules().items():
        if name in found:
            raise StackConfigError(
                f"entry point module {name!r} ({cls.__module__}) clashes with a built-in module"
            )
        found[name] = cls
    return dict(sorted(found.items()))


def get(name: str) -> type[Module]:
    modules = available()
    try:
        return modules[name]
    except KeyError:
        raise StackConfigError(
            f"unknown cfe.stack module {name!r}. Available: {', '.join(modules) or 'none'}"
        ) from None
