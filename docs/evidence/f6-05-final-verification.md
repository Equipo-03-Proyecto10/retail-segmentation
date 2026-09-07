# F6-05 — Final verification of the published delivery

Checked on 2026-09-07 against `https://mosaiq.maxthecoder.online`, as an
outsider sees it. Every request below was made without cookies and without a
prior session, which is what the "private window" criterion asks for: a `curl`
with no cookie jar cannot reuse a warm login because it has none.

This record is chronological. The first check found most of the documentation
missing from the published host; republishing it exposed a second, quieter
fault; both are resolved and re-verified below. The failed states are kept
because the way each one hid is the useful part.

## AC 1 — the published site works with no prior session

```
$ curl -sS -o /dev/null -w "%{http_code}\n" https://mosaiq.maxthecoder.online/
200

$ curl -sS -o /dev/null -w "%{http_code}\n" https://mosaiq.maxthecoder.online/login
200
```

Protected routes refuse rather than erroring, and say where to sign in:

```
302 -> /login?next=/admin/users/new
302 -> /login?next=/segment-run/
302 -> /login?next=/audit/
302 -> /login?next=/catalog/
```

Plain HTTP redirects to HTTPS at Cloudflare's edge, and the origin accepts no
port 80 at all ([`f6-07-proof-of-deployment.md`](f6-07-proof-of-deployment.md)).

## AC 2 — first check: most of the documentation was not being served

A crawl from `/`, `/login` and `/docs/`, following every `href` and `src` to a
depth of five, reached **228 URLs and every one returned 200**. That covers 76
design-system pages, 49 ADR URLs, 44 datastore URLs, 42 under `evidence/`, the
11 top-level documents and 31 stylesheets, scripts and images.

**That result is misleading, and the way it misleads is the finding.** A crawler
follows links out of HTML. The documentation is served as raw Markdown, so
nothing links onward from a `.md` file: every image reference inside every
evidence document is invisible to a crawl, and so is every document no other
page happens to name. The 228 green results are real and prove very little.

Checked directly instead, against the directory listings NGINX itself serves:

| | In the repository (on `main`) | Served |
|---|---|---|
| `docs/adr/*.md` | 16 | 8 |
| `docs/evidence/*.md` | 17 | 1 |
| `docs/evidence/*.png` | 50 | **0** |

```
$ curl -sS https://mosaiq.maxthecoder.online/docs/evidence/

../
f2-07-integrity-verification.md
```

Every screenshot in the delivery returns 404 from the published host:

```
404  /docs/evidence/f5-04-signin-1440.png
404  /docs/evidence/f4-01-menu-admin-1440.png
404  /docs/evidence/f3-10-segment-run-1440.png
404  /docs/evidence/instance-findings-conflict-1440.png
```

The served ADR set stops at 0006. Everything from
[ADR-0007](../adr/0007-permissions-in-code-with-a-default-deny-middleware.md)
onward — the authorization middleware, the deactivated demonstration accounts,
NGINX, the consultation module, the single-environment decision, the ancestry
rule, Cloudflare, service transactions — is absent from the page that is
supposed to carry the documentation.

### Why

`docs/` is published by a manual command, documented in
[`../../deploy/README.md`](../../deploy/README.md) under "Run after any change
to `docs/`":

```bash
gcloud compute scp --recurse docs mosaiq-deployment-vm:/tmp/mosaiq-docs …
sudo rm -rf /opt/mosaiq/docs && sudo mv /tmp/mosaiq-docs /opt/mosaiq/docs
```

`deploy/deploy.sh` does not mention `docs` at all, and neither does the F6-06
workflow. The pipeline that ships code on every merge to `main` has never
shipped a document. The command was run once, around the time ADR-0006 was
written, and the published copy has been that snapshot since.

This is deliverable 14 — "web page carrying all documentation and evidence" —
resting on a step no automation performs and no check notices. It is also why
deliverable 10 is unreachable in practice: the screenshots exist, are committed,
and are named correctly from documents that are themselves not served.

Nothing here is a defect in the application, the NGINX configuration, or the
deploy pipeline as written. `location /docs/` is correct and `autoindex on` is
what made the gap visible at all. The fault is that publishing documentation was
left as a human habit.

## Resolved on 2026-09-07

`docs/` was republished from `main` with the documented command, and the tree
arrived complete. It was still unreadable, for a reason the file counts could
not show.

### SELinux, and a cache that hid it

Every file returned **403** rather than 404 — present, and refused. POSIX
permissions were correct throughout: `755` directories, `644` files, owned
`mosaiq:mosaiq`, with NGINX running as `nginx`. The labels were not:

```
$ ls -Z /opt/mosaiq/docs
unconfined_u:object_r:user_tmp_t:s0 adr
unconfined_u:object_r:user_tmp_t:s0 backlog.md
unconfined_u:object_r:user_tmp_t:s0 design-system

$ getenforce
Enforcing
```

The publish command stages the tree in `/tmp` and `mv`s it into place, and `mv`
preserves the SELinux context. Everything moved carried `user_tmp_t`, which
`httpd_t` may not read. The documented procedure in
[`../../deploy/README.md`](../../deploy/README.md) has no `restorecon` step —
while [`../../deploy/postgresql/README.md`](../../deploy/postgresql/README.md)
does, for exactly this reason, on a file installed the same way. The knowledge
was in the repository and did not reach the one procedure that needed it most.

**Cloudflare made the diagnosis harder, and would have made verification lie.**
Immediately after the republish, a stylesheet answered 200 and a screenshot
answered 403 — with the same ownership, the same mode and the same label:

```
/docs/design-system/base.css        HTTP/2 200   cf-cache-status: HIT
/docs/evidence/f5-04-signin-…png    HTTP/2 403   cf-cache-status: BYPASS
```

The 200 was an edge copy from before the replacement. Anyone spot-checking a
few URLs could have concluded the publish worked, and watched it decay as the
cache expired. **Verification of this host has to read `cf-cache-status`, or
send a cache-buster.** A bare `curl` is not enough.

### The fix

```bash
sudo dnf install -y policycoreutils-python-utils
sudo semanage fcontext -a -t httpd_sys_content_t "/opt/mosaiq/docs(/.*)?"
sudo restorecon -Rv /opt/mosaiq/docs
```

`semanage` records the rule in policy so it survives a filesystem relabel and
every future republish; `restorecon` applies it now. `chcon` alone would have
fixed the symptom and been undone by the next relabel.

```
$ sudo semanage fcontext -l | grep mosaiq
/opt/mosaiq/docs(/.*)?   all files   system_u:object_r:httpd_sys_content_t:s0

$ ls -Z /opt/mosaiq/docs/evidence/f5-04-signin-1440.png
unconfined_u:object_r:httpd_sys_content_t:s0  …
```

### Verified against the origin, not the crawl

Enumerated from `main` rather than followed from links, so nothing depends on
one page happening to name another:

```
files in main's docs/: 113
checked=113  non-200=0
```

All 113 — every ADR, every evidence document, all 50 screenshots, the design
system and the datastore designs. Deliverables 10 and 14 are reachable from the
published host.

## AC 3 — still not verified here

"Used end to end" needs a signed-in session. The instance's one administrator
has a password that exists nowhere in this repository by design, so that
criterion belongs to a person holding it.

## What should still change

The publish remains a manual command that no automation performs and no check
notices, and it now demonstrably has two failure modes rather than one — a stale
tree, and an unreadable one. Whether it joins the deploy is a decision for the
team, but this story found the same class of gap twice in one afternoon: an
artifact complete in the repository and absent from where it is graded.
