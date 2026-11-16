"""Update checks and `cfe update` / `cfe upgrade`.

The check runs at most once a day in a background thread with a short timeout
and never blocks or fails a command. Standard library only.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from . import __version__

PACKAGE = "cfe"
PYPI_URL = "https://pypi.org/pypi/cfe/json"
CHECK_INTERVAL = 24 * 60 * 60
FETCH_TIMEOUT = 2.0
EXIT_WAIT = 1.0
SKIP_COMMANDS = {"update", "upgrade", "--version", "-V"}

err_console = Console(stderr=True)


# --------------------------------------------------------------- versions


def parse_version(value: str) -> tuple:
    """Loose PEP 440 parse: release numbers, then a pre-release marker.

    `0.1.0` > `0.1.0rc1` > `0.0.24`. Unparseable input sorts lowest.
    """
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)(.*)$", str(value or ""))
    if not match:
        return ((-1,), 0)
    release = tuple(int(p) for p in match.group(1).split("."))
    while len(release) > 1 and release[-1] == 0:
        release = release[:-1]
    suffix = match.group(2).strip().lower()
    is_final = 1 if not suffix or suffix.startswith((".post", "post", "+")) else 0
    return (release, is_final)


def is_newer(candidate: str | None, current: str = __version__) -> bool:
    return bool(candidate) and parse_version(candidate) > parse_version(current)


# ------------------------------------------------------------------ cache


def cache_dir() -> Path:
    override = os.environ.get("CFE_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "cfe"


def cache_file() -> Path:
    return cache_dir() / "update-check.json"


def read_cache() -> dict[str, Any]:
    try:
        data = json.loads(cache_file().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_cache(data: dict[str, Any]) -> None:
    try:
        directory = cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".update-", suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, cache_file())
    except OSError:
        pass


# ------------------------------------------------------------------ fetch


def _get_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"cfe-cli/{__version__}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_latest(host: str | None = None, timeout: float = FETCH_TIMEOUT) -> dict[str, Any]:
    """Return {latest, min_supported, message}. Tries the CFE site, then PyPI."""
    from .auth import normalize_host

    try:
        body = _get_json(f"{normalize_host(host)}/api/cli/version/", timeout)
        if isinstance(body, dict) and body.get("latest"):
            return {
                "latest": str(body["latest"]),
                "min_supported": body.get("min_supported"),
                "message": body.get("message"),
            }
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        pass
    body = _get_json(PYPI_URL, timeout)
    return {"latest": str(body["info"]["version"]), "min_supported": None, "message": None}


def refresh_cache(host: str | None = None, timeout: float = FETCH_TIMEOUT) -> dict[str, Any]:
    info = fetch_latest(host, timeout)
    info["checked_at"] = time.time()
    write_cache(info)
    return info


# -------------------------------------------------------- background check


def checks_enabled() -> bool:
    if os.environ.get("CFE_NO_UPDATE_CHECK", "").strip().lower() not in ("", "0", "false"):
        return False
    if os.environ.get("CI"):
        return False
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def start_background_check(argv: list[str]) -> threading.Thread | None:
    """Kick off a refresh if the cache is stale. Returns the thread, or None."""
    if not checks_enabled() or SKIP_COMMANDS.intersection(argv[:1] + argv[-1:]):
        return None
    if time.time() - float(read_cache().get("checked_at") or 0) < CHECK_INTERVAL:
        return None

    def run() -> None:
        try:
            refresh_cache(os.environ.get("CFE_HOST"))
        except Exception:  # noqa: BLE001 (a failed check must never surface)
            # Record the attempt so an offline machine does not retry every run.
            data = read_cache()
            data["checked_at"] = time.time()
            write_cache(data)

    thread = threading.Thread(target=run, name="cfe-update-check", daemon=True)
    thread.start()
    return thread


def notice_text(info: dict[str, Any], current: str = __version__) -> str | None:
    latest = info.get("latest")
    min_supported = info.get("min_supported")
    lines = []
    if min_supported and is_newer(min_supported, current):
        lines.append(
            f"[bold red]cfe {current} is no longer supported[/bold red] "
            f"(minimum {min_supported}). Run: [bold]cfe update[/bold]"
        )
    elif is_newer(latest, current):
        lines.append(
            f"[yellow]cfe {latest} is available[/yellow] (you have {current}). "
            "Run: [bold]cfe update[/bold]"
        )
    else:
        return None
    if info.get("message"):
        lines.append(str(info["message"]))
    return "\n".join(lines)


def finish(thread: threading.Thread | None, argv: list[str]) -> None:
    """Print the update notice (from cache) after the command has run."""
    if not checks_enabled() or SKIP_COMMANDS.intersection(argv[:1] + argv[-1:]):
        return
    if thread is not None:
        thread.join(EXIT_WAIT)
    try:
        text = notice_text(read_cache())
        if text:
            err_console.print(f"\n{text}", highlight=False)
    except Exception:  # noqa: BLE001, S110 (a broken cache must never fail a command)
        pass


# --------------------------------------------------------- install method


def _editable_install() -> bool:
    try:
        raw = metadata.distribution(PACKAGE).read_text("direct_url.json")
        return bool(raw and json.loads(raw).get("dir_info", {}).get("editable"))
    except Exception:  # noqa: BLE001 (missing or malformed metadata means not editable)
        return False


def _is_under(path: Path, parent: str | None) -> bool:
    if not parent:
        return False
    try:
        path.resolve().relative_to(Path(parent).expanduser().resolve())
        return True
    except (ValueError, OSError):
        return False


def _uv_dir(kind: str) -> str | None:
    """`uv tool dir` or `uv cache dir`, if uv is installed."""
    uv = shutil.which("uv")
    if not uv:
        return None
    args = [uv, "tool", "dir"] if kind == "tool" else [uv, "cache", "dir"]
    try:
        out = subprocess.run(args, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def detect_install() -> tuple[str, list[str] | None]:
    """Return (method, upgrade command). Command is None when there is nothing to run."""
    prefix = Path(sys.prefix)
    parts = {p.lower() for p in prefix.parts}
    uv = shutil.which("uv")

    if _editable_install():
        return "source", None
    if _is_under(prefix, _uv_dir("tool")) or ("uv" in parts and "tools" in parts):
        return "uv tool", [uv or "uv", "tool", "upgrade", PACKAGE]
    if _is_under(prefix, _uv_dir("cache")) or any(
        p.startswith(("archive-v", "builds-v")) for p in parts
    ):
        return "uvx", None
    if "pipx" in parts:
        return "pipx", [shutil.which("pipx") or "pipx", "upgrade", PACKAGE]
    if uv:
        return "pip", [uv, "pip", "install", "--python", sys.executable, "--upgrade", PACKAGE]
    return "pip", [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]


# ---------------------------------------------------------------- command


def update(
    check: bool = typer.Option(
        False, "--check", help="Only check for a newer version; do not install."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the upgrade command instead of running it."
    ),
):
    """Update cfe to the latest version."""
    try:
        info = refresh_cache(os.environ.get("CFE_HOST"), timeout=10)
    except Exception as exc:  # noqa: BLE001 (network, JSON, or PyPI shape errors)
        err_console.print(f"[bold red]✗[/bold red] Could not check for updates ({exc}).")
        raise typer.Exit(code=1)

    latest = info["latest"]
    if not is_newer(latest):
        err_console.print(
            f"[bold green]✓[/bold green] cfe {__version__} is the latest version.",
            highlight=False,
        )
        return
    err_console.print(f"cfe {latest} is available (you have {__version__}).", highlight=False)
    if check:
        return

    method, command = detect_install()
    if method == "source":
        err_console.print("cfe is installed from source (editable). Pull and re-sync instead.")
        return
    if method == "uvx":
        err_console.print(
            "You are running cfe through uvx. Use `uvx cfe@latest` to always get "
            "the newest version, or install it with `uv tool install cfe`."
        )
        return

    shown = " ".join(command)
    if dry_run:
        typer.echo(shown)
        return
    err_console.print(f"[dim]→[/dim] {shown}", highlight=False, markup=True)
    try:
        result = subprocess.run(command, check=False, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        err_console.print(f"[bold red]✗[/bold red] Update failed: {exc}", highlight=False)
        raise typer.Exit(code=1)
    if result.returncode != 0:
        err_console.print(
            f"[bold red]✗[/bold red] Update failed (exit {result.returncode}).", highlight=False
        )
        raise typer.Exit(code=result.returncode)
    err_console.print(f"[bold green]✓[/bold green] Updated cfe to {latest}.", highlight=False)
