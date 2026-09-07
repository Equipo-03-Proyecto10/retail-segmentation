# ADR-0013 — MOSAIQ is published at `mosaiq.maxthecoder.online` through Cloudflare, with a Cloudflare Origin Certificate on the instance

**Status:** Accepted
**Owner:** Max
**Issue:** #79 (F6-03)
**Supersedes:** —
**Superseded by:** —

---

## Context

F6-03 asks for "an SSL certificate with forced HTTPS" and an acceptance
criterion of "a certificate valid for the published host". The instance has been
serving HTTPS since 2026-09-06, but with a **self-signed** certificate
(`CN=34.51.123.31`) — Path B in `deploy/README.md`. Every visitor gets a
full-page browser warning, and the AC is not met: a self-signed certificate is
not *valid for* anything a browser will trust.

`docs/scope.md` §8 already diagnosed why the delivery was stuck there. The
published host is the GCP instance itself; it has no DNS name; Let's Encrypt does
not issue for a bare IP; so Path A (a publicly trusted certificate issued on the
box) had no hostname to be issued against. The scope note called reaching a
valid certificate "a deployment task and no longer a question for the Product
Owner" — this record is that task.

What unblocks it: a team member (Max) owns `maxthecoder.online` on Cloudflare
and can put the delivery under `mosaiq.maxthecoder.online`. That is a real
hostname pointing at the instance, which is all Path A ever needed. But once a
Cloudflare zone is in the path, the more robust option is to let Cloudflare
proxy the traffic: browsers then terminate TLS against Cloudflare's managed edge
certificate, and the origin only has to satisfy Cloudflare.

What is genuinely a judgement call. For proxying: the visitor-facing certificate
is issued and auto-renewed by Cloudflare with nothing to maintain on the box;
the origin IP stops being public; a CDN and DDoS filtering come for free; and
the origin certificate can be a 15-year Cloudflare Origin CA pair that never
needs a renewal timer. Against it: the delivery now depends on one team
member's personal Cloudflare account and domain; Cloudflare's edge IP ranges
have to be trusted in two places (the firewall and NGINX's `real_ip`) and
refreshed when they change; and "the certificate the grader sees" is Cloudflare's,
not one issued for MOSAIQ on the instance — which is a fine reading of the AC but
a different one from Path A. On a project that will be dismantled after grading,
the low-maintenance path wins; a longer-lived service might prefer to own its
certificate outright.

The instance's external address was already a reserved static IP (`mosaiq-ip`,
`34.51.123.31`), so pointing a DNS record at it is safe.

## Decision

MOSAIQ is published at **`mosaiq.maxthecoder.online`**, an `A` record for
`34.51.123.31` in the Cloudflare zone `maxthecoder.online`, with Cloudflare
**proxying enabled** (orange cloud). Browsers connect to Cloudflare's edge and
see its managed certificate for the hostname. Cloudflare connects to the origin
over TLS with the encryption mode set to **Full (strict)**, and NGINX on the
instance presents a **Cloudflare Origin CA certificate** (RSA 2048, 15-year
validity) at `/etc/nginx/tls/mosaiq.{crt,key}` — a certificate trusted only by
Cloudflare, which is the only client that reaches the origin.

NGINX trusts Cloudflare's published address ranges
(`deploy/nginx/cloudflare-real-ip.conf`) and reads `CF-Connecting-IP`, so the
access log and the `X-Forwarded-For` it passes to gunicorn carry the real
visitor rather than an edge IP. HSTS is staged up from `max-age=300` to 30 days,
to reach one year once the edge certificate has auto-renewed once.

Let's Encrypt on the origin (Path A) and a self-signed pair (Path B) stay
documented in `deploy/README.md` as fallbacks that write the same two file
paths. Optionally — it needs a permission no team member routinely holds — the
GCP firewall is narrowed so `:443` is reachable only from Cloudflare's ranges
and public `:80` is removed; the design does not depend on this being done.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Stay on the self-signed certificate (Path B) | The AC is not met and every visitor gets a full-page interstitial. Acceptable for local work and the offline demo, not for a graded published URL. |
| Let's Encrypt on the origin, Cloudflare DNS-only (Path A) | Works, needs no Cloudflare account feature, and keeps the certificate MOSAIQ's own. But it publishes the origin IP, puts a renewal timer on the box that has to keep working unattended, and gives up the edge cache and DDoS filtering that proxying includes at no cost. Kept as the documented fallback. |
| Buy a dedicated domain for the project | A real yearly cost and an ownership question — someone has to hold the registration after the course ends. The team already has a usable domain and no reason to pay for another. |
| Cloudflare Tunnel (`cloudflared` on the instance) | Removes every inbound port, which is attractive. But it is a second long-lived daemon whose job is to be a network entry point — close enough to "a second deployable unit" that it would want its own ADR against `docs/scope.md` C-3/C-7 — and it is more moving parts than a delivery with one 2-vCPU box needs. |
| Cloudflare SSL mode "Flexible" (edge HTTPS, plaintext to origin) | No certificate on the origin at all, so it looks simplest. It leaves the Cloudflare→origin hop unencrypted over the public internet and lets a redirect loop form when the app knows it is behind TLS. "Full (strict)" is the only mode that actually verifies the origin. |

