"""`cfe auth`: sign in to CodingForEntrepreneurs from the command line.

Implements the OAuth 2.0 Device Authorization Grant against `<host>/api/cli/`.
Standard library only (urllib), so the base install stays typer + rich.
"""

import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from . import __version__

DEFAULT_HOST = "https://codingforentrepreneurs.com"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
TIMEOUT = 15
MAX_REDIRECTS = 5
REDIRECT_CODES = {301, 302, 303, 307, 308}
TERMINAL_ERRORS = {
    "expired_token": "The code expired before it was approved. Run `cfe login` again.",
    "access_denied": "Authorization was denied in the browser.",
    "invalid_grant": "The code is no longer valid. Run `cfe login` again.",
    "invalid_request": "The server rejected the request (invalid_request).",
    "unsupported_grant_type": "The server rejected the request (unsupported_grant_type).",
}

console = Console()
err_console = Console(stderr=True)

auth_app = typer.Typer(
    name="auth",
    help="Sign in to CodingForEntrepreneurs (like `gh auth`).",
    no_args_is_help=True,
)

HostOption = typer.Option(
    None,
    "--host",
    envvar="CFE_HOST",
    help=f"CFE server URL (default {DEFAULT_HOST}).",
)


class ApiError(Exception):
    """A request failed in a way worth showing to the user."""


# ---------------------------------------------------------------- storage


