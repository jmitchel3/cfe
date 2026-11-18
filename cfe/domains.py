"""`cfe domains`: add and verify domains on CFE, scan them, see the services they use.

A verified domain lets new uptime monitors under it verify at once and routes
check@cfe.run reports from it to you. Wraps `/api/domains/` on the CFE site.
"""

import typer
from rich.table import Table

from .api import (
    FailOnOption,
    HostOption,
    JsonOption,
    Session,
    TeamOption,
    check_fail_on,
    console,
    fail,
    hostname_of,
    looks_like_hostname,
    ok,
    poll,
    print_json,
    print_sitescan,
    session,
    sitescan_exit_code,
    when,
)

domains_app = typer.Typer(
    name="domains",
    help="Domains on CodingForEntrepreneurs: add, verify with DNS, scan.",
    no_args_is_help=True,
)
DomainArg = typer.Argument(..., help="Domain id or hostname.")


def list_domains(s: Session) -> dict:
    body = s.get("domains/")
    return body if isinstance(body, dict) else {"domains": []}


def resolve_uid(s: Session, ref: str) -> str:
    if not looks_like_hostname(ref):
        return ref
    host = hostname_of(ref)
    for d in list_domains(s).get("domains") or []:
        if (d.get("hostname") or "").lower() == host:
            return d["id"]
    scope = "Personal" if not s.team_uid else "this team"
    raise fail(f"{host} is not in {scope}. Add it: cfe domains add {host}")


def print_record(domain: dict) -> None:
    v = domain.get("verification") or {}
    record = v.get("record") or {}
    if not record:
        return
    console.print("\nAdd this DNS record at your DNS provider:", highlight=False)
    console.print(f"  Type:  {record.get('type', 'TXT')}", highlight=False, markup=False)
    console.print(
        f"  Name:  {record.get('host')}   (full: {record.get('fqdn')})",
        highlight=False,
        markup=False,
    )
    console.print(f"  Value: {record.get('value')}", highlight=False, markup=False)
    console.print(
        f"Then: cfe domains verify {domain.get('hostname')} --wait", highlight=False, markup=False
    )


@domains_app.command("ls")
def ls_cmd(
    host: str | None = HostOption, team: str | None = TeamOption, as_json: bool = JsonOption
):
    """Domains in Personal or a team."""
    s = session(host, team)
    body = list_domains(s)
    if as_json:
        return print_json(body)
    domains = body.get("domains") or []
    if not domains:
        console.print(
            "No domains yet. Add one: cfe domains add example.com", highlight=False, markup=False
        )
        return
    table = Table(box=None, pad_edge=False)
    for col in ("ID", "Hostname", "Verified", "DNS", "Email", "Hosting", "Expires", "Monitors"):
        table.add_column(col)
    for d in domains:
        sm = d.get("summary") or {}
        table.add_row(
            d.get("id", "?"),
            d.get("hostname") or "-",
            "[green]yes[/green]" if d.get("verified") else "[yellow]no[/yellow]",
            sm.get("dns_host") or "-",
            sm.get("email_provider") or "-",
            sm.get("hosting") or "-",
            when(sm.get("expires_at"))[:10] if sm.get("expires_at") else "-",
            str(len(d.get("monitors") or [])),
        )
    console.print(table)


