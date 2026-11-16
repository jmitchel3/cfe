"""WordPress-style actions and filters.

Actions are events: `do_action("user_signed_up", user=user)` calls every
handler and ignores return values. Filters transform a value:
`apply_filters("dashboard_nav", items, request=request)` passes the value
through each handler in priority order (lower runs first).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("cfe.stack.hooks")

_actions: dict[str, list[tuple[int, Callable[..., Any]]]] = defaultdict(list)
_filters: dict[str, list[tuple[int, Callable[..., Any]]]] = defaultdict(list)


def add_action(name: str, fn: Callable[..., Any], priority: int = 10) -> None:
    if fn not in [f for _, f in _actions[name]]:
        _actions[name].append((priority, fn))
        _actions[name].sort(key=lambda item: item[0])


def add_filter(name: str, fn: Callable[..., Any], priority: int = 10) -> None:
    if fn not in [f for _, f in _filters[name]]:
        _filters[name].append((priority, fn))
        _filters[name].sort(key=lambda item: item[0])


def on(name: str, priority: int = 10) -> Callable[[Callable], Callable]:
    """Decorator form of add_action."""

    def decorator(fn: Callable) -> Callable:
        add_action(name, fn, priority)
        return fn

    return decorator


def filter(name: str, priority: int = 10) -> Callable[[Callable], Callable]:
    """Decorator form of add_filter."""

    def decorator(fn: Callable) -> Callable:
        add_filter(name, fn, priority)
        return fn

    return decorator


def do_action(name: str, /, *, raise_errors: bool = False, **kwargs: Any) -> None:
    """Run every handler for `name`. Handler errors are logged, not raised,
    unless `raise_errors` is set, so one broken module cannot break a request.
    """
    for _, fn in list(_actions.get(name, ())):
        try:
            fn(**kwargs)
        except Exception:
            if raise_errors:
                raise
            logger.exception("hook %r handler %r failed", name, fn)


def apply_filters(name: str, value: Any, /, **kwargs: Any) -> Any:
    for _, fn in list(_filters.get(name, ())):
        value = fn(value, **kwargs)
    return value


def has_handlers(name: str) -> bool:
    return bool(_actions.get(name) or _filters.get(name))


def register(name: str, fn: Callable[..., Any], priority: int = 10) -> None:
    """Register a module hook declared in Module.hooks.

    Callables flagged with `is_filter = True` (or named `filter_*`) become
    filters; everything else is an action.
    """
    if getattr(fn, "is_filter", False) or fn.__name__.startswith("filter_"):
        add_filter(name, fn, priority)
    else:
        add_action(name, fn, priority)


def clear() -> None:
    """Remove every handler. For tests."""
    _actions.clear()
    _filters.clear()
