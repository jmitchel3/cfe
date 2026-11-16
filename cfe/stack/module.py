"""The Module contract every cfe.stack module implements."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

# Slots that accept exactly one provider. Modules may declare other slots;
# unknown slots are treated as single-provider too.
KNOWN_SLOTS = (
    "database",
    "cache",
    "broker",
    "tasks",
    "storage",
    "email",
    "billing",
)


@dataclass(frozen=True)
class EnvVar:
    """An environment variable a module reads.

    `cast` turns the raw string into the value modules see. `secret` marks
    values that must never be printed (doctor shows names only for these).
    `aliases` are older names still accepted, checked after `name`.
    """

    name: str
    required: bool = False
    default: Any = None
    cast: Callable[[str], Any] = str
    secret: bool = False
    help: str = ""
    aliases: tuple[str, ...] = ()


@dataclass
class Env:
    """Resolved env values for one module, readable as attributes."""

    values: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)


class Module:
    """Base class for a cfe.stack module.

    Subclass it, set the class attributes, and override `settings()`.
    A module is enabled by listing its `name` in `CFE_MODULES`.
    """

    name: ClassVar[str] = ""
    label: ClassVar[str] = ""
    description: ClassVar[str] = ""
    # Capability slots this module fills (see KNOWN_SLOTS).
    provides: ClassVar[tuple[str, ...]] = ()
    # Other module names that must also be enabled.
    requires: ClassVar[tuple[str, ...]] = ()
    # Slots that some enabled module must fill, e.g. celery requires "broker".
    requires_slots: ClassVar[tuple[str, ...]] = ()
    env: ClassVar[tuple[EnvVar, ...]] = ()
    # Python import names this module needs, with the pip extra that provides
    # them, e.g. {"celery": "cfe[celery]"}. Checked by `cfe doctor`.
    python_requires: ClassVar[dict[str, str]] = {}
    # Django apps appended to INSTALLED_APPS, in order.
    installed_apps: ClassVar[tuple[str, ...]] = ()
    # Middleware: (dotted path, insert-after dotted path or None for append).
    middleware: ClassVar[tuple[tuple[str, str | None], ...]] = ()
    # Dotted path to a urls module, mounted at `url_prefix`.
    urls: ClassVar[str | None] = None
    url_prefix: ClassVar[str | None] = None
    # Hook name -> dotted path of a callable; registered on Django app ready.
    hooks: ClassVar[dict[str, str]] = {}
    # Dashboard nav items, consumed through the `dashboard_nav` filter.
    nav: ClassVar[tuple[dict[str, str], ...]] = ()

    def settings(self, env: Env) -> dict[str, Any]:
        """Return Django settings this module contributes.

        Dict values are deep-merged with other modules' contributions; any
        other value set by two modules is a conflict.
        """
        return {}

    def checks(self, env: Env) -> list[str]:
        """Return reachability problems for `cfe doctor` (empty list = ok).

        Must never include secret values in messages.
        """
        return []

    def ready(self) -> None:
        """Called once from Django's AppConfig.ready() for enabled modules."""

    def __repr__(self) -> str:
        return f"<Module {self.name}>"