@domains_app.command("add")
def add_cmd(
    hostname: str = typer.Argument(..., help="e.g. example.com"),
    wait: bool = typer.Option(False, "--wait", help="Wait until the TXT record verifies."),
    timeout: int = typer.Option(900, "--timeout", help="Seconds to wait with --wait (exit 124)."),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Add a domain. Prints the _cfe-verify TXT record unless it is already verified."""
    s = session(host, team)
    body = s.post("domains/", {"hostname": hostname_of(hostname), **s.team_body()})
    if as_json and not wait:
        return print_json(body)
    if body.get("verified"):
        ok(f"Added {body.get('hostname')}, verified ({body.get('verification_method') or 'dns'}).")
        return
    ok(f"Added {body.get('hostname')} ({body.get('id')}).")
    print_record(body)
    if wait:
        result = wait_verified(s, body["id"], 15, timeout)
        if as_json:
            print_json(result)


def wait_verified(s: Session, uid: str, interval: int, timeout: int | None) -> dict:
    result = poll(
        lambda: s.post(f"domains/{uid}/verify/"),
        lambda r: bool((r or {}).get("verified")),
        interval=interval,
        timeout=timeout,
        status="Waiting for the TXT record...",
    )
    ok(f"Verified {(result.get('domain') or {}).get('hostname') or uid}.")
    return result


@domains_app.command("verify")
def verify_cmd(
    domain: str = DomainArg,
    wait: bool = typer.Option(False, "--wait", help="Keep checking until verified."),
    interval: int = typer.Option(15, "--interval", help="Seconds between attempts with --wait."),
    timeout: int = typer.Option(
        900, "--timeout", help="Give up after this many seconds (exit 124)."
    ),
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Check the _cfe-verify TXT record now. Exits 1 if not verified."""
    s = session(host, team)
    uid = resolve_uid(s, domain)
    if wait:
        result = wait_verified(s, uid, interval, timeout)
        return print_json(result) if as_json else None
    result = s.post(f"domains/{uid}/verify/")
    if as_json:
        print_json(result)
    elif result.get("verified"):
        ok(f"Verified ({result.get('method') or 'dns'}).")
    else:
        console.print(
            f"[yellow]Not verified yet.[/yellow] {result.get('detail') or ''}", highlight=False
        )
        print_record(result.get("domain") or {})
    if not result.get("verified"):
        raise typer.Exit(code=1)


@domains_app.command("show")
def show_cmd(
    domain: str = DomainArg,
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Domain detail: verification, registrar, DNS and email providers, monitors."""
    s = session(host, team)
    uid = resolve_uid(s, domain)
    d = s.get(f"domains/{uid}/") or {}
    if as_json:
        return print_json(d)
    sm = d.get("summary") or {}
    console.print(f"[bold]{d.get('hostname')}[/bold]", highlight=False)
    rows = [
        ("ID", d.get("id")),
        (
            "Verified",
            f"yes ({d.get('verification_method')}, {when(d.get('verified_at'))})"
            if d.get("verified")
            else "no",
        ),
        ("Owner", (d.get("owner") or {}).get("name") or "-"),
        ("Registrar", sm.get("registrar") or "-"),
        ("Expires", when(sm.get("expires_at"))),
        ("DNS host", sm.get("dns_host") or "-"),
        ("Email", sm.get("email_provider") or "-"),
        ("Hosting", sm.get("hosting") or "-"),
        ("Scanned", when(d.get("scanned_at"))),
    ]
    for label, value in rows:
        console.print(f"  {label + ':':<12}{value}", highlight=False, markup=False)
    monitors = d.get("monitors") or []
    if monitors:
        console.print("\nUptime monitors", highlight=False)
        for m in monitors:
            console.print(
                f"  {m.get('hostname')}  {m.get('status') or '-'}  [dim]{m.get('id')}[/dim]",
                highlight=False,
            )
    elif d.get("verified"):
        console.print(
            f"\nNo monitor yet: cfe uptime add https://{d.get('hostname')}",
            highlight=False,
            markup=False,
        )
    print_record(d)


@domains_app.command("rescan")
def rescan_cmd(
    domain: str = DomainArg,
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Refresh DNS and registration data."""
    s = session(host, team)
    uid = resolve_uid(s, domain)
    d = s.post(f"domains/{uid}/rescan/")
    if as_json:
        return print_json(d)
    ok(f"Rescanned {d.get('hostname')}.")


@domains_app.command("scan")
def scan_cmd(
    domain: str = DomainArg,
    show_all: bool = typer.Option(False, "--all", help="Also list passing checks."),
    fail_on: str = FailOnOption,
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Site scan of a verified domain: LLM and search visibility, marketing, email, security."""
    fail_on = check_fail_on(fail_on)
    s = session(host, team)
    uid = resolve_uid(s, domain)
    result = s.post(f"domains/{uid}/sitescan/")
    if as_json:
        print_json(result)
    else:
        print_sitescan(result, show_all)
    raise typer.Exit(code=sitescan_exit_code(result, fail_on))


@domains_app.command("services")
def services_cmd(
    domain: str = DomainArg,
    host: str | None = HostOption,
    team: str | None = TeamOption,
    as_json: bool = JsonOption,
):
    """Services the domain uses (detected from DNS and the site, plus ones you added)."""
    s = session(host, team)
    uid = resolve_uid(s, domain)
    body = s.get(f"domains/{uid}/services/") or {}
    if as_json:
        return print_json(body)
    table = Table(box=None, pad_edge=False)
    for col in ("Service", "Category", "Source", "State"):
        table.add_column(col)
    for row in body.get("services") or []:
        table.add_row(
            row.get("name") or "-",
            row.get("category") or "-",
            row.get("source") or "-",
            row.get("state") or row.get("status") or "-",
        )
    console.print(table)


@domains_app.command("rm")
def rm_cmd(
    domain: str = DomainArg,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    host: str | None = HostOption,
    team: str | None = TeamOption,
):
    """Remove a domain. Uptime monitors that relied on it are paused."""
    s = session(host, team)
    uid = resolve_uid(s, domain)
    if not yes and not typer.confirm(
        f"Remove {domain}? Monitors relying on it will pause.", default=False
    ):
        raise typer.Exit(code=1)
    body = s.delete(f"domains/{uid}/") or {}
    ok(f"Removed {body.get('removed') or domain}.")
    for m in body.get("paused_monitors") or []:
        console.print(
            f"  paused monitor {m.get('hostname')} ({m.get('id')})", highlight=False, markup=False
        )


def teams_cmd(host: str | None = HostOption, as_json: bool = JsonOption):
    """List your teams (use a uid, slug or name with --team)."""
    teams = session(host).get("teams/") or []
    if as_json:
        return print_json(teams)
    table = Table(box=None, pad_edge=False)
    for col in ("Name", "Slug", "Role", "Members", "UID"):
        table.add_column(col)
    for t in teams:
        name = "Personal" if t.get("is_personal") else t.get("name")
        table.add_row(
            name or "-",
            t.get("slug") or "-",
            t.get("role") or "-",
            str(t.get("member_count") or "-"),
            str(t.get("uid")),
        )
    console.print(table)
