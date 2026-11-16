"""Env var and config file loading, forked from python-decouple 3.8.

Original: https://github.com/HBNetwork/python-decouple
Copyright (c) 2013 Henrique Bastos. MIT License (see LICENSES/python-decouple.txt).

Usage is the same as decouple:

    from cfe.env import config, Csv
    DEBUG = config("DEBUG", default=False, cast=bool)
    ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="", cast=Csv())

Lookup order: os.environ (so `doppler run --` and container env win), then
the first `settings.ini` or `.env` found walking up from the search path.

Changes from upstream:
- Python 3.12+ only; Python 2 shims removed.
- `Config.lookup()`, `Config.__contains__`, and `Config.as_mapping()` expose
  raw values so cfe.stack can resolve module env vars through the same source.
- `.env` lines may start with `export ` (shell-compatible files).
- `AutoConfig` exposes `.loaded()` to get the underlying Config for a path.
- No import of Django or any third-party package.
"""

from __future__ import annotations

import os
import string
import sys
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from configparser import ConfigParser, NoOptionError
from shlex import shlex
from typing import Any, ClassVar

__all__ = [
    "AutoConfig",
    "Choices",
    "Config",
    "Csv",
    "RepositoryEmpty",
    "RepositoryEnv",
    "RepositoryIni",
    "RepositorySecret",
    "UndefinedValueError",
    "config",
    "strtobool",
    "undefined",
]

DEFAULT_ENCODING = "UTF-8"

TRUE_VALUES = {"y", "yes", "t", "true", "on", "1"}
FALSE_VALUES = {"n", "no", "f", "false", "off", "0"}


def strtobool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    value = value.lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ValueError("Invalid truth value: " + value)


class UndefinedValueError(Exception):
    pass


class Undefined:
    """Marker type for values with no default."""


undefined = Undefined()


class Config:
    """Read options from os.environ, then a repository (a parsed config file)."""

    def __init__(self, repository: RepositoryEmpty):
        self.repository = repository

    def _cast_boolean(self, value: Any) -> bool:
        value = str(value)
        return bool(value) if value == "" else bool(strtobool(value))

    @staticmethod
    def _cast_do_nothing(value: Any) -> Any:
        return value

    def lookup(self, option: str) -> str | None:
        """Raw string value for `option`, or None when it is not defined anywhere."""
        if option in os.environ:
            return os.environ[option]
        if option in self.repository:
            try:
                return self.repository[option]
            except KeyError:
                return None
        return None

    def __contains__(self, option: str) -> bool:
        return option in os.environ or option in self.repository

    def get(self, option: str, default: Any = undefined, cast: Any = undefined) -> Any:
        """Return the value for option, or default if one is given."""
        # __contains__ is needed because a value may be an empty string.
        if option in os.environ:
            value = os.environ[option]
        elif option in self.repository:
            value = self.repository[option]
        else:
            if isinstance(default, Undefined):
                raise UndefinedValueError(
                    f"{option} not found. Declare it as envvar or define a default value."
                )
            value = default

        if isinstance(cast, Undefined):
            cast = self._cast_do_nothing
        elif cast is bool:
            cast = self._cast_boolean
        return cast(value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.get(*args, **kwargs)

    def as_mapping(self) -> ConfigMapping:
        """A read-only Mapping view (os.environ first, then the repository)."""
        return ConfigMapping(self)


class ConfigMapping(Mapping[str, str]):
    """Mapping view over a Config, for code that expects an environ-like object."""

    def __init__(self, config: Config):
        self._config = config

    def __getitem__(self, key: str) -> str:
        value = self._config.lookup(key)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._config

    def __iter__(self) -> Iterator[str]:
        seen = set(os.environ)
        yield from os.environ
        for key in self._config.repository.keys():  # noqa: SIM118 (repositories are not dicts)
            if key not in seen:
                yield key

    def __len__(self) -> int:
        return sum(1 for _ in self)


class RepositoryEmpty:
    def __init__(self, source: str = "", encoding: str = DEFAULT_ENCODING):
        pass

    def __contains__(self, key: str) -> bool:
        return False

    def __getitem__(self, key: str) -> str | None:
        return None

    def keys(self) -> list[str]:
        return []


class RepositoryIni(RepositoryEmpty):
    """Options from the [settings] section of an .ini file."""

    SECTION = "settings"

    def __init__(self, source: str, encoding: str = DEFAULT_ENCODING):
        self.parser = ConfigParser()
        with open(source, encoding=encoding) as file_:
            self.parser.read_file(file_)

    def __contains__(self, key: str) -> bool:
        return key in os.environ or self.parser.has_option(self.SECTION, key)

    def __getitem__(self, key: str) -> str:
        try:
            return self.parser.get(self.SECTION, key)
        except NoOptionError:
            raise KeyError(key) from None

    def keys(self) -> list[str]:
        if not self.parser.has_section(self.SECTION):
            return []
        # ConfigParser lowercases option names by default; upstream behaves the same.
        return list(self.parser.options(self.SECTION))


class RepositoryEnv(RepositoryEmpty):
    """Options from a .env file (KEY=value lines, optional quotes, optional `export `)."""

    def __init__(self, source: str, encoding: str = DEFAULT_ENCODING):
        self.data: dict[str, str] = {}
        with open(source, encoding=encoding) as file_:
            for line in file_:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].lstrip()
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if len(v) >= 2 and (
                    (v[0] == "'" and v[-1] == "'") or (v[0] == '"' and v[-1] == '"')
                ):
                    v = v[1:-1]
                self.data[k] = v

    def __contains__(self, key: str) -> bool:
        return key in os.environ or key in self.data

    def __getitem__(self, key: str) -> str:
        return self.data[key]

    def keys(self) -> list[str]:
        return list(self.data)