def config_dir() -> Path:
    override = os.environ.get("CFE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "cfe"


def hosts_file() -> Path:
    return config_dir() / "hosts.json"


def normalize_host(host: str | None) -> str:
    host = (host or DEFAULT_HOST).strip().rstrip("/")
    if "://" not in host:
        host = "https://" + host
    return host


def load_hosts() -> dict[str, Any]:
    try:
        data = json.loads(hosts_file().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_hosts(hosts: dict[str, Any]) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".hosts-", suffix=".tmp")
    try:
        os.chmod(tmp, 0o600)  # before the secret is written
        with os.fdopen(fd, "w") as fh:
            json.dump(hosts, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, hosts_file())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def store_token(host: str, token: str, user: dict[str, Any]) -> None:
    hosts = load_hosts()
    hosts[host] = {
        "token": token,
        "user": {k: user.get(k) for k in ("id", "email", "name")},
        "created_at": datetime.now(UTC).isoformat(),
    }
    save_hosts(hosts)


def stored_entry(host: str) -> dict[str, Any] | None:
    entry = load_hosts().get(host)
    return entry if isinstance(entry, dict) and entry.get("token") else None


def resolve_token(host: str) -> str | None:
    """CFE_TOKEN wins over the stored token."""
    env = os.environ.get("CFE_TOKEN", "").strip()
    if env:
        return env
    entry = stored_entry(host)
    return entry["token"] if entry else None


# ------------------------------------------------------------------- http


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface 3xx as HTTPError so api_request can follow them itself."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _send(req: urllib.request.Request, timeout: float):
    return _OPENER.open(req, timeout=timeout)


def _read_error_body(exc: urllib.error.HTTPError) -> bytes:
    try:
        return exc.read()
    except (OSError, TimeoutError):
        return b""


def _site(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def _follow(url: str, location: str) -> str:
    target = urllib.parse.urljoin(url, location)
    old, new = urllib.parse.urlsplit(url).scheme, urllib.parse.urlsplit(target).scheme
    if new not in ("http", "https") or (old == "https" and new != "https"):
        raise ApiError(f"Refusing to follow an insecure redirect to {target}.")
    return target


def api_request(
    method: str,
    host: str,
    path: str,
    payload: dict | None = None,
    token: str | None = None,
) -> tuple[int, Any, dict[str, str]]:
    """JSON in, JSON out. Returns (status, body, headers); HTTP errors do not raise.

    Redirects are followed with the method and body kept (urllib would turn a
    redirected POST into a GET). The token is only sent to the same site
    (www. and the bare domain count as one).
    """
    url = f"{host}/api/cli/{path.strip('/')}/"
    headers = {
        "User-Agent": f"cfe-cli/{__version__}",
        "Accept": "application/json",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for _ in range(MAX_REDIRECTS + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _send(req, TIMEOUT) as resp:
                status, raw, resp_headers = resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            status, resp_headers = exc.code, exc.headers
            # A redirect body is never needed, and some servers leave it open.
            raw = b"" if status in REDIRECT_CODES else _read_error_body(exc)
            exc.close()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ApiError(f"Could not reach {host} ({reason}).") from None
        resp_headers = {k.lower(): v for k, v in dict(resp_headers or {}).items()}
        location = resp_headers.get("location")
        if status not in REDIRECT_CODES or not location:
            break
        target = _follow(url, location)
        if _site(target) != _site(url):
            headers.pop("Authorization", None)
        if status == 303:
            method, data = "GET", None
            headers.pop("Content-Type", None)
        url = target
    else:
        raise ApiError(f"Too many redirects from {host}.")

    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    if status == 426:
        raise ApiError(f"cfe {__version__} is too old for this server. Run: cfe update")
    return status, body, resp_headers


# ------------------------------------------------------------------ login


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _fail(message: str, code: int = 1) -> "typer.Exit":
    err_console.print(f"[bold red]✗[/bold red] {message}", highlight=False)
    return typer.Exit(code=code)


def _retry_after(headers: dict[str, str], body: Any) -> int:
    for value in (headers.get("retry-after"), (body or {}).get("retry_after")):
        try:
            return max(1, int(float(value)))
        except (TypeError, ValueError):
            continue
    return 5


def _poll_for_token(host: str, device: dict[str, Any]) -> dict[str, Any]:
    interval = int(device.get("interval") or 5)
    expires_in = int(device.get("expires_in") or 600)
    waited = 0.0

    def wait(seconds: float) -> None:
        nonlocal waited
        time.sleep(seconds)
        waited += seconds

    payload = {"device_code": device["device_code"], "grant_type": GRANT_TYPE}
    with console.status("Waiting for authorization in your browser..."):
        while True:
            if waited >= expires_in:
                raise _fail(TERMINAL_ERRORS["expired_token"])
            wait(interval)
            status, body, headers = api_request("POST", host, "token", payload)
            body = body if isinstance(body, dict) else {}
            if status == 200 and body.get("access_token"):
                return body
            if status == 429:
                wait(_retry_after(headers, body))
                continue
            error = body.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                try:
                    interval = int(body["interval"])
                except (KeyError, TypeError, ValueError):
                    interval += 5
                continue
            if error in TERMINAL_ERRORS:
                raise _fail(TERMINAL_ERRORS[error])
            detail = body.get("error_description") or error or f"HTTP {status}"
            raise _fail(f"Unexpected response from the server: {detail}")


@auth_app.command("login")
def login(
    host: str | None = HostOption,
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the URL instead of opening a browser."
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Re-authenticate without asking."),
):
    """Authenticate this machine with your CFE account."""
    host = normalize_host(host)
    entry = stored_entry(host)
    if entry and not force:
        who = (entry.get("user") or {}).get("email") or "an account"
        console.print(f"Already logged in to {host} as {who}.", highlight=False)
        if not typer.confirm("Re-authenticate?", default=False):
            raise typer.Exit(code=0)

    try:
        status, device, _ = api_request(
            "POST",
            host,
            "device",
            {"client_name": f"cfe CLI {__version__}", "hostname": socket.gethostname()},
        )
        if status != 200 or not isinstance(device, dict) or "device_code" not in device:
            if status == 429:
                raise _fail("Too many login attempts. Wait a minute and try again.")
            raise _fail(f"Could not start login (HTTP {status}).")

        console.print(
            f"\nFirst copy your one-time code: [bold]{device['user_code']}[/bold]",
            highlight=False,
        )
        if no_browser or not _is_interactive():
            console.print(
                f"Then open this URL: {device['verification_uri_complete']}",
                highlight=False,
                soft_wrap=True,
            )
        else:
            input(f"Press Enter to open {device['verification_uri']} in your browser...")
            if not webbrowser.open(device["verification_uri_complete"]):
                console.print(
                    f"Could not open a browser. Open this URL: "
                    f"{device['verification_uri_complete']}",
                    highlight=False,
                    soft_wrap=True,
                )
        console.print()

        result = _poll_for_token(host, device)
    except ApiError as exc:
        raise _fail(str(exc))
    except (KeyboardInterrupt, EOFError):
        err_console.print("\nCancelled.")
        raise typer.Exit(code=130)

    user = result.get("user") or {}
    store_token(host, result["access_token"], user)
    console.print(
        f"[bold green]✓[/bold green] Logged in as {user.get('email', 'unknown')}",
        highlight=False,
    )


# ----------------------------------------------------------------- status


@auth_app.command("status")
def status_cmd(host: str | None = HostOption):
    """Show who you are logged in as and what you can access."""
    host = normalize_host(host)
    token = resolve_token(host)
    if not token:
        raise _fail(f"Not logged in to {host}. Run `cfe login`.")
    try:
        code, body, _ = api_request("GET", host, "me", token=token)
    except ApiError as exc:
        raise _fail(str(exc))
    if code == 401:
        raise _fail(f"The token for {host} is invalid or was revoked. Run `cfe login`.")
    if code != 200 or not isinstance(body, dict):
        raise _fail(f"Could not fetch your account (HTTP {code}).")

    access = body.get("access") or {}
    courses = access.get("courses") or []
    roadmaps = access.get("roadmaps") or []

    def summary(items: list) -> str:
        if not items:
            return "none"
        titles = ", ".join(i.get("title") or i.get("slug", "?") for i in items)
        return f"{len(items)} ({titles})"

    source = "CFE_TOKEN" if os.environ.get("CFE_TOKEN", "").strip() else "stored"
    console.print(f"[bold green]✓[/bold green] Logged in to {host}", highlight=False)
    rows = [
        ("Email", body.get("email")),
        ("Name", body.get("name") or "-"),
        ("Token", f"{token[:12]}… ({source})"),
        ("Subscription", "active" if access.get("active_subscription") else "none"),
        ("Lifetime", "yes" if access.get("lifetime") else "no"),
        ("Courses", summary(courses)),
        ("Roadmaps", summary(roadmaps)),
    ]
    for label, value in rows:
        console.print(f"  {label + ':':<14}{value}", highlight=False, markup=False)


# ----------------------------------------------------------------- logout


@auth_app.command("logout")
def logout(host: str | None = HostOption):
    """Revoke the token on the server and remove it from this machine."""
    host = normalize_host(host)
    entry = stored_entry(host)
    if not entry:
        err_console.print(f"Not logged in to {host}.", highlight=False)
        raise typer.Exit(code=1)
    try:
        code, _, _ = api_request("POST", host, "logout", token=entry["token"])
        if code not in (204, 200, 401):
            err_console.print(
                f"[yellow]Warning:[/yellow] the server returned HTTP {code}; "
                "the token may still be active. Revoke it from your account page.",
                highlight=False,
            )
    except ApiError as exc:
        err_console.print(
            f"[yellow]Warning:[/yellow] {exc} The token was not revoked on the server.",
            highlight=False,
        )
    hosts = load_hosts()
    hosts.pop(host, None)
    save_hosts(hosts)
    console.print(f"[bold green]✓[/bold green] Logged out of {host}", highlight=False)


# ------------------------------------------------------------------ token


@auth_app.command("token")
def token_cmd(host: str | None = HostOption):
    """Print the access token (for scripts: `$(cfe auth token)`)."""
    token = resolve_token(normalize_host(host))
    if not token:
        err_console.print("Not logged in. Run `cfe login`.")
        raise typer.Exit(code=1)
    typer.echo(token)
