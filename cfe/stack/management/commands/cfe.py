"""`python manage.py cfe <list|doctor|env|new>`: inspect and scaffold cfe.stack modules.

Never prints env var values; only names and whether they are set.
"""

from __future__ import annotations

import importlib.util
import os
import re
from collections.abc import Mapping
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cfe.stack import loader, registry
from cfe.stack.exceptions import StackConfigError
from cfe.stack.module import Env, EnvVar, Module

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def is_set(var: EnvVar, environ: Mapping[str, str]) -> bool:
    return any(environ.get(key, "") != "" for key in (var.name, *var.aliases))


def importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def table(rows: list[list[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in rows]


def scrub(message: str, env: Env, module: Module) -> str:
    """Remove secret values from a check message (defence in depth)."""
    for var in module.env:
        value = env.values.get(var.name)
        if var.secret and value not in (None, "") and str(value) in message:
            message = message.replace(str(value), "***")
    return message


class Command(BaseCommand):
    help = "Inspect cfe.stack modules: list, doctor, env, new."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="subcommand", required=True)
        sub.add_parser("list", help="Available modules and the slot map for this process.")
        doctor = sub.add_parser("doctor", help="Validate enabled modules (env, packages, checks).")
        doctor.add_argument(
            "--offline", action="store_true", help="Skip module reachability checks."
        )
        env = sub.add_parser("env", help="Env vars of enabled modules (names only).")
        env.add_argument("--all", action="store_true", help="Include every available module.")
        env.add_argument(
            "--names", action="store_true", help="Print bare names, one per line (for Doppler)."
        )
        new = sub.add_parser("new", help="Scaffold a local module file.")
        new.add_argument("name", help="Module name, e.g. analytics.")
        new.add_argument("--path", help="File to write (default ./modules/<name>.py).")
        new.add_argument("--force", action="store_true", help="Overwrite an existing file.")

    def handle(self, *args, **options):
        handler = getattr(self, f"cmd_{options['subcommand']}")
        handler(**options)

    # Helpers

    @property
    def environ(self) -> Mapping[str, str]:
        return os.environ

    def module_paths(self) -> tuple[str, ...]:
        return registry.env_module_paths(self.environ)

    def enabled_names(self) -> list[str]:
        return list(getattr(settings, "CFE_STACK_MODULES", []))

    def enabled_modules(self) -> list[Module]:
        stack = loader.current()
        names = self.enabled_names()
        if [m.name for m in stack.modules] == names:
            return list(stack.modules)
        return [registry.get(n, module_paths=self.module_paths())() for n in names]

    def ok(self, text: str) -> None:
        self.stdout.write(self.style.SUCCESS("  ok    ") + text)

    def warn(self, text: str) -> None:
        self.stdout.write(self.style.WARNING("  warn  ") + text)

    def fail(self, text: str) -> None:
        self.stdout.write(self.style.ERROR("  FAIL  ") + text)

    # Subcommands

    def cmd_list(self, **options) -> None:
        paths = self.module_paths()
        modules = registry.available(paths)
        sources = registry.sources(paths)
        enabled = set(self.enabled_names())
        rows = [["NAME", "LABEL", "SLOTS", "ENABLED", "SOURCE"]]
        for name, cls in modules.items():
            rows.append(
                [
                    name,
                    cls.label or "-",
                    ",".join(cls.provides) or "-",
                    "yes" if name in enabled else "no",
                    sources.get(name, "?"),
                ]
            )
        lines = table(rows)
        self.stdout.write(self.style.MIGRATE_HEADING(lines[0]))
        for line in lines[1:]:
            self.stdout.write(line)

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Slots"))
        slots = dict(getattr(settings, "CFE_STACK_SLOTS", {}))
        if not slots:
            self.stdout.write("  (no slots filled)")
        for slot, name in sorted(slots.items()):
            self.stdout.write(f"  {slot} -> {name}")
        skipped = loader.current().skipped
        for name, reason in skipped.items():
            self.stdout.write(self.style.WARNING(f"  skipped {name}: {reason}"))

    def cmd_doctor(self, offline: bool = False, **options) -> None:
        failures = 0
        stack = loader.current()
        try:
            modules = self.enabled_modules()
        except StackConfigError as exc:
            raise CommandError(str(exc), returncode=1) from None
        if not modules and not stack.skipped:
            self.stdout.write("No cfe.stack modules enabled (set CFE_MODULES).")

        for module in modules:
            self.stdout.write(self.style.MIGRATE_HEADING(f"{module.name}"))
            for var in module.env:
                secret = " (secret)" if var.secret else ""
                if is_set(var, self.environ):
                    self.ok(f"{var.name}{secret} is set")
                elif var.required:
                    failures += 1
                    self.fail(f"{var.name}{secret} is required but not set")
                else:
                    note = "uses default" if var.default is not None else "optional"
                    self.ok(f"{var.name}{secret} not set ({note})")
            for import_name, extra in module.python_requires.items():
                if importable(import_name):
                    self.ok(f"python package {import_name} importable")
                else:
                    failures += 1
                    self.fail(f'python package {import_name} missing: pip install "{extra}"')
            if offline:
                self.stdout.write("  skip  checks (--offline)")
                continue
            env = stack.envs.get(module.name)
            if env is None:
                try:
                    env, _ = loader.resolve_env(module, self.environ)
                except StackConfigError as exc:
                    failures += 1
                    self.fail(str(exc))
                    continue
            try:
                problems = module.checks(env) or []
            except Exception as exc:  # noqa: BLE001 (report, never echo the message)
                failures += 1
                self.fail(f"checks() raised {type(exc).__name__}")
                continue
            if not problems:
                self.ok("checks passed")
            for problem in problems:
                failures += 1
                self.fail(scrub(str(problem), env, module))

        for name, reason in stack.skipped.items():
            failures += 1
            self.stdout.write(self.style.MIGRATE_HEADING(name))
            self.fail(f"skipped by non-strict mode: {reason}")

        self.stdout.write("")
        if failures:
            raise CommandError(f"cfe doctor found {failures} problem(s)", returncode=1)
        self.stdout.write(self.style.SUCCESS("cfe doctor: all good"))

    def cmd_env(self, all: bool = False, names: bool = False, **options) -> None:
        if all:
            modules = [cls() for cls in registry.available(self.module_paths()).values()]
        else:
            modules = self.enabled_modules()
        if names:
            seen: list[str] = []
            for module in modules:
                for var in module.env:
                    if var.name not in seen:
                        seen.append(var.name)
                        self.stdout.write(var.name)
            return
        rows = [["MODULE", "NAME", "REQUIRED", "SECRET", "DEFAULT", "SET"]]
        for module in modules:
            for var in module.env:
                rows.append(
                    [
                        module.name,
                        var.name,
                        "yes" if var.required else "no",
                        "yes" if var.secret else "no",
                        "yes" if var.default is not None else "no",
                        "yes" if is_set(var, self.environ) else "no",
                    ]
                )
        if len(rows) == 1:
            self.stdout.write("No env vars declared by these modules.")
            return
        lines = table(rows)
        self.stdout.write(self.style.MIGRATE_HEADING(lines[0]))
        for line in lines[1:]:
            self.stdout.write(line)

    def cmd_new(self, name: str, path: str | None = None, force: bool = False, **options):
        if not NAME_RE.match(name):
            raise CommandError(
                f"invalid module name {name!r}: use lowercase letters, digits, - or _"
            )
        stem = name.replace("-", "_")
        target = Path(path) if path else Path("modules") / f"{stem}.py"
        if target.suffix != ".py":
            raise CommandError(f"--path must be a .py file, got {target}")
        if target.exists() and not force:
            raise CommandError(f"{target} already exists (use --force to overwrite)")
        target.parent.mkdir(parents=True, exist_ok=True)
        init = target.parent / "__init__.py"
        if not init.exists():
            init.write_text("")
        target.write_text(render_module(name))

        self.stdout.write(self.style.SUCCESS(f"Created {target}"))
        dotted = dotted_path(target)
        self.stdout.write("Enable it with:")
        if dotted:
            self.stdout.write(f"  CFE_MODULE_PATHS={dotted}")
        else:
            self.stdout.write(
                "  CFE_MODULE_PATHS=<dotted import path of the file> "
                "(the file is outside the current directory)"
            )
        self.stdout.write(f"  CFE_MODULES=...,{name}")


def dotted_path(target: Path) -> str | None:
    try:
        rel = target.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return None
    return ".".join(rel.with_suffix("").parts)


def render_module(name: str) -> str:
    cls = "".join(part.capitalize() for part in re.split(r"[-_]", name) if part)
    prefix = re.sub(r"[^A-Z0-9]", "_", name.upper())
    return f'''"""{cls}: a local cfe.stack module.

Enable with CFE_MODULE_PATHS=<this module's dotted path> and CFE_MODULES=...,{name}.
Import vendor SDKs inside methods, never at module import time.
"""

from cfe.stack.module import EnvVar, Module


class {cls}(Module):
    name = "{name}"
    label = "{cls}"
    description = "Describe what {name} provides."
    # Slots this module fills, e.g. ("cache",). Leave empty for feature modules.
    provides = ()
    env = (
        EnvVar("{prefix}_URL", required=True, secret=True, help="Connection URL."),
        EnvVar("{prefix}_TIMEOUT", default=5, cast=int, help="Timeout in seconds."),
    )
    # Import name -> pip requirement, checked by `manage.py cfe doctor`.
    python_requires = {{}}

    def settings(self, env):
        return {{
            "{prefix}": {{
                "URL": env.{prefix}_URL,
                "TIMEOUT": env.{prefix}_TIMEOUT,
            }}
        }}

    def checks(self, env):
        # Return problem strings (empty list = ok). Never include secret values.
        return []


MODULES = [{cls}]
'''
