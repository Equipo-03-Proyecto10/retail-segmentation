# F6-07 — Proof of deployment

Deliverable 13 in [`../scope.md`](../scope.md) §5. Captured on 2026-09-07 against
the published host `mosaiq.maxthecoder.online`, which
[ADR-0013](../adr/0013-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md)
records as the delivery's URL.

**This document is partial.** Two of the five acceptance criteria on #109 are
not captured here, because each one changes the state of the instance. They are named at the bottom
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
it is the only door. It also matches the firewall policy in
[`../infra.md`](../infra.md), which allows only 22, 80 and 443 inbound.

The output carries no password, key or token (AC 5): the process line shows the
bind address and worker count, and the configuration the application reads lives
in an environment file the unit loads, not in the command line.

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

These two acceptance criteria on #109 need a shell on
`mosaiq-deployment-vm` (`northamerica-south1-a`). Neither has been run, and no
output for them is claimed.

| AC | What it needs | Command |
|---|---|---|
| 2 — it restarts on its own | The unit killed, and the state before and after | `systemctl show mosaiq -p MainPID`, then `sudo kill -9 <pid>`, then the same `show` and `systemctl status` again |
| 4 — container execution | `docker compose` serving the same application, per [ADR-0006](../adr/0006-run-under-both-systemd-and-docker-compose.md) | Not a single command — see the two port clashes below |

AC 2 interrupts the published URL for as long as the restart takes, and AC 4
replaces the serving process entirely. Both change the state of the one
environment the delivery is graded on, so they are a deliberate act on a quiet
moment, not something to run mid-review.

### AC 4 is a swap, not an addition

[ADR-0006](../adr/0006-run-under-both-systemd-and-docker-compose.md) states it
directly: the two paths "are alternatives and are never up at once". Two
bindings in `compose.yaml` make that concrete on this instance, and both were
read from the configuration rather than tried:

- **Port 8000.** The `web` service publishes `127.0.0.1:8000:8000`, which is the
  address the systemd unit's gunicorn already binds. That collision is
  deliberate — NGINX needs no change when the mode is switched — but it means
  `mosaiq.service` has to be stopped first, or the container cannot bind.
- **Port 5432.** The `db` service publishes `127.0.0.1:5432:5432`, and the
  instance's own PostgreSQL already listens exactly there
  ([`../infra.md`](../infra.md)). Starting `db` on the instance clashes with it
  and would stand up a second, separately-seeded database.

The second one is the open question, not a step: ADR-0006 says the same image
"talks to the `db` service locally and to the instance's own PostgreSQL there",
so on the instance the container should use the host's database rather than its
own. `compose.yaml` has `web` depending on `db` with a health condition, and a
container's `127.0.0.1` is its own loopback rather than the host's, so reaching
the instance's PostgreSQL from inside the container needs a `DATABASE_URL` and a
network mode that this file does not currently provide.

Deciding that is the work AC 4 still needs. Whoever picks it up should settle it
before the demonstration rather than during it.
