"""`cfe mail`: the CFE pre-send email checker (check@cfe.run) from the terminal.

The loop: `cfe mail check` makes a one-time `check+<token>@cfe.run` address,
you (or `--send`) mail your campaign to it, and the report comes back here
with about 90 checks across auth, headers, DNS, content, HTML and links.
Exit code follows `--fail-on`, so it works as a CI or agent gate.

Wraps `/api/mail/` on the CFE site.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.table import Table

from .api import (
    HostOption,
    JsonOption,
    Session,
    TeamOption,
    ago,
    check_fail_on,
    console,
    emit_line,
    fail,
    info,
    ok,
    poll,
    print_json,
    session,
    when,
)

mail_app = typer.Typer(
    name="mail",
    help="Pre-send email checks: send a campaign to a check address, get the report here.",
    no_args_is_help=True,
)
address_app = typer.Typer(
    name="address",
    help="Saved check+<name>@cfe.run addresses to keep in your mailer.",
    no_args_is_help=True,
)
mail_app.add_typer(address_app, name="address")

GROUPS = ("auth", "headers", "dns", "content", "html", "links")
MARK = {
    "pass": "[green]✓[/green]",
    "info": "[blue]i[/blue]",
    "warn": "[yellow]![/yellow]",
    "fail": "[red]✗[/red]",
}
DONE = ("complete", "expired")
TokenArg = typer.Argument(..., help="Report token (from the check+<token>@ address).")
MailFailOn = typer.Option(
    "fail", "--fail-on", help="Exit 1 when any check is at this level or worse: fail, warn, never."
)
TimeoutOption = typer.Option(3600, "--timeout", help="Seconds to wait for the email (exit 124).")
IntervalOption = typer.Option(3, "--interval", help="Seconds between polls.")


# ------------------------------------------------------------------ report


def report_exit_code(report: dict, fail_on: str) -> int:
    counts = report.get("summary") or {}
    if report.get("status") == "expired":
        return 1
    if fail_on == "fail" and counts.get("fail"):
        return 1
    if fail_on == "warn" and (counts.get("fail") or counts.get("warn")):
        return 1
    return 0


def print_report(report: dict, show_all: bool = False) -> None:
    status = report.get("status")
    msg = report.get("message") or {}
    counts = report.get("summary") or {}
    if status == "expired":
        console.print("[red]The address expired before an email arrived.[/red]", highlight=False)
        return
    if msg:
        console.print(
            f"[bold]{msg.get('subject') or '(no subject)'}[/bold]", highlight=False, markup=True
        )
        console.print(
            f"  From: {msg.get('from')}  ({msg.get('size_bytes', 0)} bytes)",
            highlight=False,
            markup=False,
        )
        auth = (
            "[green]DMARC pass[/green]"
            if msg.get("authenticated")
            else "[red]not authenticated[/red]"
        )
        console.print(f"  Auth: {auth}", highlight=False)
    console.print(
        f"\n  [red]{counts.get('fail', 0)} fail[/red]  [yellow]{counts.get('warn', 0)} warn[/yellow]  "
        f"[blue]{counts.get('info', 0)} info[/blue]  [green]{counts.get('pass', 0)} pass[/green]",
        highlight=False,
    )
    if status != "complete":
        pending = ", ".join(report.get("groups_pending") or []) or "-"
        console.print(f"  [dim]{status}, pending: {pending}[/dim]", highlight=False)
    shown = ("fail", "warn", "info", "pass") if show_all else ("fail", "warn")
    results = [r for r in report.get("results") or [] if r.get("severity") in shown]
    order = {g: i for i, g in enumerate(GROUPS)}
    results.sort(
        key=lambda r: (
            ("fail", "warn", "info", "pass").index(r.get("severity", "pass")),
            order.get(r.get("group"), 99),
        )
    )
    if results:
        console.print()
    for r in results:
        console.print(
            f"  {MARK.get(r.get('severity'), '?')} {r.get('title')} [dim]({r.get('group')})[/dim]",
            highlight=False,
        )
        if r.get("detail"):
            console.print(f"      {r['detail']}", highlight=False, markup=False)
        if r.get("learn_more"):
            console.print(f"      [dim]{r['learn_more']}[/dim]", highlight=False)
    if not show_all and (counts.get("pass") or counts.get("info")):
        console.print("\n[dim]--all shows passing and info checks.[/dim]", highlight=False)


def get_report(s: Session, token: str) -> dict:
    return s.get(f"mail/checks/{token}/") or {}


def wait_report(s: Session, token: str, interval: int, timeout: int | None, label: str) -> dict:
    last = {"status": None}

    def tick(r: dict):
        if r.get("status") != last["status"]:
            last["status"] = r.get("status")
            if r.get("status") == "processing":
                info("Email received, running checks...")

    return poll(
        lambda: get_report(s, token),
        lambda r: r.get("status") in DONE,
        interval=interval,
        timeout=timeout,
        on_tick=tick,
        status=label,
    )


def finish(report: dict, as_json: bool, show_all: bool, fail_on: str) -> None:
    if as_json:
        print_json(report)
    else:
        print_report(report, show_all)
    raise typer.Exit(code=report_exit_code(report, fail_on))


def run_send(command: str, address: str, token: str) -> None:
    env = {**os.environ, "CFE_MAIL_ADDRESS": address, "CFE_MAIL_TOKEN": token}
    info(f"Running: {command}")
    result = subprocess.run(command, shell=True, env=env, check=False)
    if result.returncode:
        raise fail(f"--send command exited {result.returncode}; not waiting for a report.")


# ------------------------------------------------------------------ commands


@mail_app.command("check")
def check_cmd(
    send: str | None = typer.Option(
        None,
        "--send",
        help="Shell command that sends the email; $CFE_MAIL_ADDRESS is the check address.",
    ),
    no_wait: bool = typer.Option(False, "--no-wait", help="Print the address and exit."),
    show_all: bool = typer.Option(False, "--all", help="Also list passing and info checks."),
    fail_on: str = MailFailOn,
    timeout: int = TimeoutOption,
    interval: int = IntervalOption,
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Get a one-time check address, wait for your email, print the report.

    Examples:

      cfe mail check                     # then send your campaign to the printed address

      cfe mail check --send 'python send_test.py --to "$CFE_MAIL_ADDRESS"' --fail-on warn
    """
    check_fail_on(fail_on)
    s = session(host, team)
    created = s.post("mail/checks/", s.team_body())
    token, address = created.get("token"), created.get("address")
    if no_wait:
        if as_json:
            return print_json(created)
        typer.echo(address)
        info(
            f"Valid until {when(created.get('expires_at'))}. Report: cfe mail report {token} --wait"
        )
        return
    if as_json:
        emit_line({"event": "address", **created})
    else:
        console.print(f"Send your email to: [bold]{address}[/bold]", highlight=False)
        console.print(
            f"[dim]Valid until {when(created.get('expires_at'))}. Ctrl-C to stop waiting; resume with: cfe mail report {token} --wait[/dim]",
            highlight=False,
        )
    if send:
        run_send(send, address, token)
    report = wait_report(s, token, interval, timeout, "Waiting for your email...")
    finish(report, as_json, show_all, fail_on)