class RepositorySecret(RepositoryEmpty):
    """Options from a directory of files (file name = key, contents = value),
    e.g. Docker swarm secrets."""

    def __init__(self, source: str = "/run/secrets/"):
        self.data: dict[str, str] = {}
        for name in os.listdir(source):
            with open(os.path.join(source, name)) as f:
                self.data[name] = f.read()

    def __contains__(self, key: str) -> bool:
        return key in os.environ or key in self.data

    def __getitem__(self, key: str) -> str:
        return self.data[key]

    def keys(self) -> list[str]:
        return list(self.data)


class AutoConfig:
    """Find settings.ini or .env by walking up from `search_path` (default: the
    caller's directory) and read options from it, after os.environ."""

    SUPPORTED: ClassVar[OrderedDict[str, type[RepositoryEmpty]]] = OrderedDict(
        [("settings.ini", RepositoryIni), (".env", RepositoryEnv)]
    )
    encoding = DEFAULT_ENCODING

    def __init__(self, search_path: str | os.PathLike[str] | None = None):
        self.search_path = os.fspath(search_path) if search_path is not None else None
        self.config: Config | None = None

    def _find_file(self, path: str) -> str:
        for configfile in self.SUPPORTED:
            filename = os.path.join(path, configfile)
            if os.path.isfile(filename):
                return filename
        parent = os.path.dirname(path)
        if parent and os.path.normcase(parent) != os.path.normcase(os.path.abspath(os.sep)):
            return self._find_file(parent)
        return ""

    def _load(self, path: str) -> None:
        try:
            filename = self._find_file(os.path.abspath(path))
        except Exception:  # noqa: BLE001 (permission errors mean "no file")
            filename = ""
        repository = self.SUPPORTED.get(os.path.basename(filename), RepositoryEmpty)
        self.config = Config(repository(filename, encoding=self.encoding))

    def _caller_path(self) -> str:
        # The module that called __call__ (two frames up).
        frame = sys._getframe()
        return os.path.dirname(frame.f_back.f_back.f_code.co_filename)

    def loaded(self, path: str | None = None) -> Config:
        """The Config for this AutoConfig, loading it from `path` (or the search path)."""
        if not self.config:
            self._load(path or self.search_path or os.getcwd())
        return self.config

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not self.config:
            self._load(self.search_path or self._caller_path())
        return self.config(*args, **kwargs)


# Ready to use: `from cfe.env import config`.
config = AutoConfig()


class Csv:
    """Cast a comma separated string to a list of cast items."""

    def __init__(
        self,
        cast: Callable[[str], Any] = str,
        delimiter: str = ",",
        strip: str = string.whitespace,
        post_process: Callable[..., Any] = list,
    ):
        self.cast = cast
        self.delimiter = delimiter
        self.strip = strip
        self.post_process = post_process

    def __call__(self, value: str | None) -> Any:
        if value is None:
            return self.post_process()
        splitter = shlex(value, posix=True)
        splitter.whitespace = self.delimiter
        splitter.whitespace_split = True
        return self.post_process(self.cast(s.strip(self.strip)) for s in splitter)


class Choices:
    """Cast and validate against a list of choices."""

    def __init__(
        self,
        flat: list[Any] | None = None,
        cast: Callable[[str], Any] = str,
        choices: list[tuple[Any, Any]] | None = None,
    ):
        self.flat = flat or []
        self.cast = cast
        self.choices = choices or []
        self._valid_values = [*self.flat, *[value for value, _ in self.choices]]

    def __call__(self, value: str) -> Any:
        transform = self.cast(value)
        if transform not in self._valid_values:
            raise ValueError(
                f"Value not in list: {value!r}; valid values are {self._valid_values!r}"
            )
        return transform
