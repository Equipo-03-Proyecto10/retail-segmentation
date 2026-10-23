# F18-02 (#452): the services under `/api/<name>/` on 443

Every service is reachable at the one HTTPS origin, under its own prefix,
through its own NGINX include. No service port faces the internet.

This records the install on `mosaiq-deployment-vm`, and the outside-in check
through Cloudflare, on 2026-10-23. The design is
[ADR-0040](../adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md),
and the procedure is section F18-02 of [deploy/README.md](../../deploy/README.md).
All times are UTC.

## The release that carried it

`main` at `193f8d1` (release #704) brought `mosaiq.conf` with the include to
`/opt/mosaiq/current`. It also brought six new service versions, applied on the
instance in the same session:

| Step | Result |
|---|---|
| Customers' boundary section (#594) | 0 emails that differ only in letter case. `customer_email_key` dropped, and `ux_customer_email_lower` created, `ON public.customer (lower(email))` |
| recommendations 0.1.1 → 0.1.2 (#693) | ready 204. Its ACL line no longer grants `MULTI` |
| users 0.1.5 → 0.1.6, auth 0.1.2 → 0.1.3 (#698) | ready 204 |
| sales 0.1.1 → 0.1.2, customer-preferences 0.1.2 → 0.1.3 (#705) | ready 204 |
| customers 0.1.5 → 0.1.6 | ready 204, staged right after its section |
| `assemble-acl.sh` | 15 service users reloaded, with no restart |
| `deploy/infra/check-redis.sh` | **prints nothing.** The `svc_recommendations: MULTI is allowed` failure of [F18-01](f18-01-runtime.md#redis-acl-check) is gone |
| `deploy/check-services.sh` | prints nothing |

The web was sampled every second from 06:53:51 to 07:06:27, through the
install and every upgrade: 649 samples, all 200 with `mosaiq` active.

## What was installed

1. The staged service directories gained `nginx/`. Those staged before this
   story had only `compose.yaml`, `redis/` and `mongo/`, and F18-01's step 8
   now copies it too.
2. `deploy/nginx/mosaiq.conf` and `cloudflare-real-ip.conf` were installed by
   hand, as F6-06 left them, after a backup to
   `/root/mosaiq-backups/nginx-20261023T065408Z`.
3. Each staged service's `nginx/<name>.conf` was installed into
   `/etc/nginx/mosaiq-api.d/`: 15 files, all labelled
   `system_u:object_r:httpd_config_t:s0` by `restorecon`.
4. `nginx -t` passed and NGINX reloaded.

As loaded (`nginx -T`), the configuration has:
- 15 `location /api/<name>/` blocks;
- `zone=mosaiq_auth_signin:` declared once;
- `limit_req zone=mosaiq_auth_signin` once, in the signer's include.

Outside loopback, only `:22`, `:80` and `:443` listen. The GCP firewall's
`mosaiq-*` rules are the same as F18-01 recorded:
- `mosaiq-allow-ssh` (tcp:22);
- `mosaiq-allow-https` (tcp:443 from Cloudflare's ranges);
- `mosaiq-deny-other-ingress`.

## The criteria, one by one

[`deploy/check-api-edge.sh`](../../deploy/check-api-edge.sh) ran from a
workstation outside the instance, through Cloudflare, with `VERBOSE=1`. It
started at 07:05:08 and ended at 07:06:10, exited 0, printed no `FAIL:` line
and printed 53 `ok:` lines.

| Criterion | Evidence |
|---|---|
| `mosaiq.conf`'s 443 block includes `/etc/nginx/mosaiq-api.d/*.conf`. `zone=mosaiq_auth_signin:` is declared once, and this story declares no zone | `tests/test_nginx_api_includes.py`, CI's `nginx -t` over the fifteen includes (#703), and `nginx -T` on the instance (above) |
| Each include holds exactly one `location /api/<name>/` with the registry port, both client headers overwritten, the 64 KiB cap, `error_page 413` to the two named locations, and no `add_header`. The signer's adds the sign-in `limit_req` and its 429 | The same tests, over the 15 committed files that were installed |
| Every service's `/api/<name>/health/ready` answers through Cloudflare with HSTS, and `cf-cache-status` is never `HIT` | 15 of 15 answered **204** (the readiness contract's success, with no body), with `Strict-Transport-Security`, and `cf-cache-status: DYNAMIC` |
| Spoofed `X-Real-IP` / `X-Forwarded-For` do not move the sign-in key | Sign-ins with a nonexistent address, a well-formed body and a different spoofed `198.51.100.<n>` each time: 401, 401, 401, 401, 401, then **429 `rate_limited`** at request 6 (`burst=5`, `rate=6r/m`). The key is the address Cloudflare's real-IP block sets |
| Refreshes past the sign-in rate are never refused by that zone | Right after the 429, 10 `POST /api/auth/v1/sessions/refresh` with bogus tokens. None was 429: each was **401 `invalid_refresh`** |
| A 65 KiB body gets 413 `payload_too_large`, in XML when asked | 15 of 15 services, in both formats (samples below) |
| `nginx -t` passes, and the web behaves as before | `nginx -t` passed. `/` 200 and `/login` 200. `/reports/` and `/catalog/stock` answer 302 to `/login?next=…`, as an unauthenticated request always did |
| Service ports, Redis and MongoDB are unreachable from outside | All 17 probed ports refused or timed out on the origin's public address: 5001 to 5015, 6379 and 27017 |

## Samples

A 65 KiB `POST /api/products/v1/products`, with `X-Correlation-ID: f18-02-evidence`:

```text
Accept: application/json  ->  413, content-type: application/json, cf-cache-status: DYNAMIC
{"code":"payload_too_large","message":"The request body exceeds 64 KiB.","correlation_id":"f18-02-evidence","details":[]}

Accept: application/xml   ->  413, content-type: application/xml
<?xml version="1.0" encoding="UTF-8"?>
<Error xmlns="https://mosaiq.maxthecoder.online/xml/common/v1"><code>payload_too_large</code><message>The request body exceeds 64 KiB.</message><correlation_id>f18-02-evidence</correlation_id></Error>
```

Both carry `Strict-Transport-Security: max-age=31536000; includeSubDomains`
and echo the correlation id.

The 429, with spoofed client headers:

```text
429, content-type: application/json, retry-after: 10,
strict-transport-security: max-age=31536000; includeSubDomains, cf-cache-status: DYNAMIC
{"code":"rate_limited","message":"Too many sign-in attempts. Try again later.","correlation_id":"…","details":[]}
```

## Found while checking

The first run of the edge check sent sign-ins without `device_label`, which the
contract requires. So the signer answered 422 `invalid_body`, not a failed
sign-in. NGINX limits before the request reaches the service, so 429 came all
the same. The check now sends a well-formed body, and the table above is from
that run.

## What was not done

- **No reboot.** The includes and `mosaiq.conf` are files on disk, read at
  start, but a reboot was not run to show it.
- **F18-03's per-deploy install is not built yet.** The includes were installed
  once, by the procedure F18-03 will automate.
