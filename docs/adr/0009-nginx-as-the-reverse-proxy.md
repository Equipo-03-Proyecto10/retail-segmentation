# ADR-0009 — NGINX is the reverse proxy in front of the application

**Status:** Accepted
**Owner:** Max
**Issue:** #77 (F6-01)
**Supersedes:** —
**Superseded by:** —

---

## Context

The delivery must be published on port 80 of the assigned host (scope C-8), and
the application does not listen there: it binds `127.0.0.1:8000` under both
execution paths (ADR-0006). A web server has to sit in front of it, terminate
the public connection on `:80`, and forward to the app — and later terminate TLS
(F6-03, #79). `docs/scope.md` §3 and the README named the component as "NGINX or
Apache" without choosing; ADR-0006 already assumed NGINX when it wrote that
"F6-01 (#77) and F6-02 (#78) are written for NGINX". This record makes that
choice explicit so F6-02 and F6-03 inherit a decision rather than an assumption.

What is genuinely a judgement call: NGINX and Apache are both adequate here and
both ship in the CentOS Stream 10 AppStream. The team has no running experience
with either as an operator. The tie is broken on proxy-config ergonomics and on
not re-opening a question ADR-0006 already leaned on.

## Decision

The reverse proxy is **NGINX**, installed from the CentOS AppStream
(`dnf install nginx`). One `server` block listens on `:80` as the
`default_server` with `server_name _`, and proxies every path to an `upstream`
of `127.0.0.1:8000`. The proxy is HTTP-only at this story; the HTTP→HTTPS
redirect and the certificate are F6-03 and extend the same file. The
application is told how many proxies sit in front of it through
`TRUSTED_PROXY_HOPS` (`1` on the instance), so `X-Forwarded-*` is believed there
and nowhere else. The config lives in the repository at `deploy/nginx/` and is
applied to the instance by the runbook in `deploy/README.md`.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Apache `httpd` | Equally capable and equally available. `mod_proxy_http` + `mod_headers` is more configuration for the same result, the delivery's later static-file and TLS work is more idiomatic on NGINX, and ADR-0006 already committed the two deployment stories to NGINX by name — switching now re-opens a settled question for no gain. |
| Caddy | Automatic HTTPS is attractive for F6-03, but it is not in the base repositories (a third-party repo or a manual binary to maintain), and its opinionated TLS management overlaps awkwardly with the certificate approach F6-03 will choose. |
| No proxy — gunicorn directly on `:80` | gunicorn is an application server, not an edge server: no clean TLS termination, no static-file serving, and binding `:80` needs root or `setcap`. It also couples the public port to the app process, so a restart drops the site. |

## Consequences

**What this makes easy.** F6-02 (#78) writes a systemd unit that binds
`127.0.0.1:8000` and nothing else has to change. F6-03 (#79) adds a `listen 443
ssl` block and a redirect to the file this story ships. The compose overlay
(`compose.proxy.yaml`) reproduces the instance topology locally, so the proxy
behaviour is testable without SSH.

**What this makes hard.** There are now two NGINX config files in the repo —
`deploy/nginx/mosaiq.conf` for the instance and `deploy/nginx/mosaiq.compose.conf`
for local verification — differing only in the upstream target. They can drift;
the header comment in each says which is which, and the compose overlay exercises
the local one on every run. NGINX on the instance also needs the SELinux boolean
`httpd_can_network_connect` set, or every proxied request is a 502 — this is in
the runbook, and is the kind of thing that is invisible until it bites.

**What must now be true elsewhere.** `docs/scope.md` §3 and `README.md` say
"NGINX", not "NGINX or Apache". `docs/infra.md` records the installed proxy.
F6-02's unit file binds `127.0.0.1:8000`. Any code that needs the real client
address or scheme (the F4-05 error log already does; F3-11's audit log will)
depends on `TRUSTED_PROXY_HOPS` being set wherever a proxy is really in front.

## Compliance

```bash
# The proxy config exists and targets the app's bind address.
grep -q 'server 127.0.0.1:8000' deploy/nginx/mosaiq.conf

# The app only trusts forwarded headers when told a proxy is present.
grep -n 'TRUSTED_PROXY_HOPS' .env.example web/config/__init__.py

# Local end-to-end: NGINX forwards :80 to the app.
docker compose -f compose.yaml -f compose.proxy.yaml up -d
curl -sI http://localhost:8080/ | head -1      # expect: HTTP/1.1 200 OK
docker compose -f compose.yaml -f compose.proxy.yaml down
```

The instance side — `:80` reaches the app, `:8000` does not reach it from
outside — is verified in the F6-01/F6-02 pairing session per `deploy/README.md`,
with the output attached to #77.
