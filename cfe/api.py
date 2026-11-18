"""Shared plumbing for the member service commands (`cfe uptime`, `cfe mail`, `cfe domains`).

Every call goes to `<host>/api/<path>` with the `cfe login` token (or CFE_TOKEN).
Team scope follows the website: lists use the `X-Team-Id` header, creates send
an explicit `team` in the body when `--team` is given.

Exit codes used across the service commands:

    0  ok (or the condition you waited for happened)
    1  error, or a check found failures (see each command)
    2  usage error (typer)
    124  `--timeout` passed before the condition was met
"""

import json
import re
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import typer
from rich.console import Console

from .auth import ApiError, HostOption, http_request, normalize_host, resolve_token

console = Console()
err_console = Console(stderr=True)

EXIT_TIMEOUT = 124
PERSONAL = "personal"

TeamOption = typer.Option(
    None,
    "--team",
    "-t",
    envvar="CFE_TEAM",
    help="Team uid, slug or name ('personal' for your own). Default: Personal.",
)
JsonOption = typer.Option(False, "--json", help="Print the raw JSON response.")
__all__ = ["HostOption", "JsonOption", "Session", "TeamOption", "session"]


def fail(message: str, code: int = 1) -> typer.Exit:
    err_console.print(f"[bold red]✗[/bold red] {message}", highlight=False)
    return typer.Exit(code=code)


def ok(message: str) -> None:
    console.print(f"[bold green]✓[/bold green] {message}", highlight=False)


def info(message: str) -> None:
    """Progress lines go to stderr so `--json` stdout stays parseable."""
    err_console.print(message, highlight=False)


def print_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def emit_line(data: Any) -> None:
    """One NDJSON line, flushed, for `watch`-style streams."""
    sys.stdout.write(json.dumps(data, default=str) + "\n")
    sys.stdout.flush()


# ------------------------------------------------------------------ session


@dataclass
class Session:
    host: str
    token: str
    team_ref: str | None = None
    _team: Any = field(default=None, init=False)
    _team_resolved: bool = field(default=False, init=False)

    # -- team scope

    @property
    def team_uid(self) -> str | None:
        """The resolved team uid, or None for Personal."""
        if not self._team_resolved:
            self._team = self._resolve_team(self.team_ref)
            self._team_resolved = True
        return self._team

    def _resolve_team(self, ref: str | None) -> str | None:
        if not ref or ref.strip().lower() == PERSONAL:
            return None
        ref = ref.strip()
        try:
            return str(uuid.UUID(ref))
        except ValueError:
            pass
        teams = self.get("teams/", scoped=False) or []
        wanted = ref.lower()
        for team in teams if isinstance(teams, list) else []:
            if wanted in ((team.get("slug") or "").lower(), (team.get("name") or "").lower()):
                return None if team.get("is_personal") else str(team.get("uid"))
        names = ", ".join(t.get("name", "?") for t in teams if not t.get("is_personal"))
        raise fail(f"No team matches '{ref}'." + (f" Your teams: {names}" if names else ""))

    def team_body(self) -> dict[str, Any]:
        """`{"team": ...}` for create calls when --team was given, else {} (server default)."""
        if self.team_ref is None:
            return {}
        return {"team": self.team_uid}

    # -- http

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        params: dict[str, Any] | None = None,
        ok_statuses: tuple[int, ...] = (200, 201, 204),
        raw: bool = False,
        scoped: bool = True,
    ) -> Any:
        url = f"/api/{path.lstrip('/')}"
        query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        if query:
            url += "?" + urlencode(query)
        headers = {}
        if scoped and self.team_ref is not None and self.team_uid:
            headers["X-Team-Id"] = self.team_uid
        try:
            status, body, resp_headers = http_request(
                method, self.host, url, payload, self.token, headers=headers, raw=raw
            )
        except ApiError as exc:
            raise fail(str(exc))
        if status in ok_statuses:
            return body
        raise http_error(status, body, resp_headers)

    def get(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, payload: dict | None = None, **kw) -> Any:
        return self.request("POST", path, payload if payload is not None else {}, **kw)

    def patch(self, path: str, payload: dict, **kw) -> Any:
        return self.request("PATCH", path, payload, **kw)

    def delete(self, path: str, payload: dict | None = None, **kw) -> Any:
        return self.request("DELETE", path, payload, **kw)


class HttpError(typer.Exit):
    """A non-2xx response, already printed. Carries status and body for callers."""

    def __init__(self, status: int, body: Any):
        super().__init__(code=1)
        self.status = status
        self.body = body if isinstance(body, dict) else {}


def error_detail(body: Any) -> str | None:
    if isinstance(body, dict):
        if isinstance(body.get("detail"), str):
            return body["detail"]
        parts = []
        for key, value in body.items():
            if isinstance(value, list) and value and isinstance(value[0], str):
                parts.append(f"{key}: {' '.join(value)}")
        if parts:
            return "; ".join(parts)
    return None


