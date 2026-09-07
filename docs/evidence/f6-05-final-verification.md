# F6-05 — Final verification of the published delivery

Checked on 2026-09-07 against `https://mosaiq.maxthecoder.online`, as an
outsider sees it. Every request below was made without cookies and without a
prior session, which is what the "private window" criterion asks for: a `curl`
with no cookie jar cannot reuse a warm login because it has none.

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

## AC 2 — every link resolves, with one large exception

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

## AC 3 — not verified here

"Given the application, when it is used end to end, then it behaves as the
acceptance criteria state" needs a signed-in session. The instance now has one
administrator, whose password was typed at a prompt and exists nowhere in this
repository ([`f6-07-proof-of-deployment.md`](f6-07-proof-of-deployment.md)), so
this criterion is for a person holding that password. What is verified above is
the anonymous surface: the site answers, refusals behave, and the documentation
does not.

The per-story behaviour that AC 3 would re-check is already recorded —
[F5-01](f5-01-functional-tests.md) for the flows, [F5-02](f5-02-negative-tests.md)
for the refusals, [F5-04](f5-04-key-functionality.md) for the screenshots — but
against a local instance rather than the published one, which is the difference
AC 3 exists to close.

## What has to happen before this story closes

1. Publish `docs/` from the released branch, using the documented command.
   [ADR-0011](../adr/0011-one-environment-deployed-from-main.md) makes `main`
   the branch the instance reflects, so publishing from `develop` would put
   unreleased documents on the delivered site.
2. Re-check the three directory listings against the repository. The counts in
   the table above are the check.
3. Decide whether the manual step becomes part of the deploy. It is the second
   time a delivery artifact has been complete in the repository and absent from
   where it is graded, and a step that must be remembered will be forgotten
   again.
