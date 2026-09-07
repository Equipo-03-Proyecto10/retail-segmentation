# F6-07 — Proof of deployment

Deliverable 13 in [`../scope.md`](../scope.md) §5. Captured on 2026-09-07 against
the published host `mosaiq.maxthecoder.online`, which
[ADR-0013](../adr/0013-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md)
records as the delivery's URL.

**This document is partial.** One of the five acceptance criteria on #109 is
not captured here: container execution, which is a swap rather than an addition
and still has a question to settle first. They are named at the bottom
with the commands that produce them, so whoever has the shell can paste the
output without re-deriving what to run. Deliverable 13 is not complete until
they are.

No password, key or token appears in any output below (AC 5). Cloudflare
telemetry headers — `report-to`, `nel`, `cf-ray`, `alt-svc` — carry opaque
reporting tokens and are stripped from the captures rather than published.

## AC 1 — the service is active and enabled

```
$ systemctl status mosaiq --no-pager

● mosaiq.service - MOSAIQ web application (gunicorn)
     Loaded: loaded (/etc/systemd/system/mosaiq.service; enabled; preset: disabled)
     Active: active (running) since Mon 2026-09-07 15:50:23 UTC; 42min ago
       Docs: https://github.com/Equipo-03-Proyecto10/retail-segmentation/blob/develop/deploy/README.md
   Main PID: 64097 (gunicorn)
     Status: "Gunicorn arbiter booted"
      Tasks: 3 (limit: 48442)
     Memory: 70M (peak: 189.2M)
     CGroup: /system.slice/mosaiq.service
             ├─64097 /opt/mosaiq/venv/bin/python3.12 /opt/mosaiq/venv/bin/gunicorn --bind 127.0.0.1:8000 --workers 2 --access-logfile - --error-logfile - "web.app:create_app()"
             ├─64098 …
             └─64102 …

$ systemctl is-enabled mosaiq
enabled

$ systemctl show mosaiq -p MainPID -p NRestarts -p Restart
Restart=on-failure
MainPID=64097
NRestarts=0
```

`enabled` is what survives a reboot: systemd starts the unit from the boot
target without anyone logging in. `Restart=on-failure` is what AC 2 exercises,
and `NRestarts=0` is the baseline it moves from.

**Gunicorn binds `127.0.0.1:8000` — loopback only.** The application has no
listener on a public interface, so it cannot be reached except through NGINX.
This is the other half of AC 3: the reverse proxy is not merely the front door,
it is the only door. The firewall in [`../infra.md`](../infra.md) narrows it
further — since 2026-09-07 the only inbound rules are `tcp:22` and `tcp:443`
from Cloudflare's published ranges, `mosaiq-allow-http` having been deleted, so
the origin is not reachable on port 80 at all.

The output carries no password, key or token (AC 5): the process line shows the
bind address and worker count, and the configuration the application reads lives
in an environment file the unit loads, not in the command line.

## AC 2 — it restarts on its own

The unit is killed with `SIGKILL`, which systemd treats as a failure and
`Restart=on-failure` answers. Nothing starts it by hand afterwards.

```
$ systemctl show mosaiq -p MainPID -p NRestarts
MainPID=64097
NRestarts=0

$ sudo kill -9 $(systemctl show mosaiq -p MainPID --value)

$ sleep 3; systemctl show mosaiq -p MainPID -p NRestarts; systemctl is-active mosaiq
MainPID=0
NRestarts=0
activating
```

Three seconds after the kill the process is genuinely gone — `MainPID=0` — and
the unit reads `activating`. That snapshot is the gap worth showing: the
application is not running, and no operator is involved in what happens next.

```
$ systemctl show mosaiq -p MainPID -p NRestarts -p ExecMainStartTimestamp
MainPID=65845
NRestarts=1
ExecMainStartTimestamp=Mon 2026-09-07 16:37:18 UTC

$ systemctl is-active mosaiq
active
```

The PID changed from `64097` to `65845`, `NRestarts` moved from `0` to `1`, and
the unit is active again. Read from outside at the same time, the published
host was serving normally:

```
$ curl -sS -o /dev/null -w "%{http_code} %{time_total}s\n" https://mosaiq.maxthecoder.online/
200 0.271094s
```