@mail_app.command("report")
def report_cmd(
    token: str = TokenArg,
    wait: bool = typer.Option(False, "--wait", help="Wait until the report is complete."),
    show_all: bool = typer.Option(False, "--all", help="Also list passing and info checks."),
    fail_on: str = MailFailOn,
    timeout: int = TimeoutOption,
    interval: int = IntervalOption,
    host: str | None = HostOption,
    as_json: bool = JsonOption,
):
    """Show a report. Exit code follows --fail-on."""
    check_fail_on(fail_on)
    s = session(host)
    token = token.strip().lower().removeprefix("check+").split("@", 1)[0]
    report = (
        wait_report(s, token, interval, timeout, "Waiting for the report...")
        if wait
        else get_report(s, token)
    )
    if report.get("status") not in DONE and not as_json:
        print_report(report, show_all)
        raise typer.Exit(code=1 if report.get("status") == "waiting" else 0)
    finish(report, as_json, show_all, fail_on)


@mail_app.command("paste")
def paste_cmd(
    source: str = typer.Argument(
        ..., help="A raw message (.eml or 'Show original' text) file, or '-' for stdin."
    ),
    show_all: bool = typer.Option(False, "--all", help="Also list passing and info checks."),
    fail_on: str = MailFailOn,
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait for the checks."),
    interval: int = IntervalOption,
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Check a message you already have, without sending it. Auth is read from its headers."""
    check_fail_on(fail_on)
    raw = sys.stdin.read() if source == "-" else _read(Path(source))
    s = session(host, team)
    created = s.post("mail/checks/paste/", {"raw": raw, **s.team_body()})
    token = created.get("token")
    report = (
        created
        if created.get("status") in DONE
        else wait_report(s, token, interval, timeout, "Running checks...")
    )
    finish(report, as_json, show_all, fail_on)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise fail(f"Could not read {path}: {exc.strerror}")


@mail_app.command("ls")
def ls_cmd(
    limit: int = typer.Option(25, "--limit", help="Max reports (up to 100)."),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Saved reports in Personal or a team, plus check@cfe.run reports for your verified domains."""
    s = session(host, team)
    body = s.get("mail/checks/", params={"limit": limit}) or {}
    if as_json:
        return print_json(body)
    checks = body.get("checks") or []
    if not checks:
        console.print("No reports yet. Start one: cfe mail check", highlight=False, markup=False)
    else:
        print_summaries(checks)
    domains = body.get("verified_domains") or []
    if domains:
        console.print(
            f"[dim]check@cfe.run reports from {', '.join(domains)} show here too.[/dim]",
            highlight=False,
        )


def print_summaries(checks: list[dict]) -> None:
    table = Table(box=None, pad_edge=False)
    for col in ("Token", "Received", "Subject", "From", "Fail", "Warn", "Status"):
        table.add_column(col)
    for c in checks:
        msg = c.get("message") or {}
        counts = c.get("summary") or {}
        table.add_row(
            c.get("token", "?"),
            ago(c.get("received_at") or c.get("created_at")),
            (msg.get("subject") or "-")[:50],
            msg.get("from") or "-",
            f"[red]{counts.get('fail', 0)}[/red]" if counts.get("fail") else "0",
            f"[yellow]{counts.get('warn', 0)}[/yellow]" if counts.get("warn") else "0",
            c.get("status") or "-",
        )
    console.print(table)


@mail_app.command("raw")
def raw_cmd(
    token: str = TokenArg,
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write to this file instead of stdout."
    ),
    host: str | None = HostOption,
):
    """The stored raw message (kept 7 days after receipt)."""
    s = session(host)
    body = s.get(f"mail/checks/{token}/raw/") or {}
    text = body.get("raw") or ""
    if output:
        output.write_text(text)
        ok(f"Wrote {output}")
    else:
        typer.echo(text, nl=False)


