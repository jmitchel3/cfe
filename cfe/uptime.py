"""`cfe uptime`: CFE uptime monitors from the terminal.

Wraps `/api/uptime/monitors/` on the CFE site. `watch` and `wait` turn monitor
state into a signal for scripts and agents: `wait` blocks until a condition
holds, `watch` streams one JSON line per change and can run a command for each.
"""

import json
import os
import subprocess
import time
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.table import Table

from .api import (
    EXIT_TIMEOUT,
    FailOnOption,
    HostOption,
    HttpError,
    JsonOption,
    Session,
    TeamOption,
    ago,
    check_fail_on,
    console,
    emit_line,
    fail,
    hostname_of,
    info,
    looks_like_hostname,
    ok,
    pct,
    poll,
    print_json,
    print_sitescan,
    session,
    sitescan_exit_code,
    when,
)

uptime_app = typer.Typer(
    name="uptime",
    help="Uptime monitors on CodingForEntrepreneurs: add, verify, inspect, watch.",
    no_args_is_help=True,
)

MonitorArg = typer.Argument(..., help="Monitor id, hostname or URL.")
EVENTS = ("down", "up", "verified", "paused", "resumed", "added", "removed")


# ------------------------------------------------------------------ helpers


def list_monitors(s: Session) -> dict:
    body = s.get("uptime/monitors/")
    return body if isinstance(body, dict) else {"monitors": []}


def resolve_id(s: Session, ref: str) -> str:
    """A monitor id from an id, hostname or URL (hostnames are looked up in the active scope)."""
    if not looks_like_hostname(ref):
        return ref
    host = hostname_of(ref)
    for m in list_monitors(s).get("monitors") or []:
        if (m.get("hostname") or "").lower() == host:
            return m["id"]
    scope = "Personal" if not s.team_uid else "this team"
    raise fail(f"No monitor for {host} in {scope}. Pass --team, or use the monitor id.")


def display_state(m: dict) -> str:
    if m.get("hold"):
        return "held"
    if m.get("paused"):
        return "paused"
    if not m.get("verified"):
        return "unverified"
    return m.get("state") or "unknown"


STATE_STYLE = {
    "up": "green",
    "down": "bold red",
    "unverified": "yellow",
    "paused": "dim",
    "held": "yellow",
}


def styled(state: str) -> str:
    style = STATE_STYLE.get(state)
    return f"[{style}]{state}[/{style}]" if style else state


def print_verification(v: dict | None) -> None:
    if not v:
        return
    dns = v.get("dns") or {}
    file = v.get("file") or {}
    console.print("\nProve you own this hostname with either:", highlight=False)
    if dns:
        console.print(
            f"  DNS  {dns.get('type', 'TXT')} {dns.get('name')}", highlight=False, markup=False
        )
        console.print(f"       value: {dns.get('value')}", highlight=False, markup=False)
    if file:
        console.print(f"  File {file.get('url')}", highlight=False, markup=False)
        console.print(f"       contents: {file.get('contents')}", highlight=False, markup=False)
    console.print("Then run: cfe uptime verify <id> --wait", highlight=False, markup=False)


def summary(m: dict) -> dict:
    keys = (
        "id",
        "name",
        "hostname",
        "url",
        "state",
        "status",
        "verified",
        "paused",
        "open_incident",
        "latency_ms",
        "last_checked_at",
        "uptime_24h",
        "uptime_7d",
    )
    return {k: m.get(k) for k in keys} | {"display_state": display_state(m)}


# ------------------------------------------------------------------ commands


@uptime_app.command("ls")
def ls_cmd(
    host: str | None = HostOption, team: str | None = TeamOption, as_json: bool = JsonOption
):
    """List monitors in Personal or a team."""
    s = session(host, team)
    body = list_monitors(s)
    if as_json:
        return print_json(body)
    monitors = body.get("monitors") or []
    if not monitors:
        console.print(
            "No monitors yet. Add one: cfe uptime add https://example.com",
            highlight=False,
            markup=False,
        )
        return
    table = Table(box=None, pad_edge=False)
    for col in ("ID", "Name", "Hostname", "State", "24h", "7d", "Latency", "Checked"):
        table.add_column(col)
    for m in monitors:
        latency = f"{m['latency_ms']}ms" if m.get("latency_ms") is not None else "-"
        table.add_row(
            m.get("id", "?"),
            m.get("name") or "-",
            m.get("hostname") or "-",
            styled(display_state(m)),
            pct(m.get("uptime_24h")),
            pct(m.get("uptime_7d")),
            latency,
            ago(m.get("last_checked_at")),
        )
    console.print(table)
    limit = body.get("limit")
    if limit is not None:
        console.print(f"[dim]{len(monitors)} of {limit} monitors[/dim]", highlight=False)