def http_error(status: int, body: Any, headers: dict[str, str]) -> HttpError:
    detail = error_detail(body)
    if status == 401 or (
        status == 403 and detail == "Authentication credentials were not provided."
    ):
        message = "Your login is missing, invalid or revoked. Run `cfe login`."
    elif status == 429:
        wait = headers.get("retry-after") or (
            body.get("retry_after") if isinstance(body, dict) else None
        )
        message = detail or "Too many requests."
        if wait:
            message += f" Retry in {wait}s."
    else:
        message = detail or f"Request failed (HTTP {status})."
    err_console.print(f"[bold red]✗[/bold red] {message}", highlight=False)
    return HttpError(status, body)


def session(host: str | None, team: str | None = None) -> Session:
    host = normalize_host(host)
    token = resolve_token(host)
    if not token:
        raise fail(f"Not logged in to {host}. Run `cfe login`.")
    return Session(host=host, token=token, team_ref=team)


# ------------------------------------------------------------------ polling


def poll(
    fetch: Callable[[], Any],
    done: Callable[[Any], bool],
    *,
    interval: float,
    timeout: float | None,
    on_tick: Callable[[Any], None] | None = None,
    status: str | None = None,
) -> Any:
    """Call `fetch` until `done(result)`. Raises typer.Exit(124) after `timeout` seconds."""
    start = time.monotonic()

    def loop():
        while True:
            result = fetch()
            if on_tick:
                on_tick(result)
            if done(result):
                return result
            if timeout is not None and time.monotonic() - start + interval > timeout:
                raise fail(f"Timed out after {int(timeout)}s.", code=EXIT_TIMEOUT)
            time.sleep(interval)

    if status and err_console.is_terminal:
        with err_console.status(status):
            return loop()
    return loop()


# ------------------------------------------------------------------ formatting


def ago(value: Any) -> str:
    """'5m ago' for an ISO string or ms epoch; '-' when missing."""
    when = parse_time(value)
    if when is None:
        return "-"
    seconds = int((datetime.now(UTC) - when).total_seconds())
    if seconds < 0:
        return "in " + _span(-seconds)
    return _span(seconds) + " ago"


def _span(seconds: int) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def when(value: Any) -> str:
    """'2026-11-18 14:05 UTC' for an ISO string or ms epoch; '-' when missing."""
    parsed = parse_time(value)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if parsed else "-"


def pct(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("percent")
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def looks_like_hostname(ref: str) -> bool:
    return "://" in ref or bool(HOSTNAME_RE.match(ref))


def hostname_of(ref: str) -> str:
    ref = ref.strip().lower()
    if "://" in ref:
        ref = ref.split("://", 1)[1]
    return ref.split("/", 1)[0].split(":", 1)[0]


# ------------------------------------------------------------------ site scan

STATUS_MARK = {
    "pass": "[green]✓[/green]",
    "warn": "[yellow]![/yellow]",
    "fail": "[red]✗[/red]",
    "na": "[dim]-[/dim]",
}


def print_sitescan(result: dict, show_all: bool = False) -> None:
    score = result.get("score")
    console.print(
        f"[bold]{result.get('hostname', '?')}[/bold]  score {score if score is not None else '-'}"
        + ("  [dim](cached)[/dim]" if result.get("cached") else ""),
        highlight=False,
    )
    for section in result.get("sections") or []:
        s_score = section.get("score")
        console.print(
            f"\n[bold]{section.get('title', section.get('id'))}[/bold]  "
            f"{s_score if s_score is not None else '-'}",
            highlight=False,
        )
        for check in section.get("checks") or []:
            state = check.get("status")
            if not show_all and state in ("pass", "na"):
                continue
            line = f"  {STATUS_MARK.get(state, '?')} {check.get('title')}"
            if check.get("detail"):
                line += f" [dim]({check['detail']})[/dim]"
            console.print(line, highlight=False)
            if check.get("fix"):
                console.print(f"      fix: {check['fix']}", highlight=False, markup=False)


def sitescan_exit_code(result: dict, fail_on: str) -> int:
    statuses = [
        c.get("status") for s in result.get("sections") or [] for c in s.get("checks") or []
    ]
    if fail_on == "fail" and "fail" in statuses:
        return 1
    if fail_on == "warn" and ("fail" in statuses or "warn" in statuses):
        return 1
    return 0


FailOnOption = typer.Option(
    "never",
    "--fail-on",
    help="Exit 1 when any check is at this level or worse: fail, warn, never.",
)


def check_fail_on(value: str) -> str:
    if value not in ("fail", "warn", "never"):
        raise fail("--fail-on must be fail, warn or never.", code=2)
    return value