@mail_app.command("rm")
def rm_cmd(
    token: str = TokenArg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    host: str | None = HostOption,
):
    """Delete a report and its raw message."""
    if not yes and not typer.confirm(f"Delete report {token}?", default=False):
        raise typer.Exit(code=1)
    session(host).delete(f"mail/checks/{token}/")
    ok(f"Deleted {token}.")


# ------------------------------------------------------------------ saved addresses


@address_app.command("ls")
def address_ls(
    host: str | None = HostOption, team: str | None = TeamOption, as_json: bool = JsonOption
):
    """Saved addresses in Personal or a team."""
    s = session(host, team)
    body = s.get("mail/addresses/") or {}
    if as_json:
        return print_json(body)
    addresses = body.get("addresses") or []
    if not addresses:
        console.print(
            "No saved addresses. Add one: cfe mail address add Newsletter",
            highlight=False,
            markup=False,
        )
        return
    table = Table(box=None, pad_edge=False)
    for col in ("ID", "Name", "Address", "Reports", "Last email"):
        table.add_column(col)
    for a in addresses:
        table.add_row(
            a.get("id", "?"),
            a.get("name") or "-",
            a.get("address") or "-",
            str(a.get("report_count", 0)),
            ago(a.get("last_received_at")),
        )
    console.print(table)