## Consequences

**What this makes easy.** `https://mosaiq.maxthecoder.online/` loads with a
valid padlock and no warning, so the F6-03 AC and deliverable 12 ("application
running in the cloud", reachable) are genuinely met. The visitor-facing
certificate renews itself at Cloudflare with nothing on the instance to break.
The origin IP is no longer exposed by the published URL, and the edge absorbs
volumetric traffic before it reaches a small VM. HSTS can now be raised, because
the chain a browser pins is Cloudflare's managed one.

**What this makes hard.** The delivery depends on **one team member's** personal
Cloudflare account and domain registration: if either lapses, the published URL
goes down and no one else on the team can fix it from inside project
`iac-dev-01`. Cloudflare's edge IP ranges are trusted in two places —
`deploy/nginx/cloudflare-real-ip.conf` and, if the firewall is hardened, the
`mosaiq-allow-https` rule — and both need refreshing on the rare occasions
Cloudflare changes them; a stale `real_ip` list silently attributes every
request to an edge IP in the audit log. "Full (strict)" is a zone-wide setting
unless a Configuration Rule scopes it to the one hostname, so enabling it can
affect other things Max serves from the same domain. Debugging no longer works
by curling the IP: verification has to go through the hostname, and a TLS error
can now be at the edge or at the origin.

**What must now be true elsewhere.** `deploy/nginx/mosaiq.conf` carries
`server_name mosaiq.maxthecoder.online`, the `cloudflare-real-ip.conf` include
and `real_ip_header CF-Connecting-IP`; the two files deploy together or `nginx -t`
fails. `TRUSTED_PROXY_HOPS` stays `1` — NGINX is still the single proxy in front
of gunicorn, and it now hands over exactly one forwarded address. `docs/infra.md`
records the DNS record, the reserved IP, the certificate path and the firewall
state, because none of that is reproducible from this repository. `docs/scope.md`
§8 no longer says F6-03 is stuck on Path B. The F6-05 final verification and the
F6-07 deployment evidence use `https://mosaiq.maxthecoder.online/`, not the IP.

## Compliance

```bash
# The instance config names the published host and trusts Cloudflare for real_ip.
grep -q 'server_name mosaiq.maxthecoder.online;' deploy/nginx/mosaiq.conf
grep -q 'include conf.d/cloudflare-real-ip.conf;' deploy/nginx/mosaiq.conf
grep -q 'real_ip_header .*CF-Connecting-IP' deploy/nginx/mosaiq.conf

# The app is not told about an extra proxy hop it cannot see.
grep -q 'TRUSTED_PROXY_HOPS' .env.example    # documented; value on the instance stays 1

# From the internet: valid chain at the edge, redirect from HTTP.
curl -sI  https://mosaiq.maxthecoder.online/ | head -1            # HTTP/2 200
curl -svo /dev/null https://mosaiq.maxthecoder.online/ 2>&1 \
  | grep -E 'SSL certificate verify ok|subject:|issuer:'
curl -sI  http://mosaiq.maxthecoder.online/  | grep -i '^location: https://'

# The origin certificate is a Cloudflare Origin CA cert, not self-signed.
ssh mosaiq-deployment-vm \
  'sudo openssl x509 -in /etc/nginx/tls/mosaiq.crt -noout -issuer' \
  | grep -qi 'CloudFlare Origin'

# With the firewall hardened, the origin refuses a non-Cloudflare client.
curl --resolve mosaiq.maxthecoder.online:443:34.51.123.31 \
     -sI https://mosaiq.maxthecoder.online/ --max-time 6    # times out
```

The end-to-end check — sign in as the real administrator through the hostname,
load a page, confirm the access log shows the real client IP — is attached to
#79 with the F6-03 evidence.
