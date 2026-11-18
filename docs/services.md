# Member services: uptime, mail checks, domains

`cfe uptime`, `cfe mail` and `cfe domains` drive the member tools on CodingForEntrepreneurs from the terminal. They use the `cfe login` token (or `CFE_TOKEN`) against `/api/uptime/`, `/api/mail/` and `/api/domains/` on the CFE host, with the same rules as the website: plan limits, team roles, verification.

Every command takes `--json` for scripts and agents. Progress lines go to stderr, so stdout stays parseable.

## Teams

Items live in Personal or a team. Without `--team`, lists show Personal and new items go to Personal.

```bash
cfe teams                          # your teams: name, slug, role, uid
cfe uptime ls --team acme          # uid, slug or name; 'personal' for your own
export CFE_TEAM=acme               # default for every command
```

Detail commands (show, verify, rm, ...) find an item in any of your scopes by id; `--team` only matters when you pass a hostname instead of an id.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | ok, or the condition you waited for happened |
| 1 | error, a check failed (`--fail-on`), a monitor is down (`status`), or not verified (`verify`) |
| 2 | bad option |
| 124 | `--timeout` passed first |
| 130 | Ctrl-C |

## Uptime

```bash
cfe uptime add https://example.com --name "Marketing site"
cfe uptime dns example.com          # the exact TXT record, provider steps, one-click Domain Connect link (--open)
cfe uptime verify example.com --wait
cfe uptime ls
cfe uptime show example.com         # state, uptime 24h/7d/30d, open incident, recent checks and incidents
cfe uptime checks example.com --status down --limit 50
cfe uptime checks example.com --from 2026-11-01 --csv checks.csv
cfe uptime history example.com --days 90
cfe uptime scan example.com --fail-on fail   # site scan: LLM, search, marketing, email, security
cfe uptime pause|resume|rename|rm example.com
```

Monitors can be named by id, hostname or URL. A monitor under a domain you already verified (`cfe domains`) is verified at once.

### Signals for scripts and agents

Monitor state as a trigger, without writing a poller:

```bash
cfe uptime status                   # one line per monitor; exit 1 if any is down
cfe uptime wait example.com --until up --timeout 600     # after a deploy
cfe uptime wait example.com --until verified             # after adding DNS
cfe uptime watch                    # one JSON line per change, forever
```

`watch` polls every `--interval` seconds (default 60; checks themselves run every 1 to 5 minutes) and emits events: `down`, `up`, `verified`, `paused`, `resumed`, `added`, `removed`. Each line:

```json
{"event": "down", "at": "2026-11-18T14:05:00+00:00",
 "monitor": {"id": "...", "hostname": "example.com", "url": "https://example.com/", "state": "down",
             "display_state": "down", "open_incident": {...}, "latency_ms": null, "...": "..."},
 "previous": {"state": "up"}}
```

- `--on down,up` limits which events are reported.
- `--exec CMD` runs a shell command per event, with the event JSON on stdin and `CFE_EVENT`, `CFE_MONITOR_ID`, `CFE_MONITOR_NAME`, `CFE_MONITOR_HOSTNAME`, `CFE_MONITOR_URL`, `CFE_MONITOR_STATE` in its environment. A failing hook is logged and the watch continues.
- `--exit-on down` exits 0 after the first such event, so it chains: `cfe uptime watch --exit-on down && ./respond.sh`.
- `--initial` reports monitors that are already down when the watch starts.
- Network errors are printed and retried on the next tick; a revoked login stops the watch.

Hand an outage to an agent:

```bash
cfe uptime watch --on down --exec 'claude -p "$CFE_MONITOR_HOSTNAME just went down. Investigate: cfe uptime show $CFE_MONITOR_ID"'
```

## Mail checks

The loop: get a one-time address, send your real campaign to it, read the report here. About 90 checks across authentication, headers, DNS, content, HTML and links. Reports hold results and message metadata only, never the body.

```bash
cfe mail check                      # prints check+<token>@cfe.run, waits, prints the report
cfe mail check --send './send_test.sh "$CFE_MAIL_ADDRESS"' --fail-on warn   # fully scripted
cfe mail check --no-wait            # just the address on stdout; later: cfe mail report <token> --wait
cfe mail paste message.eml          # check a message you already have (or - for stdin), nothing is sent
cfe mail report <token> --all       # include passing and info checks
cfe mail ls                         # saved reports, plus check@cfe.run reports from your verified domains
cfe mail raw <token> -o msg.eml     # stored raw message (7 days)
cfe mail rm <token>
```

`--fail-on fail` (default) exits 1 when any check fails; `warn` also fails on warnings; `never` always exits 0. That makes `cfe mail check --send ...` a CI step or an agent's success test: change the template, send, read the JSON, repeat until it exits 0.

With `--json`, `check` prints one `{"event": "address", ...}` line when the address is ready, then the full report.

The address is valid for one hour (`--timeout`, default 3600 seconds). Plain mail to `check@cfe.run` also works: it lands under the domain you verified with `cfe domains`.

### Saved addresses

A permanent `check+<name>-<key>@cfe.run` to keep in your ESP as a test recipient. Every email to it creates a report.

```bash
cfe mail address add Newsletter     # prints the address
cfe mail address wait <id>          # waits for the next email, prints its report (exit follows --fail-on)
cfe mail address reports <id>
cfe mail address ls|rename|rm
```

## Domains

```bash
cfe domains add example.com         # prints the _cfe-verify TXT record (unless an uptime monitor already proves it)
cfe domains verify example.com --wait
cfe domains ls                      # verified, DNS host, email provider, hosting, expiry, monitors
cfe domains show example.com        # registrar, expiry, providers, monitors on it
cfe domains services example.com    # services detected from DNS and the site
cfe domains scan example.com        # site scan (verified domains only)
cfe domains rescan example.com      # refresh DNS and registration data
cfe domains rm example.com          # monitors that relied on it are paused
```

A verified domain:

- verifies new uptime monitors on it or its subdomains at once;
- routes plain `check@cfe.run` reports from it to you.