@uptime_app.command("add")
def add_cmd(
    url: str = typer.Argument(..., help="URL to monitor, e.g. https://example.com"),
    name: str | None = typer.Option(None, "--name", "-n", help="Display name."),
    wait: bool = typer.Option(False, "--wait", help="Wait until the hostname is verified."),
    timeout: int = typer.Option(900, "--timeout", help="Seconds to wait with --wait."),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Add a monitor. Prints the DNS or file proof to set up unless a verified domain covers it."""
    if "://" not in url:
        url = "https://" + url
    s = session(host, team)
    body = s.post(
        "uptime/monitors/", {"url": url, **({"name": name} if name else {}), **s.team_body()}
    )
    monitor = body.get("monitor") or {}
    if as_json and not wait:
        return print_json(body)
    mid = monitor.get("id")
    if body.get("verified") or monitor.get("verified"):
        ok(
            f"Added {monitor.get('hostname')} ({mid}), already verified. Checks start within a minute."
        )
        return
    ok(f"Added {monitor.get('hostname')} ({mid}), waiting for verification.")
    print_verification(body.get("verification"))
    console.print(f"Provider-specific steps: cfe uptime dns {mid}", highlight=False, markup=False)
    if wait:
        result = wait_verified(s, mid, interval=15, timeout=timeout)
        if as_json:
            print_json(result)


def wait_verified(s: Session, mid: str, interval: int, timeout: int | None) -> dict:
    def attempt():
        try:
            return s.post(f"uptime/monitors/{mid}/verify/", ok_statuses=(200, 422))
        except HttpError as exc:
            if exc.status == 400:  # Worker says "not yet" as a 400 with detail
                return {"verified": False}
            raise

    result = poll(
        attempt,
        lambda r: bool((r or {}).get("verified")),
        interval=interval,
        timeout=timeout,
        status="Waiting for the verification record...",
    )
    ok(f"Verified ({result.get('method') or 'dns'}).")
    return result


@uptime_app.command("verify")
def verify_cmd(
    monitor: str = MonitorArg,
    wait: bool = typer.Option(False, "--wait", help="Keep checking until verified."),
    interval: int = typer.Option(15, "--interval", help="Seconds between attempts with --wait."),
    timeout: int = typer.Option(
        900, "--timeout", help="Give up after this many seconds (exit 124)."
    ),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Check the DNS TXT record or .well-known file now."""
    s = session(host, team)
    mid = resolve_id(s, monitor)
    if wait:
        result = wait_verified(s, mid, interval, timeout)
        return print_json(result) if as_json else None
    try:
        result = s.post(f"uptime/monitors/{mid}/verify/", ok_statuses=(200, 422))
    except HttpError as exc:
        raise typer.Exit(code=1) from exc
    if as_json:
        print_json(result)
    elif result.get("verified"):
        ok(f"Verified ({result.get('method') or 'dns'}).")
    else:
        console.print("[yellow]Not verified yet.[/yellow]", highlight=False)
        print_verification(result.get("verification"))
    if not result.get("verified"):
        raise typer.Exit(code=1)


@uptime_app.command("dns")
def dns_cmd(
    monitor: str = MonitorArg,
    open_browser: bool = typer.Option(
        False, "--open", help="Open the Domain Connect link, if any."
    ),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Show the exact DNS record to add at your provider, with a one-click link where supported."""
    s = session(host, team)
    mid = resolve_id(s, monitor)
    body = s.get(f"uptime/monitors/{mid}/dns-setup/")
    if as_json:
        return print_json(body)
    if body.get("verified"):
        ok(
            f"{body.get('hostname')} is verified"
            + (" (just now)." if body.get("verified_now") else ".")
        )
        return
    record = body.get("record") or {}
    provider = body.get("provider") or {}
    console.print(f"[bold]{body.get('hostname')}[/bold] (zone {body.get('zone')})", highlight=False)
    console.print(f"\nAdd a {record.get('type', 'TXT')} record:", highlight=False)
    console.print(
        f"  Name:  {record.get('host')}   (full: {record.get('fqdn')})",
        highlight=False,
        markup=False,
    )
    console.print(f"  Value: {record.get('value')}", highlight=False, markup=False)
    if provider:
        console.print(f"\nDNS provider: {provider.get('name')}", highlight=False, markup=False)
        for i, step in enumerate(provider.get("steps") or [], 1):
            console.print(f"  {i}. {step}", highlight=False, markup=False)
    dc = body.get("domain_connect") or {}
    if dc.get("available") and dc.get("apply_url"):
        console.print(
            f"\nOne click with {dc.get('provider_name')}: {dc['apply_url']}",
            highlight=False,
            markup=False,
            soft_wrap=True,
        )
        if open_browser:
            webbrowser.open(dc["apply_url"])
    console.print(f"\nThen: cfe uptime verify {mid} --wait", highlight=False, markup=False)


@uptime_app.command("show")
def show_cmd(
    monitor: str = MonitorArg,
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Monitor detail: state, uptime, open incident, recent checks and incidents."""
    s = session(host, team)
    mid = resolve_id(s, monitor)
    body = s.get(f"uptime/monitors/{mid}/")
    if as_json:
        return print_json(body)
    m = body.get("monitor") or {}
    uptime = body.get("uptime") or {}
    console.print(f"[bold]{m.get('name')}[/bold]  {m.get('url')}", highlight=False)
    rows = [
        ("ID", m.get("id")),
        ("State", styled(display_state(m))),
        (
            "Uptime",
            f"24h {pct(uptime.get('24h'))}  7d {pct(uptime.get('7d'))}  30d {pct(uptime.get('30d'))}",
        ),
        ("Latency", f"{m['latency_ms']}ms" if m.get("latency_ms") is not None else "-"),
        ("Last check", ago(m.get("last_checked_at"))),
        ("Since", body.get("monitoring_since") or "-"),
        ("Status page", m.get("status_page_url") or "-"),
    ]
    if m.get("hold"):
        rows.append(("Hold", m["hold"].get("message")))
    for label, value in rows:
        console.print(f"  {label + ':':<13}{value}", highlight=False)
    incident = m.get("open_incident")
    if incident:
        console.print(
            f"\n[bold red]Open incident[/bold red] since {when(incident.get('started_at'))}: {incident.get('cause') or ''}",
            highlight=False,
        )
    if not m.get("verified"):
        print_verification(m.get("verification"))
    checks = body.get("checks")
    checks = checks.get("checks") if isinstance(checks, dict) else checks
    if checks:
        console.print("\nRecent checks", highlight=False)
        print_checks(checks[:10])
    incidents = body.get("incidents")
    incidents = incidents.get("incidents") if isinstance(incidents, dict) else incidents
    if incidents:
        console.print("\nIncidents", highlight=False)
        for inc in incidents[:5]:
            end = when(inc.get("resolved_at")) if inc.get("resolved_at") else "ongoing"
            console.print(
                f"  {when(inc.get('started_at'))} -> {end}  {inc.get('cause') or ''}",
                highlight=False,
                markup=False,
            )


def print_checks(checks: list[dict]) -> None:
    table = Table(box=None, pad_edge=False)
    for col in ("When", "Result", "HTTP", "Latency", "Error"):
        table.add_column(col)
    for c in checks:
        table.add_row(
            when(c.get("checked_at")),
            "[green]up[/green]" if c.get("ok") else "[red]down[/red]",
            str(c.get("status_code") or "-"),
            f"{c['latency_ms']}ms" if c.get("latency_ms") is not None else "-",
            c.get("error") or "",
        )
    console.print(table)


@uptime_app.command("checks")
def checks_cmd(
    monitor: str = MonitorArg,
    status: str | None = typer.Option(None, "--status", help="Only 'up' or 'down' checks."),
    limit: int | None = typer.Option(None, "--limit", help="Rows per page."),
    since: str | None = typer.Option(None, "--from", help="Start (ISO 8601)."),
    until: str | None = typer.Option(None, "--to", help="End (ISO 8601)."),
    cursor: str | None = typer.Option(None, "--cursor", help="Next page cursor."),
    csv: Path | None = typer.Option(
        None, "--csv", help="Write the range as CSV to this file ('-' for stdout)."
    ),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """The check log, newest first. --csv exports up to 50,000 rows."""
    s = session(host, team)
    mid = resolve_id(s, monitor)
    params = {"status": status, "from": since, "to": until}
    if csv is not None:
        data = s.get(f"uptime/monitors/{mid}/checks.csv", params=params, raw=True)
        if str(csv) == "-":
            typer.echo(data.decode("utf-8", "replace"), nl=False)
        else:
            csv.write_bytes(data)
            ok(f"Wrote {csv}")
        return
    body = s.get(
        f"uptime/monitors/{mid}/checks/", params={**params, "limit": limit, "cursor": cursor}
    )
    if as_json:
        return print_json(body)
    print_checks(body.get("checks") or [])
    if body.get("next_cursor"):
        console.print(f"[dim]More: --cursor {body['next_cursor']}[/dim]", highlight=False)


@uptime_app.command("history")
def history_cmd(
    monitor: str = MonitorArg,
    days: int = typer.Option(30, "--days", help="Days of daily rollups (max 400)."),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Daily uptime with incidents and DNS changes."""
    s = session(host, team)
    mid = resolve_id(s, monitor)
    body = s.get(f"uptime/monitors/{mid}/history/", params={"days": days})
    if as_json:
        return print_json(body)
    table = Table(box=None, pad_edge=False)
    for col in ("Day", "Uptime", "Checks", "Failures", "Avg", "p95", "Incidents"):
        table.add_column(col)
    for d in body.get("days") or []:
        table.add_row(
            str(d.get("day") or "-"),
            pct(d.get("uptime_percent")),
            str(d.get("checks", "-")),
            str(d.get("failures", "-")),
            f"{d['avg_latency_ms']}ms" if d.get("avg_latency_ms") is not None else "-",
            f"{d['p95_latency_ms']}ms" if d.get("p95_latency_ms") is not None else "-",
            str(d.get("incidents") or ""),
        )
    console.print(table)
    for e in body.get("dns_events") or []:
        kind = "notable DNS change" if e.get("notable") else "DNS change"
        console.print(f"{when(e.get('detected_at'))}  {kind}", highlight=False, markup=False)


def simple_action(action: str, verb: str, doc: str):
    def command(
        monitor: str = MonitorArg,
        host: str | None = HostOption,
        team: str | None = TeamOption,
        as_json: bool = JsonOption,
    ):
        s = session(host, team)
        mid = resolve_id(s, monitor)
        body = s.post(f"uptime/monitors/{mid}/{action}/")
        if as_json:
            return print_json(body)
        ok(f"{verb} {(body.get('monitor') or {}).get('hostname') or mid}.")

    command.__doc__ = doc
    return command


uptime_app.command("pause")(simple_action("pause", "Paused", "Pause checks on a monitor."))
uptime_app.command("resume")(simple_action("resume", "Resumed", "Resume a paused monitor."))


@uptime_app.command("rename")
def rename_cmd(
    monitor: str = MonitorArg,
    name: str = typer.Argument(...),
    host: str | None = HostOption,
    team: str | None = TeamOption,
):
    """Rename a monitor."""
    s = session(host, team)
    mid = resolve_id(s, monitor)
    s.patch(f"uptime/monitors/{mid}/", {"name": name})
    ok(f"Renamed to {name}.")


@uptime_app.command("rm")
def rm_cmd(
    monitor: str = MonitorArg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    host: str | None = HostOption,
    team: str | None = TeamOption,
):
    """Delete a monitor and all its history."""
    s = session(host, team)
    mid = resolve_id(s, monitor)
    if not yes and not typer.confirm(f"Delete monitor {mid} and all its history?", default=False):
        raise typer.Exit(code=1)
    s.delete(f"uptime/monitors/{mid}/")
    ok(f"Deleted {mid}.")


@uptime_app.command("scan")
def scan_cmd(
    monitor: str = MonitorArg,
    show_all: bool = typer.Option(False, "--all", help="Also list passing checks."),
    fail_on: str = FailOnOption,
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Site scan: LLM and search visibility, marketing, email deliverability, security."""
    fail_on = check_fail_on(fail_on)
    s = session(host, team)
    mid = resolve_id(s, monitor)
    result = s.post(f"uptime/monitors/{mid}/sitescan/")
    if as_json:
        print_json(result)
    else:
        print_sitescan(result, show_all)
    raise typer.Exit(code=sitescan_exit_code(result, fail_on))


@uptime_app.command("status")
def status_cmd(
    monitors: list[str] | None = typer.Argument(
        None, help="Monitor ids or hostnames (default: all)."
    ),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """One line per monitor. Exits 1 if any is down: use it as a health gate."""
    s = session(host, team)
    selected = select(list_monitors(s).get("monitors") or [], monitors)
    if as_json:
        print_json([summary(m) for m in selected])
    else:
        for m in selected:
            console.print(
                f"{styled(display_state(m)):<20} {m.get('hostname')}  [dim]{m.get('id')}[/dim]",
                highlight=False,
            )
    if any(display_state(m) == "down" for m in selected):
        raise typer.Exit(code=1)


def select(monitors: list[dict], refs: list[str] | None) -> list[dict]:
    if not refs:
        return monitors
    wanted = {hostname_of(r) if looks_like_hostname(r) else r for r in refs}
    picked = [
        m for m in monitors if m.get("id") in wanted or (m.get("hostname") or "").lower() in wanted
    ]
    if not picked:
        raise fail(
            "None of those monitors are in this scope. Pass --team, or check `cfe uptime ls`."
        )
    return picked


# ------------------------------------------------------------------ signals


def diff_events(before: dict[str, dict], after: dict[str, dict]) -> list[dict]:
    """Events between two snapshots of {id: monitor}."""
    now = datetime.now(UTC).isoformat()
    events = []

    def event(name: str, m: dict, prev: dict | None):
        events.append(
            {
                "event": name,
                "at": now,
                "monitor": summary(m),
                "previous": {"state": display_state(prev)} if prev else None,
            }
        )

    for mid, m in after.items():
        prev = before.get(mid)
        if prev is None:
            event("added", m, None)
            continue
        old, new = display_state(prev), display_state(m)
        if old == new:
            continue
        if not prev.get("verified") and m.get("verified"):
            event("verified", m, prev)
        if new == "down":
            event("down", m, prev)
        elif new == "up" and old in ("down", "paused", "held"):
            event("resumed" if old != "down" else "up", m, prev)
        elif new in ("paused", "held"):
            event("paused", m, prev)
    for mid, m in before.items():
        if mid not in after:
            event("removed", m, None)
    return events


def run_hook(command: str, ev: dict) -> None:
    m = ev["monitor"]
    env = {
        **os.environ,
        "CFE_EVENT": ev["event"],
        "CFE_MONITOR_ID": str(m.get("id") or ""),
        "CFE_MONITOR_NAME": str(m.get("name") or ""),
        "CFE_MONITOR_HOSTNAME": str(m.get("hostname") or ""),
        "CFE_MONITOR_URL": str(m.get("url") or ""),
        "CFE_MONITOR_STATE": m.get("display_state") or "",
    }
    try:
        result = subprocess.run(
            command, shell=True, input=json.dumps(ev), text=True, env=env, check=False
        )
    except OSError as exc:
        info(f"--exec failed to start: {exc}")
        return
    if result.returncode:
        info(f"--exec exited {result.returncode} for {ev['event']} {m.get('hostname')}")


@uptime_app.command("watch")
def watch_cmd(
    monitors: list[str] | None = typer.Argument(
        None, help="Monitor ids or hostnames (default: all in scope)."
    ),
    interval: int = typer.Option(
        60, "--interval", help="Seconds between polls (checks run every 1 to 5 minutes)."
    ),
    on: str = typer.Option(",".join(EVENTS), "--on", help="Comma-separated events to report."),
    exec_cmd: str | None = typer.Option(
        None,
        "--exec",
        help="Shell command run per event; event JSON on stdin, CFE_EVENT etc. in env.",
    ),
    exit_on: str | None = typer.Option(
        None, "--exit-on", help="Exit 0 after the first event of these kinds (comma-separated)."
    ),
    initial: bool = typer.Option(
        False, "--initial", help="Emit a 'down' event at start for monitors already down."
    ),
    timeout: int | None = typer.Option(
        None, "--timeout", help="Stop after this many seconds (exit 124)."
    ),
    host: str | None = HostOption,
    team: str | None = TeamOption,
):
    """Stream monitor changes as JSON lines: a trigger for scripts and agent loops.

    Events: down, up, verified, paused, resumed, added, removed.

    Examples:

      cfe uptime watch --exec 'claude -p "$CFE_MONITOR_HOSTNAME is $CFE_EVENT, investigate"'

      cfe uptime watch example.com --exit-on down && ./page-me.sh
    """
    wanted = parse_events(on)
    stop = parse_events(exit_on) if exit_on else set()
    s = session(host, team)
    start = time.monotonic()

    def snapshot() -> dict[str, dict] | None:
        try:
            ms = list_monitors(s).get("monitors") or []
        except HttpError as exc:
            if exc.status in (401, 403):
                raise
            return None
        except typer.Exit:
            return None  # network error, already printed; try again next tick
        return {m["id"]: m for m in select(ms, monitors) if m.get("id")}

    state = snapshot()
    while state is None:
        time.sleep(interval)
        state = snapshot()
    info(f"Watching {len(state)} monitor(s) every {interval}s. Ctrl-C to stop.")
    pending = []
    if initial:
        pending = [
            e
            for e in diff_events(
                {
                    k: {**v, "state": "up", "verified": True, "paused": False, "hold": None}
                    for k, v in state.items()
                },
                state,
            )
            if e["event"] == "down"
        ]
    try:
        while True:
            for ev in pending:
                if ev["event"] not in wanted and ev["event"] not in stop:
                    continue
                emit_line(ev)
                if exec_cmd:
                    run_hook(exec_cmd, ev)
                if ev["event"] in stop:
                    raise typer.Exit(code=0)
            if timeout is not None and time.monotonic() - start + interval > timeout:
                raise fail(f"Timed out after {timeout}s.", code=EXIT_TIMEOUT)
            time.sleep(interval)
            nxt = snapshot()
            if nxt is None:
                pending = []
                continue
            pending = diff_events(state, nxt)
            state = nxt
    except KeyboardInterrupt:
        raise typer.Exit(code=130)


def parse_events(value: str) -> set[str]:
    names = {v.strip() for v in value.split(",") if v.strip()}
    unknown = names - set(EVENTS)
    if unknown:
        raise fail(
            f"Unknown event(s): {', '.join(sorted(unknown))}. Use: {', '.join(EVENTS)}", code=2
        )
    return names


@uptime_app.command("wait")
def wait_cmd(
    monitor: str = MonitorArg,
    until: str = typer.Option("up", "--until", help="up, down or verified."),
    interval: int = typer.Option(30, "--interval", help="Seconds between polls."),
    timeout: int = typer.Option(
        1800, "--timeout", help="Give up after this many seconds (exit 124)."
    ),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Block until a monitor is up, down or verified. Exit 0 when it is, 124 on timeout.

    Example, after a deploy: cfe uptime wait example.com --until up --timeout 600
    """
    if until not in ("up", "down", "verified"):
        raise fail("--until must be up, down or verified.", code=2)
    s = session(host, team)
    mid = resolve_id(s, monitor)

    def fetch() -> dict:
        return (s.get(f"uptime/monitors/{mid}/") or {}).get("monitor") or {}

    def reached(m: dict) -> bool:
        if until == "verified":
            return bool(m.get("verified"))
        return display_state(m) == until

    m = poll(
        fetch,
        reached,
        interval=interval,
        timeout=timeout,
        status=f"Waiting for {mid} to be {until}...",
    )
    if as_json:
        print_json(summary(m))
    else:
        ok(f"{m.get('hostname')} is {until}.")