@address_app.command("add")
def address_add(
    name: str = typer.Argument(..., help="A label, e.g. Newsletter or Onboarding."),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Create a saved address. Every email sent to it creates a report."""
    s = session(host, team)
    body = s.post("mail/addresses/", {"name": name, **s.team_body()})
    if as_json:
        return print_json(body)
    ok(f"{body.get('name')}: {body.get('address')}")
    console.print(
        f"Wait for the next report: cfe mail address wait {body.get('id')}",
        highlight=False,
        markup=False,
    )


@address_app.command("rename")
def address_rename(
    address_id: str = typer.Argument(...),
    name: str = typer.Argument(...),
    host: str | None = HostOption,
):
    """Rename a saved address."""
    session(host).patch(f"mail/addresses/{address_id}/", {"name": name})
    ok(f"Renamed to {name}.")


@address_app.command("rm")
def address_rm(
    address_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    host: str | None = HostOption,
):
    """Stop accepting mail at a saved address. Its reports are kept."""
    if not yes and not typer.confirm(f"Stop accepting mail at {address_id}?", default=False):
        raise typer.Exit(code=1)
    session(host).delete(f"mail/addresses/{address_id}/")
    ok(f"Removed {address_id}.")


@address_app.command("reports")
def address_reports(
    address_id: str = typer.Argument(...),
    limit: int = typer.Option(25, "--limit"),
    host: str | None = HostOption,
    as_json: bool = JsonOption,
):
    """Reports from one saved address, newest first."""
    body = session(host).get(f"mail/addresses/{address_id}/checks/", params={"limit": limit}) or {}
    if as_json:
        return print_json(body)
    checks = body.get("checks") or []
    print_summaries(checks) if checks else console.print("No reports yet.", highlight=False)


@address_app.command("wait")
def address_wait(
    address_id: str = typer.Argument(...),
    show_all: bool = typer.Option(False, "--all", help="Also list passing and info checks."),
    fail_on: str = MailFailOn,
    timeout: int = TimeoutOption,
    interval: int = typer.Option(5, "--interval", help="Seconds between polls."),
    host: str | None = HostOption,
    as_json: bool = JsonOption,
):
    """Wait for the next email to a saved address and print its report.

    Good for ESP test sends: start this, hit "send test" in your mailer, read the report.
    """
    check_fail_on(fail_on)
    s = session(host)

    def tokens() -> tuple[dict, set[str]]:
        body = s.get(f"mail/addresses/{address_id}/checks/", params={"limit": 100}) or {}
        checks = body.get("checks") or []
        return body, {c.get("token") for c in checks}

    body, seen = tokens()
    address = (body.get("address") or {}).get("address") or address_id
    if not as_json:
        console.print(f"Waiting for the next email to [bold]{address}[/bold]...", highlight=False)
    start = time.monotonic()

    def new_token() -> str | None:
        _, current = tokens()
        fresh = current - seen
        return next(iter(fresh)) if fresh else None

    token = poll(
        new_token, bool, interval=interval, timeout=timeout, status="Waiting for an email..."
    )
    remaining = None if timeout is None else max(1, timeout - int(time.monotonic() - start))
    report = wait_report(s, token, 3, remaining, "Running checks...")
    finish(report, as_json, show_all, fail_on)