The interruption is real but short, and it ends without anyone acting. This is
the crash half of F6-02 (#78) demonstrated rather than asserted.

## AC 3 — the application answers through the reverse proxy, on the host

Not on a port, and not by IP. The request goes to the hostname over HTTPS:

```
$ curl -sS -I https://mosaiq.maxthecoder.online/

HTTP/2 200
content-type: text/html; charset=utf-8
server: cloudflare
vary: Cookie
strict-transport-security: max-age=2592000
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: strict-origin-when-cross-origin
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
cf-cache-status: DYNAMIC
```

`HTTP/2 200` with `vary: Cookie` is the Flask application responding, not a
static placeholder: the session cookie is what varies the response. TLS verifies
clean (`ssl_verify_result=0`) against Cloudflare's edge at `104.21.21.196`; the
origin certificate on the instance is the Cloudflare Origin CA pair ADR-0013
describes.

Plain HTTP is redirected rather than served:

```
$ curl -sS -I http://mosaiq.maxthecoder.online/

HTTP/1.1 301 Moved Permanently
Location: https://mosaiq.maxthecoder.online/
```

That redirect is issued by Cloudflare's edge, not by the instance: the origin
no longer accepts port 80 at all. A visitor who types the bare hostname still
arrives over HTTPS, which is what the criterion asks; the instance simply never
sees the plaintext request.

### The application is the one in this repository

Every protected route refuses an unauthenticated request by redirecting to the
sign-in page, preserving where the visitor was going. This is the default-deny
middleware of [ADR-0007](../adr/0007-permissions-in-code-with-a-default-deny-middleware.md)
running on the instance, not a local test:

```
$ for p in /admin/stores/new /admin/users/new /segment-run/ /audit/ /catalog/; do
>   curl -sS -o /dev/null -w "%{http_code} -> %{redirect_url}\n" "https://mosaiq.maxthecoder.online$p"
> done

302 -> https://mosaiq.maxthecoder.online/login?next=/admin/stores/new
302 -> https://mosaiq.maxthecoder.online/login?next=/admin/users/new
302 -> https://mosaiq.maxthecoder.online/login?next=/segment-run/
302 -> https://mosaiq.maxthecoder.online/login?next=/audit/
302 -> https://mosaiq.maxthecoder.online/login?next=/catalog/
```

`/login` and `/` both return 200. A path that matches no route returns 404.

## The administrator on the instance (F4-06, #107)

[`f4-06-real-administrator.md`](f4-06-real-administrator.md) verified the
procedure against a scratch database built from the three ordered scripts, and
recorded the run on the actual instance as owed to this document. It was
performed on 2026-09-07.

**Before.** The published password still opened the deployed instance. The
report listed the seeded accounts as active, each flagged with what it takes:

```
$ flask --app web.app account-report

  …
  MARKETING          user10@mosaiq-demo.com ← published password
  INVENTORY_PLANNER  user11@mosaiq-demo.com ← published password
  AUDITOR            user12@mosaiq-demo.com ← published password
  CUSTOMER           user13@mosaiq-demo.com ← published password
Demonstration accounts that can still sign in: 30
```

**After.** The documented command was run on the instance, the password typed
at its prompt and never passed as an argument. It was run twice: first onto
`cloudcompute97@gmail.com`, then again onto the address the team settled on.
The first account was deactivated through the application's own user management
(F3-06), which is the only path that closes an account.

```
$ flask --app web.app account-report

33 accounts, 1 of them active.
  ADMIN              mosaiq-devs-supera@udem.edu
Demonstration accounts that can still sign in: 0
```

Thirty demonstration accounts are deactivated rather than deleted, because
`audit_log.user_id` references them and deleting would turn the entries they
produced into unattributed ones ([ADR-0008](../adr/0008-the-instance-keeps-the-demonstration-accounts-deactivated.md)).
`authenticate` refuses an inactive account, so the published password now opens
nothing.

This closes finding 01 of [`instance-findings-fixes.md`](instance-findings-fixes.md),
which recorded the published demonstration credentials as pending operator
input.

**The address is deliberately not the operator's.** `mosaiq-devs-supera@udem.edu`
is a team address; the instance's SSH access is held by a personal Google
account. Keeping them separate means compromising one does not reach the other.
An earlier run of this procedure had used the SSH account itself, and was
replaced for that reason.

**The account total exceeds the seeded thirty by three**, one per account this
procedure created plus one from #107 in `8bab1bf`. Only one is active.
`account-report` counts every row but prints only the active ones, and
`authenticate` refuses an inactive account, so the others are rows rather than
ways in. `provision-administrator` never deletes: RN-01 forbids two
administrators and forbids none, so a transfer moves the role and leaves the
outgoing account holding the placeholder.

## The segment assignments, restored

The 2026-09-07 03:28:53 UTC segment run rewrote `current_segment_id` on all
thirty customers, seventeen of them to `NULL`.
[`instance-findings-fixes.md`](instance-findings-fixes.md) left the choice open:
keep the computed assignments, or restore the audited previous values. The team
chose to restore, and it was done on 2026-09-07 at 19:01:55 UTC.

Every customer is back at the value `audit_log.data_before` recorded for that
run:

```
$ psql -X -d retail

 customers | with_segment
-----------+--------------
        30 |           30

 still_differing_from_audited_previous
---------------------------------------
                                     0
```

**The restore is itself audited.** It went through the same triggers as any
other write, so `audit_log` now holds two change events on `customer` rather
than one:

```
       change_at        | rows
------------------------+------
 2026-09-07 03:28:53+00 |   30   ← the segment run
 2026-09-07 19:01:55+00 |   30   ← the restore
```

Nothing was reverted in place and no history was rewritten, which is the
property `audit_log` exists to provide. `user_id` is `NULL` on the restore's
rows: `fn_audit` records a maintenance script's actor as genuinely unknown
rather than attributing it to a person who did not perform it.

This is also the clearest available argument for the segment-history module
that [`../roadmap.md`](../roadmap.md) defers. Restoring was possible only
because the audit trail kept the previous values; a second run before the
restore would have overwritten them, and `customer.current_segment_id` would
have lost the history for good. [ADR-0004](../adr/0004-model-ahead-of-the-deferred-segmentation-modules.md)
ships that column as a known wrong shape on purpose, and this is what the
consequence looks like in practice.

## Response headers, and the narrow CSP exception

This also closes finding 13 of
[`instance-findings-fixes.md`](instance-findings-fixes.md), which recorded the
NGINX deployment as pending review. All five headers are live, and
`Strict-Transport-Security` is `max-age=2592000` — the value in
`deploy/nginx/mosaiq.conf`, no longer the `max-age=300` the earlier review
found.

The CSP exception for the published chart examples is scoped to the one path
that needs it, rather than widened globally. On `/` the script source is `'self'`
alone; under the charts directory it also allows `unpkg.com`:

```
$ curl -sS -D - -o /dev/null https://mosaiq.maxthecoder.online/docs/design-system/charts/ | grep -i content-security-policy

content-security-policy: default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; ...
```

## Deliverable 14 — the documentation page

`docs/` is served from the same host, so the evidence and the ADRs are reachable
where the delivery is reviewed:

```
$ curl -sS -o /dev/null -w "%{http_code} %{content_type}\n" https://mosaiq.maxthecoder.online/docs/

200 text/html
```

## Not evidenced here

One acceptance criterion on #109 is left. It has not been run, and no output
for it is claimed.

| AC | What it needs | Command |
|---|---|---|
| 4 — container execution | `docker compose` serving the same application, per [ADR-0006](../adr/0006-run-under-both-systemd-and-docker-compose.md) | Not a single command — see the two port clashes below |

AC 4 replaces the serving process on the one environment the delivery is graded
on, so it is a deliberate act on a quiet moment, not something to run
mid-review.

### AC 4 — containers are a development convenience, not a deploy path

The team's position, recorded on 2026-09-07: Docker Compose exists so a
developer can bring the application up in one command. **Containers have no
role in the deployment.** The instance runs gunicorn under systemd, that is
what serves the delivery, and nothing is planned to change it.

This settles the two bindings that made running Compose on the instance
awkward, by removing the reason to do it at all:

- **Port 8000.** The `web` service publishes `127.0.0.1:8000:8000`, the address
  the systemd unit's gunicorn already binds. Running both means stopping the
  one that serves the delivery.
- **Port 5432.** The `db` service publishes `127.0.0.1:5432:5432`, and the
  instance's own PostgreSQL already listens exactly there
  ([`../infra.md`](../infra.md)). Nothing in `compose.yaml` lets the `web`
  container reach the host's database instead: `web` depends on `db` with a
  health condition, and a container's `127.0.0.1` is its own loopback.

Both are only a problem on the instance. Locally, where Compose is the point,
they are correct as written.

**This changes an acceptance criterion, and that needs recording elsewhere.**
AC 4 on #109 asks for `docker compose` serving the same application, citing
[ADR-0006](../adr/0006-run-under-both-systemd-and-docker-compose.md) — which
says Compose is "what the demonstration's container item is shown with,
**including on the instance when it is switched in for that purpose**". That
sentence no longer describes the plan.

ADR-0006 is Accepted and immutable, so the change belongs in a superseding ADR
rather than an edit to it. Until that exists, two things are unresolved and are
named here rather than assumed:

1. Whether the demonstration item *"ejecución mediante contenedores"*
   ([`../requirements.md`](../requirements.md) §4, RNF-13/RNF-14) is satisfied
   by showing Compose on a developer machine. It is a graded item, and where it
   is shown is not this document's call.
2. Whether #109's AC 4 is dropped, or reworded to a local capture.

Neither is a documentation decision. Both should be settled before the
demonstration.
